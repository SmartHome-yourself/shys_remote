"""Storage and entity management for learned remote signals."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components import infrared
from homeassistant.components.infrared import InfraredReceivedSignal
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .command import RawInfraredCommand
from .const import (
    ATTR_DIRECTION,
    ATTR_RECEIVER_ENTITY_ID,
    ATTR_TRANSMITTER_ENTITY_ID,
    COMMAND_TYPE_RAW,
    CONF_DEBOUNCE_MS,
    CONF_MATCH_TOLERANCE,
    DEFAULT_CARRIER_FREQUENCY,
    DIRECTION_BOTH,
    DIRECTION_INPUT,
    DIRECTION_OUTPUT,
    DOMAIN,
    LEGACY_DOMAIN,
    STORAGE_VERSION,
    SUBENTRY_DEVICE,
    get_integration_options,
    get_signal_direction,
    input_signal_unique_id,
    output_signal_unique_id,
)
from .signal_matching import signal_matches
from .signal_transport import (
    RawSignalCommand,
    SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY,
    SIGNAL_MEDIUM_RF,
    get_signal_backend,
    get_signal_medium,
    get_transport_entity_id,
)

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .binary_sensor import ShysRemoteInputSensor
    from .button import ShysRemoteButton

_LOGGER = logging.getLogger(__name__)


class RemoteManager:
    """Manage learned remote signals and their entities per subentry."""

    def __init__(self, hass, entry: ConfigEntry) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self._store = Store(hass, STORAGE_VERSION, DOMAIN)
        self.commands: dict[str, dict[str, dict[str, Any]]] = {}
        self._legacy_commands: dict[str, dict[str, Any]] | None = None
        self._button_entities: dict[tuple[str, str], ShysRemoteButton] = {}
        self._sensor_entities: dict[tuple[str, str], ShysRemoteInputSensor] = {}
        self._add_button_entities: AddEntitiesCallback | None = None
        self._add_sensor_entities: AddEntitiesCallback | None = None
        self._pending_buttons: list[tuple[ShysRemoteButton, str]] = []
        self._pending_sensors: list[tuple[ShysRemoteInputSensor, str]] = []
        self._receiver_unsubscribes: dict[str, Callable[[], None]] = {}
        self._last_trigger: dict[tuple[str, str], float] = {}

    async def async_load(self) -> None:
        """Load learned signals from storage."""
        stored = await self._store.async_load()
        if not stored:
            legacy_store = Store(self.hass, STORAGE_VERSION, LEGACY_DOMAIN)
            stored = await legacy_store.async_load()
            if stored:
                await self._store.async_save(stored)
                _LOGGER.info(
                    "Migrated learned signals from legacy storage '%s'",
                    LEGACY_DOMAIN,
                )

        if not stored:
            return

        commands = stored.get("commands", {})
        if self._is_legacy_commands(commands):
            self._legacy_commands = commands
            self.commands = {}
            return

        self.commands = commands

    def _is_legacy_commands(self, commands: dict[str, Any]) -> bool:
        """Detect the pre-subentry flat command layout."""
        if not commands:
            return False

        for value in commands.values():
            if not isinstance(value, dict):
                continue
            if COMMAND_TYPE_RAW in value and "command" in value:
                return True
        return False

    async def async_migrate_legacy_commands(
        self,
        subentry_id: str,
        legacy_commands: dict[str, dict[str, Any]],
    ) -> None:
        """Move flat commands into a subentry bucket."""
        migrated: dict[str, dict[str, Any]] = {}
        for name, command_data in legacy_commands.items():
            migrated[name] = {
                "type": command_data.get("type", COMMAND_TYPE_RAW),
                ATTR_DIRECTION: DIRECTION_OUTPUT,
                "carrier_frequency": command_data.get(
                    "carrier_frequency", DEFAULT_CARRIER_FREQUENCY
                ),
                "command": command_data["command"],
            }
        self.commands[subentry_id] = migrated
        await self.async_save()

    async def async_save(self) -> None:
        """Persist learned signals."""
        await self._store.async_save({"commands": self.commands})

    @staticmethod
    def is_input_signal(command_data: dict[str, Any]) -> bool:
        """Return whether a stored signal is configured for input."""
        direction = get_signal_direction(command_data)
        return direction in (DIRECTION_INPUT, DIRECTION_BOTH)

    @staticmethod
    def is_output_signal(command_data: dict[str, Any]) -> bool:
        """Return whether a stored signal is configured for output."""
        direction = get_signal_direction(command_data)
        return direction in (DIRECTION_OUTPUT, DIRECTION_BOTH)

    def set_add_button_entities_callback(
        self, add_entities: AddEntitiesCallback
    ) -> None:
        """Register the button platform callback."""
        self._add_button_entities = add_entities
        if self._pending_buttons:
            for entity, subentry_id in self._pending_buttons:
                add_entities([entity], config_subentry_id=subentry_id)
            self._pending_buttons.clear()

    def set_add_sensor_entities_callback(
        self, add_entities: AddEntitiesCallback
    ) -> None:
        """Register the binary sensor platform callback."""
        self._add_sensor_entities = add_entities
        if self._pending_sensors:
            for entity, subentry_id in self._pending_sensors:
                add_entities([entity], config_subentry_id=subentry_id)
            self._pending_sensors.clear()

    def get_device_subentries(self) -> list[ConfigSubentry]:
        """Return configured device subentries."""
        return [
            subentry
            for subentry in self.entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_DEVICE
        ]

    def get_subentry_by_unique_id(self, unique_id: str) -> ConfigSubentry | None:
        """Find a device subentry by slug."""
        for subentry in self.get_device_subentries():
            if subentry.unique_id == unique_id:
                return subentry
        return None

    def get_subentry_commands(self, subentry_id: str) -> dict[str, dict[str, Any]]:
        """Return signals for one device subentry."""
        return self.commands.setdefault(subentry_id, {})

    def build_command(
        self,
        command_data: dict[str, Any],
        subentry: ConfigSubentry | None = None,
    ) -> RawInfraredCommand | RawSignalCommand:
        """Build a backend-aware command from stored data.

        ``carrier_frequency`` holds the IR carrier frequency in Hz for
        infrared signals and the RF transmit frequency in Hz for
        radio-frequency signals; the same storage key covers both since one
        learned signal is always exactly one medium.
        """
        carrier_frequency = command_data.get("carrier_frequency", DEFAULT_CARRIER_FREQUENCY)
        timings = list(command_data["command"])
        medium = get_signal_medium(command_data)
        backend = get_signal_backend(command_data)

        if medium == SIGNAL_MEDIUM_RF or backend == SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY:
            return RawSignalCommand(
                timings=timings,
                frequency=carrier_frequency,
                medium=medium,
                backend=backend,
                transport_entity_id=get_transport_entity_id(subentry, command_data),
            )

        return RawInfraredCommand(timings, modulation=carrier_frequency)

    def get_transmitter_entity_id(self, subentry: ConfigSubentry) -> str:
        """Return the transmitter configured for a device."""
        return subentry.data[ATTR_TRANSMITTER_ENTITY_ID]

    def get_receiver_entity_id(self, subentry: ConfigSubentry) -> str | None:
        """Return the optional receiver configured for a device."""
        receiver = subentry.data.get(ATTR_RECEIVER_ENTITY_ID)
        return receiver if isinstance(receiver, str) and receiver else None

    def can_use_input_signals(self, subentry: ConfigSubentry) -> bool:
        """Return whether input signals are possible for this device."""
        return self.get_receiver_entity_id(subentry) is not None

    def create_button_entity(
        self,
        subentry: ConfigSubentry,
        signal_name: str,
    ) -> ShysRemoteButton:
        """Create a button entity for an output signal."""
        from .button import ShysRemoteButton

        command_data = self.get_subentry_commands(subentry.subentry_id)[signal_name]
        entity = ShysRemoteButton(
            self.entry,
            subentry,
            self,
            signal_name,
            command_data,
        )
        self._button_entities[(subentry.subentry_id, signal_name)] = entity
        return entity

    def create_input_sensor_entity(
        self,
        subentry: ConfigSubentry,
        signal_name: str,
    ) -> ShysRemoteInputSensor:
        """Create a binary sensor entity for an input signal."""
        from .binary_sensor import ShysRemoteInputSensor

        entity = ShysRemoteInputSensor(
            self.entry,
            subentry,
            self,
            signal_name,
        )
        self._sensor_entities[(subentry.subentry_id, signal_name)] = entity
        return entity

    async def async_add_command(
        self,
        subentry: ConfigSubentry,
        signal_name: str,
        command_data: dict[str, Any],
    ) -> ShysRemoteButton | ShysRemoteInputSensor | None:
        """Store a signal and expose the matching entity."""
        subentry_commands = self.get_subentry_commands(subentry.subentry_id)
        subentry_commands[signal_name] = command_data
        await self.async_save()

        primary_entity: ShysRemoteButton | ShysRemoteInputSensor | None = None

        if self.is_output_signal(command_data):
            button_entity = self.create_button_entity(subentry, signal_name)
            if self._add_button_entities is None:
                self._pending_buttons.append((button_entity, subentry.subentry_id))
            else:
                self._add_button_entities(
                    [button_entity], config_subentry_id=subentry.subentry_id
                )
            primary_entity = button_entity

        if self.is_input_signal(command_data) and self.can_use_input_signals(subentry):
            sensor_entity = self.create_input_sensor_entity(subentry, signal_name)
            if self._add_sensor_entities is None:
                self._pending_sensors.append((sensor_entity, subentry.subentry_id))
            else:
                self._add_sensor_entities(
                    [sensor_entity], config_subentry_id=subentry.subentry_id
                )
            await self.async_refresh_receivers()
            if primary_entity is None:
                primary_entity = sensor_entity

        return primary_entity

    async def async_import_commands_bulk(
        self,
        subentry: ConfigSubentry,
        commands: dict[str, dict[str, Any]],
    ) -> int:
        """Store multiple signals and expose matching entities."""
        if not commands:
            return 0

        subentry_commands = self.get_subentry_commands(subentry.subentry_id)
        subentry_commands.update(commands)
        await self.async_save()

        button_entities = [
            self.create_button_entity(subentry, signal_name)
            for signal_name, command_data in commands.items()
            if self.is_output_signal(command_data)
        ]
        sensor_entities = [
            self.create_input_sensor_entity(subentry, signal_name)
            for signal_name, command_data in commands.items()
            if self.is_input_signal(command_data) and self.can_use_input_signals(subentry)
        ]

        if self._add_button_entities is None:
            for entity in button_entities:
                self._pending_buttons.append((entity, subentry.subentry_id))
        elif button_entities:
            self._add_button_entities(
                button_entities, config_subentry_id=subentry.subentry_id
            )

        if self._add_sensor_entities is None:
            for entity in sensor_entities:
                self._pending_sensors.append((entity, subentry.subentry_id))
        elif sensor_entities:
            self._add_sensor_entities(
                sensor_entities, config_subentry_id=subentry.subentry_id
            )

        if sensor_entities:
            await self.async_refresh_receivers()

        return len(commands)

    async def async_remove_command(
        self,
        subentry_id: str,
        signal_name: str,
    ) -> None:
        """Delete a signal and remove its entity."""
        subentry_commands = self.commands.get(subentry_id)
        if not subentry_commands or signal_name not in subentry_commands:
            return

        was_input = self.is_input_signal(subentry_commands[signal_name])
        subentry = self.entry.subentries.get(subentry_id)

        if subentry is not None:
            await self._async_remove_signal_entities(subentry, signal_name)

        self._last_trigger.pop((subentry_id, signal_name), None)
        del subentry_commands[signal_name]
        if not subentry_commands:
            self.commands.pop(subentry_id, None)
        await self.async_save()

        if was_input:
            await self.async_refresh_receivers()

    async def async_remove_device(self, subentry_id: str) -> None:
        """Delete all signals for a removed device subentry."""
        had_input = any(
            self.is_input_signal(command_data)
            for command_data in self.commands.get(subentry_id, {}).values()
        )
        subentry = self.entry.subentries.get(subentry_id)
        signal_names = list(self.commands.get(subentry_id, {}))

        for signal_name in signal_names:
            if subentry is not None:
                await self._async_remove_signal_entities(subentry, signal_name)
            else:
                self._button_entities.pop((subentry_id, signal_name), None)
                self._sensor_entities.pop((subentry_id, signal_name), None)
                self._last_trigger.pop((subentry_id, signal_name), None)

        self.commands.pop(subentry_id, None)
        await self.async_save()
        self._async_remove_subentry_entities(subentry_id)

        if had_input:
            await self.async_refresh_receivers()

    def _collect_known_unique_ids(self) -> set[str]:
        """Return unique ids for all stored signals."""
        unique_ids: set[str] = set()
        for subentry in self.get_device_subentries():
            for signal_name, command_data in self.get_subentry_commands(
                subentry.subentry_id
            ).items():
                if self.is_input_signal(command_data):
                    unique_ids.add(
                        input_signal_unique_id(subentry.unique_id, signal_name)
                    )
                if self.is_output_signal(command_data):
                    unique_ids.add(
                        output_signal_unique_id(subentry.unique_id, signal_name)
                    )
        return unique_ids

    @callback
    def async_cleanup_orphan_entities(self) -> None:
        """Remove entity registry entries without a stored signal."""
        registry = er.async_get(self.hass)
        known_unique_ids = self._collect_known_unique_ids()

        for entity_entry in er.async_entries_for_config_entry(
            registry, self.entry.entry_id
        ):
            if entity_entry.platform not in (DOMAIN, LEGACY_DOMAIN):
                continue
            if entity_entry.domain not in ("button", "binary_sensor"):
                continue
            if entity_entry.unique_id in known_unique_ids:
                continue
            registry.async_remove(entity_entry.entity_id)
            _LOGGER.debug(
                "Removed orphan entity '%s' from registry",
                entity_entry.entity_id,
            )

    @callback
    def _async_remove_subentry_entities(self, subentry_id: str) -> None:
        """Remove all integration entities linked to a subentry."""
        registry = er.async_get(self.hass)
        for entity_entry in er.async_entries_for_config_entry(
            registry, self.entry.entry_id
        ):
            if entity_entry.platform not in (DOMAIN, LEGACY_DOMAIN):
                continue
            if entity_entry.config_subentry_id != subentry_id:
                continue
            registry.async_remove(entity_entry.entity_id)

    async def _async_remove_signal_entities(
        self,
        subentry: ConfigSubentry,
        signal_name: str,
    ) -> None:
        """Remove button and binary sensor entities for a signal."""
        button_entity = self._button_entities.pop(
            (subentry.subentry_id, signal_name), None
        )
        sensor_entity = self._sensor_entities.pop(
            (subentry.subentry_id, signal_name), None
        )

        await self._async_despawn_entity(
            "button",
            output_signal_unique_id(subentry.unique_id, signal_name),
            button_entity,
        )
        await self._async_despawn_entity(
            "binary_sensor",
            input_signal_unique_id(subentry.unique_id, signal_name),
            sensor_entity,
        )

    async def _async_despawn_entity(
        self,
        platform: str,
        unique_id: str,
        entity: ShysRemoteButton | ShysRemoteInputSensor | None,
    ) -> None:
        """Remove a live entity and clean up any leftover registry entry."""
        if entity is not None and entity.entity_id is not None:
            await entity.async_remove(force_remove=True)

        registry = er.async_get(self.hass)
        for integration_domain in (DOMAIN, LEGACY_DOMAIN):
            entity_id = registry.async_get_entity_id(
                platform, integration_domain, unique_id
            )
            if entity_id is not None:
                registry.async_remove(entity_id)

    def get_button_entity(
        self,
        subentry_id: str,
        signal_name: str,
    ) -> ShysRemoteButton | None:
        """Return the button entity for an output signal."""
        return self._button_entities.get((subentry_id, signal_name))

    def get_input_sensor_entity(
        self,
        subentry_id: str,
        signal_name: str,
    ) -> ShysRemoteInputSensor | None:
        """Return the binary sensor entity for an input signal."""
        return self._sensor_entities.get((subentry_id, signal_name))

    def _iter_input_signals_for_receiver(
        self, receiver_entity_id: str
    ) -> list[tuple[str, str, list[int]]]:
        """Return input signals listening on the given receiver."""
        matches: list[tuple[str, str, list[int]]] = []
        for subentry in self.get_device_subentries():
            subentry_receiver = self.get_receiver_entity_id(subentry)
            if subentry_receiver != receiver_entity_id:
                continue
            for signal_name, command_data in self.get_subentry_commands(
                subentry.subentry_id
            ).items():
                if not self.is_input_signal(command_data):
                    continue
                matches.append(
                    (subentry.subentry_id, signal_name, command_data["command"])
                )
        return matches

    def _get_required_receivers(self) -> set[str]:
        """Return receiver entities that need an active subscription."""
        receivers: set[str] = set()
        for subentry in self.get_device_subentries():
            receiver = self.get_receiver_entity_id(subentry)
            if receiver is None:
                continue
            has_input = any(
                self.is_input_signal(command_data)
                for command_data in self.get_subentry_commands(
                    subentry.subentry_id
                ).values()
            )
            if has_input:
                receivers.add(receiver)
        return receivers

    @callback
    def _on_signal_received(
        self, receiver_entity_id: str, signal: InfraredReceivedSignal
    ) -> None:
        """Handle a received signal and match input entities."""
        if not signal.timings:
            return

        options = get_integration_options(self.entry)
        tolerance = float(options[CONF_MATCH_TOLERANCE])
        debounce_ms = int(options[CONF_DEBOUNCE_MS])
        received = list(signal.timings)
        now = time.monotonic()

        for subentry_id, signal_name, learned in self._iter_input_signals_for_receiver(
            receiver_entity_id
        ):
            if not signal_matches(learned, received, tolerance):
                continue

            trigger_key = (subentry_id, signal_name)
            last_trigger = self._last_trigger.get(trigger_key, 0.0)
            if (now - last_trigger) * 1000 < debounce_ms:
                continue

            entity = self.get_input_sensor_entity(subentry_id, signal_name)
            if entity is None:
                continue

            self._last_trigger[trigger_key] = now
            entity.async_trigger()
            _LOGGER.debug(
                "Matched input signal '%s' on receiver '%s'",
                signal_name,
                receiver_entity_id,
            )

    async def async_refresh_receivers(self) -> None:
        """Subscribe to all receivers required by input signals."""
        required = self._get_required_receivers()

        for receiver_entity_id in list(self._receiver_unsubscribes):
            if receiver_entity_id not in required:
                self._receiver_unsubscribes.pop(receiver_entity_id)()
                _LOGGER.debug(
                    "Stopped listening on infrared receiver '%s'",
                    receiver_entity_id,
                )

        for receiver_entity_id in required:
            if receiver_entity_id in self._receiver_unsubscribes:
                continue

            @callback
            def create_handler(
                entity_id: str,
            ) -> Callable[[InfraredReceivedSignal], None]:
                @callback
                def on_signal(signal: InfraredReceivedSignal) -> None:
                    self._on_signal_received(entity_id, signal)

                return on_signal

            try:
                unsubscribe = infrared.async_subscribe_receiver(
                    self.hass,
                    receiver_entity_id,
                    create_handler(receiver_entity_id),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not subscribe to infrared receiver '%s': %s",
                    receiver_entity_id,
                    err,
                )
                continue

            self._receiver_unsubscribes[receiver_entity_id] = unsubscribe
            _LOGGER.debug(
                "Listening on infrared receiver '%s' for input signals",
                receiver_entity_id,
            )

    def async_shutdown_receivers(self) -> None:
        """Unsubscribe from all infrared receivers."""
        for unsubscribe in self._receiver_unsubscribes.values():
            unsubscribe()
        self._receiver_unsubscribes.clear()
