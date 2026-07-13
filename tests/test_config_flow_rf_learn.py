"""Regression tests for the RF learn progress-step state machine.

Unlike test_config_flow_validation.py (which only exercises module-level
helper functions, since ConfigSubentryFlow is stubbed as an empty class in
this dev environment - see its docstring), these tests call
DeviceSubentryFlowHandler's async/sync methods directly as unbound
functions (``DeviceSubentryFlowHandler._async_resume_rf_learn(fake, ...)``)
against a minimal duck-typed double that implements just the FlowHandler
surface those methods actually use: ``context``, ``async_get_progress_task``,
``async_show_progress`` and ``async_show_progress_done``. That's enough to
verify the real state-machine logic - which SHOW_PROGRESS/SHOW_PROGRESS_DONE
transitions happen and what ends up in self.context - without needing Home
Assistant's real data_entry_flow runtime.

This covers a bug where an RF learn failure (mismatched captures, a second
timeout, or any unexpected exception) caused DeviceSubentryFlowHandler to
return a form/abort result directly from a step that Home Assistant's flow
manager still considered "in progress" (SHOW_PROGRESS). That transition is
invalid - only SHOW_PROGRESS or SHOW_PROGRESS_DONE may follow SHOW_PROGRESS
- and raised an untranslated ValueError deep in Home Assistant's own flow
manager, which produced a blank error dialog and, once the frontend retried
the stuck flow, a repeating traceback between async_step_learn_command and
_async_resume_rf_learn.
"""

from __future__ import annotations

import asyncio
import types
from types import SimpleNamespace

import shys_remote.config_flow as config_flow
import shys_remote.manager as manager_module

RemoteManager = manager_module.RemoteManager
DeviceSubentryFlowHandler = config_flow.DeviceSubentryFlowHandler


class _FakeHass:
    """Minimal HomeAssistant double: just async_create_task."""

    def async_create_task(self, coro, name: str | None = None) -> asyncio.Task:
        return asyncio.ensure_future(coro)


class _FakeFlow:
    """Minimal FlowHandler double: just enough for _async_resume_rf_learn.

    _rf_learn_error_done, _rf_learn_progress and _clear_rf_learn_context are
    bound from the real DeviceSubentryFlowHandler class (not reimplemented
    here), so these tests exercise the actual production code, not a second
    copy of its logic that could drift from it.
    """

    def __init__(self, manager: RemoteManager) -> None:
        self.context: dict = {}
        self.hass = _FakeHass()
        self._manager = manager
        self._progress_task: asyncio.Task | None = None
        self.progress_actions: list[str] = []
        self.progress_done_next_steps: list[str] = []

        for name in (
            "_rf_learn_error_done",
            "_rf_learn_progress",
            "_clear_rf_learn_context",
        ):
            setattr(
                self, name, types.MethodType(getattr(DeviceSubentryFlowHandler, name), self)
            )

    def async_get_progress_task(self):
        return self._progress_task

    def async_show_progress(self, *, progress_action, progress_task, description_placeholders=None):
        self._progress_task = progress_task
        self.progress_actions.append(progress_action)
        return {"type": "show_progress", "progress_action": progress_action}

    def async_show_progress_done(self, *, next_step_id):
        self._progress_task = None
        self.progress_done_next_steps.append(next_step_id)
        return {"type": "show_progress_done", "step_id": next_step_id}

    def _get_manager(self):
        return self._manager


def _subentry() -> SimpleNamespace:
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


def _done_task(coro) -> asyncio.Task:
    """Run a coroutine to completion and return its (already-finished) task."""

    async def _runner() -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        try:
            await task
        except BaseException:  # noqa: BLE001 - want the task done, error or not
            pass
        return task

    return asyncio.run(_runner())


def test_resume_rf_learn_catches_unexpected_exception() -> None:
    """A non-ServiceValidationError failure must not escape uncaught."""

    async def _boom() -> list[int]:
        raise RuntimeError("receiver went away")

    flow = _FakeFlow(_manager())
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_STAGE] = "first"
    flow._progress_task = _done_task(_boom())

    result = asyncio.run(
        DeviceSubentryFlowHandler._async_resume_rf_learn(
            flow, _subentry(), "remote.ir_receiver"
        )
    )

    assert result == {"type": "show_progress_done", "step_id": "learn_command"}
    assert flow.context[config_flow.CTX_RF_LEARN_ERROR] == "learn_failed"
    assert config_flow.CTX_RF_LEARN_STAGE not in flow.context
    assert config_flow.CTX_RF_LEARN_INPUT not in flow.context


def test_resume_rf_learn_first_stage_advances_to_second_progress(monkeypatch) -> None:
    monkeypatch.setattr(
        config_flow, "_format_entity_hint", lambda hass, entity_id: entity_id
    )
    flow = _FakeFlow(_manager())
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_STAGE] = "first"

    async def _capture() -> list[int]:
        return [350, -1050, 350, -350]

    async def _run():
        task = asyncio.ensure_future(_capture())
        try:
            await task
        except BaseException:  # noqa: BLE001 - want the task done, error or not
            pass
        flow._progress_task = task
        result = await DeviceSubentryFlowHandler._async_resume_rf_learn(
            flow, _subentry(), "remote.ir_receiver"
        )
        # Second stage started a new (not-yet-run) task via _rf_learn_progress;
        # cancel it before the loop closes so it doesn't linger/warn.
        follow_up_task = flow._progress_task
        if follow_up_task is not None:
            follow_up_task.cancel()
        return result, follow_up_task

    result, follow_up_task = asyncio.run(_run())

    assert result["type"] == "show_progress"
    assert flow.progress_actions == [config_flow.PROGRESS_LEARN_LISTENING_CONFIRM]
    assert flow.context[config_flow.CTX_RF_LEARN_STAGE] == "second"
    assert flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] == [350, -1050, 350, -350]
    # Second stage must start a *new* task via _rf_learn_progress, not reuse
    # the already-finished one - otherwise HA never re-registers a callback.
    assert follow_up_task is not None


def test_resume_rf_learn_second_stage_mismatch_hands_off_error() -> None:
    flow = _FakeFlow(_manager())
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_STAGE] = "second"
    flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] = [350, -1050, 350, -350]

    async def _capture() -> list[int]:
        return [9000, -4500, 560, -560]

    flow._progress_task = _done_task(_capture())

    result = asyncio.run(
        DeviceSubentryFlowHandler._async_resume_rf_learn(
            flow, _subentry(), "remote.ir_receiver"
        )
    )

    assert result == {"type": "show_progress_done", "step_id": "learn_command"}
    assert flow.context[config_flow.CTX_RF_LEARN_ERROR] == "rf_learn_inconsistent"
    assert config_flow.CTX_RF_LEARN_STAGE not in flow.context


def test_resume_rf_learn_second_stage_match_stores_and_hands_off_success() -> None:
    manager = _manager()
    stored: dict = {}

    async def fake_add_command(subentry, name, command_data):
        stored["name"] = name
        stored["command_data"] = command_data

    manager.async_add_command = fake_add_command

    flow = _FakeFlow(manager)
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_STAGE] = "second"
    flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] = [350, -1050, 350, -350]

    async def _capture() -> list[int]:
        return [351, -1049, 349, -351]

    flow._progress_task = _done_task(_capture())

    result = asyncio.run(
        DeviceSubentryFlowHandler._async_resume_rf_learn(
            flow, _subentry(), "remote.ir_receiver"
        )
    )

    assert result == {"type": "show_progress_done", "step_id": "learn_command"}
    assert flow.context[config_flow.CTX_RF_LEARN_SUCCESS] == {
        "name": "power",
        "device": "Test RF device",
    }
    assert config_flow.CTX_RF_LEARN_STAGE not in flow.context
    assert stored["name"] == "power"
    assert stored["command_data"]["command"] == [350, -1050, 350, -350]


def test_resume_rf_learn_store_failure_hands_off_error_not_abort() -> None:
    """Even a failure while storing the signal must go through
    show_progress_done, not raise or return an abort directly."""
    manager = _manager()

    async def fake_add_command(subentry, name, command_data):
        raise RuntimeError("storage backend exploded")

    manager.async_add_command = fake_add_command

    flow = _FakeFlow(manager)
    flow.context[config_flow.CTX_RF_LEARN_INPUT] = {
        "name": "power",
        "timeout": 10,
        "direction": "output",
    }
    flow.context[config_flow.CTX_RF_LEARN_STAGE] = "second"
    flow.context[config_flow.CTX_RF_LEARN_FIRST_TIMINGS] = [350, -1050, 350, -350]

    async def _capture() -> list[int]:
        return [351, -1049, 349, -351]

    flow._progress_task = _done_task(_capture())

    result = asyncio.run(
        DeviceSubentryFlowHandler._async_resume_rf_learn(
            flow, _subentry(), "remote.ir_receiver"
        )
    )

    assert result == {"type": "show_progress_done", "step_id": "learn_command"}
    assert flow.context[config_flow.CTX_RF_LEARN_ERROR] == "learn_failed"
