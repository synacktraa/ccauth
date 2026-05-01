"""Human-in-the-loop handoff for ccauth's cookie-based mode."""

from ccauth.handoff.config import HandoffConfig
from ccauth.handoff.notifiers import Notifier, SlackNotifier

__all__ = ["HandoffConfig", "Notifier", "SlackNotifier"]
