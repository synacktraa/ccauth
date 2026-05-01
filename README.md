# ccauth

Automate the Claude Code OAuth flow. `ccauth` runs the full PKCE handshake against Anthropic's OAuth endpoints and prints the native `~/.claude/.credentials.json` payload as JSON on stdout — suitable for piping into a file, a sandbox bootstrap script, or a secrets manager.

Two modes:

- **Default-browser** (interactive): opens the authorize URL in your system browser; you click **Authorize**; ccauth captures the callback on a local loopback port and exchanges it.
- **Cookie-based** (unattended): with exported `claude.ai` cookies, patchright drives a headed Chrome through the consent click — including Cloudflare Turnstile — without any user interaction.

## Install

```bash
pip install git+https://github.com/synacktraa/ccauth.git

# Chrome (only needed for cookie-based mode)
patchright install --with-deps chrome
```

## Usage

`ccauth` prints the credentials payload to **stdout**.

Default-browser mode (you click **Authorize** in the browser that opens):

```bash
ccauth
```

Cookie-based mode (fully unattended, drives headed Chrome via patchright):

```bash
ccauth --cookies path/to/cookies.json
```

`--cookies` accepts a file path or a raw JSON string. The format is a Cookie-Editor export of `claude.ai`.

Output shape (exactly what Claude Code expects in `~/.claude/.credentials.json`):

```json
{
  "claudeAiOauth": {
    "accessToken": "...",
    "refreshToken": "...",
    "expiresAt": 1234567890000,
    "scopes": ["..."],
    "subscriptionType": "pro",
    "rateLimitTier": "default_claude_ai"
  }
}
```

## Consumer responsibilities

If you're provisioning Claude Code end-to-end, you'll typically also need to:

- Persist the output to `~/.claude/.credentials.json` with mode `0600`.
- Seed `~/.claude.json` with `{"hasCompletedOnboarding": true}` — otherwise Claude Code shows the theme picker and OAuth prompt on first run regardless of valid credentials.

## How it works

Both modes run the same OAuth 2.0 + PKCE flow against Anthropic's endpoints (`claude.com/cai/oauth/authorize` → `platform.claude.com/v1/oauth/token`). What differs is *who clicks Authorize*.

Common plumbing:

1. Generate a PKCE verifier/challenge pair and a random `state`.
2. Bind a local HTTP server to `127.0.0.1:<random-port>` that listens on `/callback` — this satisfies OAuth's [RFC 8252 loopback redirect](https://www.rfc-editor.org/rfc/rfc8252#section-7.3) exception.
3. Build the authorize URL with `redirect_uri=http://localhost:<port>/callback` and the PKCE challenge.
4. After a `code` comes back on the callback, POST `code + code_verifier + state` to the token endpoint and receive `access_token`, `refresh_token`, `expires_in`.
5. Call `api.anthropic.com/api/oauth/profile` with the access token to resolve `subscriptionType` and `rateLimitTier`, then shape the final `{"claudeAiOauth": {...}}` payload.

All outbound HTTP uses a `User-Agent: axios/1.13.6` header — Anthropic's edge returns a fake `429` to anything that looks like `python-requests/*`, which took some poking to figure out.

### Default-browser mode

Opens the authorize URL via the stdlib `webbrowser` module. You click **Authorize** in the page that appears. Anthropic's consent page redirects to the loopback callback, the local server captures the `code`, and we proceed to token exchange.

### Cookie-based mode

This is the headless-friendly path. The trick: Anthropic's `/oauth/authorize` page lives on `claude.com`, which is same-site as `claude.ai` — so a valid `claude.ai` session cookie is enough to skip the login prompt entirely. Cloudflare Turnstile still needs to pass, which is where the browser-automation dance matters.

1. **Launch real Chrome via [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)** — a Playwright fork with stealth patches that neutralize Chrome's automation fingerprints (`navigator.webdriver`, CDP telltales, etc). We use `channel="chrome"` (system Chrome, not bundled Chromium) because Turnstile flags Chromium builds. `headless=False` is mandatory — headless is trivially detectable.
2. **Persistent browser profile** at `~/.ccauth/patchright-profile`. Turnstile accumulates trust signals across runs; reusing the profile avoids starting from zero every time.
3. **Inject `claude.ai` cookies** (exported via [Cookie-Editor](https://cookie-editor.com/)). Format is converted on the fly: `expirationDate → expires`, `sameSite` lowercase → Playwright's capitalized form, session cookies → `expires=-1`.
4. **Navigate** to the authorize URL. Because the cookies are valid, Anthropic treats the session as logged in and renders the consent page directly. Turnstile runs in the background.
5. **Click Authorize** once the button is visible (up to 60s wait to let Turnstile clear). The page 302s to the loopback callback.
6. From here it's identical to default-browser mode — callback server catches the `code`, we exchange and return.

If step 5 throws (e.g. the button never appears), the raised `ModeError` carries the final page URL and full HTML as `url` and `html` fields in the CLI's error JSON, so you can see what Cloudflare or Anthropic actually rendered.

## Handoff mode (human-in-the-loop)

When running in a sandbox or CI environment, the automated cookie-based flow might get stuck due to:

- Cloudflare Turnstile challenges that require human interaction
- Expired or invalid session cookies requiring re-login
- Other unexpected blockers

**Handoff mode** provides a fallback: when the automated flow stalls, ccauth exposes a browser-streamed UI so a human can complete the OAuth steps manually. On success, ccauth continues as normal and emits the credentials JSON.

### Install with handoff support

```bash
pip install "ccauth[handoff] @ git+https://github.com/synacktraa/ccauth.git"
```

### Configuration

Create a config file at `~/.ccauth/config.toml` (or specify a custom path via `--config` or `CCAUTH_CONFIG` env var):

```toml
[handoff]
notify = ["slack"]      # List of notifiers to fire when handoff triggers
host = "0.0.0.0"        # Bind to all interfaces (required for proxy access)
port = 8080
timeout = 600           # Seconds to wait for user to complete OAuth

# Optional: Public URL for notifications (required in sandboxed environments)
# If running in Daytona or similar, set this to the proxy URL
public_base = "https://8080-abc123.daytonaproxy01.net"

[handoff.slack]
webhook_url = "https://hooks.slack.com/services/..."
# Or use bot token + channel:
# bot_token = "xoxb-..."
# channel = "#ops"
```

### CLI usage

```bash
ccauth --cookies cookies.json --handoff
```

When the automated flow gets stuck, ccauth will:

1. Re-navigate to the OAuth page for a clean starting state
2. Start a browser-streaming server
3. Send notifications via configured notifiers (Slack, etc.) with the stream URL
4. Log the stream URL to stderr
5. Wait for a human to complete the OAuth flow via the streamed UI
6. Continue with token exchange once the OAuth callback is received

### Sandboxed environments (Daytona, containers)

When running inside a sandbox, `localhost` URLs in notifications won't be reachable from outside. Use `public_base` to specify the externally-accessible URL:

```toml
[handoff]
notify = ["slack"]
host = "0.0.0.0"
port = 8080
public_base = "https://8080-your-workspace-id.daytonaproxy01.net"
```

The server still binds to `{host}:{port}`, but notifications will contain the `public_base` URL instead.

## Python API

```python
from ccauth import run_auth

# Default-browser mode
creds = run_auth()

# Cookie-based mode
import json
cookies = json.load(open("path/to/cookies.json"))
creds = run_auth(cookies=cookies)

# Cookie-based mode with handoff
from ccauth.handoff import HandoffConfig, SlackNotifier

creds = run_auth(
    cookies=cookies,
    handoff=HandoffConfig(
        notifiers=[SlackNotifier(webhook_url="https://hooks.slack.com/...")],
        host="0.0.0.0",
        port=8080,
        timeout=600.0,
        public_base="https://8080-abc123.daytonaproxy01.net",  # Optional
    ),
)
```
