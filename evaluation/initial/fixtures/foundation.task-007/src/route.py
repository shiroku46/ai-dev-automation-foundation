def implementation_route(provider_available):
    """Choose the implementation route without requiring human action for optional provider outages."""
    if provider_available:
        return "provider"
    return "human"
