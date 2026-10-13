"""Parse durations written like "1h30m", "45m", "2h", "90s"."""
import re

_PART = re.compile(r"(\d+)([hms])")


def parse_duration(text: str) -> int:
    """Seconds in a duration string."""
    total = 0
    for amount, unit in _PART.findall(text.strip().lower()):
        amount = int(amount)
        if unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
        else:
            total += amount
    return total
