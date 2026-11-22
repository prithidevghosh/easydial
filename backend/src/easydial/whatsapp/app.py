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
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from ..controller import DomainError
from ..master import OpenAIMaster
from ..providers import ProviderWorkers
from ..settings import Settings
from .account_requests import TRANSCRIPTION_PROMPT
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
    if settings.whatsapp_multiuser:
        fields = tuple(x for x in fields if x != "whatsapp_allowed_sender") + ("customer_session_key",)
    return [name.upper() for name in fields if not getattr(settings, name).strip()]


class Runtime:
    def __init__(self, settings, inbox, transport, chat):
        self.settings, self.inbox, self.transport, self.chat = settings, inbox, transport, chat
        self.wake = asyncio.Event()
        self.payments = PaymentRequests(inbox, settings.whatsapp_public_url)
        self.chat.payments = self.payments
        self.onboarding = self.customer_chats = None
        if settings.whatsapp_multiuser:
            from .accounts import Accounts, CustomerChats
            from .onboarding import Onboarding
            from .store_login import StoreLogin

            if not inbox.secret_box:
                raise ValueError("Multi-user ingress requires encrypted queue storage")
            accounts = Accounts(inbox.db, inbox.secret_box, settings.customer_session_directory)
            self.customer_chats = CustomerChats(settings, accounts, chat.master, self.payments)
            self.onboarding = Onboarding(
                accounts,
                StoreLogin(settings, accounts),
                self.customer_chats,
                self.payments,
                settings.onboarding_max_customers,
            )

    async def process(self):
        row = self.inbox.claim()
        if not row:
            return False
        state = self.inbox.load(row["sender"]) or self.chat.initial()
        message = self.inbox.decode_event(row)
        chat = self.chat
        replies = []
        status = "done"
        progress = None
        completion = None
        try:
            if not self.inbox.window_open(row["sender"]):
                self.inbox.finish(row, state, [], "expired")
                return True
            if row["timestamp"] < state.get("last_timestamp", 0):
                raise DomainError(
                    "An older message arrived late. Please use the latest setup or shopping choices."
                )
            if self.onboarding:
                # This runs before media download, transcription, model context or cart recovery.
                onboarding_replies = await self.onboarding.handle(row, message, state)
                if onboarding_replies is not None:
                    self.inbox.supersede_waiting_replies(row["sender"])
                    connection_state = self.onboarding.accounts.state(row["sender"]) or {}
                    if (
                        connection_state.get("stage") == "ready"
                        and not state["view"]["lines"]
                        and not self.payments.existing(row["sender"], state["view"]["id"])
                    ):
                        state = self.chat.initial()
                    state["last_timestamp"] = max(row["timestamp"], state.get("last_timestamp", 0))
                    state["last_reply_event"] = row["id"]
                    self.inbox.finish(row, state, onboarding_replies)
                    return True
                chat = await self.customer_chats.get(row["sender"])
                if not state["view"]["lines"]:
                    state["view"]["address_label"] = chat.settings.live_address_label
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
            state, completion = self.payments.completion(row["sender"], state)
            if completion:
                from .order_completion import release_checkpoint
                release_checkpoint(chat.workers, completion["order_id"])
            if state.get("cart_checkpoint"):
                chat.workers.restore_checkpoint(state["cart_checkpoint"], state["view"]["id"])
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
                client = chat.master.require_client()
                result = await client.audio.transcriptions.create(
                    model=self.settings.openai_transcription_model,
                    file=("recording.wav", audio, "audio/wav"),
                    prompt=TRANSCRIPTION_PROMPT,
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
            if self.onboarding and message["type"] == "audio":
                from .onboarding import account_status_requested

                account_reply = await self.onboarding.account_request(row["sender"], row["id"], text, state)
                if account_reply is not None or account_status_requested(text):
                    replies = account_reply if account_reply is not None else self.onboarding.account_status(row["sender"])
                    self.inbox.supersede_waiting_replies(row["sender"])
                    state["last_timestamp"] = row["timestamp"]
                    state["last_reply_event"] = row["id"]
                    if progress:
                        await progress.close()
                        progress = None
                    self.inbox.finish(row, state, replies)
                    return True
            # A numeric/voice reply quoting an old search must not select from a newer list.
            context_id = message.get("context", {}).get("id")
            if context_id and not action_id:
                context = self.inbox.db.execute(
                    "SELECT event_id FROM outbox WHERE remote_id=? AND sender=?",
                    (context_id, row["sender"]),
                ).fetchone()
                if not context or context[0] != state.get("last_reply_event"):
                    raise DomainError("That reply refers to an older message. Please use the latest choices.")
            if self.onboarding:
                state["connected_stores"] = list(self.onboarding.accounts.connections(row["sender"]))
            state, replies = await chat.handle(
                text, state, action_id, progress=progress, customer=row["sender"]
            )
            if len(replies) == 1 and replies[0].get("type") == "account_action":
                if not self.onboarding:
                    replies = [text_message("Account setup is unavailable. Please contact support.")]
                elif replies[0]["action"] == "account_clarify":
                    replies = self.onboarding.confirm_logout(row["sender"], state)
                elif replies[0]["action"] == "logout":
                    replies = await self.onboarding.logout(row["sender"], row["id"], state, replies[0].get("provider"))
                else:
                    replies = self.onboarding.account_status(row["sender"])
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
            elif status in {"failed", "rejected"} and state["view"]["lines"]:
                replies.append({"type": "interactive", "interactive": {
                    "type": "button", "body": {"text": "Your basket is saved. Review it to continue."},
                    "action": {"buttons": [{"type": "reply", "reply": {
                        "id": f"review:{state['view']['version']}:current", "title": "Review basket"
                    }}]},
                }})
            if progress:
                await progress.close()
        state, newly_completed = self.payments.completion(row["sender"], state)
        completion = newly_completed or completion
        if completion:
            from .order_completion import release_checkpoint
            release_checkpoint(chat.workers, completion["order_id"])
        self.inbox.finish(row, state, replies, status, completed_order=completion)
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
            watch_order_id = payload.pop("_watch_order_id", None)
            if payload.get("type") == "payment_qr":
                try:
                    artifact = self.payments.qr_artifact(row["sender"], payload["request_id"])
                    remote_id = await self.transport.send_payment_qr(row["sender"], artifact)
                    request = self.inbox.db.execute("SELECT order_id FROM payment_requests WHERE id=? AND sender=?", (payload["request_id"], row["sender"])).fetchone()
                    self.payments.schedule_check(row["sender"], request["order_id"], reset=True)
                except DomainError as error:
                    remote_id = await self.transport.send(row["sender"], text_message(error.message))
            else:
                remote_id = await self.transport.send(row["sender"], payload)
            if watch_order_id:
                self.payments.schedule_check(row["sender"], watch_order_id, reset=True)
            self.inbox.mark_reply(row["id"], "sent", remote_id)
        except Exception as error:
            # A timeout can occur after Meta accepted a message. Do not duplicate it blindly.
            log.warning("WhatsApp reply uncertain (%s); inspect local status", type(error).__name__)
            self.inbox.mark_reply(row["id"], "uncertain")
        return True

    async def check_due_order(self):
        from ..models import SessionView
        from .order_tracking import status_messages

        job = self.inbox.db.execute(
            "SELECT * FROM automatic_order_checks WHERE done=0 AND due_at<=? ORDER BY due_at LIMIT 1",
            (time.time(),),
        ).fetchone()
        if not job:
            return False
        sender, order_id = job["sender"], job["order_id"]
        attempt = self.payments.monitor.get(sender, order_id)
        state = self.inbox.load(sender)
        allowed = bool(state and not state.get("paused") and self.inbox.window_open(sender))
        if self.onboarding:
            connections = self.onboarding.accounts.connections(sender)
            provider = json.loads(attempt["snapshot"])["review"]["provider"] if attempt else ""
            allowed = allowed and provider in connections and self.onboarding.accounts.consented(sender, provider)
        if not allowed or not attempt or attempt["status"] not in {"pending", "confirmed", "failed"}:
            with self.inbox.db:
                self.inbox.db.execute("UPDATE automatic_order_checks SET done=1 WHERE sender=? AND order_id=?", (sender, order_id))
            return True
        try:
            chat = await self.customer_chats.get(sender) if self.customer_chats else self.chat
            result = (json.loads(attempt["result"]) if attempt["status"] in {"confirmed", "failed"}
                      else await self.payments.monitor.check(sender, order_id, chat.workers, max_checks=1))
        except Exception:
            result = {"status": "pending"}
        final = result["status"] != "pending"
        exhausted = job["attempts"] + 1 >= 3
        if final:
            from .order_completion import release_checkpoint
            view = SessionView.model_validate_json(attempt["snapshot"])
            state, completion = self.payments.completion(sender, state)
            if completion:
                release_checkpoint(chat.workers, completion["order_id"])
            event_id = "auto-status:" + order_id
            state["last_reply_event"] = event_id
            self.inbox.finish({"id": event_id, "sender": sender}, state,
                              status_messages(result, view), completed_order=completion)
        with self.inbox.db:
            self.inbox.db.execute(
                "UPDATE automatic_order_checks SET attempts=attempts+1,due_at=?,done=? WHERE sender=? AND order_id=?",
                (time.time() + 3, int(final or exhausted), sender, order_id),
            )
        return True

    async def run(self):
        while True:
            self.wake.clear()
            try:
                if self.onboarding:
                    await self.onboarding.adapter.expire()
                # Preserve turn/reply ordering before accepting another conversational turn.
                if await self.send_one():
                    continue
                if await self.check_due_order():
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
            box = None
            if settings.whatsapp_multiuser:
                from .security import SecretBox

                box = SecretBox(settings.customer_session_key)
            inbox = Inbox(settings.whatsapp_database, box)
            # Prevent accidental reuse of a previous user's durable state after configuration edits.
            inbox.db.execute("CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY, fingerprint TEXT)")
            from .binding import bind

            try:
                bind(inbox.db, settings)
            except RuntimeError:
                inbox.close()
                raise
            # No pilot account/browser can be reached from the multi-user runtime.
            runtime_settings = (
                settings.model_copy(
                    update={
                        "blinkit_browser_profile": "",
                        "instamart_browser_profile": "",
                        "blinkit_storage_state": "",
                        "blinkit_verified_location": "",
                        "blinkit_verified_address": "",
                        "instamart_verified_location": "",
                        "instamart_verified_address": "",
                        "live_address_label": "",
                    }
                )
                if settings.whatsapp_multiuser
                else settings
            )
            workers, master = ProviderWorkers(runtime_settings), OpenAIMaster(settings)
            transport = MetaTransport(settings)
            runtime = Runtime(settings, inbox, transport, ShoppingChat(runtime_settings, workers, master))
            app.state.runtime = runtime
            task = asyncio.create_task(runtime.run())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                if runtime.onboarding:
                    await runtime.onboarding.adapter.close()
                    await runtime.customer_chats.close()
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

    def join_link():
        number = settings.whatsapp_business_number
        if not re.fullmatch(r"[1-9][0-9]{7,14}", number):
            raise HTTPException(503, "EasyDial join number has not been configured")
        return f"https://wa.me/{number}?text=Start"

    @app.get("/join")
    async def join():
        return RedirectResponse(join_link(), status_code=307, headers={"Cache-Control": "no-store"})

    @app.get("/join/qr.png")
    async def join_qr():
        import io

        import qrcode

        stream = io.BytesIO()
        qrcode.make(join_link()).save(stream, format="PNG")
        return Response(stream.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

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
                        sender = message.get("from")
                        if not isinstance(sender, str) or not re.fullmatch(r"[0-9]{5,20}", sender):
                            continue
                        if not settings.whatsapp_multiuser and sender != settings.whatsapp_allowed_sender:
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
