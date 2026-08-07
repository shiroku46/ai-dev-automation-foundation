def normalize_label(value):
    """Collapse surrounding and repeated whitespace to single spaces."""
    return " ".join(value.split())
