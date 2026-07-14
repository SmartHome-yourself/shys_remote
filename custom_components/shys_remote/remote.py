"""Shared learn, send and validation helpers for remote signals."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.components import infrared
from homeassistant.components.infrared import InfraredReceivedSignal
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import (
    ATTR_DIRECTION,
    ATTR_MEDIUM,
    ATTR_NAME,
    COMMAND_TYPE_RAW,
    CONF_RF_FREQUENCY,
    CONF_SEND_REPEAT_COUNT,
    CONF_SEND_REPEAT_DELAY_MS,
    DEFAULT_CARRIER_FREQUENCY,
    DEFAULT_LEARN_TIMEOUT,
    DEFAULT_RF_FREQUENCY,
    DIRECTION_BOTH,
    DIRECTION_INPUT,
    DIRECTION_OUTPUT,
    DOMAIN,
    MIN_RF_CAPTURE_PULSES,
    get_device_send_options,
)
from .signal_transport import (
    SIGNAL_BACKEND_HOMEASSISTANT_INFRARED,
    SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY,
    SIGNAL_MEDIUM_IR,
    SIGNAL_MEDIUM_RF,
    build_rf_command,
    get_signal_backend,
    get_transport_entity_id,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from .manager import RemoteManager


def validate_receiver(hass: HomeAssistant, entity_id: str) -> None:
    """Ensure the entity is a known infrared receiver."""
    if entity_id not in infrared.async_get_receivers(hass):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_receiver",
            translation_placeholders={"entity_id": entity_id},
        )


def validate_emitter(hass: HomeAssistant, entity_id: str) -> None:
    """Ensure the entity is a known infrared emitter."""
    if entity_id not in infrared.async_get_emitters(hass):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_emitter",
            translation_placeholders={"entity_id": entity_id},
        )


async def async_wait_for_signal(
    hass: HomeAssistant,
    receiver_entity_id: str,
    timeout: int,
) -> InfraredReceivedSignal:
    """Subscribe to a receiver and wait for the next signal."""
    event = asyncio.Event()
    received: dict[str, InfraredReceivedSignal] = {}

    @callback
    def on_signal(signal: InfraredReceivedSignal) -> None:
        received["signal"] = signal
        event.set()

    try:
        unsubscribe = infrared.async_subscribe_receiver(
            hass, receiver_entity_id, on_signal
        )
    except HomeAssistantError as err:
        raise ServiceValidationError(str(err)) from err

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="learn_timeout",
            translation_placeholders={"timeout": str(timeout)},
        ) from err
    finally:
        unsubscribe()

    return received["signal"]


async def async_capture_rf_signal(
    hass: HomeAssistant,
    receiver: str,
    timeout: int,
) -> list[int]:
    """Wait for one RF capture and return its raw timings.

    Raises ServiceValidationError - "learn_timeout" via async_wait_for_signal,
    "empty_signal" or "rf_capture_too_short" here - either way, the caller
    doesn't need to know why a capture failed to show a sensible error.

    A single capture is trusted as-is (no second, confirming capture). RF
    devices with rotating codes or a multi-repeat burst per button press
    (e.g. Emil-Lux/Tronic sockets sending ~4 cycles per press) don't produce
    two independent captures that agree closely enough for a comparison to
    be meaningful - two separate presses of a rotating-code remote are
    *supposed* to differ. validate_rf_capture_length() is a much weaker,
    protocol-agnostic sanity check instead: it only rejects captures too
    short to plausibly be a real signal (a stray AGC glitch), not ones that
    merely differ from a previous capture.
    """
    signal = await async_wait_for_signal(hass, receiver, timeout)
    if not signal.timings:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="empty_signal",
        )
    timings = list(signal.timings)
    validate_rf_capture_length(timings)
    return timings


def validate_rf_capture_length(timings: list[int]) -> None:
    """Raise ServiceValidationError if a capture is implausibly short.

    Pure noise filter, not a protocol check - see MIN_RF_CAPTURE_PULSES.
    """
    if len(timings) < MIN_RF_CAPTURE_PULSES:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rf_capture_too_short",
        )


def build_rf_command_data(
    subentry: ConfigSubentry,
    direction: str,
    timings: list[int],
) -> dict[str, Any]:
    """Build stored command_data for a confirmed RF capture.

    ESPHome's infrared-compatible receiver only ever reports raw timings
    (InfraredReceivedSignal(timings=...)), never the RF operating frequency.
    The device's configured RF frequency is therefore the only correct
    source here - signal.modulation would silently store the IR default
    (~38 kHz), which a real RF transmitter would reject.
    """
    return {
        "type": COMMAND_TYPE_RAW,
        ATTR_DIRECTION: direction,
        "carrier_frequency": subentry.data.get(CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY),
        "command": timings,
        "medium": SIGNAL_MEDIUM_RF,
        "backend": SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY,
    }


async def async_learn_command(
    hass: HomeAssistant,
    manager: RemoteManager,
    subentry: ConfigSubentry,
    command_name: str,
    timeout: int = DEFAULT_LEARN_TIMEOUT,
    receiver_entity_id: str | None = None,
    transmitter_entity_id: str | None = None,
    direction: str = DIRECTION_OUTPUT,
) -> None:
    """Learn a remote signal for a device subentry."""
    configured_receiver = manager.get_receiver_entity_id(subentry)
    receiver = receiver_entity_id or configured_receiver
    transmitter = transmitter_entity_id or manager.get_transmitter_entity_id(subentry)

    if receiver is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="receiver_required",
        )

    if direction in (DIRECTION_INPUT, DIRECTION_BOTH) and configured_receiver is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="receiver_required_for_input",
        )

    medium = subentry.data.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR)
    validate_receiver(hass, receiver)
    if direction in (DIRECTION_OUTPUT, DIRECTION_BOTH) and medium != SIGNAL_MEDIUM_RF:
        validate_emitter(hass, transmitter)

    subentry_commands = manager.get_subentry_commands(subentry.subentry_id)
    if command_name in subentry_commands:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="command_already_exists",
            translation_placeholders={
                "name": command_name,
                "device": subentry.title,
            },
        )

    if medium == SIGNAL_MEDIUM_RF:
        timings = await async_capture_rf_signal(hass, receiver, timeout)
        command_data = build_rf_command_data(subentry, direction, timings)
    else:
        signal = await async_wait_for_signal(hass, receiver, timeout)
        if not signal.timings:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="empty_signal",
            )
        command_data = {
            "type": COMMAND_TYPE_RAW,
            ATTR_DIRECTION: direction,
            "carrier_frequency": signal.modulation or DEFAULT_CARRIER_FREQUENCY,
            "command": list(signal.timings),
            "medium": medium,
            "backend": SIGNAL_BACKEND_HOMEASSISTANT_INFRARED,
        }

    await manager.async_add_command(subentry, command_name, command_data)


async def async_delete_command(
    manager: RemoteManager,
    subentry: ConfigSubentry,
    command_name: str,
) -> None:
    """Delete a learned command from a device subentry."""
    if command_name not in manager.get_subentry_commands(subentry.subentry_id):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="command_not_found",
            translation_placeholders={
                "name": command_name,
                "device": subentry.title,
            },
        )

    await manager.async_remove_command(subentry.subentry_id, command_name)


async def async_send_output_command(
    hass: HomeAssistant,
    manager: RemoteManager,
    subentry: ConfigSubentry,
    command_data: dict,
    *,
    context: Context | None = None,
) -> None:
    """Send an output signal using the device repeat settings."""
    send_options = get_device_send_options(subentry)
    repeat_count = send_options[CONF_SEND_REPEAT_COUNT]
    repeat_delay_ms = send_options[CONF_SEND_REPEAT_DELAY_MS]
    command = manager.build_command(command_data, subentry)
    backend = get_signal_backend(command_data)
    transport_entity_id = get_transport_entity_id(subentry, command_data)

    if transport_entity_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_emitter",
            translation_placeholders={
                "entity_id": (
                    "<radio-frequency-entity>"
                    if backend == SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY
                    else "<infrared-entity>"
                )
            },
        )

    if backend != SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY:
        validate_emitter(hass, transport_entity_id)

    async def _send(cmd: Any) -> None:
        if backend == SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY:
            try:
                from homeassistant.components import radio_frequency
            except ImportError as err:  # pragma: no cover - environment dependent
                raise HomeAssistantError(
                    "radio_frequency backend is not available in this Home Assistant build"
                ) from err

            await radio_frequency.async_send_command(
                hass,
                transport_entity_id,
                build_rf_command(cmd),
                context=context,
            )
            return

        await infrared.async_send_command(
            hass,
            transport_entity_id,
            cmd,
            context=context,
        )

    for attempt in range(repeat_count):
        await _send(command)
        if attempt < repeat_count - 1 and repeat_delay_ms > 0:
            await asyncio.sleep(repeat_delay_ms / 1000)
