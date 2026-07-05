"""Cookie-based mode: drive headed Chrome via patchright with injected cookies."""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ModeError
from ._callback import CallbackServer

if TYPE_CHECKING:
    from patchright.async_api import Page

logger = logging.getLogger(__name__)

PROFILE_DIR = Path.home() / ".ccauth" / "patchright-profile"

_SAMESITE_MAP = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def load_cookies(source: str) -> list[dict[str, Any]]:
    """Load cookies from a file path or a raw JSON string."""
    path = Path(source)
    looks_like_json = source.lstrip().startswith(("[", "{"))

    if path.is_file():
        raw_text = path.read_text()
    elif looks_like_json:
        raw_text = source
    else:
        raise ModeError(
            f"Cookies file not found: {source!r} "
            f"(and it doesn't look like a raw JSON string either)"
        )

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ModeError(f"Cookies input is not valid JSON: {e}") from e

    if not isinstance(raw, list):
        raise ModeError("Cookies input must be a JSON array (Cookie-Editor export format)")

    return [_convert_cookie(c) for c in raw]


def _convert_cookie(c: dict[str, Any]) -> dict[str, Any]:
    cookie: dict[str, Any] = {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
        "sameSite": _SAMESITE_MAP.get((c.get("sameSite") or "lax").lower(), "Lax"),
    }
    if not c.get("session", False) and c.get("expirationDate") is not None:
        cookie["expires"] = float(c["expirationDate"])
    else:
        cookie["expires"] = -1
    return cookie


async def open_and_wait(
    authorize_url: str,
    server: CallbackServer,
    cookies: list[dict[str, Any]],
    *,
    process_page: Callable[["Page"], None | Awaitable[None]] | None = None,
    timeout: float = 180.0,
) -> str:
    from inspect import iscoroutine

    from patchright.async_api import async_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    callback_pattern = re.compile(rf"localhost:{server.port}{re.escape(server.callback_path)}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
            # QUIC over UDP is unreliable inside some sandbox NATs (Daytona);
            # mid-handshake drops surface as ERR_QUIC_PROTOCOL_ERROR and don't
            # auto-fall-back. Force HTTP/2 over TCP. UseDnsHttpsSvcb must also
            # be off, otherwise DNS HTTPS records can re-enable h3.
            args=["--disable-quic", "--disable-features=UseDnsHttpsSvcb"],
        )
        try:
            await context.add_cookies(cookies)
        except Exception as e:
            await context.close()
            raise ModeError(f"Failed to inject cookies into Chrome: {e}") from e

        page = context.pages[0] if context.pages else await context.new_page()
        logger.info("Navigating to authorize URL...")
        await page.goto(authorize_url, wait_until="domcontentloaded", timeout=30000)

        if process_page is not None:
            try:
                result = process_page(page)
                if iscoroutine(result):
                    await result
            except Exception as e:
                try:
                    captured_url = page.url
                    captured_html = await page.content()
                except Exception:
                    captured_url = "<unknown>"
                    captured_html = ""
                await context.close()
                raise ModeError(
                    f"process_page failed at {captured_url}: {e}",
                    url=captured_url,
                    html=captured_html,
                ) from e

        # Give the post-Authorize redirect room to complete. Claude sometimes
        # takes well over 15s (extra interstitial, slow token issuance, sandbox
        # network latency) before bouncing back to the loopback. This resolves
        # as soon as the redirect lands, so a larger cap costs nothing on the
        # happy path; it just avoids falling through to wait_for_code early.
        try:
            await page.wait_for_url(callback_pattern, timeout=60000)
        except Exception:
            pass

        # Wait for the OAuth code to land on the callback server *before*
        # tearing down the browser. Closing the context first (as we used to)
        # meant any redirect slower than the wait_for_url window above could
        # never be delivered, surfacing as "Timed out waiting for OAuth
        # callback" even though the Authorize click had succeeded.
        try:
            return server.wait_for_code(timeout=timeout)
        finally:
            await context.close()
