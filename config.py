"""Settings from the environment and the speaker and preset config file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Device:
    aliases: tuple[str, ...] = ()
    # the loudest a call may set this speaker to
    max_volume: int = 100


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    # the Shortcuts folder whose shortcuts are the presets
    shortcuts_folder: str
    # the store the account plays from, and the stores searched for what it sells
    storefront: str
    search_stores: list[str]
    # the speaker music goes to when none is named and only the Mac itself is selected
    default_device: str | None = None
    devices: dict[str, Device] = field(default_factory=dict)
    # volume a preset starts at, by its name
    preset_volumes: dict[str, int] = field(default_factory=dict)


def load_settings() -> Settings:
    path = Path(os.getenv("MUSIC_MCP_CONFIG", Path(__file__).parent / "music.yaml"))
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    return Settings(
        host=os.getenv("MUSIC_MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MUSIC_MCP_PORT", "8961")),
        shortcuts_folder=raw.get("shortcuts_folder", "Music"),
        storefront=raw.get("storefront", "us"),
        search_stores=list(raw.get("search_stores") or [raw.get("storefront", "us")]),
        default_device=raw.get("default_device"),
        devices={
            name: Device(
                aliases=tuple(d.get("aliases") or ()),
                max_volume=int(d.get("max_volume", 100)),
            )
            for name, d in (raw.get("devices") or {}).items()
        },
        preset_volumes={
            name: int(p["volume"])
            for name, p in (raw.get("presets") or {}).items()
            if p and "volume" in p
        },
    )


settings = load_settings()
