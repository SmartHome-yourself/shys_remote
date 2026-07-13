"""Regression tests for the two-step RF learn flow in config_flow.py.

Unlike test_config_flow_validation.py (which only exercises module-level
helper functions, since ConfigSubentryFlow is stubbed as an empty class in
this dev environment - see its docstring), these tests call
DeviceSubentryFlowHandler's async_step_learn_command/
async_step_learn_command_confirm directly as unbound functions
(``DeviceSubentryFlowHandler.async_step_learn_command(fake, ...)``) against
a minimal duck-typed double that implements just the FlowHandler surface
those methods actually use: ``context``, ``async_show_form`` and
``async_abort``. That's enough to verify the real step logic without
needing Home Assistant's real data_entry_flow runtime.

RF learning used to be driven by Home Assistant's show_progress/
progress_task mechanism (to show "press now" / "press again" screens in
between the two required captures), but that turned out to be unreliable
for a *second* consecutive progress step within one flow
(home-assistant/core#95749: the flow gets re-entered - and the
still-pending task's result read - before that second task has actually
finished listening, so it fails immediately without ever really waiting,
and the progress description text never renders either). It was replaced
with two plain form-based steps: async_step_learn_command blocks
synchronously on the first capture and, on success, shows a confirmation
form; async_step_learn_command_confirm blocks on the second once that form
is submitted. These tests cover both steps and the handoff between them.
"""

from __future__ import annotations

import types
from types import SimpleNamespace

import shys_remote.config_flow as config_flow
import shys_remote.manager as manager_module

RemoteManager = manager_module.RemoteManager
DeviceSubentryFlowHandler = config_flow.DeviceSubentryFlowHandler


class _FakeFlow:
    """Minimal FlowHandler double: just enough for the learn-command steps.

    _learn_command_form is bound from the real DeviceSubentryFlowHandler
    class (not reimplemented here), so these tests exercise the actual
    production code for it too.
    """

    def __init__(self, manager: RemoteManager) -> None:
        self.context: dict = {}
        self.hass = object()
        self._manager = manager
        self._learn_command_form = types.MethodType(
            DeviceSubentryFlowHandler._learn_command_form, self
        )

    def async_show_form(self, *, step_id, data_schema=None, description_placeholders=None, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "errors": errors or {},
            "description_placeholders": description_placeholders,
        }

    def async_abort(self, *, reason, description_placeholders=None):
        return {
            "type": "abort",
            "reason": reason,
            "description_placeholders": description_placeholders,
        }

    def _get_reconfigure_subentry(self):
        return self._subentry

    def _get_manager(self):
        return self._manager


def _rf_subentry() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "transmitter_entity_id": "switch.rf_transmitter",
            "receiver_entity_id": "remote.ir_receiver",
            "medium": "rf",
            "rf_frequency": 433_920_000,
        },
        subentry_id="dev1",
        title="Test RF device",
    )


def _ir_subentry() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "transmitter_entity_id": "remote.ir_blaster",
            "receiver_entity_id": "remote.ir_receiver",
        },
        subentry_id="dev2",
        title="Test IR device",
    )


def _manager() -> RemoteManager:
    manager = RemoteManager.__new__(RemoteManager)
    manager.commands = {}
    manager.entry = SimpleNamespace(options={})
    return manager


def _flow(subentry, manager, monkeypatch) -> _FakeFlow:
    monkeypatch.setattr(
        config_flow, "_format_entity_hint", lambda hass, entity_id: entity_id
    )
    flow = _FakeFlow(manager)
    flow._subentry = subentry
    return flow


def _queued_subscribe_receiver(signals):
    def fake_subscribe_receiver(hass, entity_id, callback_):
        callback_(signals.pop(0))

        def _unsubscribe() -> None:
            return None

        return _unsubscribe

    return fake_subscribe_receiver


class _FakeSignal:
    def __init__(self, timings):
        self.timings = timings
        self.modulation = 38000


def test_learn_command_rf_first_capture_shows_confirm_step(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        _queued_subscribe_receiver([_FakeSignal([350, -1050, 350, -350])]),
    )
    manager = _manager()
    flow = _flow(_rf_subentry(), manager, monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command(
            flow, {"name": "power", "timeout": 10, "direction": "output"}
        )
    )

    assert result["type"] == "form"
    assert result["step_id"] == "learn_command_confirm"
    assert flow.context[config_flow.CTX_RF_LEARN_INPUT] == {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    assert flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] == [350, -1050, 350, -350]


def test_learn_command_rf_first_capture_failure_shows_error(monkeypatch) -> None:
    def fake_subscribe_receiver(hass, entity_id, callback_):
        from homeassistant.exceptions import HomeAssistantError

        raise HomeAssistantError("receiver gone")

    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        fake_subscribe_receiver,
    )
    manager = _manager()
    flow = _flow(_rf_subentry(), manager, monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command(
            flow, {"name": "power", "timeout": 10, "direction": "output"}
        )
    )

    assert result["type"] == "form"
    assert result["step_id"] == "learn_command"
    assert result["errors"]
    assert config_flow.CTX_RF_LEARN_INPUT not in flow.context


def test_learn_command_ir_unaffected(monkeypatch) -> None:
    """IR must still go through async_learn_command directly - single
    capture, no confirm step, no RF context keys touched."""
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_emitters",
        lambda hass: ["remote.ir_blaster"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        _queued_subscribe_receiver([_FakeSignal([9000, -4500, 560, -560])]),
    )
    manager = _manager()

    async def fake_add_command(subentry, name, command_data):
        pass

    manager.async_add_command = fake_add_command
    flow = _flow(_ir_subentry(), manager, monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command(
            flow, {"name": "power", "timeout": 10, "direction": "output"}
        )
    )

    assert result["type"] == "abort"
    assert result["reason"] == "signal_learned"
    assert config_flow.CTX_RF_LEARN_INPUT not in flow.context


def test_learn_command_confirm_match_stores_and_aborts(monkeypatch) -> None:
    manager = _manager()
    stored: dict = {}

    async def fake_add_command(subentry, name, command_data):
        stored["name"] = name
        stored["command_data"] = command_data

    manager.async_add_command = fake_add_command
    flow = _flow(_rf_subentry(), manager, monkeypatch)
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] = [350, -1050, 350, -350]

    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        _queued_subscribe_receiver([_FakeSignal([351, -1049, 349, -351])]),
    )

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command_confirm(flow, {})
    )

    assert result == {
        "type": "abort",
        "reason": "signal_learned",
        "description_placeholders": {"name": "power", "device": "Test RF device"},
    }
    assert stored["name"] == "power"
    assert stored["command_data"]["command"] == [350, -1050, 350, -350]
    assert config_flow.CTX_RF_LEARN_INPUT not in flow.context
    assert config_flow.CTX_RF_LEARN_FIRST_TIMINGS not in flow.context


def test_learn_command_confirm_mismatch_returns_to_learn_command_form(monkeypatch) -> None:
    manager = _manager()
    flow = _flow(_rf_subentry(), manager, monkeypatch)
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] = [350, -1050, 350, -350]

    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        _queued_subscribe_receiver([_FakeSignal([9000, -4500, 560, -560])]),
    )

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command_confirm(flow, {})
    )

    assert result["type"] == "form"
    assert result["step_id"] == "learn_command"
    assert result["errors"] == {"base": "rf_learn_inconsistent"}


def test_learn_command_confirm_without_prior_context_falls_back(monkeypatch) -> None:
    """Reaching the confirm step without a stashed first capture (flow
    state desync) must not crash - it should hand back a readable error."""
    flow = _flow(_rf_subentry(), _manager(), monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command_confirm(flow, None)
    )

    assert result["type"] == "form"
    assert result["step_id"] == "learn_command"
    assert result["errors"] == {"base": "learn_failed"}


def test_learn_command_confirm_renders_form_before_submission(monkeypatch) -> None:
    """A GET-style render (user_input=None) must just show the confirm
    form, not attempt a capture yet."""
    flow = _flow(_rf_subentry(), _manager(), monkeypatch)
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] = [350, -1050, 350, -350]

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command_confirm(flow, None)
    )

    assert result["type"] == "form"
    assert result["step_id"] == "learn_command_confirm"


def _run(coro):
    import asyncio

    return asyncio.run(coro)
