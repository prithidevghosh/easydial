"""Configure an isolated multi-user pilot and generate the public WhatsApp join QR."""

import argparse
import json
from pathlib import Path

import qrcode
from cryptography.fernet import Fernet
from local_settings import ENV, save_settings

from easydial.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--number", required=True, help="WhatsApp business/test number, international digits only"
    )
    parser.add_argument("--qr-output", default="../docs/assets/easydial-join-pilot.png")
    args = parser.parse_args()
    import re

    if not re.fullmatch(r"[1-9][0-9]{7,14}", args.number):
        parser.error("Use international digits only")
    settings = Settings()
    private = Path(".state")
    private.mkdir(mode=0o700, exist_ok=True)
    rollback = private / "onboarding-rollback.json"
    if not rollback.exists():
        rollback.touch(mode=0o600)
        rollback.write_text(
            json.dumps(
                {
                    "WHATSAPP_MULTIUSER": str(settings.whatsapp_multiuser).lower(),
                    "WHATSAPP_DATABASE": settings.whatsapp_database,
                }
            )
        )
    key = settings.customer_session_key or Fernet.generate_key().decode()
    Fernet(key)
    save_settings(
        ENV,
        {
            "WHATSAPP_MULTIUSER": "true",
            "WHATSAPP_BUSINESS_NUMBER": args.number,
            "CUSTOMER_SESSION_KEY": key,
            "WHATSAPP_DATABASE": ".state/whatsapp/customers.sqlite3",
            "CUSTOMER_SESSION_DIRECTORY": ".state/customers",
        },
    )
    output = Path(args.qr_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://wa.me/{args.number}?text=Start"
    qrcode.make(url).save(output)
    print("Isolated onboarding pilot configured. Restart the server.")
    print("Join link:", url)
    print("QR:", output.resolve())
    print(
        "Keep CUSTOMER_SESSION_KEY safe; losing or replacing it invalidates encrypted sessions and queued messages."
    )


if __name__ == "__main__":
    main()
