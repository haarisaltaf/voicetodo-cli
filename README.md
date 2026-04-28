# voicetodo-cli

A terminal companion for the [voicetodo-server](../voicetodo-server) daemon —
mirrors the Android app's interactions but in CLI form. Runs on macOS and
Debian/Ubuntu (or anywhere with Python 3.11+).

## What it can do

* List open and completed todos (with reminder dates rendered relative).
* Add typed todos, including with a reminder time you can write naturally.
* Record a voice memo from the microphone and upload it to the server,
  which transcribes and decomposes it into todos.
* Upload an existing audio file (`ingest`) without recording one.
* Set / change / clear reminders on existing todos.
* Mark todos done / reopen / delete.
* Hold failed audio uploads in a pending queue and retry them later.
* Cache the open list so `list --offline` and `list` (when the server is
  unreachable) still work.
* Drop into an interactive REPL when you don't pass a subcommand.

## Install
### Add via aliasing in .zshrc or .bashrc
alias voicetodo='PYTHONPATH=.../path/above/voicetodo-cli-folder python3 -m voicetodo-cli'

### Install to pip userspace
```bash
pip install --user .
```

Or, while iterating:

```bash
pip install --user -e .
```

The only required dependency is Python 3.11+ (or 3.10 with `tomli`).
Optional extras for nicer UX:

* `pip install rich` — colourised list and table output. Without it, the CLI
  still works in plain text.
* `pip install sounddevice soundfile` — best recording backend (cross-platform,
  needs PortAudio: `apt install libportaudio2` on Debian, `brew install
  portaudio` on macOS). Without it, the CLI falls back to `sox` (`rec`) or
  `ffmpeg` if either is on `PATH`.
* `pip install dateparser` — fall-back natural-language date parser if the
  built-in one doesn't recognise something.

## First-run setup

```bash
voicetodo configure
```

Walks you through the server URL and API key. Saves to
`~/.config/voicetodo-cli/config.toml` (mode 0600), then runs a health check.

You can also configure via env vars (handy in Docker / scripts):

```bash
export VOICETODO_URL=http://192.168.1.50:8765
export VOICETODO_API_KEY=...
```

`--url` / `--api-key` on the command line override both.

## Quick reference

```bash
# List open todos
voicetodo list
voicetodo list --completed       # completed todos (separate fetch)
voicetodo list --offline         # show cache only, no network

# Add a typed todo
voicetodo add Buy milk
voicetodo add Renew car rego --when "tomorrow 9am"
voicetodo add Pay rent --when "in 2d"
voicetodo add "Take meds" --priority 2 --when "today 18:00"

# Record a voice memo (press Enter to stop)
voicetodo record

# Upload an existing recording instead
voicetodo ingest ~/Downloads/memo.m4a

# Reminders
voicetodo remind 7 fri 5pm
voicetodo remind 7 --clear

# State changes
voicetodo done 3 4
voicetodo undone 3
voicetodo rm 5

# Plumbing
voicetodo refresh                # re-fetch + update cache
voicetodo retry                  # retry pending audio uploads
voicetodo health                 # server /health
```

Run `voicetodo` with no arguments to drop into the REPL — same commands,
no prefix, plus a few shortcuts (`r` = `record`, `ls` = `list`, `q` =
`quit`).

### Reminder time formats

The `--when` flag and the `remind` command both accept:

* `tomorrow 9am`, `today 17:00`, `yesterday`
* `fri 5pm`, `wednesday 9:30`
* `2026-05-01`, `2026-05-01 17:30`
* `in 2h`, `in 30m`, `in 1d`, `in 1w`
* Bare time like `8:00` or `5pm` → today if it's still in the future,
  otherwise tomorrow.

If `dateparser` is installed, anything we don't recognise is handed off to it.

## Files

```
~/.config/voicetodo-cli/config.toml      # URL + API key (mode 0600)
~/.cache/voicetodo-cli/todos_open.json   # cached open list
~/.cache/voicetodo-cli/todos_completed.json
~/.cache/voicetodo-cli/meta.json         # last-refresh timestamp
~/.cache/voicetodo-cli/pending/          # failed audio uploads
```

## Layout

```
voicetodo-cli/
├── README.md
├── pyproject.toml
├── voicetodo_cli/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py          # argparse + REPL
│   ├── config.py       # TOML + env config
│   ├── api.py          # HTTP client (stdlib only)
│   ├── store.py        # local cache + pending uploads
│   ├── record.py       # cross-platform recorder
│   ├── dateparse.py    # natural-language reminder times
│   └── display.py      # rich-or-plain rendering
└── tests/
    └── test_all.py
```

## Limitations

* The CLI doesn't show desktop reminder notifications when a todo's
  `due_at` arrives. The Android app schedules local alarms for that;
  on a workstation, integrate with `cron` / `at` / `launchd` if you want
  the same behaviour.
* No streaming uploads — audio memos load fully into memory before being
  POSTed. Fine for any memo under a few minutes; if you need longer ones,
  ingest the file from a path instead of recording inline.
* Audio is recorded at 16 kHz mono WAV. That matches what the server's
  Whisper model resamples to anyway, so quality is identical to denser
  formats while keeping uploads tiny.
