"""Default-browser mode: hand the authorize URL to the user's system browser."""

import logging
import webbrowser

from ..errors import ModeError
from ._callback import CallbackServer

logger = logging.getLogger(__name__)


def open_and_wait(
    authorize_url: str,
    server: CallbackServer,
    *,
    timeout: float = 300.0,
) -> str:
    logger.info("Opening browser: %s", authorize_url)
    if not webbrowser.open(authorize_url):
        raise ModeError(
            "Could not launch a default browser. "
            "Either run this on a desktop environment or pass --cookies to use cookie-based mode."
        )
    logger.info(
        "Waiting for callback on http://localhost:%d%s ...",
        server.port,
        server.callback_path,
    )
    return server.wait_for_code(timeout=timeout)
