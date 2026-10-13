"""Small text helpers for building URLs."""


def slugify(title: str, max_length: int = 60) -> str:
    """Turn a title into a URL slug.

    - lower case
    - accented Latin letters lose their accents ("é" -> "e")
    - every run of characters that are not a-z or 0-9 becomes one hyphen
    - no hyphen at the start or the end
    - at most `max_length` characters, cut at a hyphen boundary when the
      cut would otherwise split a word, and never ending in a hyphen
    """
    raise NotImplementedError
