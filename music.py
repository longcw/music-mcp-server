"""Music.app and Shortcuts, driven through osascript and the shortcuts CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class MusicError(Exception):
    pass


@dataclass(frozen=True)
class Device:
    name: str
    kind: str
    selected: bool
    volume: int
    available: bool


@dataclass(frozen=True)
class Playing:
    state: str
    track: str = ""
    artist: str = ""
    album: str = ""
    # seconds into the track, and its length
    position: float = 0.0
    duration: float = 0.0

    def describe(self) -> str:
        if not self.track:
            return f"Music is {self.state}."
        return (
            f"{self.state.capitalize()}: {self.track} by {self.artist} ({self.album})."
        )


async def _run(*argv: str, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise MusicError(f"{argv[0]} timed out") from None
    if proc.returncode:
        raise MusicError(err.decode().strip() or f"{argv[0]} failed")
    return out.decode().rstrip("\n")


async def osa(body: str, *args: str) -> str:
    """Run AppleScript inside a Music tell block, with args as argv."""
    script = f'on run argv\ntell application "Music"\n{body}\nend tell\nend run'
    return await _run("osascript", "-e", script, *args)


async def devices() -> list[Device]:
    out = await osa(
        """set out to ""
repeat with d in AirPlay devices
    set out to out & (name of d) & tab & (kind of d as text) & tab & (selected of d as text) & tab & (sound volume of d as text) & tab & (available of d as text) & linefeed
end repeat
return out"""
    )
    result = []
    for line in out.splitlines():
        name, kind, selected, volume, available = line.split("\t")
        result.append(
            Device(name, kind, selected == "true", int(volume), available == "true")
        )
    return result


async def select(*names: str) -> None:
    await osa(
        """set wanted to {}
repeat with n in argv
    set end of wanted to AirPlay device (n as text)
end repeat
set current AirPlay devices to wanted""",
        *names,
    )


async def master_volume() -> int:
    return int(await osa("return sound volume"))


async def set_master_volume(level: int) -> None:
    await osa("set sound volume to (item 1 of argv as integer)", str(level))


async def skip(count: int) -> None:
    """Skip ahead count tracks, each once Music has moved off the one before."""
    await osa(
        """repeat (item 1 of argv as integer) times
    set prevName to name of current track
    next track
    repeat 40 times
        if name of current track is not prevName then exit repeat
        delay 0.05
    end repeat
end repeat""",
        str(count),
    )


async def set_volume(name: str, level: int) -> None:
    await osa(
        "set sound volume of AirPlay device (item 1 of argv) to (item 2 of argv as integer)",
        name,
        str(level),
    )


async def now_playing() -> Playing:
    out = await osa(
        """set s to player state as text
if s is "stopped" then return s
try
    return s & tab & (name of current track) & tab & (artist of current track) & tab & (album of current track) & tab & (player position as text) & tab & (duration of current track as text)
on error
    return s
end try"""
    )
    state, *rest = out.split("\t")
    if not rest:
        return Playing(state)
    track, artist, album, position, duration = rest
    return Playing(state, track, artist, album, _seconds(position), _seconds(duration))


def _seconds(text: str) -> float:
    # a streamed track still loading has no position or length yet
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return 0.0


async def command(verb: str) -> None:
    await osa(verb)


async def playlists() -> list[str]:
    out = await osa(
        """set out to ""
repeat with p in (user playlists whose special kind is none)
    set out to out & (name of p) & linefeed
end repeat
return out"""
    )
    return [line for line in out.splitlines() if line]


async def play_playlist(name: str) -> None:
    await osa("play user playlist (item 1 of argv)", name)


async def open_url(url: str) -> None:
    await _run("open", url)


async def shortcuts(folder: str) -> list[str]:
    out = await _run("shortcuts", "list", "--folder-name", folder)
    return [line for line in out.splitlines() if line]


async def run_shortcut(name: str) -> None:
    await _run("shortcuts", "run", name, timeout=60)
