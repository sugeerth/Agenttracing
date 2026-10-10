"""Durations written the way people write them."""


def parse_duration(text: str) -> int:
    """Seconds in ``text``.

    Units: ``h``, ``m``, ``s``, in that order, each at most once, each
    optional but at least one present: "1h30m", "90s", "2h 15m", "1h5s".
    Spaces between parts are allowed. A number may have a decimal part
    ("1.5h" is 5400). Upper case units are accepted. Anything else, an
    empty string included, raises ValueError.
    """
    raise NotImplementedError
