from __future__ import annotations

import logging

import requests

LOGGER = logging.getLogger(__name__)


def notify_failure(webhook_url: str | None, message: str) -> None:
    """Best-effort alerting: alerts must never hide the original pipeline failure.

    The payload carries both ``text`` (Slack, Teams) and ``content`` (Discord) so one webhook
    URL works for the common chat tools.
    """
    if not webhook_url:
        return
    try:
        response = requests.post(
            webhook_url, json={"text": message, "content": message}, timeout=10
        )
        response.raise_for_status()
    except requests.RequestException:
        LOGGER.exception("failure-webhook notification could not be delivered")
