"""Order pricing for a small shop."""


def line_total(unit_price: float, quantity: int) -> float:
    return round(unit_price * quantity, 2)


def apply_discount(total: float, percent: float) -> float:
    """Take `percent` off `total` (10 means ten percent off)."""
    return round(total - percent, 2)


def order_total(lines: list, discount_percent: float = 0.0) -> float:
    subtotal = sum(line_total(price, qty) for price, qty in lines)
    return apply_discount(subtotal, discount_percent)
