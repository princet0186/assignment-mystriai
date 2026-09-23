"""Money helpers.

Amounts are stored as SQLite REAL for schema compatibility with the supplied
register, but every balance is derived in integer paise and only converted back
at the edge. Summing floats leaks representation error into the API: 10.00 plus
9.99 against a 19.99 invoice produced a balance of -3.55e-15 and a paid figure
of 19.990000000000002 before this was introduced.

Two decimal places is the documented precision for this exercise
(BUSINESS_RULES.md, "Money and reporting").
"""
from decimal import ROUND_HALF_UP, Decimal


def to_paise(value):
    """Round a stored or parsed amount to whole paise.

    Decimal(str(value)) reads the shortest repr of the float, so 19.990000000000002
    becomes 19.99 rather than propagating the error. Half-up matches the rounding
    a person doing this by hand would use.
    """
    return int(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) * 100)


def to_amount(paise):
    """Convert whole paise back to a JSON number with two-decimal precision."""
    return float(Decimal(paise) / 100)


def format_amount(paise):
    """Render whole paise as a two-decimal string for CSV export."""
    return f'{Decimal(paise) / 100:.2f}'
