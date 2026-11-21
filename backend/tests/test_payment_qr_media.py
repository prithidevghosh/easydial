import base64
import json
import time

import httpx
import pytest

from easydial.controller import DomainError
from easydial.whatsapp.transport import MetaTransport
from tests.test_payment_handoff import qr
from tests.test_whatsapp import settings


async def test_qr_uploads_exact_png_and_sends_image_id_without_link(tmp_path):
    data = qr("upi://pay?pa=test@bank&am=123&cu=INR&tr=test")
    raw = base64.b64decode(data.split(",")[1])
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/media"):
            assert raw in request.content and b"image/png" in request.content
            return httpx.Response(200, json={"id": "media123"})
        body = json.loads(request.content)
        assert body["type"] == "image"
        assert body["image"]["id"] == "media123"
        assert "link" not in body["image"]
        assert "QR above" in body["image"]["caption"]
        return httpx.Response(200, json={"messages": [{"id": "sent123"}]})

    t = MetaTransport(settings(tmp_path))
    await t.http.aclose()
    t.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (
        await t.send_payment_qr(
            "recipient", {"qr_data": data, "total_paise": 12300, "expires_at": time.time() + 60}
        )
        == "sent123"
    )
    assert len(calls) == 2
    with pytest.raises(DomainError, match="expired"):
        await t.send_payment_qr("recipient", {"qr_data": data, "total_paise": 12300, "expires_at": 0})
    assert len(calls) == 2
    await t.close()


async def test_rejected_qr_does_not_send_a_caption_claiming_image_exists(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(200, json={"id": "media123"})
            if request.url.path.endswith("/media")
            else httpx.Response(400, json={"error": {"code": 100}})
        )

    t = MetaTransport(settings(tmp_path))
    await t.http.aclose()
    t.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await t.send_payment_qr(
            "recipient",
            {
                "qr_data": qr("upi://pay?pa=test@bank&am=123&cu=INR&tr=test"),
                "total_paise": 12300,
                "expires_at": time.time() + 60,
            },
        )
    assert len(calls) == 2
    await t.close()
