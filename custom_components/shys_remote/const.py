"""Constants for the SHYS Remote integration."""

DOMAIN = "shys_remote"
LEGACY_DOMAIN = "shys_ir_remote"
LEGACY_IRDB_INDEX_STORE_KEY = f"{LEGACY_DOMAIN}_irdb_index_v2"

PLATFORMS = ["binary_sensor", "button"]
SUBENTRY_DEVICE = "device"
IRDB_FLOW_RESULTS_KEY = "irdb_flow_results"
IRDB_FLOW_PAGE_SIZE = 100

STORAGE_VERSION = 2
CONFIG_VERSION = 2

DEFAULT_CARRIER_FREQUENCY = 38000
DEFAULT_RF_FREQUENCY = 433_920_000
DEFAULT_LEARN_TIMEOUT = 10
DEFAULT_PULSE_MS = 500
DEFAULT_MATCH_TOLERANCE = 25
DEFAULT_DEBOUNCE_MS = 300
DEFAULT_SEND_REPEAT_COUNT = 1
DEFAULT_SEND_REPEAT_DELAY_MS = 45

# Cheap fixed-code OOK RF receivers (e.g. PT2262/EV1527-style sockets) generally
# don't decode a single burst - unlike NEC-style IR remotes, they expect the same
# code sent back-to-back several times with a short gap before they latch. IR
# keeps the settings above; these only apply as the RF fallback default below.
DEFAULT_RF_SEND_REPEAT_COUNT = 10
DEFAULT_RF_SEND_REPEAT_DELAY_MS = 10

# Pure noise filter for a learned RF capture - not a protocol length
# requirement. A single AGC glitch or spurious pulse can be reported as a
# 1-2 entry "signal"; anything that short is almost certainly noise, not a
# real remote. Deliberately low so genuinely short fixed codes still learn.
MIN_RF_CAPTURE_PULSES = 10

DIRECTION_OUTPUT = "output"
DIRECTION_INPUT = "input"
DIRECTION_BOTH = "both"

SERVICE_LEARN = "learn"
SERVICE_SEND = "send"
SERVICE_DELETE = "delete"

ATTR_DEVICE = "device"
ATTR_DIRECTION = "direction"
ATTR_MEDIUM = "medium"
ATTR_NAME = "name"
ATTR_RECEIVER_ENTITY_ID = "receiver_entity_id"
ATTR_TRANSMITTER_ENTITY_ID = "transmitter_entity_id"
ATTR_TIMEOUT = "timeout"

CONF_DEBOUNCE_MS = "debounce_ms"
CONF_DEVICE_NAME = "device_name"
CONF_MATCH_TOLERANCE = "match_tolerance"
CONF_PULSE_MS = "pulse_ms"
CONF_RF_FREQUENCY = "rf_frequency"
CONF_SEND_REPEAT_COUNT = "send_repeat_count"
CONF_SEND_REPEAT_DELAY_MS = "send_repeat_delay_ms"

COMMAND_TYPE_RAW = "raw"
COMMAND_TYPE_PARSED = "parsed"
LEGACY_SUBENTRY_UNIQUE_ID = "imported"

SOURCE_MANUAL = "manual"
SOURCE_IRDB = "irdb"

CONF_IRDB_PATH = "irdb_path"
CONF_IRDB_DIRECTION = "irdb_direction"
CONF_IRDB_QUERY = "irdb_query"
CONF_IRDB_REMOTE = "irdb_remote"
CONF_IRDB_CATEGORY = "irdb_category"
CONF_SIGNAL_SOURCE = "signal_source"

IRDB_CATEGORY_ALL = "all"
IRDB_SEARCH_AGAIN = "__new_search__"
IRDB_NEXT_PAGE = "__next_page__"
IRDB_PREV_PAGE = "__prev_page__"

IRDB_FILTER_CATEGORIES = (
    IRDB_CATEGORY_ALL,
    "TVs",
    "Audio",
    "ACs",
    "Fans",
    "LEDs",
    "DVD_Players",
    "Projectors",
    "Cameras",
    "Miscellaneous",
)

IRDB_REPO_ATTRIBUTION = "Flipper-IRDB (Lucaslhm)"
IRDB_REPO_URL = "https://github.com/Lucaslhm/Flipper-IRDB"
IRDB_LICENSE_NAME = "CC0 1.0"
IRDB_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"


def irdb_attribution_placeholders() -> dict[str, str]:
    """Return translation placeholders for Flipper-IRDB attribution."""
    return {
        "attribution": IRDB_REPO_ATTRIBUTION,
        "license": IRDB_LICENSE_NAME,
        "source_url": IRDB_REPO_URL,
        "license_url": IRDB_LICENSE_URL,
    }


def get_signal_direction(command_data: dict) -> str:
    """Return the direction of a stored signal, defaulting to output."""
    return command_data.get(ATTR_DIRECTION, DIRECTION_OUTPUT)


def get_integration_options(entry) -> dict[str, int | float]:
    """Return integration-wide options with defaults."""
    return {
        CONF_PULSE_MS: int(entry.options.get(CONF_PULSE_MS, DEFAULT_PULSE_MS)),
        CONF_MATCH_TOLERANCE: float(
            entry.options.get(CONF_MATCH_TOLERANCE, DEFAULT_MATCH_TOLERANCE)
        ),
        CONF_DEBOUNCE_MS: int(entry.options.get(CONF_DEBOUNCE_MS, DEFAULT_DEBOUNCE_MS)),
    }


def get_device_send_options(subentry) -> dict[str, int]:
    """Return per-device send repeat settings with defaults.

    Falls back to RF-specific defaults for devices without an explicit
    send_repeat_count/send_repeat_delay_ms stored, since a single IR-style
    burst is usually not enough for fixed-code RF receivers to react.
    Explicit per-device values (set via the config flow) always win.
    """
    # Local import: const.py is a dependency-free leaf module, imported by
    # signal_transport.py's neighbours; keep that direction one-way.
    from .signal_transport import SIGNAL_MEDIUM_IR, SIGNAL_MEDIUM_RF

    medium = subentry.data.get(ATTR_MEDIUM, SIGNAL_MEDIUM_IR)
    if medium == SIGNAL_MEDIUM_RF:
        default_repeat_count = DEFAULT_RF_SEND_REPEAT_COUNT
        default_repeat_delay_ms = DEFAULT_RF_SEND_REPEAT_DELAY_MS
    else:
        default_repeat_count = DEFAULT_SEND_REPEAT_COUNT
        default_repeat_delay_ms = DEFAULT_SEND_REPEAT_DELAY_MS

    return {
        CONF_SEND_REPEAT_COUNT: int(
            subentry.data.get(CONF_SEND_REPEAT_COUNT, default_repeat_count)
        ),
        CONF_SEND_REPEAT_DELAY_MS: int(
            subentry.data.get(CONF_SEND_REPEAT_DELAY_MS, default_repeat_delay_ms)
        ),
    }


def output_signal_unique_id(subentry_unique_id: str, signal_name: str) -> str:
    """Return the unique id for an output signal button."""
    return f"{subentry_unique_id}_{signal_name}"


def input_signal_unique_id(subentry_unique_id: str, signal_name: str) -> str:
    """Return the unique id for an input signal binary sensor."""
    return f"{subentry_unique_id}_{signal_name}_input"
