"""Helpers for abstracting signal transport across IR and RF backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

try:
    from rf_protocols import RadioFrequencyCommand
except ImportError:  # pragma: no cover - optional dependency in tests
    class RadioFrequencyCommand:  # type: ignore[no-redef]
        """Minimal fallback RF command used when rf_protocols is unavailable."""

        def __init__(
            self,
            *,
            frequency: int,
            timings: list[int],
            modulation: str = "OOK",
            repeat_count: int = 0,
        ) -> None:
            self.frequency = frequency
            self.modulation = modulation
            self.repeat_count = repeat_count
            self._timings = list(timings)

        def get_raw_timings(self) -> list[int]:
            return list(self._timings)

SIGNAL_MEDIUM_IR = "ir"
SIGNAL_MEDIUM_RF = "rf"

SIGNAL_BACKEND_HOMEASSISTANT_INFRARED = "homeassistant_infrared"
SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY = "homeassistant_radio_frequency"
SIGNAL_BACKEND_ESPHOME_PROXY = "esphome_ir_rf_proxy"


@dataclass(slots=True)
class RawSignalCommand:
    """Generic raw command payload for any transport backend.

    ``frequency`` is the IR carrier frequency in Hz for infrared signals, or
    the RF transmit frequency in Hz (e.g. 433920000) for radio-frequency
    signals. Both are stored under the same ``carrier_frequency`` key in
    command data, since a single learned signal is only ever one medium.
    """

    timings: list[int]
    frequency: int
    medium: str
    backend: str
    transport_entity_id: str | None = None

    def get_raw_timings(self) -> list[int]:
        """Return raw timings for transport backends."""
        return self.timings

    def get_carrier_frequency(self) -> int:
        """Return the carrier/transmit frequency for the transport backend."""
        return self.frequency

    def get_modulation(self) -> str:
        """Return the modulation scheme for RF transport."""
        return "OOK"


def get_signal_medium(command_data: Mapping[str, Any] | None) -> str:
    """Return the medium for a stored signal, defaulting to IR."""
    if not command_data:
        return SIGNAL_MEDIUM_IR

    medium = command_data.get("medium")
    if isinstance(medium, str) and medium:
        return medium
    return SIGNAL_MEDIUM_IR


def get_signal_backend(command_data: Mapping[str, Any] | None) -> str:
    """Return the backend for a stored signal, defaulting to IR."""
    if not command_data:
        return SIGNAL_BACKEND_HOMEASSISTANT_INFRARED

    backend = command_data.get("backend")
    if isinstance(backend, str) and backend:
        return backend

    medium = get_signal_medium(command_data)
    if medium == SIGNAL_MEDIUM_RF:
        return SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY
    return SIGNAL_BACKEND_HOMEASSISTANT_INFRARED


def get_transport_entity_id(
    subentry: Any,
    command_data: Mapping[str, Any] | None,
) -> str | None:
    """Return the transport entity for a stored signal if present."""
    if command_data is not None:
        transport_entity_id = command_data.get("transport_entity_id")
        if isinstance(transport_entity_id, str) and transport_entity_id:
            return transport_entity_id

    data = getattr(subentry, "data", {}) or {}
    entity_id = data.get("transmitter_entity_id")
    if isinstance(entity_id, str) and entity_id:
        return entity_id
    return None


def build_rf_command(command: RawSignalCommand) -> RadioFrequencyCommand:
    """Build a Home Assistant-compatible radio_frequency command from raw timings.

    repeat_count is intentionally always 0 here (single burst per command).
    Repeats are handled one layer up, by async_send_output_command() issuing
    multiple separate calls with a real delay between them - the wire-level
    repeat_count on this command has no accompanying pause/gap parameter, so
    it can't reproduce that timing on its own. Setting both would double up.
    """
    try:
        from rf_protocols.commands.ook import OOKCommand
    except ImportError:  # pragma: no cover - optional dependency in tests
        return RadioFrequencyCommand(
            frequency=command.get_carrier_frequency(),
            timings=command.get_raw_timings(),
            repeat_count=0,
        )

    return OOKCommand(
        frequency=command.get_carrier_frequency(),
        timings=command.get_raw_timings(),
        repeat_count=0,
    )
