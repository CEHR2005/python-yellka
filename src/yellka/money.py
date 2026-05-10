from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

AP_QUANT = Decimal("0.001")


def parse_ap(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        parsed = Decimal(value.strip().replace(",", "."))
    else:
        parsed = Decimal(str(value))
    return parsed.quantize(AP_QUANT, rounding=ROUND_HALF_UP)


def format_ap(value: Decimal | int | float | str) -> str:
    quantized = parse_ap(value)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def db_ap(value: Decimal | int | float | str) -> str:
    return format(parse_ap(value), "f")
