"""Test-only stubs for the sibling HA components this integration depends on.

``manager.py`` and ``remote.py`` import ``homeassistant.components.infrared``
(and, for RF, ``homeassistant.components.radio_frequency``) plus the external
``infrared_protocols`` package. None of those ship with plain ``pip install
homeassistant`` - they are separate custom components / packages that live in
a real Home Assistant instance. These stubs provide just enough of their
surface (the functions and classes this integration actually calls) so the
integration modules can be imported and their own logic exercised in
isolation, without pulling in a full Home Assistant runtime.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT_DIR = ROOT / "custom_components" / "shys_remote"


def _install_fake_package() -> None:
    """Register a lightweight ``shys_remote`` package pointing at the source dir.

    This avoids executing the real ``shys_remote/__init__.py`` (which wires up
    the full integration and services) so tests can import individual
    submodules such as ``manager`` and ``remote`` directly.
    """
    if "shys_remote" in sys.modules:
        return
    package = types.ModuleType("shys_remote")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules["shys_remote"] = package


def _install_infrared_protocols_stub() -> None:
    """Stub the external ``infrared_protocols`` package used by ``command.py``."""
    if "infrared_protocols.commands" in sys.modules:
        return

    infrared_protocols = types.ModuleType("infrared_protocols")
    commands_module = types.ModuleType("infrared_protocols.commands")

    class Command:
        """Minimal stand-in for infrared_protocols.commands.Command."""

        def __init__(self, *, modulation: int) -> None:
            self.modulation = modulation

    commands_module.Command = Command
    infrared_protocols.commands = commands_module
    sys.modules["infrared_protocols"] = infrared_protocols
    sys.modules["infrared_protocols.commands"] = commands_module


def _install_infrared_component_stub() -> None:
    """Stub the sibling ``homeassistant.components.infrared`` custom component."""
    if "homeassistant.components.infrared" in sys.modules:
        return

    import homeassistant.components as ha_components

    module = types.ModuleType("homeassistant.components.infrared")

    class InfraredReceivedSignal:
        def __init__(self, timings=None, modulation=None) -> None:
            self.timings = timings or []
            self.modulation = modulation

    def async_get_receivers(hass):
        return []

    def async_get_emitters(hass):
        return []

    def async_subscribe_receiver(hass, entity_id, callback_):
        def _unsubscribe() -> None:
            return None

        return _unsubscribe

    async def async_send_command(hass, entity_id, command, *, context=None):
        return None

    module.InfraredReceivedSignal = InfraredReceivedSignal
    module.async_get_receivers = async_get_receivers
    module.async_get_emitters = async_get_emitters
    module.async_subscribe_receiver = async_subscribe_receiver
    module.async_send_command = async_send_command

    sys.modules["homeassistant.components.infrared"] = module
    ha_components.infrared = module


def _install_radio_frequency_component_stub() -> None:
    """Stub the sibling ``homeassistant.components.radio_frequency`` component.

    The real API requires both frequency and modulation to look up compatible
    transmitters (``async_get_transmitters``) - there is no ``async_get_emitters``.
    """
    if "homeassistant.components.radio_frequency" in sys.modules:
        return

    import enum

    import homeassistant.components as ha_components

    module = types.ModuleType("homeassistant.components.radio_frequency")

    class ModulationType(enum.Enum):
        OOK = "ook"

    def async_get_transmitters(hass, *, frequency, modulation):
        return []

    async def async_send_command(hass, entity_id, command, *, context=None):
        return None

    module.ModulationType = ModulationType
    module.async_get_transmitters = async_get_transmitters
    module.async_send_command = async_send_command

    sys.modules["homeassistant.components.radio_frequency"] = module
    ha_components.radio_frequency = module


def _install_config_subentry_shim() -> None:
    """Add minimal subentry-flow placeholders to ``homeassistant.config_entries``.

    Config subentries require Home Assistant 2025.2+; this dev environment is
    pinned to an older release that predates the feature (see the pip-index
    caveat in the project README/test notes). ``manager.py`` only uses
    ``ConfigSubentry`` in (deferred, ``from __future__ import annotations``)
    type hints, and ``config_flow.py`` only needs ``ConfigSubentryFlow`` as a
    subclassable base and ``SubentryFlowResult`` as a type hint, so bare
    placeholders are enough to satisfy the imports and let the module-level
    helper functions in config_flow.py be tested directly (the flow *classes*
    themselves still need a real Home Assistant runtime and are out of scope
    here).
    """
    import homeassistant.config_entries as config_entries

    for name in ("ConfigSubentry", "ConfigSubentryFlow", "SubentryFlowResult"):
        if not hasattr(config_entries, name):
            setattr(config_entries, name, type(name, (), {}))


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    _install_fake_package()
    _install_infrared_protocols_stub()
    _install_infrared_component_stub()
    _install_radio_frequency_component_stub()
    _install_config_subentry_shim()
