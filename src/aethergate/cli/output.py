"""CLI output formatting: JSON machine output vs. human-readable default.

``--json`` writes exactly one JSON document to stdout and nothing else; human
diagnostics (including the device-login verification prompt) go to stderr.
"""

from __future__ import annotations

import json
from typing import Any

import click


def _format_item(item: Any) -> str:
    if isinstance(item, dict):
        return "  " + " ".join(f"{k}={_short(item[k])}" for k in item)
    return f"  {item}"


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def emit(data: Any, *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(data, default=str, indent=2))
        return

    if isinstance(data, dict) and "items" in data and "total" in data:
        items = data["items"]
        for item in items:
            click.echo(_format_item(item))
        click.echo(f"total={data['total']}")
        return

    if isinstance(data, list):
        for item in data:
            click.echo(_format_item(item))
        return

    if isinstance(data, dict):
        for key, value in data.items():
            click.echo(f"{key}={_short(value)}")
        return

    click.echo(_short(data))


def info(message: str) -> None:
    click.echo(message, err=True)


__all__ = ["emit", "info"]
