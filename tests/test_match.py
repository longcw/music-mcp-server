import pytest

from config import Device as DeviceConfig
from match import NoMatch, resolve
from music import Device

DEVICES = [
    Device("Mac mini", "computer", True, 30, True),
    Device("MacBook Pro", "Apple TV", False, 30, True),
    Device("AirPlay Living Room", "HomePod", False, 30, True),
    Device("客厅", "Apple TV", False, 30, True),
]
CONFIG = {
    "AirPlay Living Room": DeviceConfig(aliases=("客厅音响", "sound bar")),
    "客厅": DeviceConfig(aliases=("Apple TV",)),
}


@pytest.mark.parametrize(
    ("ref", "name"),
    [
        ("客厅音响", "AirPlay Living Room"),
        ("my Apple TV", "客厅"),
        ("the living room", "AirPlay Living Room"),
        ("Sound Bar", "AirPlay Living Room"),
        ("macbook", "MacBook Pro"),
        ("我的客厅", "客厅"),
        ("homepod", "AirPlay Living Room"),
    ],
)
def test_resolves(ref: str, name: str) -> None:
    assert resolve(ref, DEVICES, CONFIG).name == name


def test_ambiguous_lists_the_candidates() -> None:
    with pytest.raises(NoMatch, match="Mac mini, MacBook Pro"):
        resolve("mac", DEVICES, CONFIG)


def test_unknown_lists_every_speaker() -> None:
    with pytest.raises(NoMatch, match="Speakers: Mac mini"):
        resolve("kitchen", DEVICES, CONFIG)
