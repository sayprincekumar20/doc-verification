class ZohoError(Exception):
    """Base class for Zoho integration errors."""


class ZohoAuthError(ZohoError):
    """Refresh token invalid/revoked or the token endpoint rejected the request."""


class ZohoTransientError(ZohoError):
    """Rate limit, 5xx or network failure that persisted after retries. Safe to retry later."""


class ZohoAPIError(ZohoError):
    """Non-retryable API error (4xx)."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body[:2000]
        super().__init__(f"Zoho API error {status_code}: {self.body}")


class ZohoNotFoundError(ZohoError):
    pass


class FileTooLargeError(ZohoError):
    def __init__(self, size: int, limit: int):
        super().__init__(f"File is {size} bytes; limit is {limit} bytes")
        self.size = size
        self.limit = limit
