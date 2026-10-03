"""Word wrapping for plain text."""


def wrap(text: str, width: int) -> list:
    """``text`` as lines of at most ``width`` characters.

    Greedy: each line takes as many words as fit, one space between them.
    Runs of spaces and newlines inside a paragraph count as one space.
    Paragraphs (separated by a blank line) stay apart, with one empty line
    between them. A word longer than ``width`` is split into ``width``-long
    pieces. No line has leading or trailing spaces. Empty text gives [].
    ``width`` below 1 raises ValueError.
    """
    raise NotImplementedError
