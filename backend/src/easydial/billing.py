"""Provider-grounded cost tables. No inferred fee-waiver thresholds or payable amounts."""

import hashlib
import json
import re
import textwrap

from .providers.money import paise

SUBTOTALS = {"items total", "item total", "item subtotal", "items subtotal", "subtotal", "item bill"}
TOTALS = {"grand total", "to pay", "total payable", "total", "amount payable"}
SMALL_CART = re.compile(
    r"small[ -]?(?:cart|basket|order)|minimum[ -]?(?:cart|basket|order)|min[ .-]+order", re.I
)


def amount(value):
    text = str(value).strip()
    if text.casefold() in {"free", "waived", "included"}:
        return 0
    negative = bool(re.match(r"^[−-]", text))
    result = paise(re.sub(r"^[−-]\s*", "", text))
    return -result if negative and result is not None else result


def breakdown(review):
    rows, warnings = [], []
    subtotal = review.item_subtotal_paise
    components = []
    readable = True
    seen_subtotal = False
    for source in review.bill_lines:
        label, value = source["label"].strip(), source["value"].strip()
        key = label.casefold()
        parsed = amount(value)
        if key in TOTALS:
            if parsed != review.total_paise:
                readable = False
            continue
        if key in SUBTOTALS:
            seen_subtotal = True
            if subtotal is None:
                subtotal = parsed
            elif subtotal != parsed:
                readable = False
        else:
            if any(word in key for word in ("discount", "saving", "coupon")) and parsed is not None:
                parsed = -abs(parsed)
            components.append(parsed)
        rows.append({"label": label, "value": value, "amount_paise": parsed})
        readable = readable and parsed is not None
        if SMALL_CART.search(label) and (parsed is None or parsed > 0):
            warnings.append(f"{label}: {value} extra is included.")
    if not seen_subtotal:
        rows.insert(0, {"label": "Items subtotal", "value": money(subtotal), "amount_paise": subtotal})
    if not review.bill_lines and review.fees_paise is not None:
        components.append(review.fees_paise)
        rows.append({"label": "Fees", "value": money(review.fees_paise), "amount_paise": review.fees_paise})
    reconciled = (
        readable
        and subtotal is not None
        and review.total_paise is not None
        and all(value is not None for value in components)
        and subtotal + sum(components) == review.total_paise
    )
    if not reconciled and review.provider.value == "instamart" and readable and subtotal is not None and review.total_paise is not None and all(value is not None for value in components):
        warnings.append(
            f"Listed charges: {money(subtotal + sum(components))}. "
            f"Instamart asks for {money(review.total_paise)}. "
            "We’ll verify this amount on its payment screen before continuing."
        )
    elif not reconciled:
        warnings.append(
            "I rechecked, but the store’s charges still don’t match its total. I can’t continue to payment yet."
        )
    rows.append(
        {"label": "TOTAL PAYABLE", "value": money(review.total_paise), "amount_paise": review.total_paise}
    )
    return {"rows": rows, "warnings": warnings, "reconciled": reconciled}


def can_choose_payment(review):
    """A disclosed Instamart discrepancy permits menu inspection, not an inferred adjustment.

    Its payment screen must independently confirm the approved merchant total.
    """
    summary = breakdown(review)
    return summary["reconciled"] or (
        review.provider.value == "instamart"
        and review.total_paise is not None
        and review.total_paise > 0
        and review.item_subtotal_paise is not None
        and bool(review.bill_lines)
        and all(row["amount_paise"] is not None for row in summary["rows"])
        and all(amount(line["value"]) == review.total_paise for line in review.bill_lines if line["label"].strip().casefold() in TOTALS)
    )


def money(value):
    return "Unverified" if value is None else f"{'-' if value < 0 else ''}₹{abs(value) / 100:.2f}"


def table(review):
    summary = breakdown(review)
    # WhatsApp does not render Markdown tables. A monospace block preserves columns.
    lines = ["Charge                   | Amount", "-------------------------|----------"]
    for row in summary["rows"]:
        label_lines = textwrap.wrap(row["label"].replace("`", ""), width=24) or [""]
        value = money(row["amount_paise"]) if row["amount_paise"] is not None else "Unverified"
        for index, label in enumerate(label_lines):
            lines.append(f"{label:<24} | {value if index == len(label_lines) - 1 else ''}")
    result = "```\n" + "\n".join(lines) + "\n```"
    if summary["warnings"]:
        result += "\n\n" + "\n".join("⚠ " + warning for warning in summary["warnings"])
    return result


def fingerprint(view, review):
    value = {
        "provider": review.provider,
        "address": review.address_label,
        "items": [(x.offer.product_id, x.offer.sku_id, x.quantity) for x in view.lines],
        "subtotal": review.item_subtotal_paise,
        "total": review.total_paise,
        "bill": review.bill_lines,
        "version": view.version,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
