"""Parse Flipper Zero .ir files and convert signals for storage."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.util import slugify

from .const import (
    ATTR_DIRECTION,
    COMMAND_TYPE_RAW,
    DEFAULT_CARRIER_FREQUENCY,
    DIRECTION_OUTPUT,
)

_LOGGER = logging.getLogger(__name__)


def parse_flipper_ir(content: str) -> list[dict[str, Any]]:
    """Parse a Flipper .ir file into signal blocks."""
    signals: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("Filetype:") or line.startswith("Version:"):
            continue
        if line.startswith("#"):
            if current.get("name"):
                signals.append(current)
            current = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.strip().lower()] = value.strip()

    if current.get("name"):
        signals.append(current)

    return signals


def _parse_hex_bytes(value: str) -> list[int]:
    """Parse Flipper hex byte fields."""
    return [int(part, 16) for part in value.split()]


def _first_hex_byte(value: str) -> int:
    """Return the first byte of a Flipper hex field."""
    parts = _parse_hex_bytes(value)
    return parts[0] if parts else 0


def _flipper_uint32_from_hex(value: str) -> int:
    """Convert a Flipper 4-byte hex field to a little-endian integer."""
    parts = _parse_hex_bytes(value)
    while len(parts) < 4:
        parts.append(0)
    return parts[0] | (parts[1] << 8) | (parts[2] << 16) | (parts[3] << 24)


def _flipper_address_uint16(value: str) -> int:
    """Convert the first two bytes of a Flipper address field."""
    parts = _parse_hex_bytes(value)
    if len(parts) >= 2:
        return parts[0] | (parts[1] << 8)
    return parts[0] if parts else 0


def _kaseikyo_error_correction(data: bytes) -> bytes:
    """Append the Flipper/PDWM XOR checksum byte."""
    if len(data) < 5:
        return b"\x00"
    return bytes([data[2] ^ data[3] ^ data[4]])


def _build_kaseikyo_command(
    address_hex: str, command_hex: str, frequency: int
) -> Any | None:
    """Build a Kaseikyo command from Flipper address and command fields."""
    from infrared_protocols.commands.kaseikyo import KaseikyoCommand

    address = _flipper_uint32_from_hex(address_hex)
    command = _flipper_uint32_from_hex(command_hex) & 0xFFFF

    vendor_id = (address >> 8) & 0xFFFF
    genre1 = (address >> 4) & 0xF
    genre2 = address & 0xF
    device_id = (address >> 24) & 0x3

    data3 = (genre2 & 0xF) | ((command & 0xF) << 4)
    data4 = (device_id << 6) | ((command >> 4) & 0x3F)

    return KaseikyoCommand(
        address=vendor_id,
        data=bytes([(genre1 << 4), data3, data4]),
        error_correction=_kaseikyo_error_correction,
        modulation=frequency or 38000,
    )


def _sony_address_bits(protocol_key: str) -> int | None:
    """Return Sony/SIRC address bit width for a Flipper protocol name."""
    if protocol_key in {"sirc", "sony", "sony12"}:
        return 5
    if protocol_key in {"sirc15", "sony15"}:
        return 8
    if protocol_key in {"sirc20", "sony20"}:
        return 13
    if protocol_key.startswith("sirc"):
        if protocol_key.endswith("15"):
            return 8
        if protocol_key.endswith("20"):
            return 13
        return 5
    if protocol_key.startswith("sony"):
        if protocol_key.endswith("12"):
            return 5
        if protocol_key.endswith("15"):
            return 8
        if protocol_key.endswith("20"):
            return 13
        return 8
    return None


def _build_parsed_command(
    protocol: str,
    address_hex: str,
    command_hex: str,
    frequency: int,
) -> Any | None:
    """Build an infrared-protocols command for a Flipper parsed signal."""
    protocol_key = protocol.strip().lower()
    sony_address_bits = _sony_address_bits(protocol_key)

    try:
        if protocol_key in {"kaseikyo", "panasonic"}:
            return _build_kaseikyo_command(address_hex, command_hex, frequency)

        if protocol_key in {"samsung32", "samsung"}:
            from infrared_protocols.commands.samsung import Samsung32Command

            return Samsung32Command(
                address=_flipper_address_uint16(address_hex),
                command=_first_hex_byte(command_hex),
                modulation=frequency,
            )

        if protocol_key in {"nec", "necext", "nec42", "nec42ext"}:
            from infrared_protocols.commands.nec import NECCommand

            if "42" in protocol_key:
                address = _flipper_uint32_from_hex(address_hex)
                command = _flipper_uint32_from_hex(command_hex) & 0xFFFF
            else:
                address = _flipper_address_uint16(address_hex)
                command = _first_hex_byte(command_hex)
            return NECCommand(address=address, command=command, modulation=frequency)

        if protocol_key in {"rc5", "rc5x", "rc6"}:
            from infrared_protocols.commands.rc5 import RC5Command

            return RC5Command(
                address=_flipper_address_uint16(address_hex) & 0x1F,
                command=_first_hex_byte(command_hex) & 0x7F,
                modulation=frequency or 36000,
            )

        if sony_address_bits is not None:
            from infrared_protocols.commands.sony import SonyCommand

            return SonyCommand(
                address=_flipper_address_uint16(address_hex),
                address_bits=sony_address_bits,
                command=_first_hex_byte(command_hex) & 0x7F,
                modulation=frequency or 40000,
            )

        if protocol_key == "sharp":
            from infrared_protocols.commands.sharp import SharpCommand

            return SharpCommand(
                address=_flipper_address_uint16(address_hex) & 0x1F,
                command=_first_hex_byte(command_hex) & 0xFF,
                modulation=frequency,
            )
    except (ImportError, ValueError, TypeError) as err:
        _LOGGER.debug(
            "Could not build parsed command for protocol %s: %s", protocol, err
        )
        return None

    _LOGGER.debug("Unsupported Flipper protocol: %s", protocol)
    return None


def signal_to_command_data(signal: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a parsed Flipper signal into stored command data."""
    signal_type = signal.get("type", "").lower()
    frequency = int(signal.get("frequency", DEFAULT_CARRIER_FREQUENCY))

    if signal_type == "raw":
        raw_values = signal.get("data", "")
        if not raw_values:
            return None
        timings = [int(value) for value in raw_values.split()]
        if not timings:
            return None
        return {
            "type": COMMAND_TYPE_RAW,
            ATTR_DIRECTION: DIRECTION_OUTPUT,
            "carrier_frequency": frequency,
            "command": timings,
        }

    if signal_type in {"parsed", "parsed_array"}:
        protocol = signal.get("protocol")
        if not protocol:
            return None
        address_hex = signal.get("address", "00")
        command_hex = signal.get("command", "00")
        parsed_command = _build_parsed_command(
            protocol, address_hex, command_hex, frequency
        )
        if parsed_command is None:
            return None
        return {
            "type": COMMAND_TYPE_RAW,
            ATTR_DIRECTION: DIRECTION_OUTPUT,
            "carrier_frequency": parsed_command.modulation,
            "command": parsed_command.get_raw_timings(),
        }

    return None


def signals_to_command_map(signals: list[dict[str, Any]]) -> tuple[dict[str, dict], int]:
    """Convert Flipper signals to a slugged command map and skipped count."""
    commands: dict[str, dict[str, Any]] = {}
    skipped = 0
    used_names: set[str] = set()

    for signal in signals:
        command_data = signal_to_command_data(signal)
        if command_data is None:
            skipped += 1
            continue

        base_name = slugify(signal.get("name", ""))
        if not base_name:
            skipped += 1
            continue

        signal_name = base_name
        suffix = 2
        while signal_name in used_names:
            signal_name = f"{base_name}_{suffix}"
            suffix += 1

        used_names.add(signal_name)
        commands[signal_name] = command_data

    return commands, skipped
