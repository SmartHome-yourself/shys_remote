"""Binary sensor platform for learned remote input signals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import CONF_PULSE_MS, DOMAIN, get_integration_options, input_signal_unique_id
from .icons import icon_for_signal

if TYPE_CHECKING:
    from .manager import RemoteManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SHYS Remote input signal entities."""
    manager: RemoteManager = hass.data[DOMAIN][entry.entry_id]
    manager.set_add_sensor_entities_callback(async_add_entities)

    for subentry in manager.get_device_subentries():
        if not manager.can_use_input_signals(subentry):
            continue
        entities = [
            manager.create_input_sensor_entity(subentry, signal_name)
            for signal_name, command_data in manager.get_subentry_commands(
                subentry.subentry_id
            ).items()
            if manager.is_input_signal(command_data)
        ]
        if entities:
            async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class ShysRemoteInputSensor(BinarySensorEntity):
    """Binary sensor that pulses when a learned remote signal is received."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = None

    def __init__(
        self,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        manager: RemoteManager,
        signal_name: str,
    ) -> None:
        """Initialize the input sensor."""
        self._entry = entry
        self._subentry = subentry
        self._manager = manager
        self._signal_name = signal_name
        self._off_timer = None
        self._attr_unique_id = input_signal_unique_id(subentry.unique_id, signal_name)
        self._attr_name = signal_name
        self._attr_icon = icon_for_signal(signal_name)
        self._attr_is_on = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="SmartHome yourself",
            model="Remote",
        )

    @callback
    def async_trigger(self) -> None:
        """Pulse the binary sensor on for the configured duration."""
        self._attr_is_on = True
        self.async_write_ha_state()

        if self._off_timer is not None:
            self._off_timer()

        pulse_ms = get_integration_options(self._entry)[CONF_PULSE_MS]

        @callback
        def turn_off(_now) -> None:
            self._off_timer = None
            self._attr_is_on = False
            self.async_write_ha_state()

        self._off_timer = async_call_later(
            self.hass, pulse_ms / 1000, turn_off
        )
