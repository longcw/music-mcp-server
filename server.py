"""MCP server that plays Apple Music through Music.app on this Mac and its AirPlay speakers.

Presets are shortcuts in one Shortcuts folder, so what a preset plays is edited in the
Shortcuts app. The caller finds anything else with search_music and chooses from the
results; it is started by opening its album in Music, which needs no click and works on
a locked screen.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Literal

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool as MCPTool
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

import catalog
import match
import music
from config import settings

logger = logging.getLogger("music-mcp-server")

# one playback change at a time; a second one would race the first for Music's queue
_lock = asyncio.Lock()

# seconds an album may take to load after its page opens
_LOAD_TIMEOUT = 25

DeviceRef = Annotated[
    str | None,
    Field(
        description="The speaker as the person names it ('living room', 'Apple TV', "
        "'客厅音响'); list_devices has the names and aliases. Omit to keep the speaker "
        "already playing."
    ),
]


class _MusicMCP(FastMCP):
    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        presets, playlists = await _library()
        for tool in tools:
            if tool.name == "play_music" and (presets or playlists):
                tool.description = (
                    f"{tool.description} Presets: {', '.join(presets) or 'none'}. "
                    f"Library playlists: {', '.join(playlists) or 'none'}."
                )
        return tools


# given the real host, FastMCP only guards against DNS rebinding when bound to localhost
mcp = _MusicMCP("music", host=settings.host, port=settings.port)

_cache: tuple[float, list[str], list[str]] = (0.0, [], [])


async def _library() -> tuple[list[str], list[str]]:
    """Preset and playlist names, cached briefly since every tool listing asks."""
    global _cache
    if time.monotonic() - _cache[0] > 30:
        try:
            presets = await music.shortcuts(settings.shortcuts_folder)
            playlists = await music.playlists()
        except music.MusicError as exc:
            logger.warning("listing the library failed: %s", exc)
            return _cache[1], _cache[2]
        _cache = (time.monotonic(), presets, playlists)
    return _cache[1], _cache[2]


def _cap(name: str, level: int) -> int:
    device = settings.devices.get(name)
    return max(0, min(level, device.max_volume if device else 100))


async def _target(ref: str | None) -> music.Device | None:
    """The speaker to switch to, or None to keep the current one."""
    devices = await music.devices()
    if ref:
        try:
            return match.resolve(ref, devices, settings.devices)
        except match.NoMatch as exc:
            raise ToolError(str(exc)) from exc
    # the Mac's own speaker is never where music is meant to go, so it falls back too
    if any(d.selected and d.kind != "computer" for d in devices):
        return None
    return next(
        (d for d in devices if d.name == settings.default_device and d.available), None
    )


async def _route(ref: str | None, volume: int | None) -> str:
    """Switch speaker and volume before playing, and say where it plays."""
    target = await _target(ref)
    if target is not None:
        await music.select(target.name)
    selected = [d for d in await music.devices() if d.selected]
    if volume is not None:
        for d in selected:
            await music.set_volume(d.name, _cap(d.name, volume))
        selected = [d for d in await music.devices() if d.selected]
    return ", ".join(f"{d.name} (volume {d.volume})" for d in selected)


def _norm(text: str) -> str:
    return "".join(text.casefold().split())


def _same(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


async def _until_moving() -> None:
    """Wait for the current track to stream, resuming it if Music paused it."""
    for _ in range(40):
        now = await music.now_playing()
        if now.state == "playing" and now.position > 0:
            return
        if now.state == "paused":
            await music.command("play")
        await asyncio.sleep(0.25)


async def _play_catalog(item: catalog.Item) -> music.Playing:
    storefront = settings.storefront
    t0 = time.monotonic()
    # looked up while the album loads; a searched store lists tracks where the
    # account's may not
    listing = asyncio.create_task(
        catalog.listing(item.album_id, [*settings.search_stores, storefront])
    )
    await music.command("stop")
    # an AirPlay speaker buffers every track it is sent, so skipping there is slow; the
    # album loads and skips on the Mac's own speaker, muted, and the song is handed over
    devices = await music.devices()
    targets = {d.name: d.volume for d in devices if d.selected}
    local = (
        next((d for d in devices if d.kind == "computer"), None) if targets else None
    )
    # a selected speaker's volume moves Music's own, which scales every speaker, so the
    # silence is Music's volume at 0, and both are put back after
    master = await music.master_volume()
    await music.set_master_volume(0)
    if local is not None:
        await music.select(local.name)
    try:
        url = catalog.album_url(item.album_id, storefront)
        await music.open_url(url)
        album = await listing
        skip = 0
        if item.kind == "song":
            if album is not None and item.id in album.ids:
                skip = album.ids.index(item.id)
            else:
                skip = max(item.track - 1, 0)
        # Music names an album and its tracks as either store does
        albums = {_norm(item.album), *([_norm(album.album)] if album else [])}
        openers = {_norm(album.names[0])} if album else set()
        t1 = time.monotonic()
        reopened = False
        now = music.Playing("stopped")
        while time.monotonic() - t0 < _LOAD_TIMEOUT:
            await asyncio.sleep(0.4)
            # now and then Music shows the album but never queues it; opening it again does
            if not reopened and time.monotonic() - t0 > 8:
                reopened = True
                await music.open_url(url)
            # until the album has loaded, play fails, does nothing, or resumes the
            # queue from before, which is stopped again
            try:
                await music.command("play")
            except music.MusicError:
                continue
            now = await music.now_playing()
            # a track with no name yet is still loading
            if now.state != "playing" or not now.track:
                continue
            if _norm(now.album) in albums or _norm(now.track) in openers:
                break
            await music.command("stop")
        else:
            raise ToolError(f"Music did not start {item.album}. {now.describe()}")
        t2 = time.monotonic()
        if skip:
            await music.skip(skip)
        await _until_moving()
        logger.info(
            "played %s: lookup %.1fs, load %.1fs, %d skips and stream %.1fs",
            item.ref,
            t1 - t0,
            t2 - t1,
            skip,
            time.monotonic() - t2,
        )
        return await music.now_playing()
    finally:
        if local is not None:
            await music.select(*targets)
        await music.set_master_volume(master)
        for name, level in targets.items():
            await music.set_volume(name, level)


@mcp.tool()
async def play_music(
    ctx: Context,
    query: Annotated[
        str,
        Field(
            description="A preset or library playlist name exactly as listed below, "
            "or a ref from search_music ('song:123', 'album:456')."
        ),
    ],
    device: DeviceRef = None,
    volume: Annotated[
        int | None,
        Field(
            ge=0,
            le=100,
            description="Volume 0-100 to start at. Omit to keep the speaker's volume "
            "(a preset may set its own).",
        ),
    ] = None,
) -> str:
    """Play a preset, a library playlist, or a song or album search_music found, on this home's speakers."""
    async with _lock:
        presets, playlists = await _library()
        try:
            preset = next((p for p in presets if _same(p, query)), None)
            playlist = next((p for p in playlists if _same(p, query)), None)
            if preset is None and playlist is None:
                item = await catalog.lookup(query, settings.storefront)
                if item is None:
                    raise ToolError(
                        f"{query!r} is not a preset, a playlist or a ref. Call "
                        "search_music and pass the ref of the result you choose."
                    )
                where = await _route(device, volume)
                # loading an album takes a while; the first report lets the caller answer
                await ctx.report_progress(
                    0, None, f"Starting {item.name} by {item.artist} on {where}."
                )
                now = await _play_catalog(item)
            else:
                if volume is None and preset is not None:
                    volume = settings.preset_volumes.get(preset)
                where = await _route(device, volume)
                if preset is not None:
                    await music.run_shortcut(preset)
                else:
                    await music.play_playlist(playlist)
                await asyncio.sleep(2)
                now = await music.now_playing()
        except music.MusicError as exc:
            raise ToolError(str(exc)) from exc
    return f"{now.describe()} On {where}."


@mcp.tool()
async def search_music(
    query: Annotated[
        str,
        Field(
            description="Song or album title, with the artist when known "
            "('晴天 周杰伦', 'Shake It Off Taylor Swift'); for kind 'artist', just the "
            "artist's name."
        ),
    ],
    kind: Annotated[
        catalog.Kind,
        Field(
            description="'song', 'album', or 'artist' for an artist's own songs, "
            "most popular first (their best-known songs, 代表作)."
        ),
    ] = "song",
) -> str:
    """Search Apple Music; play_music plays a ref from the results.

    Results come best first but can include other artists, covers and other versions.
    Choose the one the person means: the right artist, and the original studio
    recording unless they ask for a version flagged [live], [instrumental], [remix],
    [cover] or [compilation]. If none fits, search again with other words.
    """
    try:
        items = await catalog.search(
            query,
            kind,
            stores=settings.search_stores,
            storefront=settings.storefront,
            limit=12,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Search failed: {exc}") from exc
    if not items:
        return f"Nothing found for {query!r}."
    return "\n".join(i.describe() for i in items)


@mcp.tool()
async def list_music() -> str:
    """The presets and library playlists play_music plays by name."""
    presets, playlists = await _library()
    lines = [
        f"preset {p}"
        + (
            f" (volume {settings.preset_volumes[p]})"
            if p in settings.preset_volumes
            else ""
        )
        for p in presets
    ]
    lines += [f"playlist {p}" for p in playlists]
    return "\n".join(lines) or "No presets or playlists."


@mcp.tool()
async def list_devices() -> str:
    """The speakers music can play on, with what people call them, which are playing and their volume."""
    lines = []
    for d in await music.devices():
        extra = settings.devices.get(d.name)
        aliases = (
            f", also called {', '.join(extra.aliases)}"
            if extra and extra.aliases
            else ""
        )
        state = (
            "playing here"
            if d.selected
            else ("available" if d.available else "offline")
        )
        lines.append(f"{d.name} ({d.kind}{aliases}): {state}, volume {d.volume}")
    return "\n".join(lines)


@mcp.tool()
async def set_volume(
    level: Annotated[
        int | None, Field(ge=0, le=100, description="Set exactly to this, 0-100.")
    ] = None,
    change: Annotated[
        int | None,
        Field(
            ge=-100,
            le=100,
            description="Or change by this much: about +10 for 'louder', -10 for "
            "'quieter', ±25 for 'a lot'.",
        ),
    ] = None,
    device: DeviceRef = None,
) -> str:
    """Set or change the volume of the speaker that is playing, or of a named one."""
    if (level is None) == (change is None):
        raise ToolError("Give either level or change.")
    async with _lock:
        devices = await music.devices()
        if device:
            try:
                targets = [match.resolve(device, devices, settings.devices)]
            except match.NoMatch as exc:
                raise ToolError(str(exc)) from exc
        else:
            targets = [d for d in devices if d.selected]
        for d in targets:
            new = level if level is not None else d.volume + change
            await music.set_volume(d.name, _cap(d.name, new))
        after = {d.name: d.volume for d in await music.devices()}
    return ", ".join(f"{d.name} volume {after[d.name]}" for d in targets) + "."


@mcp.tool()
async def control_music(
    action: Literal["pause", "resume", "next", "previous", "stop"],
) -> str:
    """Pause, resume, skip or stop what is playing."""
    verb = {"resume": "play", "next": "next track", "previous": "previous track"}
    try:
        await music.command(verb.get(action, action))
        await asyncio.sleep(1)
        return (await music.now_playing()).describe()
    except music.MusicError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def now_playing() -> str:
    """What is playing, on which speakers, at what volume."""
    now = await music.now_playing()
    where = ", ".join(
        f"{d.name} (volume {d.volume})" for d in await music.devices() if d.selected
    )
    return f"{now.describe()} Output: {where}."


# a small REST API beside MCP, for a home automation hub to show and control playback


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(request: Request) -> JSONResponse:
    try:
        now = await music.now_playing()
        devices = await music.devices()
    except music.MusicError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    selected = [d for d in devices if d.selected]
    return JSONResponse(
        {
            "state": now.state,
            "track": now.track,
            "artist": now.artist,
            "album": now.album,
            "position": now.position,
            "duration": now.duration,
            "volume": selected[0].volume if selected else None,
            "devices": [
                {
                    "name": d.name,
                    "kind": d.kind,
                    "selected": d.selected,
                    "available": d.available,
                    "volume": d.volume,
                }
                for d in devices
            ],
        }
    )


@mcp.custom_route("/api/control", methods=["POST"])
async def api_control(request: Request) -> JSONResponse:
    body = await request.json()
    verb = {
        "play": "play",
        "pause": "pause",
        "stop": "stop",
        "next": "next track",
        "previous": "previous track",
    }.get(body.get("action"))
    if verb is None:
        return JSONResponse({"error": "unknown action"}, status_code=400)
    try:
        await music.command(verb)
    except music.MusicError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    if verb.endswith("track"):
        # Music reports the track it skipped to a moment after the skip
        await asyncio.sleep(0.8)
    return await api_state(request)


@mcp.custom_route("/api/volume", methods=["POST"])
async def api_volume(request: Request) -> JSONResponse:
    body = await request.json()
    async with _lock:
        for d in await music.devices():
            if d.selected:
                await music.set_volume(d.name, _cap(d.name, int(body["level"])))
    return await api_state(request)


@mcp.custom_route("/api/source", methods=["POST"])
async def api_source(request: Request) -> JSONResponse:
    body = await request.json()
    async with _lock:
        devices = await music.devices()
        if not any(d.name == body.get("device") for d in devices):
            return JSONResponse({"error": "unknown device"}, status_code=400)
        await music.select(body["device"])
    return await api_state(request)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mcp.run("streamable-http")
