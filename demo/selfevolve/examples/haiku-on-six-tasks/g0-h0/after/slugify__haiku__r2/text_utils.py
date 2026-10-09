"""Text helpers for URLs."""

import unicodedata


def slugify(text: str, max_length: int = 50) -> str:
    """A URL slug for ``text``.

    - lower case; letters with accents lose them ("Crème brûlée" -> "creme-brulee")
    - every run of characters that are not a-z or 0-9 becomes one hyphen
    - no hyphen at either end
    - at most ``max_length`` characters, cut at a hyphen so no word is split
      (a single word longer than ``max_length`` is cut at ``max_length``)
    - text with nothing usable gives "n-a"
    """
    # Normalize to NFD and remove accents
    normalized = unicodedata.normalize('NFD', text)
    text_without_accents = ''.join(
        char for char in normalized if unicodedata.category(char) != 'Mn'
    )

    # Convert to lowercase and build slug
    slug = ''
    prev_was_separator = True
    for char in text_without_accents.lower():
        if char.isalnum():
            slug += char
            prev_was_separator = False
        elif not prev_was_separator:
            slug += '-'
            prev_was_separator = True

    # Strip hyphens from ends
    slug = slug.strip('-')

    # Handle empty case
    if not slug:
        return 'n-a'

    # Truncate to max_length at a hyphen
    if len(slug) > max_length:
        truncated = slug[:max_length]
        last_hyphen = truncated.rfind('-')
        if last_hyphen > 0:
            slug = truncated[:last_hyphen]
        else:
            slug = truncated

    return slug
