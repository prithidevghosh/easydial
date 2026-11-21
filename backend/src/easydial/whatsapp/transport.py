import asyncio
import base64
import hashlib
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..controller import DomainError
from .presentation import prepare_outbound, qr_caption

log = logging.getLogger(__name__)

MAX_AUDIO = 8 * 1024 * 1024


class MetaTransport:
    def __init__(self, settings):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=35, follow_redirects=False)
        self.base = f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        self.headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}

    async def send(self, sender, payload, *, image_fallback=True):
        payload = prepare_outbound(payload)
        response = await self.http.post(
            f"{self.base}/{self.settings.whatsapp_phone_number_id}/messages",
            headers=self.headers,
            json={"messaging_product": "whatsapp", "to": sender, **payload},
        )
        # A definite image rejection is safe to replace with its useful text caption.
        # Never retry a timeout or uncertain send: it may already have been delivered.
        if (
            image_fallback
            and response.status_code == 400
            and payload.get("type") == "image"
            and payload["image"].get("caption")
        ):
            return await self.send(sender, {"type": "text", "text": {"body": payload["image"]["caption"]}})
        if response.is_error:
            # Log only numeric diagnostics, never recipient, token, URL, or message content.
            try:
                error = response.json().get("error", {})
                code = error.get("code")
                subcode = error.get("error_subcode")
                log.warning(
                    "Meta send rejected: HTTP %s, code %s, subcode %s",
                    response.status_code,
                    code if isinstance(code, int) else None,
                    subcode if isinstance(subcode, int) else None,
                )
            except (ValueError, AttributeError):
                log.warning("Meta send rejected: HTTP %s", response.status_code)
        response.raise_for_status()
        return response.json()["messages"][0]["id"]

    async def send_payment_qr(self, sender, artifact):
        # Upload the exact verified merchant PNG; never redraw or regenerate a payment request.
        if artifact["expires_at"] <= time.time():
            raise DomainError("This QR has expired. Check order status before paying again.")
        data = artifact["qr_data"]
        if not data.startswith("data:image/png;base64,"):
            raise DomainError("I couldn’t read the payment QR. Please check order status.")
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 5 * 1024 * 1024:
            raise DomainError("I couldn’t read the payment QR. Please check order status.")
        try:
            response = await self.http.post(
                f"{self.base}/{self.settings.whatsapp_phone_number_id}/media",
                headers=self.headers,
                data={"messaging_product": "whatsapp", "type": "image/png"},
                files={"file": ("blinkit-payment.png", raw, "image/png")},
            )
            response.raise_for_status()
            media_id = response.json()["id"]
        except Exception:
            # Uploading media cannot deliver a chat message, so a failure notice is safe here.
            raise DomainError(
                "I couldn’t attach the payment QR. Please check order status before trying again."
            ) from None
        if artifact["expires_at"] <= time.time():
            raise DomainError("This QR has expired. Check order status before paying again.")
        return await self.send(
            sender,
            {
                "type": "image",
                "image": {
                    "id": media_id,
                    "caption": qr_caption(artifact['total_paise']),
                },
            },
            image_fallback=False,
        )

    async def typing(self, message_id):
        response = await self.http.post(
            f"{self.base}/{self.settings.whatsapp_phone_number_id}/messages",
            headers=self.headers,
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            },
        )
        response.raise_for_status()

    async def audio(self, media):
        media_id = media.get("id", "")
        if not re.fullmatch(r"[0-9]+", media_id):
            raise DomainError("That recording could not be retrieved. Please record it again.")
        response = await self.http.get(
            f"{self.base}/{media_id}",
            headers=self.headers,
            params={"phone_number_id": self.settings.whatsapp_phone_number_id},
        )
        response.raise_for_status()
        meta = response.json()
        if int(meta.get("file_size", MAX_AUDIO + 1)) > MAX_AUDIO:
            raise DomainError("Please send a shorter voice note (at most 60 seconds and 8 MB).")
        # Only follow Meta's fresh authenticated metadata URL, never webhook/user URLs.
        url = urlparse(meta.get("url", ""))
        if url.scheme != "https" or url.hostname != "lookaside.fbsbx.com" or url.port not in {None, 443}:
            raise DomainError("The recording download host was not recognized. Please contact support.")
        mime = meta.get("mime_type", "").split(";")[0]
        if mime not in {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr", "audio/wav"}:
            raise DomainError("Please send an audio recording, up to 60 seconds.")
        data = bytearray()
        async with self.http.stream("GET", meta["url"], headers=self.headers) as download:
            download.raise_for_status()
            async for part in download.aiter_bytes():
                data.extend(part)
                if len(data) > MAX_AUDIO:
                    raise DomainError("Please send a shorter recording.")
        expected = meta.get("sha256")
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise DomainError("The recording download was incomplete. Please send it again.")
        return await normalize_audio(bytes(data))

    async def close(self):
        await self.http.aclose()


async def normalize_audio(data: bytes) -> bytes:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise DomainError("Voice processing needs FFmpeg on the server. You can send text meanwhile.", 503)
    with tempfile.TemporaryDirectory(prefix="easydial-audio-") as folder:
        source, output = Path(folder) / "input", Path(folder) / "recording.wav"
        source.write_bytes(data)
        process = await asyncio.create_subprocess_exec(
            executable,
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-t",
            "61",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode or not output.exists():
            raise DomainError("I couldn't read that recording. Please send another voice note.", 422)
        import wave

        with wave.open(str(output)) as wav:
            duration = wav.getnframes() / wav.getframerate()
        if not 0 < duration <= 60:
            raise DomainError("Please keep each voice note to 60 seconds or less.", 422)
        return output.read_bytes()
