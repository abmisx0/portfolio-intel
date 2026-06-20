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


_DISCORD_LIMIT = 2000
_MAX_PARTS = 6


def _post_raw(content: str) -> bool:
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


def _split_for_discord(text: str, room: int) -> list[str]:
    """Split text into chunks that fit Discord's message limit, on line boundaries."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > room and current:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks[:_MAX_PARTS]


def post_discord(title: str, body: str, portfolio: str | None = None,
                 markdown: bool = False) -> bool:
    """
    Post an insight to Discord. Long bodies are split across up to 6 messages
    on line boundaries (previously truncated at 2000 chars mid-sentence).

    markdown=True sends the body as native Discord markdown (for checkup
    reports written in markdown); default wraps in a code block (for the
    fixed-width weekly-sync tables).
    """
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping notification")
        return False

    port_tag = f" [{portfolio}]" if portfolio else ""
    header = f"**{title}{port_tag}**\n"
    wrap_overhead = 0 if markdown else len("```\n\n```")
    room = _DISCORD_LIMIT - len(header) - wrap_overhead - 20  # margin for part tag

    parts = _split_for_discord(body, room)
    ok = True
    for i, part in enumerate(parts):
        tag = f" ({i + 1}/{len(parts)})" if len(parts) > 1 else ""
        title_line = f"**{title}{port_tag}{tag}**\n"
        content = part if markdown else f"```\n{part}\n```"
        ok = _post_raw(title_line + content) and ok
    return ok
