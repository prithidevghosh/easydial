import asyncio
import fcntl
import hashlib
import hmac
import json
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from ..controller import DomainError
from ..master import OpenAIMaster
from ..providers import ProviderWorkers
from ..settings import Settings
from .conversation import ShoppingChat, text_message
from .payments import PaymentRequests, page_html
from .progress import TurnProgress
from .storage import Inbox
from .transport import MetaTransport

log = logging.getLogger(__name__)


def missing_settings(settings):
    fields = (
        "whatsapp_phone_number_id",
        "whatsapp_access_token",
        "whatsapp_verify_token",
        "meta_app_secret",
        "whatsapp_allowed_sender",
        "whatsapp_support_contact",
    )
    return [name.upper() for name in fields if not getattr(settings, name).strip()]


class Runtime:
    def __init__(self, settings, inbox, transport, chat):
        self.settings, self.inbox, self.transport, self.chat = settings, inbox, transport, chat
        self.wake = asyncio.Event()
        self.payments = PaymentRequests(inbox, settings.whatsapp_public_url)
        self.chat.payments = self.payments

    async def process(self):
        row = self.inbox.claim()
        if not row:
            return False
        state = self.inbox.load(row["sender"]) or self.chat.initial()
        message = json.loads(row["payload"])
        replies = []
        status = "done"
        progress = None
        try:
            if not self.inbox.window_open(row["sender"]):
                self.inbox.finish(row, state, [], "expired")
                return True
            if self.inbox.interrupted(row["sender"]) and not self.payments.existing(
                row["sender"], state["view"]["id"]
            ):
                raise DomainError(
                    "The previous request was interrupted. I haven't repeated it. "
                    "Please ask to review your basket again."
                )
            if row["timestamp"] < state.get("last_timestamp", 0):
                raise DomainError("An older message arrived late. Please repeat your latest request.")
            self.inbox.supersede_waiting_replies(row["sender"])
            if state.get("cart_checkpoint"):
                self.chat.workers.restore_checkpoint(state["cart_checkpoint"], state["view"]["id"])
            text, action_id = "", None
            if not state["paused"] and message["type"] in {"audio", "text", "interactive"}:
                progress = TurnProgress(
                    self.inbox,
                    self.transport,
                    row,
                    "Listening to your voice note…"
                    if message["type"] == "audio"
                    else "Checking your request…",
                )
                progress.start()
            if message["type"] == "audio":
                if state["paused"]:
                    self.inbox.finish(row, state, [], "paused")
                    return True
                audio = await self.transport.audio(message.get("audio", {}))
                client = self.chat.master.require_client()
                result = await client.audio.transcriptions.create(
                    model=self.settings.openai_transcription_model,
                    file=("recording.wav", audio, "audio/wav"),
                    prompt="Grocery conversation in Indian languages. Preserve brands, quantities, negation and corrections.",
                )
                text = result.text.strip()
                if not text:
                    raise DomainError("I couldn't hear any words. Please send another recording.")
            elif message["type"] == "text":
                text = message.get("text", {}).get("body", "").strip()
            elif message["type"] == "interactive":
                interactive = message.get("interactive", {})
                reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
                action_id = reply.get("id", "")
                text = reply.get("title", "")
            else:
                raise DomainError("Please send a voice recording or text describing your groceries.")
            if not text or len(text) > 3000:
                raise DomainError("Please send a shorter message describing your groceries.")
            # A numeric/voice reply quoting an old search must not select from a newer list.
            context_id = message.get("context", {}).get("id")
            if context_id and not action_id:
                context = self.inbox.db.execute(
                    "SELECT event_id FROM outbox WHERE remote_id=? AND sender=?",
                    (context_id, row["sender"]),
                ).fetchone()
                if not context or context[0] != state.get("last_reply_event"):
                    raise DomainError("That reply refers to an older message. Please use the latest choices.")
            state, replies = await self.chat.handle(
                text, state, action_id, progress=progress, customer=row["sender"]
            )
            state["last_timestamp"] = row["timestamp"]
            if replies:
                state["last_reply_event"] = row["id"]
        except DomainError as error:
            status = "rejected"
            replies = [text_message(error.message)]
        except Exception as error:
            # Never print payloads, URLs, tokens, provider addresses or exception details.
            log.warning("WhatsApp turn failed (%s); no automatic replay", type(error).__name__)
            status = "failed"
            replies = [
                text_message(
                    "I couldn't finish that request. The store cart may have changed. "
                    "Ask to review your basket, or contact support."
                )
            ]
        finally:
            if status in {"failed", "rejected"} and self.payments.existing(
                row["sender"], state["view"]["id"]
            ):
                from ..models import SessionView
                from .order_tracking import status_button

                replies = [
                    text_message(
                        "An order or payment attempt already exists. Check its status before trying again."
                    ),
                    status_button(SessionView.model_validate(state["view"])),
                ]
            if progress:
                await progress.close()
        self.inbox.finish(row, state, replies, status)
        return True

    async def send_one(self):
        row = self.inbox.claim_reply()
        if not row:
            return False
        if not self.inbox.window_open(row["sender"]):
            self.inbox.mark_reply(row["id"], "expired")
            return True
        try:
            payload = json.loads(row["payload"])
            if payload.get("type") == "payment_qr":
                try:
                    artifact = self.payments.qr_artifact(row["sender"], payload["request_id"])
                    remote_id = await self.transport.send_payment_qr(row["sender"], artifact)
                except DomainError as error:
                    remote_id = await self.transport.send(row["sender"], text_message(error.message))
            else:
                remote_id = await self.transport.send(row["sender"], payload)
            self.inbox.mark_reply(row["id"], "sent", remote_id)
        except Exception as error:
            # A timeout can occur after Meta accepted a message. Do not duplicate it blindly.
            log.warning("WhatsApp reply uncertain (%s); inspect local status", type(error).__name__)
            self.inbox.mark_reply(row["id"], "uncertain")
        return True

    async def run(self):
        while True:
            self.wake.clear()
            try:
                # Preserve turn/reply ordering before accepting another conversational turn.
                if await self.send_one():
                    continue
                if await self.process():
                    continue
            except Exception as error:
                log.error("WhatsApp worker paused (%s)", type(error).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=1)


def create_whatsapp_app(settings=None):
    settings = settings or Settings()
    missing = missing_settings(settings)
    if not re.fullmatch(r"v[0-9]+\.0", settings.whatsapp_api_version):
        raise ValueError("WHATSAPP_API_VERSION must look like v27.0")
    for name in ("whatsapp_phone_number_id", "whatsapp_allowed_sender"):
        value = getattr(settings, name)
        if value and not re.fullmatch(r"[0-9]{5,30}", value):
            raise ValueError(f"{name.upper()} must contain only digits, without + or spaces")

    @asynccontextmanager
    async def lifespan(app):
        if missing:
            yield
            return
        lock_path = Path(settings.whatsapp_database + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Only one WhatsApp worker may own this pilot database") from None
            inbox = Inbox(settings.whatsapp_database)
            # Prevent accidental reuse of a previous user's durable state after configuration edits.
            inbox.db.execute("CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY, fingerprint TEXT)")
            fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        settings.whatsapp_phone_number_id,
                        settings.whatsapp_allowed_sender,
                        settings.live_address_label,
                        settings.blinkit_browser_profile,
                        settings.instamart_browser_profile,
                    ]
                ).encode()
            ).hexdigest()
            existing = inbox.db.execute("SELECT fingerprint FROM binding WHERE id=1").fetchone()
            if existing and existing[0] != fingerprint:
                inbox.close()
                raise RuntimeError(
                    "WhatsApp account/address configuration changed; use a new private database"
                )
            with inbox.db:
                inbox.db.execute("INSERT OR IGNORE INTO binding VALUES (1,?)", (fingerprint,))
            workers, master = ProviderWorkers(settings), OpenAIMaster(settings)
            transport = MetaTransport(settings)
            runtime = Runtime(settings, inbox, transport, ShoppingChat(settings, workers, master))
            app.state.runtime = runtime
            task = asyncio.create_task(runtime.run())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                await transport.close()
                await workers.close()
                if master.client:
                    await master.client.close()
                inbox.close()
                app.state.runtime = None

    # Signed WhatsApp ingress plus opaque, expiring payment pages; never the local app session API.
    app = FastAPI(
        title="EasyDial WhatsApp", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.runtime = None

    @app.get("/health")
    async def health():
        return {
            "status": "setup_required" if missing else "ok",
            "channel": "whatsapp",
            "payments": False,
            "payment_link_host_configured": bool(settings.whatsapp_public_url),
        }

    @app.get("/pay/{token}")
    async def payment_page(token: str):
        runtime = app.state.runtime
        row = runtime.payments.lookup(token) if runtime else None
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        }
        if not row:
            return HTMLResponse(
                "This payment link expired or is unavailable. Check the store before trying again.",
                status_code=410,
                headers=headers,
            )
        return HTMLResponse(page_html(row), headers=headers)

    @app.get("/webhooks/whatsapp")
    async def verify(request: Request):
        if missing:
            raise HTTPException(503, "WhatsApp setup is incomplete")
        query = request.query_params
        if query.get("hub.mode") != "subscribe" or not hmac.compare_digest(
            query.get("hub.verify_token", ""), settings.whatsapp_verify_token
        ):
            raise HTTPException(403, "Verification failed")
        challenge = query.get("hub.challenge", "")
        if len(challenge) > 1000:
            raise HTTPException(400, "Invalid challenge")
        return PlainTextResponse(challenge, headers={"Cache-Control": "no-store"})

    @app.post("/webhooks/whatsapp")
    async def receive(request: Request):
        runtime = app.state.runtime
        if runtime is None:
            raise HTTPException(503, "WhatsApp setup is incomplete")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 256 * 1024:
                raise HTTPException(413, "Payload too large")
        expected = "sha256=" + hmac.new(settings.meta_app_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
            raise HTTPException(403, "Invalid signature")
        try:
            payload = json.loads(body)
            if payload.get("object") != "whatsapp_business_account":
                return {"ok": True}
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if value.get("metadata", {}).get("phone_number_id") != settings.whatsapp_phone_number_id:
                        continue
                    for message in value.get("messages", []):
                        if message.get("from") != settings.whatsapp_allowed_sender:
                            continue  # No message, media fetch, AI request or cart access for strangers.
                        timestamp = int(message.get("timestamp", 0))
                        if not time.time() - 86400 < timestamp <= time.time() + 60:
                            continue
                        if (
                            not isinstance(message.get("id"), str)
                            or not message["id"]
                            or len(message["id"]) > 512
                        ):
                            continue
                        if not isinstance(message.get("type"), str):
                            continue
                        runtime.inbox.enqueue(message)
                    for status in value.get("statuses", []):
                        runtime.inbox.delivery(status.get("id"), status.get("status"))
        except (ValueError, TypeError, AttributeError, KeyError):
            raise HTTPException(400, "Invalid event") from None
        runtime.wake.set()
        return {"ok": True}

    return app


app = create_whatsapp_app()
