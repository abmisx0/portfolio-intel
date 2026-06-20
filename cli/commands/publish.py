from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json

import click

from core.insights import publish_checkup


@click.command()
@click.option("--title", required=True, help="Report title, e.g. 'Portfolio Checkup — 2026-06-12'.")
@click.option("--body-file", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Path to the markdown report body.")
@click.option("--metrics", default=None,
              help="Optional JSON dict of headline metrics (sharpe, total_value, …).")
def publish_cmd(title: str, body_file: str, metrics: str | None):
    """Publish a checkup report to the dashboard Insights page and Discord.

    \b
    Examples:
      publish --title "Portfolio Checkup — 2026-06-12" --body-file /tmp/checkup.md
      publish --title "..." --body-file report.md --metrics '{"sharpe_3y": 1.89}'
    """
    with open(body_file) as f:
        body = f.read()

    metrics_dict = json.loads(metrics) if metrics else None
    result = publish_checkup(title, body, metrics_dict)

    click.echo(f"  insight #{result['insight_id']} saved (dashboard /insights)")
    click.echo(f"  discord: {'posted' if result['discord_ok'] else 'FAILED — check DISCORD_WEBHOOK_URL'}")
    if not result["discord_ok"]:
        raise SystemExit(1)
