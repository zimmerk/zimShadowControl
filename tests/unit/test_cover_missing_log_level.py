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


# ── Azimut-Rueckfall ────────────────────────────────────────────────────────
#
# ⚠️ Anders als beim fehlenden Cover ist das hier NICHT zeitabhaengig: Der
# Rueckfall greift, sobald die Sonne die Fassade streift — bei den
# Nordfassaden jeden Abend. Eine Bindung an den Anlauf haette nichts bewirkt.
# Unterschieden wird stattdessen nach Ausgang: der vorgesehene Rueckfall ist
# `debug`, sein Scheitern bleibt `warning`.

import math


def _winkel_manager(slat_width, slat_distance, elevation, azimuth, facade_azimuth):
    """Manager-Attrappe fuer _calculate_shutter_angle."""
    m = MagicMock()
    m.logger = MagicMock()
    m._facade_config.slat_width = slat_width
    m._facade_config.slat_distance = slat_distance
    m._facade_config.slat_angle_offset = 0
    m._facade_config.slat_min_angle = 0
    m._facade_config.azimuth = facade_azimuth
    m._shadow_config.shutter_max_angle = 100
    m._dynamic_config.sun_elevation = elevation
    m._dynamic_config.sun_azimuth = azimuth
    m._effective_elevation = math.degrees(
        math.atan(math.tan(math.radians(elevation)) / max(1e-9, math.cos(math.radians(abs(azimuth - facade_azimuth)))))
    )
    m._handle_shutter_angle_stepping.side_effect = lambda v: v
    m._calculate_shutter_angle = ShadowControlManager._calculate_shutter_angle.__get__(m)
    return m


def test_azimut_rueckfall_ist_nur_debug():
    """Streifende Sonne: vorgesehener Rueckfall, keine Warnung."""
    from custom_components.zimshadow.const import ShutterType

    m = _winkel_manager(95.0, 67.0, elevation=5.0, azimuth=290.0, facade_azimuth=345.0)
    m._facade_config.shutter_type = ShutterType.MODE1
    m._calculate_shutter_angle()

    meldungen = [str(c) for c in m.logger.debug.call_args_list]
    assert any("impossible geometry" in t for t in meldungen), "Rueckfall muss protokolliert werden"
    assert not any("impossible geometry" in str(c) for c in m.logger.warning.call_args_list), (
        "der vorgesehene Rueckfall darf keine Warnung sein"
    )
