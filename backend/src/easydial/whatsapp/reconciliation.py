"""Present an existing cart and proposed draft before explicitly authorized recovery."""

from uuid import uuid4

from ..models import now
from ..providers.blinkit import cart_signature, normalized


def proposal(view, conflict):
    cart = conflict.cart
    if not cart:
        return None
    signature = cart_signature(cart)
    known = {}
    for line in [*conflict.previous_lines, *view.lines]:
        key = (normalized(line.offer.name), normalized(line.offer.pack_size))
        variants = known.setdefault(key, {})
        variants[(line.offer.product_id, line.offer.sku_id)] = line
    existing = []
    for name, pack, quantity in signature:
        candidates = known.get((name, pack), {})
        if len(candidates) != 1:
            return {"items": cart["items"], "checkpoint": None}
        line = next(iter(candidates.values())).model_copy(deep=True)
        line.quantity = quantity
        existing.append(line)
    checkpoint = view.model_copy(deep=True)
    checkpoint.lines = existing
    checkpoint.review = None
    checkpoint.payment_preparation = None
    return {
        "id": uuid4().hex,
        "version": view.version,
        "created_at": now().isoformat(),
        "address": cart["address"],
        "items": cart["items"],
        "checkpoint": checkpoint.model_dump(mode="json"),
    }


def messages(view, pending):
    current = "\n".join(f"• {x['quantity']} × {x['name']} ({x['pack']})" for x in pending["items"])
    target = "\n".join(f"• {x.quantity} × {x.offer.name} ({x.offer.pack_size})" for x in view.lines)
    body = (
        f"I found an existing {view.synced_provider.value.title()} cart. Nothing has changed yet.\n\n"
        f"Currently in the store:\n{current}\n\nYour requested basket:\n{target}\n\n"
    )
    replies = []
    if pending["checkpoint"]:
        body += (
            "Tap Update cart to make the store cart match your requested basket above, "
            "including the shown quantity changes, additions and removals. "
            "Or tell me what to change first. I'll show the full live bill afterwards. No payment or order will be placed."
        )
        replies.append({"type": "text", "text": {"body": body}})
        replies.append(
            {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "Apply these basket changes?"},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": f"reconcile:{view.version}:{pending['id']}",
                                    "title": "Update cart",
                                },
                            }
                        ]
                    },
                },
            }
        )
    else:
        body += (
            "Some existing products cannot be matched safely to saved product records. "
            "I have kept the store cart untouched. Search for those exact products first so their variants can be verified."
        )
        replies.append({"type": "text", "text": {"body": body}})
    return replies
