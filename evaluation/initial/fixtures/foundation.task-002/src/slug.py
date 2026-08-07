import re


def slugify(value):
    """Return a lowercase ASCII slug with repeated separators collapsed."""
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")
