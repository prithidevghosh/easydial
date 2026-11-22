"""Render an allowlisted secret JSON to a private Docker env file; never print values."""
import json
import os
import sys
from pathlib import Path

ALLOWED = {
    "OPENAI_API_KEY", "OPENAI_TEXT_MODEL", "OPENAI_TRANSCRIPTION_MODEL",
    "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_VERIFY_TOKEN",
    *(f"WHATSAPP_ALLOWED_SENDER_{i}" for i in range(1, 6)),
    "META_APP_SECRET", "WHATSAPP_ALLOWED_SENDER", "WHATSAPP_SUPPORT_CONTACT",
    "WHATSAPP_API_VERSION", "WHATSAPP_BUSINESS_NUMBER", "CUSTOMER_SESSION_KEY",
    "ONBOARDING_MAX_CUSTOMERS", "BLINKIT_PROXY_USERNAME", "BLINKIT_PROXY_PASSWORD",
}
REQUIRED = {"OPENAI_API_KEY", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_VERIFY_TOKEN", "META_APP_SECRET", "CUSTOMER_SESSION_KEY",
            "WHATSAPP_BUSINESS_NUMBER", "WHATSAPP_SUPPORT_CONTACT"}


def render(data):
    if not isinstance(data, dict) or any(not data.get(key) for key in REQUIRED):
        raise ValueError("Required deployment credentials are missing")
    lines = []
    for key in sorted(ALLOWED & data.keys()):
        value = str(data[key])
        if any(c in value for c in "\n\r\x00"):
            raise ValueError("Multiline environment values are unsupported")
        # Compose env-file single quotes preserve dollar signs and literal content.
        if "'" in value:
            raise ValueError("Single quotes in deployment values are unsupported")
        lines.append(f"{key}='{value}'")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = render(json.load(sys.stdin))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
