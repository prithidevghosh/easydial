import json
import os
import sqlite3
import time
from pathlib import Path


class Inbox:
    """One worker owns this database. Commit session + replies atomically after a turn.

    External browser writes cannot share this transaction. Interrupted jobs are never
    replayed automatically; store cart guards must reconcile any uncertain write.
    """

    def __init__(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.touch(mode=0o600, exist_ok=True)
        os.chmod(target, 0o600)
        self.db = sqlite3.connect(target)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, timestamp INTEGER NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued');
            CREATE TABLE IF NOT EXISTS customers (
                sender TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS order_drafts (
                sender TEXT NOT NULL, order_id TEXT NOT NULL, version INTEGER NOT NULL,
                status TEXT NOT NULL, snapshot TEXT NOT NULL, last_event_id TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(sender, order_id));
            CREATE TABLE IF NOT EXISTS order_revisions (
                event_id TEXT PRIMARY KEY, sender TEXT NOT NULL, order_id TEXT NOT NULL,
                version INTEGER NOT NULL, outcome TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS order_revisions_by_customer
                ON order_revisions(sender, order_id, created_at);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, sender TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                remote_id TEXT, delivery TEXT);
        """)
        with self.db:
            # Idempotently migrate existing private customer snapshots without touching store carts.
            for row in self.db.execute("SELECT sender,state FROM customers").fetchall():
                state = json.loads(row["state"])
                view = state["view"]
                self.db.execute(
                    "INSERT OR IGNORE INTO order_drafts VALUES (?,?,?,?,?,?,?)",
                    (
                        row["sender"],
                        view["id"],
                        view["version"],
                        view["phase"],
                        json.dumps(view),
                        None,
                        int(time.time()),
                    ),
                )
            self.db.execute("UPDATE events SET status='interrupted' WHERE status='processing'")
            self.db.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")

    def enqueue(self, message):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO events(id,sender,timestamp,payload) VALUES (?,?,?,?)",
                (message["id"], message["from"], int(message["timestamp"]), json.dumps(message)),
            )

    def claim(self):
        row = self.db.execute("SELECT * FROM events WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if row:
            with self.db:
                self.db.execute("UPDATE events SET status='processing' WHERE id=?", (row["id"],))
        return row

    def load(self, sender):
        row = self.db.execute("SELECT state FROM customers WHERE sender=?", (sender,)).fetchone()
        if not row:
            return None
        state = json.loads(row[0])
        # The order record, not the conversational snapshot, supplies all basket/bill data.
        order = self.load_order(sender, state["view"]["id"])
        if order is None:
            raise RuntimeError("Missing durable order draft; refusing checkout from conversation state")
        state["view"] = order
        return state

    def load_order(self, sender, order_id):
        row = self.db.execute(
            "SELECT snapshot FROM order_drafts WHERE sender=? AND order_id=?",
            (sender, order_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def interrupted(self, sender):
        row = self.db.execute(
            "SELECT id FROM events WHERE sender=? AND status='interrupted'", (sender,)
        ).fetchone()
        if row:
            with self.db:
                self.db.execute(
                    "UPDATE events SET status='recovery_notified', payload='{}' "
                    "WHERE sender=? AND status='interrupted'",
                    (sender,),
                )
        return bool(row)

    def finish(self, row, state, replies, status="done"):
        expanded = []
        for reply in replies:
            if reply.get("type") == "text":
                text = reply["text"]["body"]
                expanded.extend(
                    {"type": "text", "text": {"body": text[i : i + 3800]}} for i in range(0, len(text), 3800)
                )
            else:
                expanded.append(reply)
        replies = expanded
        with self.db:
            view = state["view"]
            existing = self.load_order(row["sender"], view["id"])
            if existing and existing["version"] > view["version"]:
                raise RuntimeError("Refusing to overwrite a newer order draft")
            if self.db.execute("SELECT 1 FROM order_revisions WHERE event_id=?", (row["id"],)).fetchone():
                return  # Finishing the same inbound event must not duplicate replies or revisions.
            snapshot = json.dumps(view)
            timestamp = int(time.time())
            self.db.execute(
                "INSERT OR REPLACE INTO order_drafts VALUES (?,?,?,?,?,?,?)",
                (row["sender"], view["id"], view["version"], view["phase"], snapshot, row["id"], timestamp),
            )
            self.db.execute(
                "INSERT INTO order_revisions VALUES (?,?,?,?,?,?,?)",
                (row["id"], row["sender"], view["id"], view["version"], status, snapshot, timestamp),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO customers VALUES (?,?)", (row["sender"], json.dumps(state))
            )
            self.db.execute("UPDATE events SET status=?, payload='{}' WHERE id=?", (status, row["id"]))
            self.db.executemany(
                "INSERT INTO outbox(event_id,sender,payload) VALUES (?,?,?)",
                [(row["id"], row["sender"], json.dumps(reply)) for reply in replies],
            )

    def claim_reply(self):
        row = self.db.execute("SELECT * FROM outbox WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row:
            with self.db:
                self.db.execute("UPDATE outbox SET status='sending' WHERE id=?", (row["id"],))
        return row

    def mark_reply(self, id, status, remote_id=None):
        with self.db:
            self.db.execute(
                "UPDATE outbox SET status=?, remote_id=?, payload='{}' WHERE id=?", (status, remote_id, id)
            )

    def delivery(self, remote_id, status):
        if status not in {"sent", "delivered", "read", "failed"}:
            return
        previous = self.db.execute("SELECT delivery FROM outbox WHERE remote_id=?", (remote_id,)).fetchone()
        rank = {None: 0, "failed": 1, "sent": 2, "delivered": 3, "read": 4}
        if previous and (rank[status] >= rank[previous[0]] or (status == "failed" and previous[0] == "sent")):
            with self.db:
                self.db.execute("UPDATE outbox SET delivery=? WHERE remote_id=?", (status, remote_id))

    def window_open(self, sender):
        timestamp = self.db.execute("SELECT MAX(timestamp) FROM events WHERE sender=?", (sender,)).fetchone()[
            0
        ]
        return timestamp is not None and 0 <= time.time() - timestamp < 86400 - 60

    def close(self):
        self.db.close()
