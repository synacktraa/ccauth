"""OAuth primitives for Claude Code: PKCE, state, authorize URL, token exchange."""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from .errors import AuthError


@dataclass
class PKCE:
    verifier: str
    challenge: str


@dataclass
class TokenResult:
    access_token: str
    refresh_token: str
    expires_at_ms: int
    scopes: list[str]
    raw: dict[str, Any] = field(default_factory=dict)


def generate_pkce() -> PKCE:
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return PKCE(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    return secrets.token_hex(32)


def build_authorize_url(
    *,
    authorize_url: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    code_challenge: str,
    state: str,
    extra_params: dict[str, str] | None = None,
) -> str:
    params: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if extra_params:
        params.update(extra_params)
    return f"{authorize_url}?{urlencode(params)}"


async def exchange_code(
    *,
    token_url: str,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    state: str,
    user_agent: str,
    timeout: float = 15.0,
) -> TokenResult:
    body = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            headers={"User-Agent": user_agent},
            json=body,
            timeout=timeout,
        )
    if response.status_code != 200:
        raise AuthError(
            f"Token exchange failed: {response.status_code} {response.text}"
        )

    data = response.json()
    missing = [k for k in ("access_token", "refresh_token", "expires_in") if k not in data]
    if missing:
        raise AuthError(f"Token response missing fields: {missing}")

    scopes = data["scope"].split(" ") if data.get("scope") else []
    return TokenResult(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at_ms=int(time.time() * 1000) + data["expires_in"] * 1000,
        scopes=scopes,
        raw=data,
    )


async def refresh_access_token(
    *,
    token_url: str,
    client_id: str,
    refresh_token: str,
    user_agent: str,
    timeout: float = 15.0,
) -> TokenResult:
    """Mint a new access token from a refresh token (grant_type=refresh_token).

    Claude rotates the refresh token on every call; if the response omits one,
    the returned token is empty (see below). Raises AuthError with
    ``refresh_expired=True`` when the refresh token is rejected (invalid_grant),
    so callers can distinguish "re-run the full OAuth flow" from a transient error.
    """
    body = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            headers={"User-Agent": user_agent},
            json=body,
            timeout=timeout,
        )
    if response.status_code != 200:
        text = response.text
        if response.status_code in (400, 401) and (
            "invalid_grant" in text or "invalid_token" in text or "expired" in text
        ):
            raise AuthError(
                f"Refresh token rejected: {response.status_code} {text}",
                refresh_expired=True,
            )
        raise AuthError(f"Token refresh failed: {response.status_code} {text}")

    data = response.json()
    missing = [k for k in ("access_token", "expires_in") if k not in data]
    if missing:
        raise AuthError(f"Refresh response missing fields: {missing}")

    scopes = data["scope"].split(" ") if data.get("scope") else []
    return TokenResult(
        access_token=data["access_token"],
        # Claude rotates the refresh token on every refresh, so a missing one is
        # anomalous. Return empty rather than silently reusing the old (now
        # likely invalidated) token — an empty token makes the next refresh fail
        # cleanly and fall back to the full OAuth flow.
        refresh_token=data.get("refresh_token", ""),
        expires_at_ms=int(time.time() * 1000) + data["expires_in"] * 1000,
        scopes=scopes,
        raw=data,
    )
