"""Apple Music catalog search through the public iTunes Search API.

The API finds no music in some stores, China among them, while its lookup still works
there. So searches run in other stores, and only what the account's store also sells is
kept. Those stores share item ids with it where the same label releases there; Taiwan's
does for Mandarin music and the US one for English.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import httpx

Kind = Literal["song", "album", "artist"]

_API = "https://itunes.apple.com"

# versions people seldom mean when they name a song
_FLAGS = {
    "live": re.compile(r"\blive\b|现场|現場|演唱会|演唱會", re.IGNORECASE),
    "instrumental": re.compile(
        r"instrumental|伴奏|karaoke|纯音乐|純音樂|off vocal|伴唱|钢琴曲|鋼琴曲|piano version"
        r"|八音盒|music box",
        re.IGNORECASE,
    ),
    "remix": re.compile(r"\bremix\b|\bmix\)|混音", re.IGNORECASE),
    "cover": re.compile(r"翻唱|\bcover\b|tribute|in the style of", re.IGNORECASE),
}


@dataclass(frozen=True)
class Item:
    kind: Literal["song", "album"]
    id: int
    name: str
    artist: str
    album: str
    album_id: int
    year: str = ""
    # position on its disc, for an album whose track list no store serves
    track: int = 1
    compilation: bool = False

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def flags(self) -> list[str]:
        text = f"{self.name} {self.album}"
        found = [flag for flag, pattern in _FLAGS.items() if pattern.search(text)]
        return [*found, "compilation"] if self.compilation else found

    def describe(self) -> str:
        year = f", {self.year}" if self.year else ""
        flags = "".join(f" [{f}]" for f in self.flags)
        if self.kind == "album":
            return f"{self.ref}  album {self.name} by {self.artist}{year}{flags}"
        return f"{self.ref}  {self.name} by {self.artist} (album {self.album}{year}){flags}"


def _item(r: dict) -> Item | None:
    year = r.get("releaseDate", "")[:4]
    # a various-artists album is a compilation; an artist's own best-of is not flagged
    compilation = r.get("collectionArtistName", "").lower() in (
        "various artists",
        "群星",
    )
    if r.get("wrapperType") == "track" and r.get("kind") == "song":
        return Item(
            "song",
            r["trackId"],
            r["trackName"],
            r["artistName"],
            r["collectionName"],
            r["collectionId"],
            year,
            r.get("trackNumber", 1),
            compilation,
        )
    if r.get("wrapperType") == "collection":
        name = r["collectionName"]
        return Item(
            "album",
            r["collectionId"],
            name,
            r["artistName"],
            name,
            r["collectionId"],
            year,
            compilation=compilation,
        )
    return None


async def _get(path: str, **params: str | int) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{_API}/{path}", params=params)
        resp.raise_for_status()
        return resp.json()["results"]


async def search(
    query: str, kind: Kind, *, stores: list[str], storefront: str, limit: int
) -> list[Item]:
    """Matches from each store in turn that the account's store sells, best first."""
    params: dict[str, str | int] = {"entity": "album" if kind == "album" else "song"}
    if kind == "artist":
        # the artist's own songs, most popular first
        params["attribute"] = "artistTerm"
    found: list[Item] = []
    for store in stores:
        rows = await _get(
            "search", term=query, media="music", country=store, limit=50, **params
        )
        found += [i for r in rows if (i := _item(r))]
    ids = list(dict.fromkeys(i.id for i in found))
    if not ids:
        return []
    # names as the account's store shows them, which is also what Music reports back
    sold = await _get("lookup", id=",".join(map(str, ids[:200])), country=storefront)
    local = {i.id: i for r in sold if (i := _item(r))}
    items = [local[i] for i in ids if i in local]
    # versions the person did not ask for go last, in their found order
    asked = {flag for flag, pattern in _FLAGS.items() if pattern.search(query)}
    items.sort(key=lambda i: bool(set(i.flags) - asked))
    return items[:limit]


async def lookup(ref: str, storefront: str) -> Item | None:
    kind, _, id_ = ref.partition(":")
    if kind not in ("song", "album") or not id_.isdigit():
        return None
    rows = await _get("lookup", id=id_, country=storefront)
    return next((i for r in rows if (i := _item(r)) and i.kind == kind), None)


@dataclass(frozen=True)
class Listing:
    """An album's songs in play order, under one store's names."""

    album: str
    ids: list[int]
    names: list[str]


async def listing(album_id: int, stores: list[str]) -> Listing | None:
    """The album's track list from the first store that serves one."""
    for store in stores:
        rows = await _get("lookup", id=album_id, entity="song", country=store)
        songs = sorted(
            (r for r in rows if r.get("kind") == "song"),
            key=lambda r: (r.get("discNumber", 1), r.get("trackNumber", 0)),
        )
        if songs:
            return Listing(
                songs[0]["collectionName"],
                [r["trackId"] for r in songs],
                [r["trackName"] for r in songs],
            )
    return None


def album_url(album_id: int, storefront: str) -> str:
    return f"music://music.apple.com/{storefront}/album/{album_id}"
