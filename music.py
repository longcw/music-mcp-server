"""Music.app and Shortcuts, driven through osascript and the shortcuts CLI."""

from __future__ import annotations

import asyncio
import time
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
    # False with no current track, which Music may still call paused: after a stop, or
    # while a shortcut's queue waits for a play
    loaded: bool = True

    @property
    def shown(self) -> str:
        return self.state if self.loaded else "stopped"

    def describe(self) -> str:
        if not self.loaded:
            return "Music is stopped."
        if not self.track:
            return f"Music is {self.state}."
        return (
            f"{self.state.capitalize()}: {self.track} by {self.artist} ({self.album})."
        )


async def _run(*argv: str, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        # shortcuts run reads its input from a stdin that is not a terminal
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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
    # every Apple event about AirPlay devices costs Music ~0.4 s, so ask once for all
    out = await osa(
        """set out to ""
repeat with p in (get properties of every AirPlay device)
    set out to out & (name of p) & tab & (kind of p as text) & tab & (selected of p as text) & tab & (sound volume of p as text) & tab & (available of p as text) & linefeed
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


async def reconnect() -> None:
    """Rebuild the sessions to the selected AirPlay speakers, silently while nothing plays.

    Music keeps a speaker's session while it stays selected and, once the speaker drops
    its end, plays into it without sound; selecting the speaker again changes nothing.
    """
    await osa(
        """set wanted to current AirPlay devices
if (count of (AirPlay devices whose selected is true and kind is not computer)) is 0 then return
set current AirPlay devices to {first AirPlay device whose kind is computer}
set current AirPlay devices to wanted"""
    )


async def airplay_started(since: float) -> bool:
    """Whether AirPlay began streaming to a speaker after the given epoch time.

    Music may drop a selected speaker from its stream and play on the Mac while still
    calling it selected; only AirPlay's own log tells.
    """
    start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    out = await _run(
        "log",
        "show",
        "--style",
        "compact",
        "--start",
        start,
        "--predicate",
        'process == "AirPlayXPCHelper" AND eventMessage CONTAINS "playback state from" '
        'AND eventMessage CONTAINS " to Started"',
    )
    return any(line.startswith("20") for line in out.splitlines())


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
        return Playing(state, loaded=False)
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
