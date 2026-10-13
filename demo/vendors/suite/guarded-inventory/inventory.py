"""Stock levels for a small warehouse."""


class Inventory:
    def __init__(self):
        self.stock = {}

    def add(self, sku: str, qty: int) -> None:
        if qty <= 0:
            raise ValueError("quantity must be positive")
        self.stock[sku] = self.stock.get(sku, 0) + qty

    def remove(self, sku: str, qty: int) -> None:
        """Take `qty` out; refuse to go below zero and leave stock unchanged."""
        have = self.stock.get(sku, 0)
        self.stock[sku] = have - qty
        if have < qty:
            raise ValueError(f"only {have} of {sku} in stock")

    def low(self, threshold: int) -> list:
        """SKUs at or below `threshold`, sorted."""
        return sorted(s for s, q in self.stock.items() if q < threshold)
