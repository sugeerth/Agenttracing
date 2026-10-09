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
    # Remove accents by decomposing and filtering combining marks
    nfd = unicodedata.normalize('NFD', text)
    no_accents = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')

    # Convert to lowercase and replace non-alphanumeric runs with hyphens
    lower = no_accents.lower()
    slug = ''
    for c in lower:
        if c.isalnum():
            slug += c
        elif slug and slug[-1] != '-':
            slug += '-'

    # Strip hyphens from ends
    slug = slug.strip('-')

    # Return "n-a" if empty
    if not slug:
        return 'n-a'

    # Truncate to max_length, preferring to cut at a hyphen
    if len(slug) <= max_length:
        return slug

    truncated = slug[:max_length]
    last_hyphen = truncated.rfind('-')

    if last_hyphen > 0:
        return truncated[:last_hyphen]
    return truncated
