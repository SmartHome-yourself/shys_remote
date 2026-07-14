"""Tests for the (now medium-agnostic) learn-command step in config_flow.py.

Unlike test_config_flow_validation.py (which only exercises module-level
helper functions, since ConfigSubentryFlow is stubbed as an empty class in
this dev environment - see its docstring), these tests call
DeviceSubentryFlowHandler's async_step_learn_command/async_step_delete_command
directly as unbound functions (``DeviceSubentryFlowHandler.async_step_learn_command
(fake, ...)``) against a minimal duck-typed double that implements just the
FlowHandler surface those methods actually use: ``context``, ``async_show_form``
and ``async_abort``. That's enough to verify the real step logic without
needing Home Assistant's real data_entry_flow runtime.

RF learning previously needed a second, confirming capture and its own
config-flow step (async_step_learn_command_confirm) - removed because it
doesn't work for devices that send a multi-repeat burst with rotating/
jittering content per press (e.g. Emil-Lux/Tronic sockets): two independent
presses of such a remote are *expected* to differ, so comparing them only
produced false "doesn't match" failures. RF and IR now both go through
async_learn_command() with a single capture (see test_rf_dispatch.py for the
capture/length-validation logic itself); config_flow.py no longer branches
on medium at all, so there's little left to test at this layer beyond "it
still delegates correctly" and the safety nets that don't depend on medium.
"""

from __future__ import annotations

import types
from types import SimpleNamespace

import shys_remote.config_flow as config_flow
import shys_remote.manager as manager_module

RemoteManager = manager_module.RemoteManager
DeviceSubentryFlowHandler = config_flow.DeviceSubentryFlowHandler


class _FakeFlow:
    """Minimal FlowHandler double: just enough for the learn-command step.

    _async_step_learn_command and _learn_command_form are bound from the
    real DeviceSubentryFlowHandler class (not reimplemented here), so these
    tests exercise the actual production code for them too. Only implement
    methods here that really exist on ConfigSubentryFlow otherwise - a
    previous version of this file stubbed a ConfigFlow-only method
    (_set_confirm_only) "helpfully", which let a real AttributeError bug
    slip through undetected. Don't repeat that.
    """

    def __init__(self, manager: RemoteManager) -> None:
        self.context: dict = {}
        self.hass = SimpleNamespace(config=SimpleNamespace(language="en"))
        self._manager = manager
        for name in ("_async_step_learn_command", "_learn_command_form"):
            setattr(
                self, name, types.MethodType(getattr(DeviceSubentryFlowHandler, name), self)
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


def test_learn_command_rf_delegates_to_async_learn_command(monkeypatch) -> None:
    """RF now takes the exact same single-capture path as IR."""
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        _queued_subscribe_receiver(
            [_FakeSignal([350, -1050, 350, -350, 350, -1050, 350, -350, 350, -350])]
        ),
    )
    manager = _manager()
    stored: dict = {}

    async def fake_add_command(subentry, name, command_data):
        stored["name"] = name
        stored["command_data"] = command_data

    manager.async_add_command = fake_add_command
    flow = _flow(_rf_subentry(), manager, monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command(
            flow, {"name": "power", "timeout": 10, "direction": "output"}
        )
    )

    assert result == {
        "type": "abort",
        "reason": "signal_learned",
        "description_placeholders": {"name": "power", "device": "Test RF device"},
    }
    assert stored["name"] == "power"
    assert stored["command_data"]["medium"] == "rf"


def test_learn_command_rf_short_capture_shows_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_get_receivers",
        lambda hass: ["remote.ir_receiver"],
    )
    monkeypatch.setattr(
        "homeassistant.components.infrared.async_subscribe_receiver",
        _queued_subscribe_receiver([_FakeSignal([350, -1050, 350, -350])]),
    )
    flow = _flow(_rf_subentry(), _manager(), monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command(
            flow, {"name": "power", "timeout": 10, "direction": "output"}
        )
    )

    assert result["type"] == "form"
    assert result["step_id"] == "learn_command"
    assert result["errors"] == {"base": "rf_capture_too_short"}


def test_learn_command_wrapper_catches_unanticipated_exception(monkeypatch) -> None:
    """async_step_learn_command must never let an exception it didn't
    specifically anticipate (e.g. _get_reconfigure_subentry itself failing)
    turn into Home Assistant's generic "Unknown error occurred" - it must
    fall back to a translated abort instead."""
    flow = _flow(_rf_subentry(), _manager(), monkeypatch)

    def _boom():
        raise RuntimeError("registry lookup exploded")

    flow._get_reconfigure_subentry = _boom

    result = _run(
        DeviceSubentryFlowHandler.async_step_learn_command(
            flow, {"name": "power", "timeout": 10, "direction": "output"}
        )
    )

    assert result == {
        "type": "abort",
        "reason": "learn_step_failed",
        "description_placeholders": None,
    }


def test_delete_command_empty_does_not_use_set_confirm_only(monkeypatch) -> None:
    """async_step_delete_command's "no signals" dead end had the same
    _set_confirm_only() bug the learn-confirm step used to have (that step
    is gone now, but this one predates it and had the identical issue) - it
    must render without needing that ConfigFlow-only method."""
    manager = _manager()
    manager.commands = {"dev1": {}}
    flow = _flow(_rf_subentry(), manager, monkeypatch)

    result = _run(
        DeviceSubentryFlowHandler.async_step_delete_command(flow, None)
    )

    assert result["type"] == "form"
    assert result["step_id"] == "delete_command_empty"


def _run(coro):
    import asyncio

    return asyncio.run(coro)
