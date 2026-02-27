"""Telegram notification utilities for GMX Freqtrade integration.

Provides a thin, reusable wrapper around the Telegram Bot API that reads
credentials from a standard Freqtrade configuration dict.  All functions
are intentionally non-fatal — failures are logged as warnings so that a
misconfigured or unavailable Telegram bot never interrupts trading.
"""

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def send_freqtrade_telegram_message(config: dict[str, Any], message: str) -> bool:
    """Send a Markdown-formatted message via the Telegram bot configured in *config*.

    Reads ``config["telegram"]["token"]`` and ``config["telegram"]["chat_id"]``
    from the supplied Freqtrade configuration dict.  Does nothing and returns
    ``False`` if Telegram is disabled or credentials are absent.

    :param config: Freqtrade configuration dict (as passed to exchange classes).
    :param message: Message text.  Markdown is enabled (``parse_mode="Markdown"``).
    :returns: ``True`` if the message was sent successfully, ``False`` otherwise.
    """
    tg = config.get("telegram", {})
    if not tg.get("enabled") or not tg.get("token") or not tg.get("chat_id"):
        return False

    url = f"https://api.telegram.org/bot{tg['token']}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": tg["chat_id"],
            "text": message,
            "parse_mode": "Markdown",
        }
    ).encode()

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        logger.debug("Telegram message sent successfully")
        return True
    except Exception as err:
        logger.warning("Could not send Telegram message: %s", err)
        return False
