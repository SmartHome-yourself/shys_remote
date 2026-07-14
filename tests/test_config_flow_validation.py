"""Tests for the RF-aware validation and entity-scoping helpers in config_flow.py.

Only the module-level helper functions are exercised here. The actual flow
classes (``ShysRemoteConfigFlow``, ``DeviceSubentryFlowHandler``) need a real
Home Assistant runtime (``ConfigSubentryFlow``/``FlowHandler`` machinery) that
this dev environment doesn't have — see conftest.py for why. Importing the
module still works because the flow classes only need a subclassable
placeholder, not working behavior, and that's enough to reach the standalone
functions below.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import shys_remote.config_flow as config_flow

RF_FREQUENCY = 433_920_000


def _hass(language: str = "en") -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(language=language))


def _rf_transmitters(entities: list[str]):
    def _async_get_transmitters(hass, *, frequency, modulation):
        assert frequency == RF_FREQUENCY
        return entities

    return _async_get_transmitters


def test_validate_transport_entities_ir_valid(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters",
        lambda hass: ["remote.ir_blaster"],
    )

    error = config_flow._validate_transport_entities(
        object(), "remote.ir_receiver", "remote.ir_blaster", "ir"
    )

    assert error is None


def test_validate_transport_entities_ir_invalid_emitter(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters",
        lambda hass: [],
    )

    error = config_flow._validate_transport_entities(
        object(), "remote.ir_receiver", "remote.ir_blaster", "ir"
    )

    assert error == "invalid_emitter"


def test_validate_transport_entities_checks_receiver_even_for_rf(monkeypatch) -> None:
    """The receiver check must not be skipped for RF devices (that was the bug)."""
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: [],
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters(["switch.rf_transmitter"]),
    )

    error = config_flow._validate_transport_entities(
        object(), "remote.unknown_receiver", "switch.rf_transmitter", "rf", RF_FREQUENCY
    )

    assert error == "invalid_receiver"


def test_validate_transport_entities_rf_valid(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters(["switch.rf_transmitter"]),
    )

    error = config_flow._validate_transport_entities(
        object(), "remote.ir_receiver", "switch.rf_transmitter", "rf", RF_FREQUENCY
    )

    assert error is None


def test_validate_transport_entities_rf_rejects_ir_only_emitter(monkeypatch) -> None:
    """An entity that is a valid IR emitter but not an RF one must fail for medium=rf."""
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters",
        lambda hass: ["remote.ir_blaster"],
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters([]),
    )

    error = config_flow._validate_transport_entities(
        object(), "remote.ir_receiver", "remote.ir_blaster", "rf", RF_FREQUENCY
    )

    assert error == "invalid_emitter"


def test_validate_transport_entities_rf_treats_hass_error_as_no_transmitters(
    monkeypatch,
) -> None:
    """radio_frequency.async_get_transmitters can raise HomeAssistantError; degrade to []."""
    from homeassistant.exceptions import HomeAssistantError

    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )

    def _raise(hass, *, frequency, modulation):
        raise HomeAssistantError("radio_frequency backend not loaded")

    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters", _raise
    )

    error = config_flow._validate_transport_entities(
        object(), "remote.ir_receiver", "switch.rf_transmitter", "rf", RF_FREQUENCY
    )

    assert error == "invalid_emitter"


def test_receiver_entity_ids_uses_infrared_receivers(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.b", "remote.a"],
    )

    assert config_flow._receiver_entity_ids(object()) == ["remote.a", "remote.b"]


def test_transmitter_entity_ids_unions_infrared_and_rf(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters",
        lambda hass: ["remote.ir_blaster"],
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters(["switch.rf_transmitter"]),
    )

    assert config_flow._transmitter_entity_ids(object(), rf_frequency=RF_FREQUENCY) == [
        "remote.ir_blaster",
        "switch.rf_transmitter",
    ]


def test_radio_frequency_transmitters_returns_empty_when_component_missing(
    monkeypatch,
) -> None:
    """If the radio_frequency component isn't installed, this must degrade to []."""
    monkeypatch.delitem(sys.modules, "homeassistant.components.radio_frequency", raising=False)
    monkeypatch.delattr(
        sys.modules["homeassistant.components"], "radio_frequency", raising=False
    )

    assert config_flow._radio_frequency_transmitters(object(), RF_FREQUENCY) == []


def test_transmitter_hint_empty_when_transmitters_exist(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters",
        lambda hass: ["remote.ir_blaster"],
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters([]),
    )

    assert config_flow._transmitter_hint(_hass()) == ""


def test_transmitter_hint_explains_when_none_found_en(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters", lambda hass: []
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters([]),
    )

    hint = config_flow._transmitter_hint(_hass("en"))

    assert "No transmitter found" in hint
    assert "ir_rf_proxy" in hint


def test_transmitter_hint_explains_when_none_found_de(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters", lambda hass: []
    )
    monkeypatch.setattr(
        "homeassistant.components.radio_frequency.async_get_transmitters",
        _rf_transmitters([]),
    )

    hint = config_flow._transmitter_hint(_hass("de"))

    assert "Kein Transmitter gefunden" in hint
    assert "ir_rf_proxy" in hint


def test_capture_hint_empty_for_ir() -> None:
    assert config_flow._capture_hint(_hass(), "ir") == ""


def test_capture_hint_explains_hold_button_for_rf_en() -> None:
    hint = config_flow._capture_hint(_hass("en"), "rf")

    assert "hold the button" in hint.lower()


def test_capture_hint_explains_hold_button_for_rf_de() -> None:
    hint = config_flow._capture_hint(_hass("de"), "rf")

    assert "gedrückt halten" in hint
