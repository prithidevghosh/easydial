"""Meta acceptance is not handset delivery: hold dependent replies until a receipt."""

from easydial.whatsapp.storage import Inbox
from tests.test_whatsapp import make_runtime, message


def queued(runtime):
    inbox = runtime.inbox
    inbox.enqueue(message())
    row = inbox.claim()
    inbox.finish(
        row,
        runtime.chat.initial(),
        [
            {"type": "image", "image": {"link": "https://cdn.grofers.com/one.png"}},
            {"type": "image", "image": {"link": "https://cdn.grofers.com/two.png"}},
            {"type": "text", "text": {"body": "Which product would you like?"}},
        ],
    )
    return inbox


async def test_images_delivered_before_next_image_and_choice_prompt(tmp_path):
    runtime = make_runtime(tmp_path)
    inbox = queued(runtime)
    assert await runtime.send_one()
    assert len(runtime.transport.sent) == 1
    assert not await runtime.send_one()
    inbox.delivery("out-1", "sent")
    assert not await runtime.send_one()  # API/provider acknowledgement is insufficient.
    inbox.delivery("out-1", "delivered")
    assert await runtime.send_one()
    assert len(runtime.transport.sent) == 2
    assert not await runtime.send_one()  # Second photo is still in transit.
    inbox.delivery("out-2", "read")  # Read also proves delivery, even without a delivered event.
    inbox.delivery("out-2", "sent")  # Late callbacks must not downgrade receipt status.
    assert await runtime.send_one()
    assert runtime.transport.sent[-1][1]["text"]["body"] == "Which product would you like?"
    assert not await runtime.send_one()
    inbox.close()


def test_early_receipt_and_restart_preserve_dependencies(tmp_path):
    runtime = make_runtime(tmp_path)
    inbox = queued(runtime)
    first = inbox.claim_reply()
    inbox.delivery("remote-first", "delivered")  # Webhook can beat HTTP response persistence.
    inbox.mark_reply(first["id"], "sent", "remote-first")
    second = inbox.claim_reply()
    inbox.mark_reply(second["id"], "sent", "remote-second")
    path = runtime.settings.whatsapp_database
    inbox.close()
    inbox = Inbox(path)
    assert inbox.claim_reply() is None
    inbox.delivery("remote-second", "delivered")
    assert inbox.claim_reply()["id"] > second["id"]
    inbox.close()


def test_fresh_user_turn_supersedes_stale_waiting_choices(tmp_path):
    runtime = make_runtime(tmp_path)
    inbox = queued(runtime)
    first = inbox.claim_reply()
    inbox.mark_reply(first["id"], "sent", "remote-first")
    inbox.supersede_waiting_replies("different-sender")
    assert inbox.db.execute("SELECT count(*) FROM outbox WHERE status='queued'").fetchone()[0] == 2
    inbox.supersede_waiting_replies("919999999999")
    inbox.delivery("remote-first", "delivered")
    assert inbox.claim_reply() is None
    assert inbox.db.execute("SELECT count(*) FROM outbox WHERE status='superseded'").fetchone()[0] == 2
    inbox.close()


def test_definite_failure_releases_chain_but_uncertain_send_does_not(tmp_path):
    runtime = make_runtime(tmp_path)
    inbox = queued(runtime)
    first = inbox.claim_reply()
    inbox.mark_reply(first["id"], "sent", "remote-first")
    inbox.delivery("remote-first", "failed")
    second = inbox.claim_reply()
    assert second is not None
    inbox.mark_reply(second["id"], "uncertain")
    assert inbox.claim_reply() is None  # Do not guess delivery or resend a potentially accepted image.
    inbox.close()
