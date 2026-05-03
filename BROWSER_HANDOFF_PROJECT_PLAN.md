# browser-handoff - Project Plan

A standalone library that provides human-in-the-loop fallback for browser automation via CDP-based streaming when automation gets blocked.

## Overview

`browser-handoff` takes a Playwright/Patchright page instance and creates a streaming server that allows humans to complete tasks when automation fails. Users define detection rules (as JSON config or Python objects) for:

- **Triggers**: When to start handoff (blockers detected)
- **Completions**: When to end handoff (task succeeded)

## Core Concept

```python
from browser_handoff import Handoff, Detection

handoff = Handoff(
    trigger_on=[...],    # When to START handoff (blockers)
    complete_on=[...],   # When to END handoff (success)
    notifiers=[...],
    server=ServerConfig(...),
)

async with handoff.guard(page) as session:
    await page.click("#login")
    # Auto-detects blockers, streams if needed, waits for completion
```

## Detection Types

### 1. Content Detection
Check page title or body for substrings/patterns.

```json
{
  "type": "content",
  "title_contains": ["Just a moment", "Access Denied"],
  "title_matches": ["regex pattern"],
  "body_contains": ["challenges.cloudflare.com", "captcha"],
  "body_matches": ["regex pattern"]
}
```

**Event trigger**: `page.on("domcontentloaded")`

### 2. URL Detection
Check URL components.

```json
{
  "type": "url",
  "scheme_equals": "https",
  "host_equals": ["localhost", "accounts.google.com"],
  "host_not_equals": ["expected-domain.com"],
  "path_matches": ["/login", "/callback"],
  "path_contains": ["/auth/"],
  "query_contains": ["code=", "error="]
}
```

**Event trigger**: `page.on("framenavigated")`

### 3. Element Detection
Check for presence/absence of DOM elements.

```json
{
  "type": "element",
  "present": [".captcha-container", "#challenge-form"],
  "missing": ["button#submit", ".main-content"],
  "visible": [".modal-overlay"],
  "hidden": [".loading-spinner"]
}
```

**Event trigger**: MutationObserver via CDP

### 4. LLM Detection (Optional)
Natural language condition checked against screenshot.

```json
{
  "type": "llm",
  "model": "anthropic/claude-sonnet-4-20250514",
  "condition": "The page is showing a CAPTCHA or security challenge"
}
```

**Model format**: LiteLLM format (`provider/model`)

**Event trigger**: Frame diff threshold (significant visual change)

**Internal implementation**:
```python
SYSTEM_PROMPT = """You are analyzing a browser screenshot to determine if a condition is met.
Respond with only "yes" or "no"."""

USER_PROMPT = """Based on this screenshot, is the following condition true?

Condition: {condition}

Answer only "yes" or "no"."""
```

### 5. Combinators

**all** - AND logic (all must match):
```json
{
  "type": "all",
  "conditions": [
    {"type": "url", "path_matches": ["/dashboard"]},
    {"type": "element", "present": [".user-avatar"]}
  ]
}
```

**any** - OR logic (any must match):
```json
{
  "type": "any",
  "conditions": [
    {"type": "element", "present": ["#success"]},
    {"type": "content", "body_contains": ["Welcome"]}
  ]
}
```

**not** - Invert result:
```json
{
  "type": "not",
  "condition": {"type": "element", "present": [".error-message"]}
}
```

## Full JSON Configuration Schema

```json
{
  "trigger_on": [
    {
      "type": "content",
      "title_contains": ["Just a moment", "Access Denied"],
      "body_contains": ["challenges.cloudflare.com"]
    },
    {
      "type": "url",
      "path_matches": ["/login"]
    },
    {
      "type": "element",
      "present": [".cf-turnstile", "#captcha"],
      "missing": ["button#authorize"]
    },
    {
      "type": "llm",
      "model": "anthropic/claude-sonnet-4-20250514",
      "condition": "The page shows a CAPTCHA, bot detection, or security challenge"
    }
  ],

  "complete_on": [
    {
      "type": "url",
      "host_equals": ["localhost"],
      "path_matches": ["/callback"],
      "query_contains": ["code="]
    },
    {
      "type": "all",
      "conditions": [
        {"type": "element", "present": ["#dashboard"]},
        {"type": "not", "condition": {"type": "element", "present": [".error"]}}
      ]
    },
    {
      "type": "llm",
      "model": "openai/gpt-4o",
      "condition": "The user has successfully logged in and can see their dashboard"
    }
  ],

  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "public_base": "${HANDOFF_PUBLIC_URL}",
    "timeout": 600
  },

  "notifiers": [
    {
      "type": "slack",
      "webhook_url": "${SLACK_WEBHOOK_URL}"
    },
    {
      "type": "email",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "username": "${SMTP_USER}",
      "password": "${SMTP_PASS}",
      "to": ["ops@example.com"]
    }
  ]
}
```

## Python API

### Programmatic Configuration

```python
from browser_handoff import Handoff, Detection, ServerConfig
from browser_handoff.notifiers import SlackNotifier

handoff = Handoff(
    trigger_on=[
        Detection.content(
            title_contains=["Just a moment"],
            body_contains=["challenges.cloudflare.com"],
        ),
        Detection.url(path_matches=["/login"]),
        Detection.element(
            present=[".cf-turnstile"],
            missing=["button#authorize"],
        ),
        Detection.llm(
            model="anthropic/claude-sonnet-4-20250514",
            condition="The page shows a CAPTCHA or security challenge",
        ),
    ],
    complete_on=[
        Detection.url(
            host_equals=["localhost"],
            path_matches=["/callback"],
            query_contains=["code="],
        ),
        Detection.all([
            Detection.element(present=["#dashboard"]),
            Detection.not_(Detection.element(present=[".error"])),
        ]),
    ],
    server=ServerConfig(
        host="0.0.0.0",
        port=8080,
        public_base="https://proxy.example.com",
        timeout=600,
    ),
    notifiers=[
        SlackNotifier(webhook_url="https://hooks.slack.com/..."),
    ],
)
```

### From JSON/YAML Config

```python
from browser_handoff import Handoff

# Load from file
handoff = Handoff.from_file("handoff.json")
handoff = Handoff.from_file("handoff.yaml")

# Load from string
handoff = Handoff.from_json(json_string)
handoff = Handoff.from_yaml(yaml_string)
```

### Usage with Playwright

```python
from playwright.async_api import async_playwright
from browser_handoff import Handoff

handoff = Handoff.from_file("config.json")

async with async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()

    await page.goto("https://example.com")

    # Option 1: Context manager (recommended)
    async with handoff.guard(page) as session:
        await page.click("#login")
        # Automatically detects blockers and streams if needed
        # Waits for completion condition

    # Option 2: Manual control
    if await handoff.is_blocked(page):
        result = await handoff.wait_for_human(
            page,
            reason="Cloudflare challenge detected",
        )
        print(f"Completed via: {result.detection_type}")
```

### Session Result

```python
@dataclass
class CompletionResult:
    success: bool
    reason: str
    detection_type: str  # "url", "element", "content", "llm", etc.
    matched_detection: Detection  # The detection that triggered completion
    duration: float  # Seconds spent in handoff
```

## Package Structure

```
browser-handoff/
├── pyproject.toml
├── README.md
├── src/
│   └── browser_handoff/
│       ├── __init__.py          # Main exports: Handoff, Detection, ServerConfig
│       ├── handoff.py           # Handoff class, guard context manager
│       ├── detection/
│       │   ├── __init__.py      # Detection factory class
│       │   ├── base.py          # BaseDetection ABC
│       │   ├── content.py       # ContentDetection
│       │   ├── url.py           # UrlDetection
│       │   ├── element.py       # ElementDetection
│       │   ├── llm.py           # LLMDetection (optional import)
│       │   └── combinators.py   # AllDetection, AnyDetection, NotDetection
│       ├── server/
│       │   ├── __init__.py
│       │   ├── streaming.py     # FastAPI app, CDP screencast, WebSocket handlers
│       │   ├── config.py        # ServerConfig dataclass
│       │   └── session.py       # HandoffSession management
│       ├── notifiers/
│       │   ├── __init__.py
│       │   ├── base.py          # Notifier ABC
│       │   ├── slack.py         # SlackNotifier
│       │   └── email.py         # EmailNotifier
│       ├── config/
│       │   ├── __init__.py
│       │   ├── loader.py        # JSON/YAML loading, env var interpolation
│       │   └── schema.py        # Validation
│       └── templates/
│           ├── intervention.html
│           └── notification.jinja
└── tests/
    ├── test_detection.py
    ├── test_server.py
    ├── test_config.py
    └── test_integration.py
```

## Dependencies

### Core (required)
```toml
dependencies = [
    "playwright>=1.40",      # Or patchright
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "jinja2>=3.1",
    "pyyaml>=6.0",           # For YAML config support
]
```

### Optional - LLM Detection
```toml
[project.optional-dependencies]
llm = [
    "litellm>=1.0",
]
```

Install with: `pip install browser-handoff[llm]`

## Event Listener Architecture

Each detection type registers event listeners:

```python
class BaseDetection(ABC):
    @abstractmethod
    def register_listeners(self, page: Page, callback: Callable) -> None:
        """Register event listeners that call callback when detection should be checked."""
        pass

    @abstractmethod
    async def check(self, page: Page) -> DetectionResult:
        """Check if detection condition is met."""
        pass


class UrlDetection(BaseDetection):
    def register_listeners(self, page: Page, callback: Callable) -> None:
        page.on("framenavigated", lambda frame: callback(self, frame))

    async def check(self, page: Page) -> DetectionResult:
        url = page.url
        # Check path_matches, host_equals, query_contains, etc.
        ...


class ContentDetection(BaseDetection):
    def register_listeners(self, page: Page, callback: Callable) -> None:
        page.on("domcontentloaded", lambda: callback(self))

    async def check(self, page: Page) -> DetectionResult:
        title = await page.title()
        content = await page.content()
        # Check title_contains, body_contains, etc.
        ...


class ElementDetection(BaseDetection):
    def register_listeners(self, page: Page, callback: Callable) -> None:
        # Inject MutationObserver via CDP
        # Call callback on significant DOM changes
        ...


class LLMDetection(BaseDetection):
    def register_listeners(self, page: Page, callback: Callable) -> None:
        # Compare consecutive screencast frames
        # Call callback when visual diff exceeds threshold
        ...

    async def check(self, page: Page) -> DetectionResult:
        screenshot = await page.screenshot(type="jpeg")
        result = await self._check_with_llm(screenshot)
        ...
```

## Environment Variable Interpolation

Config files support `${VAR_NAME}` syntax:

```json
{
  "notifiers": [
    {"type": "slack", "webhook_url": "${SLACK_WEBHOOK_URL}"}
  ],
  "server": {
    "public_base": "${HANDOFF_PUBLIC_URL}"
  }
}
```

Implementation:
```python
import os
import re

def interpolate_env_vars(config: dict) -> dict:
    """Recursively interpolate ${VAR} patterns with environment variables."""
    pattern = re.compile(r'\$\{([^}]+)\}')

    def replace(value):
        if isinstance(value, str):
            return pattern.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
        elif isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [replace(v) for v in value]
        return value

    return replace(config)
```

## Streaming Server

Reuse the CDP screencast approach from ccauth:

- **MJPEG stream**: `/stream?session={id}` - continuous frame stream
- **WebSocket**: `/ws?session={id}` - input events (mouse, keyboard)
- **HTML UI**: `/` - intervention interface with toolbar

Key features:
- Mouse movement forwarding at ~60 FPS (important for Turnstile)
- Keyboard event forwarding with proper key codes
- Stop screencast before sensitive data (like OAuth codes) appears
- Completion overlay when task is done

## ccauth Integration (After Publishing)

Once `browser-handoff` is published, ccauth becomes a thin consumer:

```python
# ccauth/handoff.py (simplified)
from browser_handoff import Handoff, Detection, ServerConfig
from browser_handoff.notifiers import SlackNotifier

def create_handoff(config: HandoffConfig) -> Handoff:
    return Handoff(
        trigger_on=[
            Detection.content(title_contains=["Just a moment"]),
            Detection.url(path_matches=["/login"]),
        ],
        complete_on=[
            Detection.url(
                host_equals=["localhost"],
                path_matches=["/callback"],
                query_contains=["code="],
            ),
        ],
        server=ServerConfig(
            host=config.host,
            port=config.port,
            public_base=config.public_base,
            timeout=config.timeout,
        ),
        notifiers=config.notifiers,
    )
```

## Out of Scope for v1

- **Multi-page support**: Handling popups/new tabs (future consideration)
- **Sync Playwright API**: Async-only for v1
- **Per-detection timeouts**: Only global timeout for v1
- **Authentication on streaming server**: Rely on network isolation for now

## Success Criteria

1. `pip install browser-handoff` works
2. `pip install browser-handoff[llm]` enables LLM detection
3. JSON/YAML config loading with env var interpolation
4. All detection types work: content, url, element, llm, combinators
5. Event-driven detection (not polling)
6. Streaming server works with real CDP screencast
7. Slack notifications deliver correctly
8. ccauth can be refactored to use this library

## Implementation Order

1. **Core structure**: Package setup, base classes, Detection factory
2. **Detection types**: content, url, element (without listeners first)
3. **Combinators**: all, any, not
4. **Config loading**: JSON/YAML parsing, env var interpolation
5. **Event listeners**: Wire up detection triggers
6. **Streaming server**: Port from ccauth, adapt for generic use
7. **Notifiers**: Slack, Email
8. **LLM detection**: Optional extra with litellm
9. **Handoff class**: Main orchestration, guard context manager
10. **Tests**: Unit tests for each component
11. **Documentation**: README with examples
