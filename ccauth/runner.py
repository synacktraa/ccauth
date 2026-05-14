"""Orchestrator: run the Claude Code OAuth flow end-to-end."""

from collections.abc import Awaitable
from inspect import iscoroutine
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from .errors import AuthError
from .modes import cookie_based, default_browser, CallbackServer
from .oauth import build_authorize_url, exchange_code, generate_pkce, generate_state

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

async def run_auth(cookies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
