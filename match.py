"""Resolve the way a person names a speaker to a Music AirPlay device."""

from __future__ import annotations

import re

from config import Device as DeviceConfig
from music import Device


class NoMatch(Exception):
    pass


# filler around a speaker's name, in English and Chinese
_FILLER = re.compile(r"\b(my|the|on)\b|我的")


def _norm(text: str) -> str:
    text = _FILLER.sub("", text.lower().replace("’", "'"))
    return re.sub(r"[\s'_\-]+", "", text)


def _names(device: Device, config: dict[str, DeviceConfig]) -> list[str]:
    extra = config.get(device.name)
    return [device.name, *(extra.aliases if extra else ())]


def resolve(ref: str, devices: list[Device], config: dict[str, DeviceConfig]) -> Device:
    want = _norm(ref)
    if not want:
        raise NoMatch(f"No speaker named {ref!r}.")
    exact = [d for d in devices if want in map(_norm, _names(d, config))]
    if len(exact) == 1:
        return exact[0]
    loose = exact or [
        d
        for d in devices
        if any(want in n or n in want for n in map(_norm, _names(d, config)) if n)
        or want in _norm(d.kind)
    ]
    if len(loose) == 1:
        return loose[0]
    listed = ", ".join(d.name for d in (loose or devices))
    if loose:
        raise NoMatch(f"{ref!r} could be any of: {listed}. Ask which one.")
    raise NoMatch(f"No speaker matches {ref!r}. Speakers: {listed}.")
