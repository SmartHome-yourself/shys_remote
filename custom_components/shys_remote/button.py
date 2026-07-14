"""Button platform for learned remote commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, output_signal_unique_id
from .signal_transport import get_transport_entity_id
from .icons import icon_for_signal

if TYPE_CHECKING:
    from .manager import RemoteManager
from .remote import async_send_output_command


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SHYS Remote button entities."""
    manager: RemoteManager = hass.data[DOMAIN][entry.entry_id]
    manager.set_add_button_entities_callback(async_add_entities)

    for subentry in manager.get_device_subentries():
        entities = [
            manager.create_button_entity(subentry, signal_name)
            for signal_name, command_data in manager.get_subentry_commands(
                subentry.subentry_id
            ).items()
            if manager.is_output_signal(command_data)
        ]
        if entities:
            async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class ShysRemoteButton(ButtonEntity):
    """Button that sends a learned remote command."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        manager: RemoteManager,
        command_name: str,
        command_data: dict,
    ) -> None:
        """Initialize the button."""
        self._entry = entry
        self._subentry = subentry
        self._manager = manager
        self._command_name = command_name
        self._command_data = command_data
        self._transport_entity_id = get_transport_entity_id(subentry, command_data)
        self._attr_unique_id = output_signal_unique_id(subentry.unique_id, command_name)
        self._attr_name = command_name
        self._attr_icon = icon_for_signal(command_name)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="SmartHome yourself",
            model="Remote",
        )

    async def async_press(self) -> None:
        """Send the learned remote command."""
        await async_send_output_command(
            self.hass,
            self._manager,
            self._subentry,
            self._command_data,
            context=self._context,
        )
