# music-mcp-server

An MCP server that plays Apple Music on a Mac and the AirPlay speakers it can reach. A voice or chat assistant can play a preset, a library playlist or anything in the catalog, send it to a speaker people name in their own words, and turn it up or down.

It drives Music.app with AppleScript and runs shortcuts with the `shortcuts` CLI, so it needs no Apple Music API key, and it works while the Mac's screen is locked.

It was built for the [Home Assistant voice agent](https://github.com/longcw/home-assistant-mcp-agent), but works with any MCP client.

## Tools

| Tool | What it does |
| --- | --- |
| `play_music(query, device?, volume?)` | Plays a preset or library playlist by name, or a song or album by the ref `search_music` gave |
| `search_music(query, kind?)` | Catalog songs, albums, or an artist's own songs most popular first, with refs and flags for live, instrumental, remix, cover and compilation versions |
| `list_music()` | The presets and library playlists |
| `list_devices()` | AirPlay speakers with their aliases, which are playing and their volume |
| `set_volume(level? \| change?, device?)` | Sets the volume, or changes it by a step, on the playing speakers or a named one |
| `control_music(action)` | `pause`, `resume`, `next`, `previous` or `stop` |
| `now_playing()` | The track, the speakers and their volume |

`play_music` lists the current presets and playlists in its own description, so a model can pick one without calling `list_music` first. It plays nothing it was not given exactly: the calling model reads the search results and chooses, since a result list mixes in other artists, covers and other versions.

A `device` is the speaker as a person says it: its name in Music, an alias from the config, part of either, or its kind ("Apple TV"). When more than one speaker fits, the tool lists them so the client can ask. With no `device`, music stays on the speakers already selected, unless only the Mac itself is, in which case it goes to `default_device`.

## Presets

A preset is a shortcut in one Shortcuts folder (`Music` by default) whose only job is a **Play Music** action, so what it plays is edited in the Shortcuts app. The server picks the speaker and volume before it runs the shortcut, which keeps the shortcut free of macOS-only steps; iOS's **Change Playback Destination** action does not run on a Mac. A Play Music action made on an iPhone has to have its music picked again on the Mac.

## How catalog items play

Music.app has no AppleScript for catalog search or playback, and its Siri intents are not offered to Shortcuts. The server searches with Apple's public [iTunes Search API](https://performance-partners.apple.com/search-api) and starts an item by stopping Music, opening its album's `music://` link, and sending `play` until it takes; a song is reached by skipping forward from the album's first track. An AirPlay speaker buffers every track it is sent, which makes skipping there slow, so the album loads and skips on the Mac's own speaker with Music's volume at 0, and once the song streams it is handed to the speakers that were selected. It takes 8 to 12 seconds, so `play_music` sends an MCP progress notification as soon as it knows what will play.

The Search API finds no music in some stores, China among them, though its lookup works there. So `search_stores` are searched instead, and only what `storefront` (the account's store) sells is kept, under the names that store gives it. A store only helps if it shares item ids with the account's: Taiwan's does with China's for Mandarin music, and the US one for English; Hong Kong's does not.

## REST API

Beside MCP, the server answers plain HTTP for a home automation hub to show and control playback; the [LiveKit Voice integration](https://github.com/longcw/ha-livekit-agent-frontend) turns it into a Home Assistant media player.

| Endpoint | |
| --- | --- |
| `GET /api/state` | `state`, `track`, `artist`, `album`, `position`, `duration`, `volume` and the speakers |
| `POST /api/control` | `{"action": "play" \| "pause" \| "stop" \| "next" \| "previous"}` |
| `POST /api/volume` | `{"level": 0-100}` for the playing speakers |
| `POST /api/source` | `{"device": "<speaker name>"}` |

Each POST answers with the new state. Neither API has authentication, so serve it only on a trusted network.

## Run

Needs [uv](https://docs.astral.sh/uv/) and Music.app signed in to Apple Music. Turn on Sync Library in Music to reach playlists made on other devices.

```bash
cp music.example.yaml music.yaml   # speakers, aliases, presets
uv sync
uv run python server.py            # serves http://0.0.0.0:8961/mcp
```

On macOS, to run it at login and restart it if it dies:

```bash
scripts/install-launchagent.sh
```

The first call asks for permission to control Music; unlock the Mac and click Allow.

| Variable | Default | |
| --- | --- | --- |
| `MUSIC_MCP_HOST` | `0.0.0.0` | |
| `MUSIC_MCP_PORT` | `8961` | |
| `MUSIC_MCP_CONFIG` | `music.yaml` | read once at start |
