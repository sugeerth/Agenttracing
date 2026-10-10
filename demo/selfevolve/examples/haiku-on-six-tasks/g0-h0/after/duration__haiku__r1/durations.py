"""Durations written the way people write them."""

import re


def parse_duration(text: str) -> int:
    """Seconds in ``text``.

    Units: ``h``, ``m``, ``s``, in that order, each at most once, each
    optional but at least one present: "1h30m", "90s", "2h 15m", "1h5s".
    Spaces between parts are allowed. A number may have a decimal part
    ("1.5h" is 5400). Upper case units are accepted. Anything else, an
    empty string included, raises ValueError.
    """
    if not text or not text.strip():
        raise ValueError("Duration string cannot be empty")

    text = text.strip()

    values = {'h': 0.0, 'm': 0.0, 's': 0.0}
    order = {'h': 0, 'm': 1, 's': 2}
    last_order = -1
    found_any = False

    pattern = r'\s*([0-9.]+)\s*([hms])\s*'
    pos = 0

    for match in re.finditer(pattern, text, re.IGNORECASE):
        if match.start() != pos:
            raise ValueError(f"Invalid duration format: {text}")

        number_str = match.group(1)
        unit = match.group(2).lower()

        current_order = order[unit]
        if current_order <= last_order:
            raise ValueError(f"Invalid duration format: units out of order in {text}")

        if values[unit] != 0.0:
            raise ValueError(f"Invalid duration format: {unit} appears more than once in {text}")

        try:
            value = float(number_str)
            if value < 0:
                raise ValueError("Negative values not allowed")
        except ValueError:
            raise ValueError(f"Invalid duration format: invalid number {number_str}")

        values[unit] = value
        last_order = current_order
        found_any = True
        pos = match.end()

    if pos != len(text):
        raise ValueError(f"Invalid duration format: {text}")

    if not found_any:
        raise ValueError(f"Invalid duration format: no duration units found in {text}")

    total_seconds = values['h'] * 3600 + values['m'] * 60 + values['s']

    return int(total_seconds)
