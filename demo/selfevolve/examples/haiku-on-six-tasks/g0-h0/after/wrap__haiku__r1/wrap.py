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
    if width < 1:
        raise ValueError()

    if not text:
        return []

    paragraphs = text.split('\n\n')
    result = []

    for paragraph in paragraphs:
        if not paragraph.strip():
            continue

        words = paragraph.split()

        chunks = []
        for word in words:
            while len(word) > width:
                chunks.append(word[:width])
                word = word[width:]
            if word:
                chunks.append(word)

        para_lines = []
        current_line = ""

        for chunk in chunks:
            if current_line:
                if len(current_line) + 1 + len(chunk) <= width:
                    current_line += " " + chunk
                else:
                    para_lines.append(current_line)
                    current_line = chunk
            else:
                current_line = chunk

        if current_line:
            para_lines.append(current_line)

        if para_lines:
            if result:
                result.append('')
            result.extend(para_lines)

    return result
