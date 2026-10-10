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
                # Check if it's a doubled quote (escaped quote)
                if i + 1 < len(line) and line[i + 1] == '"':
                    current_field.append('"')
                    i += 2
                    continue
                else:
                    # Closing quote
                    in_quotes = False
                    i += 1
                    # After closing quote, must be delimiter or end of line
                    if i < len(line) and line[i] != delimiter:
                        raise ValueError("Expected delimiter or end of line after closing quote")
                    continue
            else:
                # Opening quote
                in_quotes = True
                i += 1
                continue

        elif char == delimiter:
            if in_quotes:
                # Delimiter inside quotes is literal
                current_field.append(char)
            else:
                # End of field
                fields.append(''.join(current_field))
                current_field = []
            i += 1
            continue

        else:
            # Regular character
            current_field.append(char)
            i += 1
            continue

    # Handle unterminated quote
    if in_quotes:
        raise ValueError("Unterminated quote")

    # Add the last field
    fields.append(''.join(current_field))

    return fields
