"""Command-line entry point for ccauth."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from .errors import CCAuthError
from .modes.cookie_based import load_cookies
from .runner import run_auth

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".ccauth" / "config.toml"

# Available notifier types (for error messages)
AVAILABLE_NOTIFIERS = ["slack"]


def _build_handoff_config_from_toml(config_path: Path) -> Any:
    """Load and validate handoff configuration from TOML file.

    Args:
        config_path: Path to the TOML configuration file.

    Returns:
        A fully constructed HandoffConfig instance.

    Raises:
        SystemExit: If configuration is invalid or missing required fields.
    """
    from ccauth.handoff import HandoffConfig, SlackNotifier

    if not config_path.exists():
        _cli_error(
            f"--handoff requires a config file at {config_path} with [handoff].notify "
            f"listing at least one notifier; available: {', '.join(AVAILABLE_NOTIFIERS)}"
        )

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        _cli_error(f"Invalid TOML syntax in {config_path}: {e}")

    handoff_section = config.get("handoff")
    if not handoff_section:
        _cli_error(
            f"--handoff requires [handoff] section in {config_path} with notify "
            f"listing at least one notifier; available: {', '.join(AVAILABLE_NOTIFIERS)}"
        )

    notify_list = handoff_section.get("notify", [])
    if not notify_list:
        _cli_error(
            f"--handoff requires at least one notifier in [handoff].notify; "
            f"available: {', '.join(AVAILABLE_NOTIFIERS)}"
        )

    # Build notifier instances
    notifiers = []
    for notifier_name in notify_list:
        if notifier_name == "slack":
            slack_config = handoff_section.get("slack")
            if not slack_config:
                _cli_error(
                    f"notify lists 'slack' but [handoff.slack] section is missing in {config_path}"
                )

            webhook_url = slack_config.get("webhook_url")
            bot_token = slack_config.get("bot_token")
            channel = slack_config.get("channel")

            if not webhook_url and not (bot_token and channel):
                _cli_error(
                    "[handoff.slack] requires either webhook_url or (bot_token + channel)"
                )

            notifiers.append(
                SlackNotifier(
                    webhook_url=webhook_url,
                    bot_token=bot_token,
                    channel=channel,
                )
            )
        else:
            logger.debug("Unknown notifier '%s' in notify list, ignoring", notifier_name)

    if not notifiers:
        _cli_error(
            f"No valid notifiers configured. Check [handoff].notify in {config_path}; "
            f"available: {', '.join(AVAILABLE_NOTIFIERS)}"
        )

    # Build HandoffConfig with optional fields
    return HandoffConfig(
        notifiers=notifiers,
        host=handoff_section.get("host", "localhost"),
        port=handoff_section.get("port", 8080),
        timeout=handoff_section.get("timeout", 600.0),
        public_base=handoff_section.get("public_base"),
    )


def _cli_error(message: str) -> None:
    """Print error as JSON and exit."""
    print(json.dumps({"error": message}))
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ccauth",
        description="Automate the Claude Code OAuth flow. Emits ~/.claude/.credentials.json as JSON on stdout.",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        metavar="PATH",
        help="Cookie-Editor JSON for claude.ai: file path or raw JSON string. "
        "When provided, uses cookie-based mode (patchright + headed Chrome) instead of launching the default browser.",
    )
    parser.add_argument(
        "--handoff",
        action="store_true",
        help="Enable human-in-the-loop handoff for cookie-based mode. "
        "When the automated flow gets stuck, exposes a browser stream for manual completion. "
        "Requires a config file with notifier settings.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=f"Path to TOML config file for handoff settings. "
        f"Default: {DEFAULT_CONFIG_PATH}. Can also be set via CCAUTH_CONFIG env var.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging on stderr.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # Build handoff config if --handoff is set
    handoff_config = None
    if args.handoff:
        if not args.cookies:
            _cli_error("--handoff requires --cookies (handoff only applies to cookie-based mode)")

        # Resolve config path: --config flag > CCAUTH_CONFIG env > default
        config_path_str = args.config or os.environ.get("CCAUTH_CONFIG")
        config_path = Path(config_path_str) if config_path_str else DEFAULT_CONFIG_PATH

        handoff_config = _build_handoff_config_from_toml(config_path)

        # Warn if binding to 0.0.0.0 (network exposure)
        if handoff_config.host == "0.0.0.0":
            logger.warning(
                "Handoff server binding to 0.0.0.0 - the stream will be accessible on all network interfaces"
            )

    try:
        output = run_auth(
            cookies=load_cookies(args.cookies) if args.cookies else None,
            handoff=handoff_config,
        )
    except CCAuthError as e:
        print(json.dumps({"error": str(e), **e.extra}))
        return 1

    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
