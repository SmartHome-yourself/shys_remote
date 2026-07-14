"""Type stub for Home Assistant's built-in `radio_frequency` integration.

Real (introduced in HA 2026.5), but newer than the Home Assistant version
pinned in this project's dev .venv, so it doesn't resolve for editors/type
checkers without this stub. Not used at runtime.

Looking up compatible transmitters requires both frequency and modulation
(async_get_transmitters), not a plain async_get_emitters() - this project
targeted the wrong name until that was caught against the real 2026.7 API.
"""

from enum import Enum
from typing import Protocol

from homeassistant.core import Context, HomeAssistant

class ModulationType(Enum):
    OOK = "ook"

class _RawRfLikeCommand(Protocol):
    def get_raw_timings(self) -> list[int]: ...

def async_get_transmitters(
    hass: HomeAssistant,
    *,
    frequency: int,
    modulation: ModulationType,
) -> list[str]: ...
async def async_send_command(
    hass: HomeAssistant,
    entity_id: str,
    command: _RawRfLikeCommand,
    *,
    context: Context | None = ...,
) -> None: ...
