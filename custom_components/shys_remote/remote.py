"""Shared learn, send and validation helpers for remote signals."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from homeassistant.components import infrared
from homeassistant.components.infrared import InfraredReceivedSignal
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import (
    ATTR_DIRECTION,
    ATTR_NAME,
    COMMAND_TYPE_RAW,
    CONF_SEND_REPEAT_COUNT,
    CONF_SEND_REPEAT_DELAY_MS,
    DEFAULT_CARRIER_FREQUENCY,
    DEFAULT_LEARN_TIMEOUT,
    DIRECTION_BOTH,
    DIRECTION_INPUT,
    DIRECTION_OUTPUT,
    DOMAIN,
    get_device_send_options,
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

    validate_receiver(hass, receiver)
    if direction in (DIRECTION_OUTPUT, DIRECTION_BOTH):
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
    sender: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    """Send an output signal using the device repeat settings."""
    transmitter = manager.get_transmitter_entity_id(subentry)
    if sender is None:
        validate_emitter(hass, transmitter)

    send_options = get_device_send_options(subentry)
    repeat_count = send_options[CONF_SEND_REPEAT_COUNT]
    repeat_delay_ms = send_options[CONF_SEND_REPEAT_DELAY_MS]
    command = manager.build_command(command_data)

    async def _default_sender(cmd: Any) -> None:
        await infrared.async_send_command(
            hass,
            transmitter,
            cmd,
            context=context,
        )

    send = sender or _default_sender

    for attempt in range(repeat_count):
        await send(command)
        if attempt < repeat_count - 1 and repeat_delay_ms > 0:
            await asyncio.sleep(repeat_delay_ms / 1000)
