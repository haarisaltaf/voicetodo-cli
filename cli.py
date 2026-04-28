"""Command-line interface.

Subcommands when called with arguments; otherwise drops into an interactive
REPL that mirrors the Android app's main screen (record / type / list).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import __version__
from . import config as cfgmod
from . import dateparse, display, record
from .api import Api, ApiError
from .store import Cache, PendingUploads


# ---------------------------------------------------------------- helpers

def _make_api(args) -> Api:
    cfg = cfgmod.load(
        url_override=getattr(args, "url", None),
        key_override=getattr(args, "api_key", None),
    )
    if not cfg.is_configured:
        raise SystemExit(
            "Server URL not configured. Run `voicetodo configure` "
            "or set VOICETODO_URL."
        )
    return Api(cfg.url, cfg.api_key)


def _make_cache(args) -> Cache:
    cfg = cfgmod.load(
        url_override=getattr(args, "url", None),
        key_override=getattr(args, "api_key", None),
    )
    return Cache(cfg.cache_dir)


def _make_pending(args) -> PendingUploads:
    cfg = cfgmod.load(
        url_override=getattr(args, "url", None),
        key_override=getattr(args, "api_key", None),
    )
    return PendingUploads(cfg.audio_dir)


def _refresh_open(api: Api, cache: Cache) -> list[dict]:
    todos = api.list_open()
    cache.save_open(todos)
    cache.record_refresh(time.time())
    return todos


def _prompt(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        v = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return v or default


# ---------------------------------------------------------------- commands

def cmd_configure(args) -> int:
    cfg = cfgmod.load()
    print("Configure voicetodo-cli (press Enter to keep existing values)")
    url = _prompt("Server URL", cfg.url or "http://localhost:8765")
    cur_key = cfg.api_key
    masked = ("•" * 8 + cur_key[-4:]) if cur_key else ""
    if cur_key:
        print(f"Current API key: {masked}")
        try:
            new_key = getpass.getpass("New API key (Enter to keep): ").strip()
        except EOFError:
            new_key = ""
    else:
        try:
            new_key = getpass.getpass("API key: ").strip()
        except EOFError:
            new_key = ""
    api_key = new_key if new_key else cur_key
    new_cfg = cfgmod.Config(url=url, api_key=api_key).normalised()
    cfgmod.save(new_cfg)
    print(f"Saved to {cfgmod.config_path()}")

    # Smoke test
    if new_cfg.is_configured:
        api = Api(new_cfg.url, new_cfg.api_key)
        try:
            h = api.health()
            display.info(f"OK: {h}")
        except ApiError as e:
            display.warn(f"Saved, but health check failed: {e}")
    return 0


def cmd_list(args) -> int:
    cache = _make_cache(args)
    if args.offline:
        todos = cache.get_completed() if args.completed else cache.get_open()
        display.render_list(todos, completed_view=args.completed)
        return 0
    api = _make_api(args)
    try:
        todos = api.list_completed() if args.completed else api.list_open()
        if args.completed:
            cache.save_completed(todos)
        else:
            cache.save_open(todos)
            cache.record_refresh(time.time())
    except ApiError as e:
        display.warn(f"{e} — showing cached list")
        todos = cache.get_completed() if args.completed else cache.get_open()
    display.render_list(todos, completed_view=args.completed)
    return 0


def cmd_add(args) -> int:
    api = _make_api(args)
    text = " ".join(args.text).strip()
    if not text:
        display.error("Empty todo text.")
        return 1
    due = None
    if args.when:
        when = dateparse.parse_when(args.when)
        if when is None:
            display.error(f"Couldn't parse --when: {args.when!r}")
            return 1
        due = display.format_iso_utc(when)
    try:
        t = api.create_todo(text, priority=args.priority, due_at=due)
    except ApiError as e:
        display.error(str(e))
        return 1
    cache = _make_cache(args)
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    rel = ""
    if t.get("due_at"):
        w = display.parse_iso(t["due_at"])
        if w:
            rel = f" @ {display.format_relative(w)}"
    display.info(f"Added [{t.get('id')}]: {t.get('text')}{rel}")
    return 0


def cmd_done(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    for tid in args.ids:
        try:
            api.update_todo(tid, completed=True)
            display.info(f"Completed [{tid}]")
        except ApiError as e:
            display.error(f"[{tid}] {e}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    return 0


def cmd_undone(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    for tid in args.ids:
        try:
            api.update_todo(tid, completed=False)
            display.info(f"Reopened  [{tid}]")
        except ApiError as e:
            display.error(f"[{tid}] {e}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    return 0


def cmd_rm(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    for tid in args.ids:
        try:
            api.delete_todo(tid)
            display.info(f"Deleted   [{tid}]")
        except ApiError as e:
            display.error(f"[{tid}] {e}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    return 0


def cmd_remind(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    if args.clear:
        try:
            api.update_todo(args.id, due_at="")
            display.info(f"Cleared reminder on [{args.id}]")
        except ApiError as e:
            display.error(str(e))
            return 1
    else:
        when_text = " ".join(args.when).strip()
        when = dateparse.parse_when(when_text)
        if when is None:
            display.error(f"Couldn't parse {when_text!r}")
            return 1
        try:
            api.update_todo(args.id, due_at=display.format_iso_utc(when))
            display.info(f"Reminder on [{args.id}] set to {display.format_relative(when)}")
        except ApiError as e:
            display.error(str(e))
            return 1
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    return 0


def cmd_record(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    pending = _make_pending(args)

    backend = record.detect_backend()
    if backend == "none":
        display.error(
            "No audio backend available. Install one of:\n"
            "  pip install sounddevice soundfile (recommended)\n"
            "  apt install sox    /  brew install sox\n"
            "  apt install ffmpeg /  brew install ffmpeg"
        )
        return 1

    out_path = Path(tempfile.mkstemp(prefix="voicetodo_", suffix=".wav")[1])
    try:
        result = record.record_until_enter(out_path)
    except record.RecorderError as e:
        display.error(str(e))
        out_path.unlink(missing_ok=True)
        return 1

    display.info(f"recorded {result.duration:.1f}s with {result.backend}; uploading…")
    try:
        upload = api.upload_audio(result.path, source="cli")
    except ApiError as e:
        display.error(f"Upload failed: {e}")
        # Save for retry
        saved = pending.enqueue(result.path)
        display.warn(f"Audio saved to {saved} for later retry "
                     f"(`voicetodo retry`).")
        return 1

    out_path.unlink(missing_ok=True)
    display.info(f"transcript: {upload.transcript}")
    if not upload.todos:
        display.info("(no todos extracted)")
    else:
        for t in upload.todos:
            display.info(f"  + [{t['id']}] {t['text']}")

    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    return 0


def cmd_ingest(args) -> int:
    """Upload an existing audio file rather than recording one."""
    api = _make_api(args)
    cache = _make_cache(args)
    p = Path(args.path)
    if not p.exists():
        display.error(f"No such file: {p}")
        return 1
    try:
        upload = api.upload_audio(p, source="cli-ingest")
    except ApiError as e:
        display.error(str(e))
        return 1
    display.info(f"transcript: {upload.transcript}")
    for t in upload.todos:
        display.info(f"  + [{t['id']}] {t['text']}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass
    return 0


def cmd_retry(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    pending = _make_pending(args)
    files = pending.list()
    if not files:
        display.info("No pending uploads.")
        return 0
    succeeded = 0
    for f in files:
        try:
            upload = api.upload_audio(f, source="cli-retry")
            display.info(f"{f.name}: {upload.transcript}")
            for t in upload.todos:
                display.info(f"  + [{t['id']}] {t['text']}")
            pending.remove(f)
            succeeded += 1
        except ApiError as e:
            display.warn(f"{f.name}: {e}")
    display.info(f"{succeeded}/{len(files)} uploaded")
    if succeeded:
        try:
            _refresh_open(api, cache)
        except ApiError:
            pass
    return 0


def cmd_refresh(args) -> int:
    api = _make_api(args)
    cache = _make_cache(args)
    try:
        todos = _refresh_open(api, cache)
    except ApiError as e:
        display.error(str(e))
        return 1
    display.render_list(todos)
    return 0


def cmd_health(args) -> int:
    api = _make_api(args)
    try:
        h = api.health()
    except ApiError as e:
        display.error(str(e))
        return 1
    display.info(str(h))
    return 0


# ---------------------------------------------------------------- REPL

REPL_HELP = """\
Commands:
  list / ls            show open todos (refreshed)
  done <id>...         mark todo(s) completed
  undone <id>...       reopen todo(s)
  rm <id>...           delete todo(s)
  add <text>           add a typed todo
                         (append "@ <when>" for a reminder, e.g. "@ tomorrow 9am")
  remind <id> <when>   set/update a reminder ("clear" to remove)
  record / r           start recording until you press Enter
  ingest <path>        upload an existing audio file
  retry                retry uploads in the pending queue
  completed            show completed todos
  refresh              re-fetch the open list
  configure            change server URL / API key
  health               server health check
  help / ?             this help
  quit / exit / q
"""


def repl(args) -> int:
    cfg = cfgmod.load(
        url_override=getattr(args, "url", None),
        key_override=getattr(args, "api_key", None),
    )
    if not cfg.is_configured:
        display.warn("No server configured yet — running `configure` first.")
        cmd_configure(args)
        cfg = cfgmod.load()
        if not cfg.is_configured:
            return 1

    api = Api(cfg.url, cfg.api_key)
    cache = Cache(cfg.cache_dir)
    pending = PendingUploads(cfg.audio_dir)

    # Initial render: cached first (instant), then refresh.
    cached = cache.get_open()
    if cached:
        display.render_list(cached)
    try:
        todos = _refresh_open(api, cache)
        if not cached or todos != cached:
            print()
            display.render_list(todos)
    except ApiError as e:
        display.warn(f"refresh failed: {e}")

    while True:
        try:
            line = input("\nvoicetodo> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as e:
            display.error(f"parse error: {e}")
            continue
        cmd = parts[0].lower()
        rest = parts[1:]

        try:
            if cmd in ("quit", "exit", "q"):
                return 0
            if cmd in ("help", "?", "h"):
                print(REPL_HELP)
                continue
            if cmd in ("list", "ls"):
                _do_repl_list(api, cache)
                continue
            if cmd == "completed":
                try:
                    todos = api.list_completed()
                    cache.save_completed(todos)
                except ApiError as e:
                    display.warn(str(e))
                    todos = cache.get_completed()
                display.render_list(todos, completed_view=True)
                continue
            if cmd == "refresh":
                try:
                    todos = _refresh_open(api, cache)
                    display.render_list(todos)
                except ApiError as e:
                    display.error(str(e))
                continue
            if cmd == "configure":
                cmd_configure(args)
                # Reload prefs
                cfg = cfgmod.load()
                api = Api(cfg.url, cfg.api_key)
                cache = Cache(cfg.cache_dir)
                pending = PendingUploads(cfg.audio_dir)
                continue
            if cmd == "health":
                try:
                    display.info(str(api.health()))
                except ApiError as e:
                    display.error(str(e))
                continue
            if cmd == "add":
                if not rest:
                    display.error("Usage: add <text> [@ when]")
                    continue
                _do_repl_add(api, cache, rest)
                continue
            if cmd == "done":
                _do_repl_state_change(api, cache, rest, completed=True, label="Completed")
                continue
            if cmd == "undone":
                _do_repl_state_change(api, cache, rest, completed=False, label="Reopened")
                continue
            if cmd == "rm":
                _do_repl_delete(api, cache, rest)
                continue
            if cmd == "remind":
                _do_repl_remind(api, cache, rest)
                continue
            if cmd in ("record", "r"):
                _do_repl_record(api, cache, pending)
                continue
            if cmd == "ingest":
                if not rest:
                    display.error("Usage: ingest <path>")
                    continue
                _do_repl_ingest(api, cache, Path(rest[0]))
                continue
            if cmd == "retry":
                _do_repl_retry(api, cache, pending)
                continue
            display.error(f"Unknown command: {cmd}. Type `help`.")
        except KeyboardInterrupt:
            print()
            continue


def _do_repl_list(api, cache) -> None:
    try:
        todos = _refresh_open(api, cache)
    except ApiError as e:
        display.warn(f"{e} — showing cached")
        todos = cache.get_open()
    display.render_list(todos)


def _do_repl_add(api, cache, parts: list[str]) -> None:
    # split on " @ " (or trailing "@ ...") to allow inline reminder time
    raw = " ".join(parts)
    text, when_str = raw, None
    if " @ " in raw:
        text, when_str = raw.split(" @ ", 1)
    elif raw.startswith("@ "):
        text, when_str = "", raw[2:]
    text = text.strip()
    if not text:
        display.error("Empty todo text.")
        return
    due = None
    if when_str:
        when = dateparse.parse_when(when_str.strip())
        if when is None:
            display.error(f"Couldn't parse time: {when_str!r}")
            return
        due = display.format_iso_utc(when)
    try:
        t = api.create_todo(text, due_at=due)
    except ApiError as e:
        display.error(str(e))
        return
    rel = ""
    if t.get("due_at"):
        w = display.parse_iso(t["due_at"])
        if w:
            rel = f" @ {display.format_relative(w)}"
    display.info(f"Added [{t['id']}]: {t['text']}{rel}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass


def _do_repl_state_change(api, cache, parts, *, completed: bool, label: str) -> None:
    if not parts:
        display.error(f"Usage: {label.lower()} <id>...")
        return
    for s in parts:
        try:
            tid = int(s)
        except ValueError:
            display.error(f"Not an id: {s}")
            continue
        try:
            api.update_todo(tid, completed=completed)
            display.info(f"{label} [{tid}]")
        except ApiError as e:
            display.error(f"[{tid}] {e}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass


def _do_repl_delete(api, cache, parts) -> None:
    if not parts:
        display.error("Usage: rm <id>...")
        return
    for s in parts:
        try:
            tid = int(s)
        except ValueError:
            display.error(f"Not an id: {s}")
            continue
        try:
            api.delete_todo(tid)
            display.info(f"Deleted [{tid}]")
        except ApiError as e:
            display.error(f"[{tid}] {e}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass


def _do_repl_remind(api, cache, parts) -> None:
    if len(parts) < 2:
        display.error("Usage: remind <id> <when>  (or `remind <id> clear`)")
        return
    try:
        tid = int(parts[0])
    except ValueError:
        display.error(f"Not an id: {parts[0]}")
        return
    rest = " ".join(parts[1:]).strip()
    if rest.lower() in ("clear", "none", "off"):
        try:
            api.update_todo(tid, due_at="")
            display.info(f"Cleared reminder on [{tid}]")
        except ApiError as e:
            display.error(str(e))
        return
    when = dateparse.parse_when(rest)
    if when is None:
        display.error(f"Couldn't parse {rest!r}")
        return
    try:
        api.update_todo(tid, due_at=display.format_iso_utc(when))
        display.info(f"Reminder on [{tid}] set to {display.format_relative(when)}")
    except ApiError as e:
        display.error(str(e))
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass


def _do_repl_record(api, cache, pending) -> None:
    backend = record.detect_backend()
    if backend == "none":
        display.error(
            "No audio backend. Install one of: sounddevice/soundfile, sox, ffmpeg."
        )
        return
    out_path = Path(tempfile.mkstemp(prefix="voicetodo_", suffix=".wav")[1])
    try:
        result = record.record_until_enter(out_path)
    except record.RecorderError as e:
        display.error(str(e))
        out_path.unlink(missing_ok=True)
        return
    display.info(f"recorded {result.duration:.1f}s; uploading…")
    try:
        upload = api.upload_audio(result.path, source="cli-repl")
    except ApiError as e:
        display.error(f"Upload failed: {e}")
        saved = pending.enqueue(result.path)
        display.warn(f"Saved to {saved}; try `retry` later.")
        return
    out_path.unlink(missing_ok=True)
    display.info(f"transcript: {upload.transcript}")
    for t in upload.todos:
        display.info(f"  + [{t['id']}] {t['text']}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass


def _do_repl_ingest(api, cache, p: Path) -> None:
    if not p.exists():
        display.error(f"No such file: {p}")
        return
    try:
        upload = api.upload_audio(p, source="cli-ingest")
    except ApiError as e:
        display.error(str(e))
        return
    display.info(f"transcript: {upload.transcript}")
    for t in upload.todos:
        display.info(f"  + [{t['id']}] {t['text']}")
    try:
        _refresh_open(api, cache)
    except ApiError:
        pass


def _do_repl_retry(api, cache, pending) -> None:
    files = pending.list()
    if not files:
        display.info("No pending uploads.")
        return
    succeeded = 0
    for f in files:
        try:
            upload = api.upload_audio(f, source="cli-retry")
            display.info(f"{f.name}: {upload.transcript}")
            pending.remove(f)
            succeeded += 1
        except ApiError as e:
            display.warn(f"{f.name}: {e}")
    display.info(f"{succeeded}/{len(files)} uploaded")
    if succeeded:
        try:
            _refresh_open(api, cache)
        except ApiError:
            pass


# ---------------------------------------------------------------- argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voicetodo",
        description="VoiceTodo CLI — talk to a voicetodo-server from the terminal.",
    )
    p.add_argument("--url", default=None, help="server URL (overrides config)")
    p.add_argument("--api-key", default=None, help="API key (overrides config)")
    p.add_argument("--version", action="version", version=f"voicetodo-cli {__version__}")

    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("configure", help="set the server URL and API key interactively")
    sp.set_defaults(func=cmd_configure)

    sp = sub.add_parser("list", help="list open todos")
    sp.add_argument("--completed", action="store_true",
                    help="show completed todos instead")
    sp.add_argument("--offline", action="store_true",
                    help="don't contact the server, just show the cache")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("add", help="add a typed todo")
    sp.add_argument("text", nargs="+")
    sp.add_argument("-p", "--priority", type=int, default=0)
    sp.add_argument(
        "--when", default=None,
        help="reminder time (e.g. 'tomorrow 9am', '2026-05-01 17:00', 'in 2h')",
    )
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("done", help="mark todos completed")
    sp.add_argument("ids", nargs="+", type=int)
    sp.set_defaults(func=cmd_done)

    sp = sub.add_parser("undone", help="reopen completed todos")
    sp.add_argument("ids", nargs="+", type=int)
    sp.set_defaults(func=cmd_undone)

    sp = sub.add_parser("rm", help="delete todos")
    sp.add_argument("ids", nargs="+", type=int)
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("remind", help="set or clear a reminder")
    sp.add_argument("id", type=int)
    sp.add_argument("when", nargs="*", help="reminder time (omit with --clear)")
    sp.add_argument("--clear", action="store_true", help="remove the reminder")
    sp.set_defaults(func=cmd_remind)

    sp = sub.add_parser("record", help="record a voice memo and upload it")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("ingest", help="upload an existing audio file")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("retry", help="retry uploads in the pending queue")
    sp.set_defaults(func=cmd_retry)

    sp = sub.add_parser("refresh", help="re-fetch the open list and update the cache")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("health", help="check the server's /health endpoint")
    sp.set_defaults(func=cmd_health)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        return repl(args)
    try:
        return args.func(args) or 0
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as e:  # last-resort: don't dump a traceback at the user
        display.error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
