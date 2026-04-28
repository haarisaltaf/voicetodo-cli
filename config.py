"""Configuration: server URL + API key, with environment override.

Resolution order (highest priority first):
  1. Explicit --url / --api-key on the command line
  2. VOICETODO_URL / VOICETODO_API_KEY env vars
  3. The TOML file at ~/.config/voicetodo-cli/config.toml
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

# tomllib is stdlib in 3.11+; fall back to tomli for older.
if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[import-not-found]
    except ImportError as e:
        raise SystemExit(
            "voicetodo-cli needs Python 3.11+ or `pip install tomli`"
        ) from e


@dataclass(frozen=True)
class Config:
    url: str = ""
    api_key: str = ""
    cache_dir: Path = Path.home() / ".cache" / "voicetodo-cli"
    audio_dir: Path = Path.home() / ".cache" / "voicetodo-cli" / "pending"

    def normalised(self) -> "Config":
        u = self.url.strip()
        if u and "://" not in u:
            u = "http://" + u
        while u.endswith("/"):
            u = u[:-1]
        return replace(self, url=u, api_key=self.api_key.strip())

    @property
    def is_configured(self) -> bool:
        return bool(self.url)


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "voicetodo-cli" / "config.toml"


def load(
    *,
    url_override: Optional[str] = None,
    key_override: Optional[str] = None,
) -> Config:
    cfg = Config()

    p = config_path()
    if p.exists():
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
            cfg = replace(
                cfg,
                url=str(data.get("url", "")),
                api_key=str(data.get("api_key", "")),
            )
        except Exception as e:  # corrupt/unparseable config shouldn't kill the CLI
            print(f"warning: ignoring corrupt {p}: {e}", file=sys.stderr)

    env_url = os.environ.get("VOICETODO_URL")
    if env_url:
        cfg = replace(cfg, url=env_url)
    env_key = os.environ.get("VOICETODO_API_KEY")
    if env_key:
        cfg = replace(cfg, api_key=env_key)

    if url_override is not None:
        cfg = replace(cfg, url=url_override)
    if key_override is not None:
        cfg = replace(cfg, api_key=key_override)

    return cfg.normalised()


def save(cfg: Config) -> None:
    """Persist URL and API key to the TOML file. We hand-format because the
    stdlib ships only a TOML reader, not a writer."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    body = (
        '# voicetodo-cli configuration. Either edit this file, set\n'
        '# VOICETODO_URL / VOICETODO_API_KEY env vars, or run\n'
        '# `voicetodo configure` to update it.\n'
        f'url = "{esc(cfg.url)}"\n'
        f'api_key = "{esc(cfg.api_key)}"\n'
    )
    p.write_text(body)
    # API key is sensitive — restrict permissions on POSIX.
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
