"""Deterministic onboarding: credentials never reach the shopping master agent."""

import logging
import math
import re
import time
import traceback
from pathlib import Path
from uuid import uuid4

from ..controller import DomainError
from ..models import SessionView
from .accounts import CONSENT_TEXT
from .conversation import text_message
from .store_login import LoginRateLimited, LoginStillPending, LoginSubmissionRejected

log = logging.getLogger(__name__)


def button_message(body, nonce, options):
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"onboard:{nonce}:{key}", "title": title}}
                    for key, title in options
                ]
            },
        },
    }


def content(message):
    if message.get("type") == "text":
        return message.get("text", {}).get("body", "").strip(), None
    if message.get("type") == "interactive":
        reply = (
            message.get("interactive", {}).get("button_reply")
            or message.get("interactive", {}).get("list_reply")
            or {}
        )
        return reply.get("title", ""), reply.get("id", "")
    return "", None


def logout_requested(text):
    """Explicit commands only: questions and negated speech must not revoke access."""
    text = re.sub(r"[.!?]+$", "", text.casefold().strip())
    text = re.sub(r"\b(?:pls|plz)\b", "please", text)
    if re.search(r"\b(?:not|don't|dont|never|nahi|nhi|mat)\b|नहीं|मत", text):
        return False
    return bool(re.fullmatch(
        r"(?:please |can you please |can you |could you |i want to |i would like to |mujhe |मुझे )?"
        r"(?:log(?: me)?\s*out|sign(?: me)?\s*out|लॉग\s*आउट)"
        r"(?: me)?(?: of (?:my |the )?account| from (?:my |the )?account)?"
        r"(?: please| now| kar do| kr do| kardo| krdo| karna hai| krna h| कर दो| कर दीजिए)?",
        text,
    ))


def account_status_requested(text):
    text = text.casefold()
    # Read-only account questions can be answered even when surrounding speech
    # is garbled. Never infer login/logout or a shopping action from this path.
    login = bool(re.search(r"\blog(?:ged)?[ -]?in\b|\bsessions?\b|लॉगिन|लॉग इन|सेशन", text))
    status = bool(re.search(r"\b(?:saved?|status|sessions?|connected|am i|do i|have|any|which|kya|hai|h|login status)\b|है|क्या|सेव|सेशन", text))
    store_question = bool(re.search(r"blinkit|instamart", text)) and bool(re.search(r"\b(?:connected|logged in|linked|saved)\b", text))
    return (login and status) or store_question


class Onboarding:
    def __init__(self, accounts, adapter, chats, payments, max_customers=100):
        self.accounts, self.adapter, self.chats, self.payments = accounts, adapter, chats, payments
        self.max_customers = max_customers
        self.accounts.db.execute(
            "CREATE TABLE IF NOT EXISTS login_requests(sender TEXT,provider TEXT,created_at REAL)"
        )
        columns = {row[1] for row in self.accounts.db.execute("PRAGMA table_info(login_requests)")}
        if "account_key" not in columns:
            self.accounts.db.execute("ALTER TABLE login_requests ADD COLUMN account_key TEXT")
        self.accounts.db.execute(
            "CREATE INDEX IF NOT EXISTS login_requests_by_account ON login_requests(account_key,created_at)"
        )
        self.accounts.db.commit()
        # An in-flight browser OTP challenge cannot safely survive a process restart.
        for row in self.accounts.db.execute("SELECT sender,state FROM onboarding").fetchall():
            state = self.accounts.box.open(row["state"])
            # Consent/phone prompts do not depend on a live browser and can
            # survive restart with their original expiry and consent checks.
            if state["stage"] not in {"ready", "store", "consent", "phone"}:
                self.accounts.begin(row["sender"], "store")

    def menu(self, sender, intro="Welcome to EasyDial. Which store would you like to connect?"):
        state = self.accounts.begin(sender)
        return [button_message(intro, state["nonce"], [("blinkit", "Blinkit"), ("instamart", "Instamart")])]

    async def cancel(self, sender, provider=None):
        await self.adapter.cancel(sender, provider)

    def account_status(self, sender):
        saved = [
            provider.title() for provider in self.accounts.connections(sender)
            if self.accounts.consented(sender, provider)
            and Path(self.accounts.session_path(sender, provider)).is_file()
        ]
        if not saved:
            return [text_message("No store login is saved. Send Connect to log in.")]
        return [text_message(
            "Saved login: " + ", ".join(saved) + ". Send Logout to remove saved logins."
        )]

    def confirm_logout(self, sender, shopping_state):
        providers = list(self.accounts.connections(sender))
        if not providers:
            return [text_message("No store login is saved. Send Connect to log in.")]
        nonce = uuid4().hex
        shopping_state["logout_confirmation"] = {
            "nonce": nonce, "providers": providers, "expires": time.time() + 600,
        }
        reply = button_message("Which grocery account should I sign out of?", nonce,
                               [(p, p.title()) for p in providers])
        for button in reply["interactive"]["action"]["buttons"]:
            button["reply"]["id"] = button["reply"]["id"].replace("onboard:", "account-logout:", 1)
        return [reply]

    async def account_request(self, sender, event_id, text, shopping_state, action=None):
        pending = shopping_state.get("logout_confirmation")
        if pending and pending["expires"] < time.time():
            shopping_state.pop("logout_confirmation", None)
            pending = None
        if action and action.startswith("account-logout:"):
            parts = action.split(":")
            if len(parts) != 3 or not pending or parts[1] != pending["nonce"] or parts[2] not in pending["providers"]:
                return [text_message("That logout choice expired. Please ask to sign out again.")]
            return await self.logout(sender, event_id, shopping_state, parts[2])
        if pending and text.casefold().strip() in pending["providers"]:
            return await self.logout(sender, event_id, shopping_state, text.casefold().strip())
        # Retain existing short commands; natural-language requests go to the master.
        if logout_requested(text):
            return await self.logout(sender, event_id, shopping_state)
        disconnect = re.fullmatch(r"disconnect (blinkit|instamart)", text.casefold().strip())
        if disconnect:
            return await self.logout(sender, event_id, shopping_state, disconnect[1])
        return None

    async def logout(self, sender, event_id, shopping_state, provider=None):
        # Revoke access before awaiting browser cleanup; no active draft or stale
        # payment button may survive logout, even with a pending merchant order.
        targets = (provider,) if provider else ("blinkit", "instamart")
        for target in targets:
            self.accounts.consent(sender, target, event_id, "withdrawn")
            self.accounts.forget(sender, target)
        view = shopping_state["view"]
        affected = not provider or not view["lines"] or view.get("synced_provider") == provider or any(
            line["offer"]["provider"] == provider for line in view["lines"]
        )
        shopping_state.pop("logout_confirmation", None)
        if affected:
            self.payments.revoke_customer(sender)
            shopping_state.clear()
            shopping_state.update({
                "view": SessionView(id=uuid4().hex, mode="live", address_label="").model_dump(mode="json"),
                "offers": {}, "choices": [], "paused": False, "last_timestamp": 0,
            })
        if provider:
            remaining = self.accounts.connections(sender)
            self.accounts.begin(sender, "ready" if remaining else "store")
            replies = [text_message(f"You're logged out of {provider.title()}. Its saved login was removed. Existing orders stay active.")]
        else:
            replies = self.menu(sender, "You're logged out. Saved logins removed. Existing orders stay active. Choose a store to log in again.")
        try:
            await self.cancel(sender, provider)
        finally:
            await self.chats.close_customer(sender, save=False)
        return replies

    async def handle(self, row, message, shopping_state):
        sender, event_id = row["sender"], row["id"]
        text, action = content(message)
        low = text.casefold().strip()
        account_reply = await self.account_request(sender, event_id, text, shopping_state, action)
        if account_reply is not None:
            return account_reply
        if account_status_requested(text):
            return self.account_status(sender)
        state = self.accounts.state(sender)
        connections = self.accounts.connections(sender)
        if not state:
            count = self.accounts.db.execute("SELECT COUNT(*) FROM onboarding").fetchone()[0]
            if count >= self.max_customers:
                return [text_message("EasyDial is full for this pilot. Please try again later.")]
        command = low in {"start", "login", "log in", "sign in", "connect", "connect store", "reconnect", "change address"}
        if command or not state:
            if shopping_state["view"]["lines"] or self.payments.existing(
                sender, shopping_state["view"]["id"]
            ):
                return [
                    text_message(
                        "Finish checking your current order or empty your basket before changing store connections."
                    )
                ]
            await self.cancel(sender)
            return self.menu(sender)
        if low == "cancel" and state["stage"] != "ready":
            await self.cancel(sender, state.get("provider"))
            self.accounts.begin(sender, "ready" if connections else "store")
            return [text_message("Setup cancelled. Send Connect whenever you are ready.")]
        if state["stage"] == "ready":
            if action and action.startswith("onboard:"):
                return [text_message("That setup choice has expired. Send Connect to manage your stores.")]
            if not any(self.accounts.consented(sender, p) for p in connections):
                return self.menu(sender)
            # Never forward bare phone numbers/OTPs accidentally sent after login to the LLM.
            if re.fullmatch(r"\+?[\d\s-]{4,18}", text) and len(re.sub(r"\D", "", text)) >= 4:
                return [
                    text_message(
                        "If this is a login code, do not send another. Send Connect to start a fresh login."
                    )
                ]
            return None
        if time.time() > state["expires"]:
            await self.cancel(sender, state.get("provider"))
            return self.menu(sender, "Setup expired. Choose a store to start again.")
        choice = None
        if action:
            prefix = f"onboard:{state['nonce']}:"
            if not action.startswith(prefix):
                return [text_message("That choice has expired. Please use the latest setup message.")]
            choice = action[len(prefix) :]
        stage = state["stage"]
        if stage == "store":
            provider = choice or low
            if provider not in {"blinkit", "instamart"}:
                return [button_message(
                    "First choose which store to connect below. I'll ask for your phone number next.",
                    state["nonce"], [("blinkit", "Blinkit"), ("instamart", "Instamart")],
                )]
            if shopping_state["view"]["lines"] or self.payments.existing(
                sender, shopping_state["view"]["id"]
            ):
                return [text_message("Finish your current basket or order before changing connections.")]
            state = self.accounts.begin(sender, "consent", provider=provider)
            return [
                button_message(
                    CONSENT_TEXT.format(store=provider.title()),
                    state["nonce"],
                    [("agree", "I agree"), ("decline", "Not now")],
                )
            ]
        provider = state["provider"]
        if stage == "consent":
            if choice == "decline" or low in {"not now", "no", "decline"}:
                self.accounts.consent(sender, provider, event_id, "declined")
                # Declining reconnection must also revoke reuse of the prior session.
                await self.chats.close_customer(sender, save=False)
                self.accounts.forget(sender, provider)
                self.accounts.begin(sender, "ready" if self.accounts.connections(sender) else "store")
                return [text_message("No login started. Send Connect if you change your mind.")]
            if choice != "agree" and low != "i agree":
                return [
                    button_message(
                        "Please choose I agree or Not now to continue.",
                        state["nonce"],
                        [("agree", "I agree"), ("decline", "Not now")],
                    )
                ]
            self.accounts.consent(sender, provider, event_id, "accepted")
            self.accounts.begin(sender, "phone", provider=provider)
            return [
                text_message(
                    f"What is the 10-digit Indian mobile number for your {provider.title()} account? Send Cancel to stop."
                )
            ]
        if not self.accounts.consented(sender, provider):
            return self.menu(sender, "Permission is needed before signing in. Choose a store.")
        try:
            if stage == "otp_wait":
                if choice == "different_number" or low == "use another number":
                    await self.cancel(sender, provider)
                    self.accounts.begin(sender, "phone", provider=provider)
                    return [text_message(f"What is the other 10-digit number for your {provider.title()} account?")]
                await self.adapter.wait_for_otp(sender, provider, timeout=10)
                state = self.accounts.begin(sender, "otp", provider=provider, phone=state["phone"])
                stage = "otp"
                if not re.fullmatch(r"\d{4,8}", text):
                    return [text_message(f"Enter the OTP sent by {provider.title()}. Send Cancel to stop.")]
            if stage == "phone":
                if choice == "different_number" or low == "use another number":
                    self.accounts.begin(sender, "phone", provider=provider)
                    return [text_message(f"What is the other 10-digit number for your {provider.title()} account? Send Cancel to stop.")]
                if choice == "retry_login" or low == "retry login":
                    text = state.get("retry_phone", "")
                    if not text:
                        return [text_message(f"What is the 10-digit number for your {provider.title()} account? Send Cancel to stop.")]
                phone = re.sub(r"[\s()-]", "", text)
                if phone.startswith("+91"):
                    phone = phone[3:]
                elif len(phone) == 12 and phone.startswith("91"):
                    phone = phone[2:]
                if not re.fullmatch(r"[6-9]\d{9}", phone):
                    return [text_message("Please type a valid 10-digit Indian mobile number.")]
                account_key = self.accounts.account_key(provider, phone)
                now = time.time()
                recent = self.accounts.db.execute(
                    "SELECT created_at FROM login_requests WHERE account_key=? AND created_at>? ORDER BY created_at DESC",
                    (account_key, now - 900),
                ).fetchall()
                if len(recent) >= 3:
                    minutes = max(1, math.ceil((recent[2][0] + 900 - now) / 60))
                    state["retry_phone"] = phone
                    state["expires"] = max(state["expires"], recent[2][0] + 1500)
                    self.accounts.put(sender, state)
                    return [button_message(
                        f"Login for this {provider.title()} number is paused for {minutes} minute{'s' if minutes != 1 else ''}. "
                        "Then tap Retry login. You can use another number or cancel now.",
                        state["nonce"],
                        [("retry_login", "Retry login"), ("different_number", "Use another number"), ("cancel", "Cancel")],
                    )]
                self.accounts.claim_account(sender, provider, phone, reserve=False)
                await self.chats.close_customer(sender)
                # Reconnection starts clean; a failed new login cannot silently reuse the old account.
                self.accounts.forget(sender, provider)
                with self.accounts.db:
                    self.accounts.db.execute(
                        "INSERT INTO login_requests(sender,provider,created_at,account_key) VALUES (?,?,?,?)",
                        (sender, provider, now, account_key),
                    )
                # Save a non-replayable stage BEFORE the external OTP request.
                self.accounts.begin(sender, "requesting", provider=provider, phone=phone)
                await self.adapter.request_otp(sender, provider, phone)
                self.accounts.begin(sender, "otp", provider=provider, phone=phone)
                return [
                    text_message(
                        f"Enter the OTP sent by {provider.title()}. It is used only for this login. Send Cancel to stop."
                    )
                ]
            if stage == "otp":
                if not re.fullmatch(r"\d{4,8}", text):
                    return [text_message("Please type only the OTP digits, or send Cancel.")]
                state["tries"] += 1
                self.accounts.put(sender, state)
                if state["tries"] > 3:
                    await self.cancel(sender, provider)
                    return self.menu(sender, "Too many incorrect attempts. Choose a store to reconnect.")
                addresses = await self.adapter.verify_otp(sender, provider, text)
                if addresses is None:
                    return [
                        text_message("That code was not accepted. Check it and try again, or send Cancel.")
                    ]
                self.accounts.claim_account(sender, provider, state["phone"])
                if not addresses:
                    await self.cancel(sender, provider)
                    return self.menu(
                        sender, "No saved address was found. Add one in the store app, then connect again."
                    )
                # Full text is retained encrypted and shown only to this sender.
                state = self.accounts.begin(sender, "address", provider=provider, addresses=addresses)
                return self.address_menu(state)
            if stage == "address":
                selected = choice or text
                if selected.startswith("address-"):
                    selected = selected[8:]
                if not selected.isdigit() or not 1 <= int(selected) <= len(state["addresses"]):
                    return self.address_menu(state)
                address = state["addresses"][int(selected) - 1]
                if any(p != provider for p in connections):
                    state = self.accounts.begin(sender, "same_address", provider=provider, address=address)
                    return [
                        button_message(
                            "Is this the same delivery address as your other connected store?\n"
                            + address["label"],
                            state["nonce"],
                            [("same", "Same address"), ("different", "Choose another")],
                        )
                    ]
                return await self.complete(sender, provider, address)
            if stage == "same_address":
                if choice == "same" or low == "same address":
                    return await self.complete(sender, provider, state["address"])
                if choice == "different" or low == "choose another":
                    addresses = await self.adapter.addresses(sender, provider)
                    state = self.accounts.begin(sender, "address", provider=provider, addresses=addresses)
                    return self.address_menu(state)
                return [text_message("Choose Same address or Choose another in the message above.")]
            return self.menu(sender, "Login was interrupted. Choose a store to reconnect.")
        except LoginSubmissionRejected:
            pending = self.accounts.state(sender)
            await self.cancel(sender, provider)
            state = self.accounts.begin(
                sender, "phone", provider=provider,
                retry_phone=pending.get("phone") or pending.get("retry_phone"),
            )
            return [button_message(
                f"{provider.title()} couldn’t start login. Please wait a moment, then tap Retry login.",
                state["nonce"], [("retry_login", "Retry login"), ("different_number", "Use another number"), ("cancel", "Cancel")],
            )]
        except LoginStillPending as error:
            log.warning("Store login awaiting OTP screen: provider=%s; request not repeated", provider)
            pending = self.accounts.state(sender)
            state = self.accounts.begin(sender, "otp_wait", provider=provider, phone=pending["phone"])
            return [button_message(
                ("The store says there have been too many login attempts. Please wait. Check login only checks the current screen; it won’t send another OTP."
                 if isinstance(error, LoginRateLimited) else
                 "The store hasn’t opened the OTP screen yet. Tap Check login to check the same request; I won’t send another OTP."),
                state["nonce"], [("check_login", "Check login"), ("different_number", "Use another number"), ("cancel", "Cancel")],
            )]
        except DomainError as error:
            await self.cancel(sender, provider)
            return self.menu(sender, error.message + " Choose a store to retry, or send Cancel.")
        except Exception as error:
            # Never log exception text/traceback: browser calls can include phone or OTP.
            frames = [f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(error.__traceback__)
                      if frame.filename.endswith("store_login.py")]
            reason = next((label for marker, label in [
                ("strict mode violation", "ambiguous_locator"),
                ("not an input", "not_input"),
                ("not supported", "unsupported_input"),
                ("Target closed", "browser_closed"),
            ] if marker in str(error)), "unclassified")
            log.warning("Store onboarding failed: provider=%s stage=%s category=%s reason=%s frames=%s",
                        provider, stage, type(error).__name__, reason, ",".join(frames))
            await self.cancel(sender, provider)
            return self.menu(
                sender, "I could not finish signing in. No order was placed. Choose a store to retry."
            )

    def address_menu(self, state):
        # A text list keeps full addresses intact; opaque buttons bind to one attempt.
        addresses = state["addresses"]
        lines = "\n".join(f"{i}. {a['label']}" for i, a in enumerate(addresses, 1))
        return [text_message("Choose your delivery address. Reply with its number:\n" + lines)]

    async def complete(self, sender, provider, address):
        data = await self.adapter.select_address(sender, provider, address)
        self.accounts.save_connection(sender, provider, data)
        await self.cancel(sender, provider)
        await self.chats.close_customer(sender)
        self.accounts.begin(sender, "ready")
        return [
            text_message(
                f"{provider.title()} connected.\nDelivery: {data['address']}\nSend a voice note or type what you need. Send Connect to add another store, or Disconnect {provider} to remove access."
            )
        ]
