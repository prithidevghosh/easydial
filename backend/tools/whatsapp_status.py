"""Read pilot queue counters without displaying phone numbers, messages or secrets."""

import sqlite3
from pathlib import Path

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
finally:
    connection.close()
