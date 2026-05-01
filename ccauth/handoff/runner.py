"""Async runner for cookie-based OAuth with handoff support."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from jinja2 import Environment, FileSystemLoader

from ccauth.errors import ModeError
from ccauth.handoff.server import StreamingServer

if TYPE_CHECKING:
    from patchright.async_api import Page

    from ccauth.handoff.config import HandoffConfig
    from ccauth.modes._callback import CallbackServer

logger = logging.getLogger(__name__)

PROFILE_DIR = Path.home() / ".ccauth" / "patchright-profile"
TEMPLATE_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)

# Known blocker URL patterns that trigger immediate handoff
BLOCKER_URL_PATTERNS = [
    re.compile(r"/login", re.IGNORECASE),
    re.compile(r"/sign-?in", re.IGNORECASE),
    re.compile(r"/challenge", re.IGNORECASE),
]

# Content patterns that indicate a Cloudflare challenge page
# Cloudflare serves challenge pages at the same URL, so we need to check content
CLOUDFLARE_CHALLENGE_INDICATORS = [
    "Just a moment...",  # Page title
    "Performing security verification",  # Challenge text
    "challenges.cloudflare.com",  # Turnstile iframe source
    "cf-chl-widget",  # Cloudflare challenge widget ID prefix
]

# Default viewport size for the browser
VIEWPORT_SIZE = {"width": 1280, "height": 800}


async def _is_cloudflare_challenge(page: "Page") -> bool:
    """Check if the current page is a Cloudflare challenge page.

    Cloudflare serves challenge pages at the same URL as the target,
    so we need to inspect the page content to detect them.
    """
    try:
        # Check page title
        title = await page.title()
        if "Just a moment" in title:
            return True

        # Check for Cloudflare-specific elements in HTML
        html = await page.content()
        for indicator in CLOUDFLARE_CHALLENGE_INDICATORS:
            if indicator in html:
                return True

        return False
    except Exception:
        return False


async def open_and_wait_async(
    authorize_url: str,
    callback_server: CallbackServer,
    cookies: list[dict[str, Any]],
    handoff: HandoffConfig,
    *,
    process_page: Callable[[Page], Coroutine[Any, Any, None]] | None = None,
    timeout: float = 180.0,
) -> str:
    """Async implementation of cookie-based OAuth with handoff support.

    Args:
        authorize_url: The OAuth authorize URL.
        callback_server: The callback server waiting for the OAuth code.
        cookies: Converted cookies to inject into the browser.
        handoff: Handoff configuration.
        process_page: Async callback to process the page (e.g., click Authorize).
        timeout: Timeout for waiting for callback URL without handoff.

    Returns:
        The OAuth authorization code.

    Raises:
        ModeError: If the flow fails or times out.
    """
    from patchright.async_api import async_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    callback_pattern = re.compile(
        rf"localhost:{callback_server.port}{re.escape(callback_server.callback_path)}"
    )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport=VIEWPORT_SIZE,
            args=["--disable-quic", "--disable-features=UseDnsHttpsSvcb"],
        )

        try:
            await context.add_cookies(cookies)
        except Exception as e:
            await context.close()
            raise ModeError(f"Failed to inject cookies into Chrome: {e}") from e

        page = context.pages[0] if context.pages else await context.new_page()
        handoff_reason: str | None = None

        try:
            logger.info("Navigating to authorize URL...")
            await page.goto(authorize_url, wait_until="domcontentloaded", timeout=30000)

            # Check for known blocker URLs
            current_url = page.url
            for pattern in BLOCKER_URL_PATTERNS:
                if pattern.search(current_url):
                    handoff_reason = f"Login required (redirected to {current_url})"
                    break

            # Check for Cloudflare challenge page (served at same URL)
            if handoff_reason is None and await _is_cloudflare_challenge(page):
                handoff_reason = "Cloudflare security challenge detected"

            # Try process_page if no blocker detected
            if handoff_reason is None and process_page is not None:
                try:
                    await process_page(page)
                except Exception as e:
                    # Timeout or failure in process_page triggers handoff
                    handoff_reason = f"Could not complete OAuth flow: {e}"

            # Wait for callback URL if no handoff needed yet
            if handoff_reason is None:
                try:
                    await page.wait_for_url(callback_pattern, timeout=30000)
                except Exception:
                    # Check if we actually got the callback
                    if not callback_pattern.search(page.url):
                        handoff_reason = "OAuth flow stalled after Authorize click"

            # If we need handoff, do it
            if handoff_reason is not None:
                await _handoff_to_user(
                    page=page,
                    context=context,
                    authorize_url=authorize_url,
                    callback_pattern=callback_pattern,
                    handoff=handoff,
                    reason=handoff_reason,
                )

        finally:
            await context.close()

    return callback_server.wait_for_code(timeout=timeout)


async def _handoff_to_user(
    page: Page,
    context: Any,  # BrowserContext
    authorize_url: str,
    callback_pattern: re.Pattern[str],
    handoff: HandoffConfig,
    reason: str,
) -> None:
    """Hand off to user for manual OAuth completion.

    Args:
        page: The browser page.
        context: The browser context.
        authorize_url: The OAuth authorize URL to navigate back to.
        callback_pattern: Pattern to detect successful OAuth callback.
        handoff: Handoff configuration.
        reason: Reason for handoff (shown to user).
    """
    session_id = str(uuid.uuid4())[:8]
    server: StreamingServer | None = None
    server_task: asyncio.Task[None] | None = None
    screencast_stopped = False

    try:
        # Re-navigate to authorize URL for a clean starting state
        logger.info("Handoff triggered: %s", reason)
        logger.info("Re-navigating to authorize URL for handoff...")
        await page.goto(authorize_url, wait_until="domcontentloaded", timeout=30000)

        # Start the streaming server
        server = StreamingServer(port=handoff.port, host=handoff.host)
        server_task = asyncio.create_task(server.start())

        # Wait a bit for server to start
        await asyncio.sleep(1)

        # Register session
        await server.register_session(
            session_id=session_id,
            page=page,
            context=context,
            reason=reason,
            viewport_size=VIEWPORT_SIZE,
        )

        # Generate stream URL using public_base if configured
        base_url = handoff.get_base_url()
        stream_url = f"{base_url}/?session={session_id}"

        # Log to stderr (CLI users see this)
        logger.info("=" * 70)
        logger.info("HANDOFF: Human intervention required")
        logger.info("=" * 70)
        logger.info("Reason: %s", reason)
        logger.info("Stream URL: %s", stream_url)
        logger.info("=" * 70)

        # Send notifications to all configured notifiers
        notification_template = jinja_env.get_template("notification.jinja")
        notification_message = notification_template.render(
            reason=reason, stream_url=stream_url
        )
        notification_title = "Human Intervention Required - Claude OAuth"

        # Fire all notifiers in parallel
        async def send_notification(notifier: Any) -> None:
            try:
                await notifier.send(
                    title=notification_title,
                    message=notification_message,
                    urgency="critical",
                )
            except Exception as e:
                logger.error("Failed to send notification via %s: %s", type(notifier).__name__, e)

        await asyncio.gather(
            *[send_notification(n) for n in handoff.notifiers],
            return_exceptions=True,
        )

        # Hook to stop screencast before callback URL is visible
        async def on_frame_navigated(frame: Any) -> None:
            nonlocal screencast_stopped
            if frame == page.main_frame and not screencast_stopped:
                url = frame.url
                if callback_pattern.search(url):
                    screencast_stopped = True
                    # Stop screencast immediately to prevent code exposure
                    if server and session_id in server.sessions:
                        session = server.sessions[session_id]
                        if session.capture_task and not session.capture_task.done():
                            session.capture_task.cancel()

        page.on("framenavigated", on_frame_navigated)

        # Wait for callback URL with handoff timeout
        try:
            await page.wait_for_url(callback_pattern, timeout=int(handoff.timeout * 1000))
        except Exception:
            if not callback_pattern.search(page.url):
                raise ModeError(
                    f"Handoff timeout: user did not complete OAuth within {handoff.timeout}s"
                )

        # Notify frontend of completion
        if server:
            await server.notify_task_completed(session_id)
            await asyncio.sleep(0.5)  # Give time for message to be sent

    finally:
        # Cleanup
        if server:
            await server.unregister_session(session_id)
            await server.stop()

        if server_task and not server_task.done():
            server_task.cancel()
            with suppress(asyncio.CancelledError):
                await server_task
