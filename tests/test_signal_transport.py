from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "custom_components" / "shys_remote" / "signal_transport.py"
SPEC = importlib.util.spec_from_file_location("shys_remote_signal_transport", MODULE_PATH)
assert SPEC and SPEC.loader
signal_transport = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = signal_transport
SPEC.loader.exec_module(signal_transport)

(
    SIGNAL_BACKEND_HOMEASSISTANT_INFRARED,
    SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY,
    SIGNAL_MEDIUM_IR,
    SIGNAL_MEDIUM_RF,
    get_signal_backend,
    get_signal_medium,
    get_transport_entity_id,
) = (
    signal_transport.SIGNAL_BACKEND_HOMEASSISTANT_INFRARED,
    signal_transport.SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY,
    signal_transport.SIGNAL_MEDIUM_IR,
    signal_transport.SIGNAL_MEDIUM_RF,
    signal_transport.get_signal_backend,
    signal_transport.get_signal_medium,
    signal_transport.get_transport_entity_id,
)


def test_default_signal_metadata_is_ir() -> None:
    command_data: dict[str, object] = {}
    assert get_signal_medium(command_data) == SIGNAL_MEDIUM_IR
    assert get_signal_backend(command_data) == SIGNAL_BACKEND_HOMEASSISTANT_INFRARED


def test_rf_metadata_uses_radio_frequency_backend() -> None:
    command_data = {"medium": SIGNAL_MEDIUM_RF, "backend": SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY}
    assert get_signal_medium(command_data) == SIGNAL_MEDIUM_RF
    assert get_signal_backend(command_data) == SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY


def test_transport_entity_id_prefers_command_override() -> None:
    subentry = SimpleNamespace(data={"transmitter_entity_id": "ir_emit"})
    command_data = {"transport_entity_id": "rf_entity"}
    assert get_transport_entity_id(subentry, command_data) == "rf_entity"
