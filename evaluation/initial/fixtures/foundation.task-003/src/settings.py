DEFAULT_RETRY_COUNT = 2


def retry_count(overrides):
    """Return the configured retry count."""
    return int(overrides.get("retries", DEFAULT_RETRY_COUNT))
