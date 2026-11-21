#!/usr/bin/env python3
"""Export a stopped multi-user pilot; never copy a live SQLite database or plaintext keys."""
import argparse
import fcntl
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/src'))
from easydial.settings import Settings
from easydial.whatsapp.binding import bind
from easydial.whatsapp.security import SecretBox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    settings = Settings(_env_file=ROOT / 'backend/.env')
    if not settings.whatsapp_multiuser:
        raise SystemExit('Only isolated multi-user state may be migrated')
    source = Path(settings.whatsapp_database)
    if not source.is_absolute():
        source = ROOT / 'backend' / source
    sessions = Path(settings.customer_session_directory)
    if not sessions.is_absolute():
        sessions = ROOT / 'backend' / sessions
    with Path(str(source) + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Stop the local WhatsApp server before exporting; it still owns the database')
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            db = sqlite3.connect(source)
            for table in ('events', 'outbox'):
                count = db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued','processing','sending')").fetchone()[0]
                if count:
                    raise SystemExit('Finish pending messages before exporting; no queued messages will be replayed')
            active = db.execute("SELECT COUNT(*) FROM order_checks WHERE status IN ('prepared','pending')").fetchone()[0]
            if active:
                raise SystemExit('Resolve active order/payment checks before migrating')
            target = sqlite3.connect(stage / 'whatsapp.sqlite3')
            db.backup(target); db.close()
            bind(target, settings)  # Canonical account/key binding, independent of local legacy paths.
            # A browser OTP challenge cannot travel between hosts.
            box = SecretBox(settings.customer_session_key)
            with target:
                for sender, raw in target.execute('SELECT sender,state FROM onboarding').fetchall():
                    state = box.open(raw)
                    if state['stage'] not in {'ready', 'store', 'phone', 'consent'}:
                        state['stage'] = 'store'
                        for key in ('phone', 'retry_phone', 'addresses'):
                            state.pop(key, None)
                        target.execute('UPDATE onboarding SET state=? WHERE sender=?', (box.seal(state), sender))
            target.close()
            if sessions.exists():
                shutil.copytree(sessions, stage / 'customers')
            else:
                (stage / 'customers').mkdir()
            args.destination.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(args.destination, 'w:gz') as archive:
                for name in ('whatsapp.sqlite3', 'customers'):
                    archive.add(stage / name, arcname=name)
            args.destination.chmod(0o600)
    print('Private state bundle created. Keep it outside Git; use the same CUSTOMER_SESSION_KEY on AWS.')


if __name__ == '__main__':
    main()
