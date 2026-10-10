"""De-duplicate event ids from a log, keeping the first occurrence."""


def unique_in_order(items):
    """The items with duplicates removed, in the order they first appear."""
    seen = []
    out = []
    for item in items:
        if item not in seen:
            seen.append(item)
            out.append(item)
    return out
