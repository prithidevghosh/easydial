import copy
import json
import sqlite3

import pytest

from easydial.whatsapp.storage import Inbox
from tests.test_whatsapp import make_runtime, message


def finish(inbox, sender, event_id, state):
    inbox.enqueue(message(id=event_id, **{"from": sender}))
    row = inbox.claim()
    inbox.finish(row, state, [{"type": "text", "text": {"body": "Saved"}}])
    return row


def test_order_database_is_authoritative_and_scoped_per_customer(tmp_path):
    runtime = make_runtime(tmp_path)
    state = runtime.chat.initial()
    inbox = runtime.inbox
    finish(inbox, "alice", "a1", state)
    other = copy.deepcopy(state)
    other["view"]["version"] = 4
    finish(inbox, "bob", "b1", other)
    assert inbox.load_order("alice", state["view"]["id"])["version"] == 0
    assert inbox.load_order("bob", state["view"]["id"])["version"] == 4
    assert inbox.load_order("stranger", state["view"]["id"]) is None
    # A stale conversation copy must not supply the basket used by the next turn.
    stale = copy.deepcopy(state)
    stale["view"]["version"] = 99
    with inbox.db:
        inbox.db.execute("UPDATE customers SET state=? WHERE sender=?", (json.dumps(stale), "alice"))
    assert inbox.load("alice")["view"]["version"] == 0
    path = runtime.settings.whatsapp_database
    inbox.close()
    reopened = Inbox(path)
    assert reopened.load("alice")["view"]["version"] == 0
    assert reopened.db.execute("SELECT COUNT(*) FROM order_revisions").fetchone()[0] == 2
    reopened.close()


def test_order_reply_revision_transaction_and_duplicate_finish(tmp_path):
    runtime = make_runtime(tmp_path)
    inbox = runtime.inbox
    state = runtime.chat.initial()
    row = finish(inbox, "alice", "a1", state)
    inbox.finish(row, state, [{"type": "text", "text": {"body": "Duplicate"}}])
    assert inbox.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    inbox.enqueue(message(id="a2", **{"from": "alice"}))
    row = inbox.claim()
    state["view"]["version"] = 1
    inbox.db.execute(
        "CREATE TRIGGER fail_reply BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'test'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        inbox.finish(row, state, [{"type": "text", "text": {"body": "Saved"}}])
    assert inbox.load("alice")["view"]["version"] == 0
    assert inbox.db.execute("SELECT COUNT(*) FROM order_revisions").fetchone()[0] == 1
    inbox.close()


def test_existing_customer_migrates_and_older_revision_cannot_overwrite(tmp_path):
    runtime = make_runtime(tmp_path)
    inbox = runtime.inbox
    state = runtime.chat.initial()
    state["view"]["version"] = 3
    with inbox.db:
        inbox.db.execute("INSERT INTO customers VALUES (?,?)", ("alice", json.dumps(state)))
    path = runtime.settings.whatsapp_database
    inbox.close()
    inbox = Inbox(path)
    assert inbox.load_order("alice", state["view"]["id"])["version"] == 3
    old = copy.deepcopy(state)
    old["view"]["version"] = 2
    with pytest.raises(RuntimeError, match="newer order"):
        finish(inbox, "alice", "old", old)
    assert inbox.load("alice")["view"]["version"] == 3
    assert inbox.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    inbox.close()
