"""Tests for get_device_send_options()'s medium-aware repeat/delay defaults."""

from __future__ import annotations

from types import SimpleNamespace

import shys_remote.const as const_module

get_device_send_options = const_module.get_device_send_options
CONF_SEND_REPEAT_COUNT = const_module.CONF_SEND_REPEAT_COUNT
CONF_SEND_REPEAT_DELAY_MS = const_module.CONF_SEND_REPEAT_DELAY_MS


def test_rf_device_without_explicit_settings_gets_rf_defaults() -> None:
    subentry = SimpleNamespace(data={"medium": "rf"})

    options = get_device_send_options(subentry)

    assert options[CONF_SEND_REPEAT_COUNT] == 10
    assert options[CONF_SEND_REPEAT_DELAY_MS] == 10


def test_ir_device_without_explicit_settings_keeps_ir_defaults() -> None:
    subentry = SimpleNamespace(data={"medium": "ir"})

    options = get_device_send_options(subentry)

    assert options[CONF_SEND_REPEAT_COUNT] == 1
    assert options[CONF_SEND_REPEAT_DELAY_MS] == 45


def test_device_without_medium_key_keeps_ir_defaults() -> None:
    """subentry.data with no 'medium' key at all defaults to IR, unchanged."""
    subentry = SimpleNamespace(data={})

    options = get_device_send_options(subentry)

    assert options[CONF_SEND_REPEAT_COUNT] == 1
    assert options[CONF_SEND_REPEAT_DELAY_MS] == 45


def test_rf_device_explicit_settings_are_not_overridden() -> None:
    """A user-configured value always wins over the medium-based default."""
    subentry = SimpleNamespace(
        data={
            "medium": "rf",
            "send_repeat_count": 3,
            "send_repeat_delay_ms": 20,
        }
    )

    options = get_device_send_options(subentry)

    assert options[CONF_SEND_REPEAT_COUNT] == 3
    assert options[CONF_SEND_REPEAT_DELAY_MS] == 20
