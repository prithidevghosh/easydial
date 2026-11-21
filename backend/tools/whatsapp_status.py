"""Read pilot queue counters without displaying phone numbers, messages or secrets."""

import sqlite3
import time
from pathlib import Path

import httpx

from easydial.settings import Settings

settings = Settings()
path = Path(settings.whatsapp_database).resolve()
if not path.exists():
    raise SystemExit("No WhatsApp database yet. Complete setup and start the channel server first.")
connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
try:
    for table in ("events", "outbox"):
        print(table + ":")
        for status, count in connection.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status"):
            print(f"  {status}: {count}")
    print("delivery:")
    for status, count in connection.execute("SELECT delivery, COUNT(*) FROM outbox GROUP BY delivery"):
        print(f"  {status or 'unconfirmed'}: {count}")
    latest = connection.execute("SELECT MAX(timestamp) FROM events").fetchone()[0]
    print("last inbound age (seconds):", int(time.time()) - latest if latest else "none")
finally:
    connection.close()

# Local health alone does not establish that Meta can reach the callback.
for label, origin in (("local", "http://127.0.0.1:8001"), ("public", settings.whatsapp_public_url)):
    if not origin:
        print(f"{label} health: not configured")
        continue
    try:
        response = httpx.get(origin.rstrip("/") + "/health", timeout=10)
        healthy = response.status_code == 200 and response.json().get("status") == "ok"
        print(f"{label} health:", "ok" if healthy else f"unhealthy (HTTP {response.status_code})")
    except Exception as error:
        # Exception details can contain URLs or secrets; report only the category.
        print(f"{label} health: unreachable ({type(error).__name__})")

