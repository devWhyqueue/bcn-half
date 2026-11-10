"""Send bot status messages to the configured Telegram chats."""

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("bcn-half")


def send(token: str, chat_id: str, text: str) -> None:
    """Send a listing or workflow status to a Telegram chat."""
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        json.load(r)


@dataclass(frozen=True)
class Telegram:
    token: str
    chat_ids: list[str]

    def notify(self, text: str) -> bool:
        """Report success if any chat received it, so one unreachable chat cannot re-alert."""
        sent = False
        for chat_id in self.chat_ids:
            try:
                send(self.token, chat_id, text)
                sent = True
            except (OSError, ValueError) as error:
                log.warning(
                    "Telegram-Versand fehlgeschlagen (%s)", type(error).__name__
                )
        return sent
