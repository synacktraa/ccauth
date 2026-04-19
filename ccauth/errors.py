from typing import Any


class CCAuthError(Exception):
    """Base error. Accepts arbitrary keyword fields which the CLI surfaces in its JSON output."""

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra: dict[str, Any] = extra


class AuthError(CCAuthError):
    pass


class ModeError(CCAuthError):
    pass
