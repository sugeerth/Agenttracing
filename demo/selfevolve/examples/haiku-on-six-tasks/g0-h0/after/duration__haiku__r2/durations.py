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
    text = text.strip()
    if not text:
        raise ValueError("Empty duration string")

    pattern = r'(\d+\.?\d*)\s*([hms])'
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        raise ValueError("No valid duration components found")

    matched_text = ''.join(num + unit for num, unit in matches)
    cleaned_text = re.sub(r'\s+', '', text)
    if matched_text.lower() != cleaned_text.lower():
        raise ValueError("Invalid characters in duration string")

    unit_order = {'h': 0, 'm': 1, 's': 2}
    last_index = -1
    total_seconds = 0
    seen_units = set()

    for num_str, unit in matches:
        unit_lower = unit.lower()

        if unit_lower in seen_units:
            raise ValueError(f"Duplicate unit: {unit_lower}")
        seen_units.add(unit_lower)

        current_index = unit_order[unit_lower]
        if current_index <= last_index:
            raise ValueError("Units out of order")
        last_index = current_index

        num = float(num_str)
        if unit_lower == 'h':
            total_seconds += num * 3600
        elif unit_lower == 'm':
            total_seconds += num * 60
        elif unit_lower == 's':
            total_seconds += num

    return int(total_seconds)
