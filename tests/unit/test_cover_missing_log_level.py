"""Ein fehlendes Cover: im Anlauf debug, im Betrieb warning.

⚠️ WARUM ES DIESEN TEST GIBT: Am 2026-08-13 standen 162 Zeilen
"Cover entity ... not found" im Tageslog — allesamt vom Start, weil diese
Integration eingerichtet wird, waehrend free@home seine Cover noch anlegt.
Das ist erwartbar und kein Befund. Faellt ein Cover dagegen im LAUFENDEN
Betrieb weg, ist es sehr wohl einer — und ging bisher im Startrauschen unter.

Der Test haelt genau diese Unterscheidung fest. Wer die Meldung kuenftig
pauschal herabstuft oder pauschal zurueck auf `warning` hebt, faellt hier auf.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.zimshadow import ShadowControlManager


@pytest.fixture
def manager():
    """Manager-Attrappe mit der echten Methode."""
    m = MagicMock()
    m.logger = MagicMock()
    m._target_cover_entity_id = ["cover.gibtsnochnicht"]
    m.hass.states.get.return_value = None          # Cover ist (noch) nicht da
    m._get_current_cover_position = ShadowControlManager._get_current_cover_position.__get__(m)
    return m


@pytest.mark.asyncio
async def test_anlauf_meldet_nur_debug(manager):
    """Initiallauf: erwartbar, also debug — und keine Warnung."""
    manager._is_initial_run = True
    manager._is_in_ha_restart_grace_period.return_value = False

    assert await manager._get_current_cover_position() == (0.0, 0.0)
    manager.logger.warning.assert_not_called()
    manager.logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_karenzzeit_meldet_nur_debug(manager):
    """Innerhalb der HA-Neustart-Karenzzeit ebenso."""
    manager._is_initial_run = False
    manager._is_in_ha_restart_grace_period.return_value = True

    assert await manager._get_current_cover_position() == (0.0, 0.0)
    manager.logger.warning.assert_not_called()
    manager.logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_im_betrieb_bleibt_es_eine_warnung(manager):
    """Weder Anlauf noch Karenzzeit: ein fehlendes Cover ist ein echter Befund."""
    manager._is_initial_run = False
    manager._is_in_ha_restart_grace_period.return_value = False

    assert await manager._get_current_cover_position() == (0.0, 0.0)
    manager.logger.warning.assert_called_once()
    assert "cover.gibtsnochnicht" in str(manager.logger.warning.call_args)


@pytest.mark.asyncio
async def test_gar_kein_cover_konfiguriert_bleibt_warnung(manager):
    """Eine Fassade ohne Cover ist immer ein Konfigurationsfehler, nie Anlauf."""
    manager._target_cover_entity_id = []
    manager._is_initial_run = True

    assert await manager._get_current_cover_position() == (0.0, 0.0)
    manager.logger.warning.assert_called_once()
