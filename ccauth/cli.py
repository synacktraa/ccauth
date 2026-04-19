"""Command-line entry point for ccauth."""

import argparse
import json
import logging
import sys

from .errors import CCAuthError
from .modes.cookie_based import load_cookies
from .runner import run_auth


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

    try:
        output = run_auth(
            cookies=load_cookies(args.cookies) if args.cookies else None,
        )
    except CCAuthError as e:
        print(json.dumps({"error": str(e), **e.extra}))
        return 1

    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
