"""Type stub for the sibling `infrared` custom component.

This is not a real Home Assistant core integration package installable via
pip - it is a separate custom component that must be present in the target
Home Assistant instance at runtime. This stub only exists so editors/type
checkers can resolve the symbols SHYS Remote imports from it; it is not used
at runtime.
"""

from collections.abc import Callable
from typing import Protocol

from homeassistant.core import Context, HomeAssistant

class InfraredReceivedSignal:
    timings: list[int]
    modulation: int | None

    def __init__(
        self, timings: list[int] | None = ..., modulation: int | None = ...
    ) -> None: ...

class _RawInfraredLikeCommand(Protocol):
    def get_raw_timings(self) -> list[int]: ...

def async_get_receivers(hass: HomeAssistant) -> list[str]: ...
def async_get_emitters(hass: HomeAssistant) -> list[str]: ...
def async_subscribe_receiver(
    hass: HomeAssistant,
    entity_id: str,
    callback_: Callable[[InfraredReceivedSignal], None],
) -> Callable[[], None]: ...
async def async_send_command(
    hass: HomeAssistant,
    entity_id: str,
    command: _RawInfraredLikeCommand,
    *,
    context: Context | None = ...,
) -> None: ...
