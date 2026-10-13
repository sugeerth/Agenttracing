"""One line of CSV, as RFC 4180 writes it."""


def split_csv_line(line: str, delimiter: str = ",") -> list:
    """The fields of one CSV record.

    A field may be quoted with double quotes; inside quotes the delimiter
    is literal and a doubled quote is one quote. Unquoted fields keep
    their spaces. An unterminated quote, or anything but the delimiter
    right after a closing quote, raises ValueError. An empty line is one
    empty field.
    """
    fields = []
    current_field = []
    in_quotes = False
    i = 0

    while i < len(line):
        char = line[i]

        if char == '"':
            if in_quotes:
                if i + 1 < len(line) and line[i + 1] == '"':
                    current_field.append('"')
                    i += 2
                else:
                    in_quotes = False
                    i += 1
                    if i < len(line) and line[i] != delimiter:
                        raise ValueError("Closing quote must be followed by delimiter or end of line")
            else:
                in_quotes = True
                i += 1
        elif char == delimiter and not in_quotes:
            fields.append(''.join(current_field))
            current_field = []
            i += 1
        else:
            current_field.append(char)
            i += 1

    if in_quotes:
        raise ValueError("Unterminated quote in CSV field")

    fields.append(''.join(current_field))
    return fields
