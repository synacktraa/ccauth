"""Configuration for handoff streaming server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccauth.handoff.notifiers import Notifier


@dataclass
class HandoffConfig:
    """Configuration for human-in-the-loop handoff.

    Attributes:
        notifiers: List of notifiers to send stream URL to when handoff fires.
            Must be non-empty - validated in __post_init__.
        host: Host to bind the streaming server to.
        port: Port to bind the streaming server to.
        timeout: Maximum time (seconds) to wait for user to complete OAuth.
        public_base: Public base URL for notifications. If set, this URL is used
            in notifications instead of http://{host}:{port}. Useful when running
            inside a sandbox (e.g., Daytona) where localhost isn't reachable from
            outside, but a proxy URL is available.
    """

    notifiers: list[Notifier] = field(default_factory=list)
    host: str = "localhost"
    port: int = 8080
    timeout: float = 600.0
    public_base: str | None = None

    def __post_init__(self) -> None:
        if not self.notifiers:
            raise ValueError("HandoffConfig.notifiers must be non-empty")

    def get_base_url(self) -> str:
        """Get the base URL for stream access.

        Returns public_base if set, otherwise constructs from host:port.
        """
        if self.public_base:
            return self.public_base.rstrip("/")
        return f"http://{self.host}:{self.port}"
