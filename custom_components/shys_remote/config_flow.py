"""Config flow for SHYS Remote."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import infrared
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    ATTR_DIRECTION,
    ATTR_MEDIUM,
    ATTR_NAME,
    ATTR_RECEIVER_ENTITY_ID,
    ATTR_TIMEOUT,
    ATTR_TRANSMITTER_ENTITY_ID,
    CONF_DEBOUNCE_MS,
    CONF_DEVICE_NAME,
    CONF_IRDB_DIRECTION,
    CONF_IRDB_PATH,
    CONF_IRDB_QUERY,
    CONF_IRDB_REMOTE,
    CONF_IRDB_CATEGORY,
    CONF_MATCH_TOLERANCE,
    CONF_PULSE_MS,
    CONF_RF_FREQUENCY,
    CONF_SEND_REPEAT_COUNT,
    CONF_SEND_REPEAT_DELAY_MS,
    CONF_SIGNAL_SOURCE,
    DEFAULT_DEBOUNCE_MS,
    DEFAULT_LEARN_TIMEOUT,
    DEFAULT_MATCH_TOLERANCE,
    DEFAULT_PULSE_MS,
    DEFAULT_RF_FREQUENCY,
    DEFAULT_SEND_REPEAT_COUNT,
    DEFAULT_SEND_REPEAT_DELAY_MS,
    DIRECTION_BOTH,
    DIRECTION_INPUT,
    DIRECTION_OUTPUT,
    DOMAIN,
    IRDB_CATEGORY_ALL,
    IRDB_FILTER_CATEGORIES,
    IRDB_FLOW_PAGE_SIZE,
    IRDB_FLOW_RESULTS_KEY,
    IRDB_NEXT_PAGE,
    IRDB_PREV_PAGE,
    IRDB_SEARCH_AGAIN,
    SOURCE_IRDB,
    SOURCE_MANUAL,
    SUBENTRY_DEVICE,
    get_device_send_options,
    irdb_attribution_placeholders,
)
from .irdb import IrdbClient
from .remote import async_delete_command, async_learn_command
from .manager import RemoteManager
from .signal_transport import SIGNAL_MEDIUM_IR, SIGNAL_MEDIUM_RF

_LOGGER = logging.getLogger(__name__)

MENU_EDIT_DEVICE = "edit_device"
MENU_LEARN_COMMAND = "learn_command"
MENU_DELETE_COMMAND = "delete_command"

CTX_IRDB_PENDING = "irdb_pending_device"
CTX_IRDB_QUERY = "irdb_last_query"
CTX_IRDB_CATEGORY = "irdb_last_category"
CTX_IRDB_PAGE = "irdb_results_page"
CTX_IRDB_PREVIEW = "irdb_import_preview"


def _device_send_schema_fields() -> dict:
    """Return schema fields for per-device send repeat settings."""
    return {
        vol.Optional(CONF_SEND_REPEAT_COUNT, default=DEFAULT_SEND_REPEAT_COUNT): (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=50,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        ),
        vol.Optional(
            CONF_SEND_REPEAT_DELAY_MS, default=DEFAULT_SEND_REPEAT_DELAY_MS
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=5000,
                unit_of_measurement="ms",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }


def _send_options_from_input(user_input: dict[str, Any]) -> dict[str, int]:
    """Return send repeat settings from config flow input."""
    return {
        CONF_SEND_REPEAT_COUNT: int(
            user_input.get(CONF_SEND_REPEAT_COUNT, DEFAULT_SEND_REPEAT_COUNT)
        ),
        CONF_SEND_REPEAT_DELAY_MS: int(
            user_input.get(CONF_SEND_REPEAT_DELAY_MS, DEFAULT_SEND_REPEAT_DELAY_MS)
        ),
    }


def _medium_schema_field() -> dict:
    """Return the schema field for signal medium selection."""
    return {
        vol.Optional(ATTR_MEDIUM, default=SIGNAL_MEDIUM_IR): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[SIGNAL_MEDIUM_IR, SIGNAL_MEDIUM_RF],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="medium",
            )
        ),
    }


def _rf_frequency_schema_field() -> dict:
    """Return the schema field for the RF transmit frequency (RF devices only)."""
    return {
        vol.Optional(CONF_RF_FREQUENCY, default=DEFAULT_RF_FREQUENCY): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                unit_of_measurement="Hz",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }


def _transport_entity_schema_fields(hass, *, rf_frequency: int = DEFAULT_RF_FREQUENCY) -> dict:
    """Return schema fields for receiver/transmitter entities known to the transports."""
    return {
        vol.Optional(ATTR_RECEIVER_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(include_entities=_receiver_entity_ids(hass))
        ),
        vol.Required(ATTR_TRANSMITTER_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                include_entities=_transmitter_entity_ids(hass, rf_frequency=rf_frequency)
            )
        ),
    }


def _device_edit_schema(hass, *, rf_frequency: int = DEFAULT_RF_FREQUENCY) -> vol.Schema:
    """Return the schema for editing an existing device."""
    return vol.Schema(
        {
            vol.Required(CONF_DEVICE_NAME): selector.TextSelector(),
            **_medium_schema_field(),
            **_rf_frequency_schema_field(),
            **_transport_entity_schema_fields(hass, rf_frequency=rf_frequency),
            **_device_send_schema_fields(),
        }
    )


def _device_schema(hass, *, include_manual_source: bool = True) -> vol.Schema:
    """Return the schema for remote device subentries."""
    source_options = [SOURCE_IRDB]
    if include_manual_source:
        source_options = [SOURCE_MANUAL, SOURCE_IRDB]

    return vol.Schema(
        {
            vol.Required(CONF_DEVICE_NAME): selector.TextSelector(),
            **_medium_schema_field(),
            **_rf_frequency_schema_field(),
            **_transport_entity_schema_fields(hass),
            vol.Required(CONF_SIGNAL_SOURCE, default=SOURCE_MANUAL): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=source_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="signal_source",
                )
            ),
            **_device_send_schema_fields(),
        }
    )


def _direction_schema(*, include_input: bool = True) -> vol.Schema:
    """Return the schema for signal direction selection."""
    options = [DIRECTION_OUTPUT, DIRECTION_INPUT, DIRECTION_BOTH]
    if not include_input:
        options = [DIRECTION_OUTPUT]
    return vol.Schema(
        {
            vol.Required(ATTR_DIRECTION, default=DIRECTION_OUTPUT): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="direction",
                )
            ),
        }
    )


def _learn_schema(*, include_input: bool = True) -> vol.Schema:
    """Return the schema for learning a signal."""
    return vol.Schema(
        {
            vol.Required(ATTR_NAME): selector.TextSelector(),
            **_direction_schema(include_input=include_input).schema,
            vol.Optional(ATTR_TIMEOUT, default=DEFAULT_LEARN_TIMEOUT): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=120,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _options_schema() -> vol.Schema:
    """Return the schema for integration-wide options."""
    return vol.Schema(
        {
            vol.Optional(CONF_PULSE_MS, default=DEFAULT_PULSE_MS): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=100,
                    max=5000,
                    unit_of_measurement="ms",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_MATCH_TOLERANCE, default=DEFAULT_MATCH_TOLERANCE
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=50,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_DEBOUNCE_MS, default=DEFAULT_DEBOUNCE_MS): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=5000,
                    unit_of_measurement="ms",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _radio_frequency_transmitters(hass, rf_frequency: int) -> list[str]:
    """Return known RF transmitter entities compatible with the given frequency.

    Home Assistant's radio_frequency integration requires both a frequency and a
    modulation to look up compatible transmitters, and raises HomeAssistantError
    if the backend isn't loaded or no matching transmitter exists - both are
    treated as "no known transmitters" here.
    """
    try:
        from homeassistant.components.radio_frequency import (
            ModulationType,
            async_get_transmitters,
        )
    except ImportError:
        return []
    try:
        return list(
            async_get_transmitters(
                hass, frequency=rf_frequency, modulation=ModulationType.OOK
            )
        )
    except HomeAssistantError:
        return []


def _receiver_entity_ids(hass) -> list[str]:
    """Return known infrared receiver entities.

    This is intentionally the same list for both media, not a placeholder that
    still needs an RF-specific counterpart: as of Home Assistant Core 2026.7.2,
    the radio_frequency integration only ever creates transmitter entities.
    Home Assistant's esphome integration filters incoming RF entities by their
    capability bits (homeassistant/components/esphome/radio_frequency.py,
    ``info_filter=lambda info: bool(info.capabilities &
    RadioFrequencyCapability.TRANSMITTER)``), so a receiver-only RF proxy is
    silently dropped and never becomes a Home Assistant entity - there is no
    ``radio_frequency`` receiver entity to look up here, confirmed against
    homeassistant/components/radio_frequency/entity.py defining only
    ``RadioFrequencyTransmitterEntity``.

    Because of that gap, RF signals are learned through a receiver exposed via
    the infrared platform instead (raw pulse/space timings are protocol-agnostic
    there), typically a second `ir_rf_proxy` instance in ESPHome wired to the
    same RF receiver hardware and declared under `infrared:` rather than
    `radio_frequency:` (see the README for a worked example). This is a
    documented compatibility workaround for the current state of Home
    Assistant Core, not a guaranteed or final architecture - once Home
    Assistant ships a native radio_frequency receiver entity, this should be
    revisited.
    """
    return sorted(infrared.async_get_receivers(hass))


def _transmitter_entity_ids(hass, *, rf_frequency: int = DEFAULT_RF_FREQUENCY) -> list[str]:
    """Return known transmitter entities for both infrared and RF backends."""
    entities = set(infrared.async_get_emitters(hass))
    entities.update(_radio_frequency_transmitters(hass, rf_frequency))
    return sorted(entities)


def _transmitter_hint(hass) -> str:
    """Return a diagnostic hint appended to the add-device description.

    A device with zero transmitter entities usually isn't a SHYS Remote
    problem: it means Home Assistant's esphome integration never created an
    infrared/radio_frequency entity for that device in the first place
    (wrong ir_rf_proxy platform key, missing hardware wiring, or an
    ESPHome/HA version too old for the hardware in use - CC1101 boards in
    particular need ESPHome's `radio_frequency:` platform, not `infrared:`,
    plus `on_transmit`/`on_complete` state hooks). Point at the README
    instead of silently showing an empty picker.
    """
    if _transmitter_entity_ids(hass):
        return ""

    if hass.config.language.lower().startswith("de"):
        return (
            "\n\nKein Transmitter gefunden: Das ESPHome-Gerät hat (noch) keine "
            "infrared- oder radio_frequency-Entität in Home Assistant erzeugt. "
            "Prüfe die ir_rf_proxy-Plattform in deiner ESPHome-YAML (siehe "
            "README) und ob das Gerät online ist."
        )
    return (
        "\n\nNo transmitter found: your ESPHome device hasn't created an "
        "infrared or radio_frequency entity in Home Assistant (yet). Check "
        "the ir_rf_proxy platform in your ESPHome YAML (see the README) and "
        "that the device is online."
    )


def _capture_hint(hass, medium: str) -> str:
    """Return extra learn-form guidance for RF captures.

    RF signals are learned as a single raw capture with no confirmation
    step (see _async_step_learn_command) - holding the button down until
    it's released lets the ESPHome receiver's own idle timeout capture the
    whole multi-repeat burst in one raw dump, rather than cutting it off
    after the first repeat. IR receivers demodulate a clean single code, so
    they don't need this.
    """
    if medium != SIGNAL_MEDIUM_RF:
        return ""

    if hass.config.language.lower().startswith("de"):
        return (
            "\n\nFür Funksignale: Taste gedrückt halten, bis das Signal "
            "erfasst wurde, damit die komplette Wiederholungsfolge in "
            "einer Aufnahme landet."
        )
    return (
        "\n\nFor RF signals: hold the button down until the signal is "
        "captured, so the whole repeat burst ends up in one recording."
    )


def _validate_transport_entities(
    hass,
    receiver: str | None,
    transmitter: str,
    medium: str,
    rf_frequency: int = DEFAULT_RF_FREQUENCY,
) -> str | None:
    """Validate receiver and transmitter entities for the selected medium."""
    # Receiver validation is medium-independent by design - see _receiver_entity_ids().
    if receiver and receiver not in infrared.async_get_receivers(hass):
        return "invalid_receiver"

    if medium == SIGNAL_MEDIUM_RF:
        if transmitter not in _radio_frequency_transmitters(hass, rf_frequency):
            return "invalid_emitter"
        return None

    if transmitter not in infrared.async_get_emitters(hass):
        return "invalid_emitter"
    return None


def _service_error_key(err: ServiceValidationError) -> str:
    """Map a service validation error to a config flow error key."""
    if err.translation_key:
        return err.translation_key
    return "learn_failed"


def _normalize_entity_id(value: Any) -> str:
    """Normalize entity selector values from the config flow."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        return first if isinstance(first, str) else str(first)
    return str(value) if value is not None else ""


def _flow_results_store(hass) -> dict[str, list[dict[str, str]]]:
    """Return the in-memory IRDB search cache keyed by config flow id."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(IRDB_FLOW_RESULTS_KEY)
    if not isinstance(store, dict):
        store = {}
        domain_data[IRDB_FLOW_RESULTS_KEY] = store
    return store


def _format_entity_hint(hass, entity_id: str) -> str:
    """Return a human-readable label for a configured entity."""
    registry = er.async_get(hass)
    entity_entry = registry.async_get(entity_id)
    if entity_entry and entity_entry.name:
        return f"{entity_entry.name} ({entity_id})"

    state = hass.states.get(entity_id)
    if state is not None and state.name:
        return f"{state.name} ({entity_id})"

    return entity_id


class ShysRemoteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SHYS Remote."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="SHYS Remote", data={})

        self._set_confirm_only()
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {SUBENTRY_DEVICE: DeviceSubentryFlowHandler}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return ShysRemoteOptionsFlowHandler(config_entry)


class ShysRemoteOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle integration-wide options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _options_schema(),
                {
                    CONF_PULSE_MS: options.get(CONF_PULSE_MS, DEFAULT_PULSE_MS),
                    CONF_MATCH_TOLERANCE: options.get(
                        CONF_MATCH_TOLERANCE, DEFAULT_MATCH_TOLERANCE
                    ),
                    CONF_DEBOUNCE_MS: options.get(CONF_DEBOUNCE_MS, DEFAULT_DEBOUNCE_MS),
                },
            ),
        )


class DeviceSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for remote devices."""

    def _get_pending_device(self) -> dict[str, Any] | None:
        """Return pending device data stored in the flow context."""
        pending = self.context.get(CTX_IRDB_PENDING)
        return pending if isinstance(pending, dict) else None

    def _set_pending_device(self, user_input: dict[str, Any]) -> None:
        """Store pending device data in the flow context."""
        receiver = _normalize_entity_id(user_input.get(ATTR_RECEIVER_ENTITY_ID))
        self.context[CTX_IRDB_PENDING] = {
            CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
            ATTR_MEDIUM: user_input.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR),
            CONF_RF_FREQUENCY: int(
                user_input.get(CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY)
            ),
            ATTR_RECEIVER_ENTITY_ID: receiver,
            ATTR_TRANSMITTER_ENTITY_ID: _normalize_entity_id(
                user_input[ATTR_TRANSMITTER_ENTITY_ID]
            ),
            CONF_SIGNAL_SOURCE: user_input.get(CONF_SIGNAL_SOURCE, SOURCE_IRDB),
            **_send_options_from_input(user_input),
        }

    def _set_flow_search_results(self, results: list[dict[str, str]]) -> None:
        """Cache IRDB search results outside the serialized flow context."""
        _flow_results_store(self.hass)[self.flow_id] = [
            {"path": entry["path"], "label": entry["label"]}
            for entry in results
            if isinstance(entry.get("path"), str) and isinstance(entry.get("label"), str)
        ]
        self.context[CTX_IRDB_PAGE] = 0

    def _get_flow_search_results(self) -> list[dict[str, str]]:
        """Return cached IRDB search results for this flow."""
        results = _flow_results_store(self.hass).get(self.flow_id)
        return results if isinstance(results, list) else []

    def _clear_flow_search_results(self) -> None:
        """Drop cached IRDB search results for this flow."""
        _flow_results_store(self.hass).pop(self.flow_id, None)
        self.context.pop(CTX_IRDB_PAGE, None)

    def _get_results_page(self) -> int:
        """Return the current zero-based results page."""
        page = self.context.get(CTX_IRDB_PAGE, 0)
        if isinstance(page, int) and page >= 0:
            return page
        return 0

    def _set_results_page(self, page: int) -> None:
        """Store the current results page."""
        self.context[CTX_IRDB_PAGE] = max(0, page)

    async def _async_resolve_search_results(self) -> list[dict[str, str]]:
        """Return cached search results or rebuild them from the last query."""
        cached = self._get_flow_search_results()
        if cached:
            return cached

        query = self._get_last_query()
        if len(query) < 2:
            return []

        category = self._get_last_category()
        device_type = None if category == IRDB_CATEGORY_ALL else category
        results = await self._get_irdb_client().async_search(
            query, device_type=device_type
        )
        if results:
            self._set_flow_search_results(results)
        return results

    def _irdb_pick_description_placeholders(
        self, results: list[dict[str, str]]
    ) -> dict[str, str]:
        """Return placeholders for the remote pick step."""
        page = self._get_results_page()
        total_pages = max(
            1, (len(results) + IRDB_FLOW_PAGE_SIZE - 1) // IRDB_FLOW_PAGE_SIZE
        )
        return {
            "count": str(len(results)),
            "query": self._get_last_query(),
            "page": str(page + 1),
            "pages": str(total_pages),
        }

    def _get_last_query(self) -> str:
        """Return the last IRDB search query."""
        query = self.context.get(CTX_IRDB_QUERY)
        return query if isinstance(query, str) else ""

    def _set_last_query(self, query: str) -> None:
        """Store the last IRDB search query in the flow context."""
        self.context[CTX_IRDB_QUERY] = query

    def _is_german(self) -> bool:
        """Return whether the UI language is German."""
        return self.hass.config.language.lower().startswith("de")

    def _irdb_category_label(self, category: str) -> str:
        """Return a human-readable label for an IRDB category value."""
        if category == IRDB_CATEGORY_ALL:
            return "Alle Kategorien" if self._is_german() else "All categories"
        return category.replace("_", " ")

    def _irdb_category_options(self) -> list[dict[str, str]]:
        """Return select options for IRDB category filter."""
        return [
            {"value": category, "label": self._irdb_category_label(category)}
            for category in IRDB_FILTER_CATEGORIES
        ]

    def _get_last_category(self) -> str:
        """Return the last IRDB category filter."""
        category = self.context.get(CTX_IRDB_CATEGORY)
        if not isinstance(category, str):
            return IRDB_CATEGORY_ALL
        if category == "__all__":
            return IRDB_CATEGORY_ALL
        return category

    def _set_last_category(self, category: str) -> None:
        """Store the last IRDB category filter in the flow context."""
        self.context[CTX_IRDB_CATEGORY] = category

    def _get_import_preview(self) -> dict[str, Any] | None:
        """Return IRDB import preview stored in the flow context."""
        preview = self.context.get(CTX_IRDB_PREVIEW)
        return preview if isinstance(preview, dict) else None

    def _set_import_preview(self, preview: dict[str, Any]) -> None:
        """Store IRDB import preview in the flow context."""
        self.context[CTX_IRDB_PREVIEW] = {
            "path": preview["path"],
            "label": preview["label"],
            "signal_count": preview["signal_count"],
            "skipped_count": preview["skipped_count"],
        }

    def _irdb_search_schema(self) -> vol.Schema:
        """Return the schema for IRDB search."""
        return vol.Schema(
            {
                vol.Required(CONF_IRDB_QUERY): selector.TextSelector(),
                vol.Optional(
                    CONF_IRDB_CATEGORY, default=IRDB_CATEGORY_ALL
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._irdb_category_options(),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    def _irdb_remote_options(
        self, results: list[dict[str, str]], page: int
    ) -> list[dict[str, str]]:
        """Return select options for one page of IRDB remotes."""
        start = page * IRDB_FLOW_PAGE_SIZE
        end = start + IRDB_FLOW_PAGE_SIZE
        page_results = results[start:end]

        if self._is_german():
            new_search_label = "↩ Neue Suche"
            prev_page_label = "← Vorherige Seite"
            next_page_label = "Nächste Seite →"
        else:
            new_search_label = "↩ New search"
            prev_page_label = "← Previous page"
            next_page_label = "Next page →"

        options: list[dict[str, str]] = []
        if page > 0:
            options.append({"value": IRDB_PREV_PAGE, "label": prev_page_label})
        options.append({"value": IRDB_SEARCH_AGAIN, "label": new_search_label})
        if end < len(results):
            options.append({"value": IRDB_NEXT_PAGE, "label": next_page_label})
        options.extend(
            {"value": entry["path"], "label": entry["label"]}
            for entry in page_results
        )
        return options

    def _get_manager(self) -> RemoteManager:
        """Return the integration manager for the parent config entry."""
        manager = self.hass.data.get(DOMAIN, {}).get(self._get_entry().entry_id)
        if not isinstance(manager, RemoteManager):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        return manager

    def _get_irdb_client(self) -> IrdbClient:
        """Return the IR database client."""
        return IrdbClient(self.hass, self._get_entry())

    def _create_device_entry(
        self,
        device_name: str,
        receiver: str | None,
        transmitter: str,
        *,
        medium: str = SIGNAL_MEDIUM_IR,
        rf_frequency: int = DEFAULT_RF_FREQUENCY,
        send_options: dict[str, int] | None = None,
        irdb_path: str | None = None,
        irdb_direction: str | None = None,
    ) -> SubentryFlowResult:
        """Create a device subentry."""
        send_settings = send_options or {
            CONF_SEND_REPEAT_COUNT: DEFAULT_SEND_REPEAT_COUNT,
            CONF_SEND_REPEAT_DELAY_MS: DEFAULT_SEND_REPEAT_DELAY_MS,
        }
        data: dict[str, str | int] = {
            ATTR_MEDIUM: medium,
            CONF_RF_FREQUENCY: rf_frequency,
            ATTR_TRANSMITTER_ENTITY_ID: transmitter,
            CONF_SEND_REPEAT_COUNT: send_settings[CONF_SEND_REPEAT_COUNT],
            CONF_SEND_REPEAT_DELAY_MS: send_settings[CONF_SEND_REPEAT_DELAY_MS],
        }
        if receiver:
            data[ATTR_RECEIVER_ENTITY_ID] = receiver
        if irdb_path:
            data[CONF_IRDB_PATH] = irdb_path
        if irdb_direction:
            data[CONF_IRDB_DIRECTION] = irdb_direction

        return self.async_create_entry(
            title=device_name,
            data=data,
            unique_id=slugify(device_name),
        )

    def _try_create_irdb_device(
        self,
        pending_device: dict[str, Any],
        irdb_path: str,
        *,
        irdb_direction: str = DIRECTION_OUTPUT,
    ) -> tuple[SubentryFlowResult | None, dict[str, str]]:
        """Validate pending device data and create a subentry for IRDB import."""
        errors: dict[str, str] = {}
        device_name = str(pending_device.get(CONF_DEVICE_NAME, "")).strip()
        receiver = _normalize_entity_id(pending_device.get(ATTR_RECEIVER_ENTITY_ID))
        receiver_value = receiver or None
        transmitter = _normalize_entity_id(
            pending_device.get(ATTR_TRANSMITTER_ENTITY_ID)
        )
        medium = str(pending_device.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR) or SIGNAL_MEDIUM_IR)
        rf_frequency = int(pending_device.get(CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY))

        if not device_name or not slugify(device_name):
            errors["base"] = "invalid_device_name"
        elif (
            error := _validate_transport_entities(
                self.hass, receiver_value, transmitter, medium, rf_frequency
            )
        ):
            errors["base"] = error
        else:
            unique_id = slugify(device_name)
            config_entry = self._get_entry()
            for existing in config_entry.subentries.values():
                if existing.unique_id == unique_id:
                    errors["base"] = "device_already_exists"
                    break
            else:
                self._clear_flow_search_results()
                self.context.pop(CTX_IRDB_PENDING, None)
                self.context.pop(CTX_IRDB_PREVIEW, None)
                try:
                    return (
                        self._create_device_entry(
                            device_name,
                            receiver_value,
                            transmitter,
                            medium=medium,
                            rf_frequency=rf_frequency,
                            send_options=_send_options_from_input(pending_device),
                            irdb_path=irdb_path,
                            irdb_direction=irdb_direction,
                        ),
                        errors,
                    )
                except Exception:
                    _LOGGER.exception("Failed to create IRDB device subentry")
                    errors["base"] = "irdb_import_failed"

        return None, errors

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new remote device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_name = user_input[CONF_DEVICE_NAME].strip()
            receiver = _normalize_entity_id(user_input.get(ATTR_RECEIVER_ENTITY_ID))
            receiver_value = receiver or None
            transmitter = _normalize_entity_id(user_input[ATTR_TRANSMITTER_ENTITY_ID])
            medium = str(user_input.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR) or SIGNAL_MEDIUM_IR)
            rf_frequency = int(user_input.get(CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY))
            signal_source = user_input.get(CONF_SIGNAL_SOURCE, SOURCE_MANUAL)

            if not device_name:
                errors["base"] = "invalid_device_name"
            elif receiver_value is None and signal_source == SOURCE_MANUAL:
                errors[CONF_SIGNAL_SOURCE] = "manual_requires_receiver"
            elif (
                error := _validate_transport_entities(
                    self.hass, receiver_value, transmitter, medium, rf_frequency
                )
            ):
                errors["base"] = error
            else:
                unique_id = slugify(device_name)
                config_entry = self._get_entry()
                for existing in config_entry.subentries.values():
                    if existing.unique_id == unique_id:
                        errors["base"] = "device_already_exists"
                        break
                else:
                    if signal_source == SOURCE_IRDB:
                        self._set_pending_device(user_input)
                        return await self.async_step_irdb_search()
                    return self._create_device_entry(
                        device_name,
                        receiver_value,
                        transmitter,
                        medium=medium,
                        rf_frequency=rf_frequency,
                        send_options=_send_options_from_input(user_input),
                    )

        schema = _device_schema(
            self.hass,
            include_manual_source=not (
                user_input is not None
                and not _normalize_entity_id(user_input.get(ATTR_RECEIVER_ENTITY_ID))
            )
        )
        return self.async_show_form(
            step_id="user",
            data_schema=(
                self.add_suggested_values_to_schema(schema, user_input)
                if user_input is not None
                else schema
            ),
            description_placeholders={
                "transmitter_hint": _transmitter_hint(self.hass)
            },
            errors=errors,
        )

    async def async_step_irdb_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Search the Flipper-IRDB for a remote."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get(CONF_IRDB_QUERY, "").strip()
            category = user_input.get(CONF_IRDB_CATEGORY, IRDB_CATEGORY_ALL)
            if category == "__all__":
                category = IRDB_CATEGORY_ALL
            if len(query) < 2:
                errors["base"] = "irdb_query_too_short"
            else:
                try:
                    device_type = (
                        None
                        if category == IRDB_CATEGORY_ALL
                        else category
                    )
                    results = await self._get_irdb_client().async_search(
                        query, device_type=device_type
                    )
                except HomeAssistantError:
                    errors["base"] = "irdb_index_failed"
                except Exception:
                    _LOGGER.exception("IRDB search failed")
                    errors["base"] = "irdb_index_failed"
                else:
                    if not results:
                        errors["base"] = "irdb_no_results"
                    else:
                        self._set_last_query(query)
                        self._set_last_category(category)
                        self._set_flow_search_results(results)
                        return await self.async_step_irdb_pick_remote()

        return self.async_show_form(
            step_id="irdb_search",
            data_schema=self.add_suggested_values_to_schema(
                self._irdb_search_schema(),
                {
                    CONF_IRDB_QUERY: self._get_last_query(),
                    CONF_IRDB_CATEGORY: self._get_last_category(),
                },
            ),
            description_placeholders=irdb_attribution_placeholders(),
            errors=errors,
        )

    async def async_step_irdb_pick_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select a remote from the search results."""
        errors: dict[str, str] = {}

        if not self._get_pending_device():
            return await self.async_step_user()

        try:
            search_results = await self._async_resolve_search_results()
        except HomeAssistantError:
            errors["base"] = "irdb_index_failed"
            search_results = []
        except Exception:
            _LOGGER.exception("IRDB search failed while loading pick step")
            errors["base"] = "irdb_index_failed"
            search_results = []

        if not search_results:
            if not errors:
                errors["base"] = "irdb_no_results"
            return await self.async_step_irdb_search()

        page = self._get_results_page()
        max_page = max(
            0, (len(search_results) - 1) // IRDB_FLOW_PAGE_SIZE
        )
        if page > max_page:
            page = max_page
            self._set_results_page(page)

        if user_input is not None:
            selected_path = user_input.get(CONF_IRDB_REMOTE)
            if not isinstance(selected_path, str):
                selected_path = ""
            if selected_path == IRDB_SEARCH_AGAIN:
                self._clear_flow_search_results()
                return await self.async_step_irdb_search()
            if selected_path == IRDB_NEXT_PAGE:
                self._set_results_page(min(page + 1, max_page))
                return await self.async_step_irdb_pick_remote()
            if selected_path == IRDB_PREV_PAGE:
                self._set_results_page(max(page - 1, 0))
                return await self.async_step_irdb_pick_remote()

            selected_entry = next(
                (
                    entry
                    for entry in search_results
                    if entry["path"] == selected_path
                ),
                None,
            )
            if selected_entry is None:
                errors["base"] = "irdb_remote_not_found"
            else:
                try:
                    preview = await self._get_irdb_client().async_preview_remote(
                        selected_path
                    )
                except HomeAssistantError:
                    errors["base"] = "irdb_download_failed"
                except Exception:
                    _LOGGER.exception("IRDB preview failed for %s", selected_path)
                    errors["base"] = "irdb_download_failed"
                else:
                    if preview["signal_count"] == 0:
                        errors["base"] = "irdb_no_supported_signals"
                    else:
                        self._set_import_preview(
                            {
                                "path": selected_path,
                                "label": selected_entry["label"],
                                "signal_count": preview["signal_count"],
                                "skipped_count": preview["skipped_count"],
                            }
                        )
                        return await self.async_step_irdb_confirm()

        return self.async_show_form(
            step_id="irdb_pick_remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IRDB_REMOTE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._irdb_remote_options(search_results, page),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders=self._irdb_pick_description_placeholders(
                search_results
            ),
            errors=errors,
        )

    async def async_step_irdb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm IRDB import options and create the device."""
        pending_device = self._get_pending_device()
        import_preview = self._get_import_preview()

        if not pending_device or not import_preview:
            return await self.async_step_user()

        placeholders = {
            **irdb_attribution_placeholders(),
            "remote": import_preview["label"],
            "count": str(import_preview["signal_count"]),
            "skipped": str(import_preview["skipped_count"]),
        }

        if user_input is not None:
            errors: dict[str, str] = {}
            has_receiver = bool(pending_device.get(ATTR_RECEIVER_ENTITY_ID))
            direction = user_input.get(ATTR_DIRECTION, DIRECTION_OUTPUT)
            if not has_receiver:
                direction = DIRECTION_OUTPUT
            result, create_errors = self._try_create_irdb_device(
                pending_device,
                import_preview["path"],
                irdb_direction=direction,
            )
            if result:
                self._clear_flow_search_results()
                return result
            errors.update(create_errors)

            return self.async_show_form(
                step_id="irdb_confirm",
                data_schema=_direction_schema(include_input=has_receiver),
                description_placeholders=placeholders,
                errors=errors,
            )

        return self.async_show_form(
            step_id="irdb_confirm",
            data_schema=_direction_schema(
                include_input=bool(pending_device.get(ATTR_RECEIVER_ENTITY_ID))
            ),
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the device admin menu."""
        subentry = self._get_reconfigure_subentry()
        has_receiver = bool(subentry.data.get(ATTR_RECEIVER_ENTITY_ID))
        menu_options = [MENU_EDIT_DEVICE]
        if has_receiver:
            menu_options.append(MENU_LEARN_COMMAND)
        menu_options.append(MENU_DELETE_COMMAND)
        receiver_note = (
            "Empfänger konfiguriert: Anlernen und Input verfügbar."
            if self._is_german()
            else "Receiver configured: learning and input are available."
        )
        if not has_receiver:
            receiver_note = (
                "Kein Empfänger konfiguriert: Anlernen ist deaktiviert, Input/Binary-Sensoren sind nicht möglich."
                if self._is_german()
                else "No receiver configured: learning is disabled and input/binary sensors are not available."
            )
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=menu_options,
            description_placeholders={
                "device": subentry.title,
                "receiver_note": receiver_note,
            },
        )

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit device name or infrared hardware."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            device_name = user_input[CONF_DEVICE_NAME].strip()
            receiver = _normalize_entity_id(user_input.get(ATTR_RECEIVER_ENTITY_ID))
            receiver_value = receiver or None
            transmitter = _normalize_entity_id(
                user_input[ATTR_TRANSMITTER_ENTITY_ID]
            )
            medium = str(
                user_input.get(ATTR_MEDIUM, subentry.data.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR))
                or SIGNAL_MEDIUM_IR
            )
            rf_frequency = int(user_input.get(CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY))

            if not device_name:
                errors["base"] = "invalid_device_name"
            elif (
                error := _validate_transport_entities(
                    self.hass, receiver_value, transmitter, medium, rf_frequency
                )
            ):
                errors["base"] = error
            else:
                unique_id = slugify(device_name)
                config_entry = self._get_entry()
                for existing in config_entry.subentries.values():
                    if (
                        existing.subentry_id != subentry.subentry_id
                        and existing.unique_id == unique_id
                    ):
                        errors["base"] = "device_already_exists"
                        break
                else:
                    device_data: dict[str, str | int] = {
                        ATTR_MEDIUM: medium,
                        CONF_RF_FREQUENCY: rf_frequency,
                        ATTR_TRANSMITTER_ENTITY_ID: transmitter,
                        **_send_options_from_input(user_input),
                    }
                    if receiver_value is not None:
                        device_data[ATTR_RECEIVER_ENTITY_ID] = receiver_value
                    return self.async_update_and_abort(
                        config_entry,
                        subentry,
                        title=device_name,
                        data=device_data,
                    )

        return self.async_show_form(
            step_id="edit_device",
            data_schema=self.add_suggested_values_to_schema(
                _device_edit_schema(
                    self.hass,
                    rf_frequency=subentry.data.get(CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY),
                ),
                {
                    CONF_DEVICE_NAME: subentry.title,
                    ATTR_MEDIUM: subentry.data.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR),
                    CONF_RF_FREQUENCY: subentry.data.get(
                        CONF_RF_FREQUENCY, DEFAULT_RF_FREQUENCY
                    ),
                    ATTR_RECEIVER_ENTITY_ID: subentry.data.get(
                        ATTR_RECEIVER_ENTITY_ID
                    ),
                    ATTR_TRANSMITTER_ENTITY_ID: subentry.data[
                        ATTR_TRANSMITTER_ENTITY_ID
                    ],
                    **get_device_send_options(subentry),
                },
            ),
            description_placeholders={"device": subentry.title},
            errors=errors,
        )

    def _learn_command_form(
        self,
        subentry: ConfigSubentry,
        receiver_entity_id: str,
        errors: dict[str, str] | None = None,
    ) -> SubentryFlowResult:
        """Return the learn-signal form, optionally showing an error."""
        return self.async_show_form(
            step_id="learn_command",
            data_schema=_learn_schema(include_input=bool(receiver_entity_id)),
            description_placeholders={
                "device": subentry.title,
                "receiver": _format_entity_hint(self.hass, receiver_entity_id),
                "capture_hint": _capture_hint(
                    self.hass, subentry.data.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR)
                ),
            },
            errors=errors or {},
        )

    async def async_step_learn_command(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Learn a new remote signal - see _async_step_learn_command.

        Thin wrapper that guarantees this step never lets an exception
        escape uncaught. The inner implementation already converts every
        error it anticipates (ServiceValidationError, capture/store
        failures) into a form with a translated errors["base"], but a step
        method raising *anything* uncaught - including something as basic
        as _get_reconfigure_subentry() or _format_entity_hint() hitting an
        edge case - makes Home Assistant's frontend show a generic "Unknown
        error occurred" with no indication of what happened or where. This
        outer try/except is the last line of defense against that, on top
        of (not instead of) the inner handling.
        """
        try:
            return await self._async_step_learn_command(user_input)
        except Exception:  # noqa: BLE001 - see docstring
            _LOGGER.exception("Unexpected error in learn_command step")
            return self.async_abort(reason="learn_step_failed")

    async def _async_step_learn_command(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Learn a new remote signal.

        IR and RF are both learned with a single blocking capture within
        this step call, via async_learn_command(). RF used to need a
        second, confirming capture, but that doesn't work for devices that
        send a multi-repeat burst with rotating/jittering content per press
        (e.g. Emil-Lux/Tronic sockets, which send ~4 cycles per button
        press) - two independent presses of such a remote are *expected* to
        differ, so comparing them only ever produced false "doesn't match"
        failures. async_learn_command() now applies a much weaker,
        protocol-agnostic sanity check instead
        (validate_rf_capture_length() in remote.py): reject only captures
        too short to plausibly be a real signal, not ones that merely
        differ from an earlier one.
        """
        subentry = self._get_reconfigure_subentry()
        receiver_entity_id = subentry.data.get(ATTR_RECEIVER_ENTITY_ID)
        if not receiver_entity_id:
            return await self.async_step_reconfigure()
        receiver_entity_id = str(receiver_entity_id)

        errors: dict[str, str] = {}

        if user_input is not None:
            signal_name = slugify(user_input[ATTR_NAME].strip())
            timeout = int(user_input.get(ATTR_TIMEOUT, DEFAULT_LEARN_TIMEOUT))
            direction = user_input.get(ATTR_DIRECTION, DIRECTION_OUTPUT)

            if not signal_name:
                errors["base"] = "invalid_signal_name"
            else:
                try:
                    manager = self._get_manager()
                except ServiceValidationError as err:
                    errors["base"] = _service_error_key(err)
                else:
                    if signal_name in manager.get_subentry_commands(
                        subentry.subentry_id
                    ):
                        errors["base"] = "command_already_exists"
                    else:
                        try:
                            await async_learn_command(
                                self.hass,
                                manager,
                                subentry,
                                signal_name,
                                timeout=timeout,
                                direction=direction,
                            )
                        except ServiceValidationError as err:
                            _LOGGER.debug("Learn failed: %s", err)
                            errors["base"] = _service_error_key(err)
                        else:
                            return self.async_abort(
                                reason="signal_learned",
                                description_placeholders={
                                    "name": signal_name,
                                    "device": subentry.title,
                                },
                            )

        return self._learn_command_form(subentry, receiver_entity_id, errors)

    async def async_step_delete_command(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delete a learned remote signal."""
        subentry = self._get_reconfigure_subentry()
        manager = self._get_manager()
        signals = list(manager.get_subentry_commands(subentry.subentry_id).keys())

        if not signals:
            # No data_schema needed - see the comment in
            # async_step_learn_command for why (_set_confirm_only() only
            # exists on ConfigFlow, not ConfigSubentryFlow, and raised
            # AttributeError here too before this fix).
            return self.async_show_form(
                step_id="delete_command_empty",
                description_placeholders={"device": subentry.title},
            )

        if user_input is not None:
            signal_name = user_input[ATTR_NAME]
            await async_delete_command(manager, subentry, signal_name)
            return self.async_abort(
                reason="signal_deleted",
                description_placeholders={
                    "name": signal_name,
                    "device": subentry.title,
                },
            )

        return self.async_show_form(
            step_id="delete_command",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=signals,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={"device": subentry.title},
        )
