"""Text helpers for URLs."""


def slugify(text: str, max_length: int = 50) -> str:
    """A URL slug for ``text``.

    - lower case; letters with accents lose them ("Crème brûlée" -> "creme-brulee")
    - every run of characters that are not a-z or 0-9 becomes one hyphen
    - no hyphen at either end
    - at most ``max_length`` characters, cut at a hyphen so no word is split
      (a single word longer than ``max_length`` is cut at ``max_length``)
    - text with nothing usable gives "n-a"
    """
    raise NotImplementedError
