"""Version precedence, as Semantic Versioning 2.0.0 defines it."""


def compare(a: str, b: str) -> int:
    """-1 when version ``a`` has lower precedence than ``b``, 1 when higher, 0 when equal.

    Follows SemVer 2.0.0 exactly: MAJOR.MINOR.PATCH, an optional
    pre-release after "-", optional build metadata after "+" (ignored for
    precedence). A version that is not valid SemVer 2.0.0 raises ValueError.
    """
    raise NotImplementedError
