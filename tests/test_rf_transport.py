from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "custom_components" / "shys_remote" / "signal_transport.py"
SPEC = importlib.util.spec_from_file_location("shys_remote_signal_transport", MODULE_PATH)
assert SPEC and SPEC.loader
signal_transport = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = signal_transport
SPEC.loader.exec_module(signal_transport)

RawSignalCommand = signal_transport.RawSignalCommand
SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY = (
    signal_transport.SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY
)
SIGNAL_MEDIUM_RF = signal_transport.SIGNAL_MEDIUM_RF
build_rf_command = signal_transport.build_rf_command


def test_build_rf_command_uses_frequency_and_timings() -> None:
    command = RawSignalCommand(
        timings=[350, -1050, 350, -350],
        frequency=433_920_000,
        medium=SIGNAL_MEDIUM_RF,
        backend=SIGNAL_BACKEND_HOMEASSISTANT_RADIO_FREQUENCY,
    )

    rf_command = build_rf_command(command)

    assert rf_command.frequency == 433_920_000
    assert rf_command.get_raw_timings() == [350, -1050, 350, -350]
    assert rf_command.repeat_count == 0
