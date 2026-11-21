from decimal import Decimal, InvalidOperation


def paise(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace("₹", "").replace(",", "").strip())
        if not number.is_finite() or number < 0 or number > 1_000_000:
            return None
        return int((number * 100).quantize(Decimal(1)))
    except (InvalidOperation, ValueError):
        return None
