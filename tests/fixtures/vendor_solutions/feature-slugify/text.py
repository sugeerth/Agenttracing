import re
import unicodedata


def slugify(title: str, max_length: int = 60) -> str:
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    if len(slug) <= max_length:
        return slug
    cut = slug[:max_length]
    if slug[max_length] != "-" and "-" in cut:
        cut = cut[:cut.rfind("-")]
    return cut.rstrip("-")
