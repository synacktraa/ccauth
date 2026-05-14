"""Local HTTP callback server shared by all modes."""

import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ..errors import AuthError

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html><head><title>ccauth</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f5f5;display:flex;
justify-content:center;align-items:center;height:100vh;margin:0}
.card{background:#fff;padding:40px;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,.1);
text-align:center;max-width:400px}h1{color:#4CAF50;margin-bottom:10px}p{color:#666}
</style></head><body><div class="card"><h1>Login Successful</h1>
<p>You may close this window and return to the terminal.</p></div></body></html>\
"""

_ERROR_HTML = """\
<!DOCTYPE html>
<html><head><title>ccauth</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f5f5;display:flex;
justify-content:center;align-items:center;height:100vh;margin:0}
.card{background:#fff;padding:40px;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,.1);
text-align:center;max-width:400px}h1{color:#f44336;margin-bottom:10px}p{color:#666}
</style></head><body><div class="card"><h1>Login Failed</h1><p>%s</p></div></body></html>\
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server: _Server = self.server  # type: ignore[assignment]
        parsed = urlparse(self.path)

        if parsed.path != server.callback_path:
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]

        if not code:
            error = (params.get("error") or ["Unknown error"])[0]
            self._send_html(400, _ERROR_HTML % error)
            server.error = error
            return

        if state != server.expected_state:
            self._send_html(400, _ERROR_HTML % "Invalid state parameter")
            server.error = "state mismatch"
            return

        server.auth_code = code
        self._send_html(200, _SUCCESS_HTML)

    def _send_html(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_args, **_kwargs) -> None:  # silence stdlib access logs
        pass


class _Server(HTTPServer):
    expected_state: str
    callback_path: str
    auth_code: str | None
    error: str | None


@dataclass
class CallbackServer:
    port: int
    callback_path: str
    _server: _Server
    _thread: threading.Thread

    @classmethod
    def create(cls, *, expected_state: str, path: str) -> "CallbackServer":
        server = _Server(("localhost", 0), _Handler)
        server.expected_state = expected_state
        server.callback_path = path
        server.auth_code = None
        server.error = None

        actual_port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        return cls(
            port=actual_port,
            callback_path=path,
            _server=server,
            _thread=thread,
        )

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.port}{self.callback_path}"

    def wait_for_code(self, timeout: float = 300.0) -> str:
        self._thread.join(timeout=timeout)
        try:
            if self._server.auth_code:
                return self._server.auth_code
            if self._server.error:
                raise AuthError(f"OAuth callback error: {self._server.error}")
            raise AuthError("Timed out waiting for OAuth callback")
        finally:
            self._server.server_close()
