"""Display helpers: relative date formatting and a list renderer.

The renderer prefers `rich` for colours and a tidy table layout, but falls
back to plain text when rich isn't installed.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from typing import Iterable, Optional

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
    _console: Optional[Console] = Console()
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None


# ---------------------------------------------------------------- ISO 8601

_ISO_TZ = re.compile(r"([+-])(\d{2}):?(\d{2})$")


def parse_iso(iso: str) -> Optional[_dt.datetime]:
    """Parse an ISO-8601 datetime to an aware datetime in local time.
    Returns None if the input is empty or malformed."""
    if not iso:
        return None
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Normalise "+0000" style to "+00:00" because fromisoformat is strict on 3.10
    if _ISO_TZ.search(s) is None:
        # No timezone — treat as UTC since that's what the server emits.
        s = s + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.astimezone()


def format_iso_utc(when: _dt.datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_relative(when: _dt.datetime, *, now: Optional[_dt.datetime] = None,
                    use24h: Optional[bool] = None) -> str:
    if now is None:
        now = _dt.datetime.now().astimezone()
    when = when.astimezone()
    if use24h is None:
        use24h = _detect_24h()

    today = now.date()
    when_d = when.date()
    delta_days = (when_d - today).days
    if delta_days == 0:
        prefix = "Today"
    elif delta_days == 1:
        prefix = "Tomorrow"
    elif delta_days == -1:
        prefix = "Yesterday"
    elif 0 < delta_days < 7:
        prefix = when.strftime("%a")
    elif when.year == now.year:
        prefix = when.strftime("%b %-d") if hasattr(when, "strftime") and \
            sys.platform != "win32" else when.strftime("%b %d").lstrip("0")
    else:
        prefix = when.strftime("%b %-d %Y") if sys.platform != "win32" \
            else when.strftime("%b %d %Y").lstrip("0")

    fmt = "%H:%M" if use24h else "%-I:%M %p" if sys.platform != "win32" else "%I:%M %p"
    time_part = when.strftime(fmt)
    if not use24h and sys.platform == "win32":
        time_part = time_part.lstrip("0")
    return f"{prefix} {time_part}"


def _detect_24h() -> bool:
    # Heuristic: look at LC_TIME / LANG. Default to 24h on most non-US locales.
    locale = os.environ.get("LC_TIME") or os.environ.get("LANG") or ""
    if "en_US" in locale:
        return False
    return True


# ---------------------------------------------------------------- list view


def render_list(todos: list[dict], *, completed_view: bool = False) -> None:
    """Pretty-print a list of todo dicts (raw server format)."""
    if not todos:
        print("(no completed todos)" if completed_view else "(no todos)")
        return

    use24h = _detect_24h()

    if _HAS_RICH and _console is not None:
        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        table.add_column("ID", justify="right", no_wrap=True)
        table.add_column(" ")  # checkbox column
        table.add_column("Task")
        table.add_column("When", no_wrap=True)
        for t in todos:
            check = "[green]✓[/green]" if t.get("completed") else "[dim]·[/dim]"
            due = ""
            iso = t.get("due_at")
            if iso:
                w = parse_iso(iso)
                if w:
                    rel = format_relative(w, use24h=use24h)
                    overdue = (
                        not t.get("completed")
                        and w < _dt.datetime.now().astimezone()
                    )
                    due = f"[orange3]⚠ {rel}[/orange3]" if overdue else f"[cyan]{rel}[/cyan]"
            text = t.get("text", "")
            if t.get("completed"):
                text = f"[dim]{text}[/dim]"
            prio = t.get("priority", 0) or 0
            if prio:
                text = f"[yellow]!{prio}[/yellow] {text}"
            table.add_row(str(t.get("id", "?")), check, text, due)
        _console.print(table)
        return

    # Plain fallback
    for t in todos:
        check = "[x]" if t.get("completed") else "[ ]"
        prio = t.get("priority", 0) or 0
        prio_s = f" !{prio}" if prio else ""
        due = ""
        iso = t.get("due_at")
        if iso:
            w = parse_iso(iso)
            if w:
                rel = format_relative(w, use24h=use24h)
                overdue = (
                    not t.get("completed")
                    and w < _dt.datetime.now().astimezone()
                )
                due = f"  ⚠ {rel}" if overdue else f"  ⏰ {rel}"
        print(f"{check} {t.get('id'):>4}{prio_s}  {t.get('text', '')}{due}")


# ---------------------------------------------------------------- prompts


def info(msg: str) -> None:
    if _HAS_RICH and _console is not None:
        _console.print(msg)
    else:
        print(msg)


def warn(msg: str) -> None:
    if _HAS_RICH and _console is not None:
        _console.print(f"[yellow]warning:[/yellow] {msg}")
    else:
        print(f"warning: {msg}", file=sys.stderr)


def error(msg: str) -> None:
    if _HAS_RICH and _console is not None:
        _console.print(f"[red]error:[/red] {msg}")
    else:
        print(f"error: {msg}", file=sys.stderr)
