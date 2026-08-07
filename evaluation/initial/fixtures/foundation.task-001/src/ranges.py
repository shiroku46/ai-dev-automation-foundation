def clamp(value, lower, upper):
    """Clamp value to the inclusive [lower, upper] interval."""
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return min(lower, max(value, upper))
