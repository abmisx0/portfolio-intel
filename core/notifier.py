"""
Discord notification helper.

Posts insight summaries to a Discord webhook channel.
Webhook URL is read from DISCORD_WEBHOOK_URL in the environment / .env file.
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)


def post_discord(title: str, body: str, portfolio: str | None = None) -> bool:
    """
    Post an insight to Discord as a code-block embed.
    Returns True on success, False on failure (logs the error).
    """
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping notification")
        return False

    port_tag = f" [{portfolio}]" if portfolio else ""
    content = f"**{title}{port_tag}**\n```\n{body}\n```"
    # Discord message limit is 2000 chars
    if len(content) > 2000:
        content = content[:1990] + "\n…```"

    payload = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "PortfolioIntel/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 204):
                logger.error("Discord webhook returned %s", resp.status)
                return False
        return True
    except urllib.error.URLError as exc:
        logger.error("Discord webhook failed: %s", exc)
        return False
