"""SHYS Remote - learn and replay remote commands via infrared entities."""

from __future__ import annotations

import logging
from types import MappingProxyType

import voluptuous as vol

from homeassistant.components import infrared
import asyncio

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DEVICE,
    ATTR_DIRECTION,
    ATTR_NAME,
    ATTR_RECEIVER_ENTITY_ID,
    ATTR_TIMEOUT,
    ATTR_TRANSMITTER_ENTITY_ID,
    CONF_IRDB_DIRECTION,
    CONF_IRDB_PATH,
    CONFIG_VERSION,
    DEFAULT_LEARN_TIMEOUT,
    DIRECTION_BOTH,
    DIRECTION_INPUT,
    DIRECTION_OUTPUT,
    DOMAIN,
    LEGACY_SUBENTRY_UNIQUE_ID,
    PLATFORMS,
    SERVICE_DELETE,
    SERVICE_LEARN,
    SERVICE_SEND,
    SUBENTRY_DEVICE,
)
from .irdb import IrdbClient
from .manager import RemoteManager
from .remote import (
    async_delete_command,
    async_learn_command,
    validate_emitter,
    validate_receiver,
)

_LOGGER = logging.getLogger(__name__)

LEARN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE): cv.slug,
        vol.Required(ATTR_NAME): cv.slug,
        vol.Optional(ATTR_DIRECTION, default=DIRECTION_OUTPUT): vol.In(
            (DIRECTION_OUTPUT, DIRECTION_INPUT, DIRECTION_BOTH)
        ),
        vol.Optional(ATTR_RECEIVER_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_TRANSMITTER_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_TIMEOUT, default=DEFAULT_LEARN_TIMEOUT): vol.All(
            cv.positive_int,
            vol.Range(min=1, max=120),
        ),
    }
)

COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE): cv.slug,
        vol.Required(ATTR_NAME): cv.slug,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SHYS Remote from a config entry."""
    manager = RemoteManager(hass, entry)
    await manager.async_load()
    await _async_migrate_legacy_storage(hass, manager, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = manager

    if not hass.data[DOMAIN].get("services_registered"):
        _async_register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_process_pending_irdb_imports(hass, entry, manager)
    manager.async_cleanup_orphan_entities()
    await manager.async_refresh_receivers()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload SHYS Remote."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        manager = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if isinstance(manager, RemoteManager):
            manager.async_shutdown_receivers()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not any(key != "services_registered" for key in hass.data[DOMAIN]):
            _async_unregister_services(hass)
            hass.data.pop(DOMAIN, None)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries."""
    if entry.version >= CONFIG_VERSION:
        return True

    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=CONFIG_VERSION)

    return True


async def _async_migrate_legacy_storage(
    hass: HomeAssistant,
    manager: RemoteManager,
    entry: ConfigEntry,
) -> None:
    """Migrate flat commands from before subentries."""
    if not manager._legacy_commands:
        return

    if manager.get_subentry_by_unique_id(LEGACY_SUBENTRY_UNIQUE_ID):
        return

    first_command = next(iter(manager._legacy_commands.values()))
    receiver = first_command.get(ATTR_RECEIVER_ENTITY_ID, "")
    transmitter = first_command.get(ATTR_TRANSMITTER_ENTITY_ID, "")

    subentry = ConfigSubentry(
        subentry_type=SUBENTRY_DEVICE,
        title="Importiert",
        unique_id=LEGACY_SUBENTRY_UNIQUE_ID,
        data=MappingProxyType(
            {
                ATTR_RECEIVER_ENTITY_ID: receiver,
                ATTR_TRANSMITTER_ENTITY_ID: transmitter,
            }
        ),
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    await manager.async_migrate_legacy_commands(
        subentry.subentry_id,
        manager._legacy_commands,
    )
    manager._legacy_commands = None

    _LOGGER.info(
        "Migrated legacy commands to subentry '%s'",
        LEGACY_SUBENTRY_UNIQUE_ID,
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after subentry changes once the config flow has finished."""
    entry_id = entry.entry_id

    async def _deferred_reload() -> None:
        await asyncio.sleep(0)
        current_entry = hass.config_entries.async_get_entry(entry_id)
        manager: RemoteManager | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if current_entry is None or manager is None:
            hass.config_entries.async_schedule_reload(entry_id)
            return

        for subentry_id in list(manager.commands):
            if subentry_id not in current_entry.subentries:
                await manager.async_remove_device(subentry_id)

        hass.config_entries.async_schedule_reload(entry_id)

    hass.async_create_task(_deferred_reload())


async def _async_process_pending_irdb_imports(
    hass: HomeAssistant,
    entry: ConfigEntry,
    manager: RemoteManager,
) -> None:
    """Import signals for devices created from the IR database."""
    client = IrdbClient(hass, entry)

    for subentry in manager.get_device_subentries():
        path = subentry.data.get(CONF_IRDB_PATH)
        if not path:
            continue

        direction = subentry.data.get(CONF_IRDB_DIRECTION, DIRECTION_OUTPUT)
        if (
            direction in (DIRECTION_INPUT, DIRECTION_BOTH)
            and not subentry.data.get(ATTR_RECEIVER_ENTITY_ID)
        ):
            direction = DIRECTION_OUTPUT

        try:
            preview = await client.async_preview_remote(path)
            commands = {
                signal_name: {**command_data, ATTR_DIRECTION: direction}
                for signal_name, command_data in preview["commands"].items()
            }
            imported = await manager.async_import_commands_bulk(subentry, commands)
        except HomeAssistantError as err:
            _LOGGER.error(
                "Failed to import IRDB remote '%s' for device '%s': %s",
                path,
                subentry.title,
                err,
            )
            continue

        new_data = {
            key: value
            for key, value in subentry.data.items()
            if key not in (CONF_IRDB_PATH, CONF_IRDB_DIRECTION)
        }
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data=new_data,
        )
        _LOGGER.info(
            "Imported %s signals from IRDB for device '%s' (%s skipped)",
            imported,
            subentry.title,
            preview["skipped_count"],
        )


def _get_manager(hass: HomeAssistant) -> RemoteManager:
    """Return the active manager."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, value in domain_data.items():
        if key != "services_registered" and isinstance(value, RemoteManager):
            return value
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_loaded",
    )


def _get_device_subentry(manager: RemoteManager, device_slug: str) -> ConfigSubentry:
    """Resolve a device slug to a subentry."""
    subentry = manager.get_subentry_by_unique_id(device_slug)
    if subentry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device": device_slug},
        )
    return subentry


def _async_register_services(hass: HomeAssistant) -> None:
    """Register learn, send and delete services."""

    async def async_handle_learn(call: ServiceCall) -> None:
        manager = _get_manager(hass)
        device_slug = call.data[ATTR_DEVICE]
        command_name = call.data[ATTR_NAME]
        timeout = call.data[ATTR_TIMEOUT]

        subentry = _get_device_subentry(manager, device_slug)
        await async_learn_command(
            hass,
            manager,
            subentry,
            command_name,
            timeout=timeout,
            receiver_entity_id=call.data.get(ATTR_RECEIVER_ENTITY_ID),
            transmitter_entity_id=call.data.get(ATTR_TRANSMITTER_ENTITY_ID),
            direction=call.data.get(ATTR_DIRECTION, DIRECTION_OUTPUT),
        )
        _LOGGER.info(
            "Learned signal '%s' (%s) on device '%s'",
            command_name,
            call.data.get(ATTR_DIRECTION, DIRECTION_OUTPUT),
            subentry.title,
        )

    async def async_handle_send(call: ServiceCall) -> None:
        manager = _get_manager(hass)
        device_slug = call.data[ATTR_DEVICE]
        command_name = call.data[ATTR_NAME]

        subentry = _get_device_subentry(manager, device_slug)
        command_data = manager.get_subentry_commands(subentry.subentry_id).get(
            command_name
        )
        if command_data is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="command_not_found",
                translation_placeholders={
                    "name": command_name,
                    "device": subentry.title,
                },
            )

        if not manager.is_output_signal(command_data):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="signal_not_output",
                translation_placeholders={
                    "name": command_name,
                    "device": subentry.title,
                },
            )

        transmitter_entity_id = manager.get_transmitter_entity_id(subentry)
        validate_emitter(hass, transmitter_entity_id)

        await infrared.async_send_command(
            hass,
            transmitter_entity_id,
            manager.build_command(command_data),
            context=call.context,
        )

    async def async_handle_delete(call: ServiceCall) -> None:
        manager = _get_manager(hass)
        device_slug = call.data[ATTR_DEVICE]
        command_name = call.data[ATTR_NAME]

        subentry = _get_device_subentry(manager, device_slug)
        await async_delete_command(manager, subentry, command_name)
        _LOGGER.info(
            "Deleted signal '%s' from device '%s'",
            command_name,
            subentry.title,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_LEARN,
        async_handle_learn,
        schema=LEARN_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND,
        async_handle_send,
        schema=COMMAND_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE,
        async_handle_delete,
        schema=COMMAND_SCHEMA,
    )


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Remove services when the integration is unloaded."""
    for service_name in (SERVICE_LEARN, SERVICE_SEND, SERVICE_DELETE):
        hass.services.async_remove(DOMAIN, service_name)
