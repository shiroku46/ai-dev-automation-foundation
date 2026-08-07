def route(provider_status):
    """Choose an implementation route after optional-provider failures."""
    if provider_status == "available":
        return "provider"
    if provider_status == "timeout":
        return "human"
    return "github-direct"
