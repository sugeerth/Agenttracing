"""One line of CSV, as RFC 4180 writes it."""


def split_csv_line(line: str, delimiter: str = ",") -> list:
    """The fields of one CSV record.

    A field may be quoted with double quotes; inside quotes the delimiter
    is literal and a doubled quote is one quote. Unquoted fields keep
    their spaces. An unterminated quote, or anything but the delimiter
    right after a closing quote, raises ValueError. An empty line is one
    empty field.
    """
    raise NotImplementedError
