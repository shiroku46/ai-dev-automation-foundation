def route(quota_available):
    """Choose a route when an optional provider reaches quota."""
    if quota_available:
        return "provider"
    return "human"
