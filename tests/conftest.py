"""Root conftest für alle Tests."""

import logging

import pytest

# Muss im top-level conftest stehen (neuere pytest-Versionen lehnen
# pytest_plugins in verschachtelten conftest.py-Dateien ab).
pytest_plugins = "pytest_homeassistant_custom_component"

# Konfiguriere Logging beim Import
logging.getLogger("homeassistant").setLevel(logging.WARNING)
logging.getLogger("homeassistant.core").setLevel(logging.ERROR)
logging.getLogger("homeassistant.helpers").setLevel(logging.ERROR)
logging.getLogger("homeassistant.loader").setLevel(logging.ERROR)
logging.getLogger("homeassistant.setup").setLevel(logging.WARNING)
logging.getLogger("homeassistant.components").setLevel(logging.ERROR)

# Asyncio Logs ausschalten
logging.getLogger("asyncio").setLevel(logging.ERROR)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests automatically."""
    return


@pytest.fixture(autouse=True)
def expected_lingering_tasks() -> bool:
    """Allow lingering tasks in tests."""
    return True


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow lingering timers in tests."""
    return True


@pytest.fixture(autouse=True)
def configure_test_logging(caplog):
    """Configure logging for tests."""
    # Nur Shadow Control auf DEBUG
    caplog.set_level(logging.DEBUG, logger="custom_components.shadow_control")

    # Test Logger auf INFO
    caplog.set_level(logging.INFO, logger="tests")

    # HA auf WARNING
    caplog.set_level(logging.WARNING, logger="homeassistant")
