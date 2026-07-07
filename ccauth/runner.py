"""Orchestrator: run the Claude Code OAuth flow end-to-end."""

import asyncio
from collections.abc import Awaitable
from inspect import iscoroutine
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from .errors import AuthError
from .modes import cookie_based, default_browser, CallbackServer
from .oauth import (
    TokenResult,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    generate_state,
    refresh_access_token,
)

if TYPE_CHECKING:
    from patchright.async_api import Page

AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPE = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload"
)
CALLBACK_PATH = "/callback"

# Anthropic's edge returns a fake 429 for python-requests/*; axios UA bypasses it.
USER_AGENT = "axios/1.13.6"

_ORG_TYPE_TO_SUB = {
    "claude_max": "max",
    "claude_pro": "pro",
    "claude_enterprise": "enterprise",
    "claude_team": "team",
}


async def _click_authorize(page: "Page") -> None:
    """Click the 'Authorize' button on Claude's consent page.

    Waits up to 60s for visibility so Cloudflare Turnstile has time to clear.
    """
    btn = page.get_by_role("button", name="Authorize", exact=True).first
    await btn.wait_for(state="visible", timeout=60000)
    await btn.click()


async def _fetch_profile(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            PROFILE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=10.0,
        )
    if response.status_code != 200:
        raise AuthError(
            f"GET {PROFILE_URL} failed: {response.status_code} {response.text}"
        )
    return response.json()


async def _tokens_to_credentials(tokens: TokenResult) -> dict[str, Any]:
    """Build the ~/.claude/.credentials.json shape from a token result, fetching
    the profile for subscription/rate-limit info (not present on the token
    response). Shared by both the OAuth and refresh flows so their output matches.
    """
    profile = await _fetch_profile(tokens.access_token)
    org = profile.get("organization") or {}
    return {
        "claudeAiOauth": {
            "accessToken": tokens.access_token,
            "refreshToken": tokens.refresh_token,
            "expiresAt": tokens.expires_at_ms,
            "scopes": tokens.scopes,
            "subscriptionType": _ORG_TYPE_TO_SUB.get(org.get("organization_type") or ""),
            "rateLimitTier": org.get("rate_limit_tier"),
        }
    }


class CaptureCodeCallback(Protocol):
    def __call__(
        self, authorize_url: str, server: CallbackServer
    ) -> str | Awaitable[str]: ...


async def run_auth_custom(cb: CaptureCodeCallback) -> dict[str, Any]:
    pkce = generate_pkce()
    state = generate_state()

    server = CallbackServer.create(expected_state=state, path=CALLBACK_PATH)

    authorize_url = build_authorize_url(
        authorize_url=AUTHORIZE_URL,
        client_id=CLIENT_ID,
        redirect_uri=server.redirect_uri,
        scope=SCOPE,
        code_challenge=pkce.challenge,
        state=state,
        extra_params={"code": "true"},
    )

    output = cb(authorize_url, server)
    code = await output if iscoroutine(output) else output

    tokens = await exchange_code(
        token_url=TOKEN_URL,
        client_id=CLIENT_ID,
        code=code,
        code_verifier=pkce.verifier,
        redirect_uri=server.redirect_uri,
        state=state,
        user_agent=USER_AGENT,
    )

    return await _tokens_to_credentials(tokens)

async def run_auth_async(cookies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Async version of run_auth. Use this when calling from an async context."""
    if cookies is not None:
        return await run_auth_custom(
            lambda url, server: cookie_based.open_and_wait(
                url, server, cookies, process_page=_click_authorize
            )
        )
    else:
        return await run_auth_custom(
            lambda url, server: default_browser.open_and_wait(url, server)
        )


def run_auth(cookies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run the Claude Code OAuth flow end-to-end.

    This is a sync wrapper around run_auth_async(). If you're calling from an
    async context, use `await run_auth_async(...)` instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - safe to use asyncio.run()
        return asyncio.run(run_auth_async(cookies=cookies))

    # There's a running loop - raise a helpful error
    raise RuntimeError(
        "run_auth() cannot be called from an async context. "
        "Use 'await run_auth_async(...)' instead."
    )


async def run_refresh_async(refresh_token: str) -> dict[str, Any]:
    """Mint a new access token from a refresh token — no browser, just HTTP.

    Emits the same shape as run_auth. Raises AuthError with
    ``refresh_expired=True`` when the refresh token is rejected, so the caller
    can fall back to the full OAuth flow.
    """
    tokens = await refresh_access_token(
        token_url=TOKEN_URL,
        client_id=CLIENT_ID,
        refresh_token=refresh_token,
        user_agent=USER_AGENT,
    )
    return await _tokens_to_credentials(tokens)


def run_refresh(refresh_token: str) -> dict[str, Any]:
    """Sync wrapper around run_refresh_async(). If you're calling from an async
    context, use `await run_refresh_async(...)` instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - safe to use asyncio.run()
        return asyncio.run(run_refresh_async(refresh_token))

    # There's a running loop - raise a helpful error
    raise RuntimeError(
        "run_refresh() cannot be called from an async context. "
        "Use 'await run_refresh_async(...)' instead."
    )
