"""Interactive local credential setup. Never makes API calls or sends messages."""

import argparse
import getpass
import secrets
import shutil

from local_settings import ENV, save_settings

from easydial.settings import Settings
from easydial.whatsapp.app import missing_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Show readiness only, never credential values")
    args = parser.parse_args()
    settings = Settings(_env_file=ENV)
    if args.check:
        missing = missing_settings(settings)
        print("WhatsApp configuration: " + ("ready" if not missing else "missing " + ", ".join(missing)))
        print("OpenAI: " + ("configured" if settings.openai_api_key else "missing OPENAI_API_KEY"))
        print("FFmpeg: " + ("installed" if shutil.which("ffmpeg") else "missing"))
        print("Store address: " + ("configured" if settings.live_address_label else "missing"))
        print("This checks local settings only; it does not verify Meta access or send a message.")
        return
    print("Enter values from Meta API Setup and App Settings > Basic. Blank keeps an existing value.")
    updates = {}
    fields = [
        ("WHATSAPP_PHONE_NUMBER_ID", "Meta test/business Phone Number ID (not the actual phone number)"),
        ("WHATSAPP_ACCESS_TOKEN", "Meta access token"),
        ("META_APP_SECRET", "Meta App Secret"),
        ("WHATSAPP_ALLOWED_SENDER", "Your test recipient WhatsApp number, country code + digits without +"),
        ("WHATSAPP_SUPPORT_CONTACT", "Support email or phone to show to the test user"),
        ("OPENAI_API_KEY", "OpenAI API key"),
    ]
    for key, prompt in fields:
        value = getpass.getpass(prompt + ": ").strip()
        if value:
            if key in {"WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ALLOWED_SENDER"} and (
                not value.isascii() or not value.isdigit() or not 5 <= len(value) <= 30
            ):
                raise SystemExit(
                    "Phone IDs/numbers must contain 5–30 digits without + or spaces. Nothing saved."
                )
            updates[key] = value
    if not settings.whatsapp_verify_token:
        updates["WHATSAPP_VERIFY_TOKEN"] = secrets.token_urlsafe(32)
    save_settings(ENV, updates)
    print("Saved privately to backend/.env (owner-only permissions). No API calls or messages sent.")
    print("Copy WHATSAPP_VERIFY_TOKEN from that file into Meta's webhook Verify token field.")
    print("Run again with --check to inspect readiness without displaying secrets.")


if __name__ == "__main__":
    main()
