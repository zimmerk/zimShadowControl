"""Integration regression test: shadow_shutter_max_height 'sticky default' on entity unavailability.

Hintergrund (siehe tests/unit/test_regression_zimenergy_incidents.py fuer den vollen Kontext und
Memory shadow_control_jalousien.md): _calculate_shutter_height() gibt bei light_strip_width==0
_shadow_config.shutter_max_height direkt zurueck. Dieser Wert wurde in _update_input_values()
bislang bei unavailable werdender shadow_shutter_max_height_manual-Entity (z.B. waehrend eines
Config-Entry-Reload-Race) durch den hartkodierten Upstream-Default
SCDefaults.SHADOW_SHUTTER_MAX_HEIGHT_VALUE (=100) ersetzt - unabhaengig vom real konfigurierten
Wert (in diesem Haus: 0).

Der urspruengliche Regressionstest dafuer testete versehentlich die falsche Ebene: er injizierte
den (bereits falschen) Wert direkt in _shadow_config.shutter_max_height und rief die PURE
_calculate_shutter_height() auf. Der eigentliche Fix liegt aber in der Zuweisungslogik in
_update_input_values() - die von keinem existierenden Unit-Test ueberhaupt aufgerufen wird (alle
mocken sie komplett via AsyncMock() weg). Dieser Test arbeitet deshalb auf Integrationsebene mit
echter hass/Entity-Registry, um den tatsaechlich reparierten Code-Pfad zu treffen.
"""

import logging

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from custom_components.shadow_control import ShutterState
from custom_components.shadow_control.const import DOMAIN, SCInternal
from tests.integration.conftest import (
    get_cover_position,
    get_internal_entity_id_for_test,
    set_internal_entity,
    set_sun_position,
    setup_instance,
    time_travel_and_check,
)

_LOGGER = logging.getLogger(__name__)

# Wie test_shutter_automation_01.TEST_CONFIG (sun_elevation/azimuth_entity zeigen bewusst auf die
# input_number-Helper, NICHT auf sun.sun - sun.sun ist die echte, unmockbare Astronomie-Entity und
# folgt in Tests der tatsaechlichen Systemzeit, nicht set_sun_position()), aber mit
# shadow_shutter_max_height_manual=0 - der realen Konfiguration aller 13 Hausinstanzen
# (only_close-Philosophie: Hoehe soll strukturell nie ueber 0 hinaus bewegt werden).
# movement_restriction_height ist bewusst "no_restriction", damit dieser Test ausschliesslich den
# Sticky-Default-Mechanismus prueft, unvermischt mit dem separaten only_close/previous_value=None-Fix
# aus test_regression_zimenergy_incidents.py.
TEST_CONFIG = {
    DOMAIN: [
        {
            "name": "SC Test Instance",
            "debug_enabled": False,
            "target_cover_entity": ["cover.sc_dummy"],
            "facade_shutter_type_static": "mode1",
            #
            # Dynamic configuration inputs
            "brightness_entity": "input_number.d01_brightness",
            "sun_elevation_entity": "input_number.d03_sun_elevation",
            "sun_azimuth_entity": "input_number.d04_sun_azimuth",
            "facade_azimuth_static": 180,
            "facade_offset_sun_in_static": -80,
            "facade_offset_sun_out_static": 80,
            "facade_elevation_sun_min_static": 10,
            "facade_elevation_sun_max_static": 80,
            "facade_slat_width_static": 60,
            "facade_slat_distance_static": 50,
            "facade_slat_angle_offset_static": 0,
            "facade_slat_min_angle_static": 0,
            "facade_shutter_stepping_height_static": 5,
            "facade_shutter_stepping_angle_static": 5,
            "facade_light_strip_width_static": 0,  # -> _calculate_shutter_height() gibt shutter_max_height direkt zurueck
            "facade_shutter_height_static": 1000,
            "facade_max_movement_duration_static": 3,
            "facade_modification_tolerance_height_static": 3,
            "facade_modification_tolerance_angle_static": 3,
            "sc_internal_values": {
                "unlock_integration_manual": False,
                "lock_integration_manual": False,
                "lock_integration_with_position_manual": False,
                "lock_height_manual": 50,
                "lock_angle_manual": 50,
                "movement_restriction_height_manual": "no_restriction",
                "movement_restriction_angle_manual": "no_restriction",
                "facade_neutral_pos_height_manual": 0,
                "facade_neutral_pos_angle_manual": 0,
                "enforce_positioning_manual": False,
                #
                # Shadow configuration
                "shadow_control_enabled_manual": True,
                "shadow_brightness_threshold_winter_manual": 50000,
                "shadow_after_seconds_manual": 10,
                "shadow_shutter_max_height_manual": 0,  # echte Haus-Konfiguration (nicht der Upstream-Default 100!)
                "shadow_shutter_max_angle_manual": 90,
                "shadow_shutter_look_through_seconds_manual": 10,
                "shadow_shutter_open_seconds_manual": 10,
                "shadow_shutter_look_through_angle_manual": 54,
                "shadow_height_after_sun_manual": 0,
                "shadow_angle_after_sun_manual": 0,
                #
                # Dawn configuration
                "dawn_control_enabled_manual": False,
                "dawn_brightness_threshold_manual": 500,
                "dawn_after_seconds_manual": 10,
                "dawn_shutter_max_height_manual": 100,
                "dawn_shutter_max_angle_manual": 100,
                "dawn_shutter_look_through_seconds_manual": 10,
                "dawn_shutter_open_seconds_manual": 10,
                "dawn_shutter_look_through_angle_manual": 45,
                "dawn_height_after_dawn_manual": 0,
                "dawn_angle_after_dawn_manual": 0,
            },
        }
    ]
}


async def test_sticky_default_survives_entity_unavailable(
    hass: HomeAssistant,
    setup_from_user_config,
    time_travel,
    caplog,
):
    """Nach mindestens einem erfolgreichen Read soll ein spaeter unavailable werdendes
    shadow_shutter_max_height_manual NICHT auf den hartkodierten Upstream-Default (100)
    zurueckfallen, sondern den zuletzt bekannten echten Wert (hier: 0) behalten."""
    pos_calls, tilt_calls = await setup_instance(caplog, hass, setup_from_user_config, TEST_CONFIG, time_travel)

    # Schatten-Bedingungen erfuellen, damit tatsaechlich eine Hoehenberechnung stattfindet.
    # Gleiche Parameter + Timing wie test_shutter_automation_01.py::test_shadow_full_closed, dort
    # erwiesenermassen ausreichend um zuverlaessig SHADOW_FULL_CLOSED zu erreichen (12x2s = 24s,
    # mehr als shadow_after_seconds=10 fuer den Timer-Uebergang).
    await set_sun_position(hass, elevation=60, azimuth=180, brightness=70000)
    state = await time_travel_and_check(
        time_travel, hass, "sensor.sc_test_instance_state", seconds=2, executions=12, pos_calls=pos_calls, tilt_calls=tilt_calls
    )
    assert state.state == ShutterState.SHADOW_FULL_CLOSED.name.lower(), (
        f"Schattensteuerung hat SHADOW_FULL_CLOSED nicht erreicht (state={state.state}) - "
        f"Testaufbau pruefen, bevor der eigentliche Sticky-Default-Mechanismus getestet werden kann"
    )

    assert pos_calls, "Keine Positionierung ausgeloest - Testaufbau pruefen"
    height_with_real_value, _ = get_cover_position(pos_calls, tilt_calls)
    _LOGGER.info("Gesendete Hoehe mit verfuegbarer Entity: %s", height_with_real_value)

    # shadow_shutter_max_height_manual-Entity unavailable machen (simuliert die Config-Entry-
    # Reload-Race aus dem West-Vorfall vom 2026-07-13)
    entity_id = get_internal_entity_id_for_test(hass, "sc_test_instance", SCInternal.SHADOW_SHUTTER_MAX_HEIGHT_MANUAL)
    hass.states.async_set(entity_id, STATE_UNAVAILABLE, {})
    await hass.async_block_till_done()

    # Frische Berechnung+Sendung erzwingen (wie im Produktiv-Flow ueber den "Positioniere"-Button).
    # Enforce Positioning setzt send_height_command unconditional auf True, unabhaengig davon ob
    # sich der berechnete Wert ueberhaupt geaendert hat (__init__.py, Kommentar "Enforcing position
    # update") - genau deshalb ist dieser Ablauf geeignet, auch ein *unveraendertes* Ergebnis
    # (der korrekt reparierte Fall) sichtbar zu machen.
    initial_pos_count = len(pos_calls)
    await set_internal_entity(hass, "sc_test_instance", SCInternal.ENFORCE_POSITIONING_MANUAL)
    state = await time_travel_and_check(
        time_travel, hass, "sensor.sc_test_instance_state", seconds=2, executions=5, pos_calls=pos_calls, tilt_calls=tilt_calls
    )
    assert state.state == ShutterState.SHADOW_FULL_CLOSED.name.lower(), (
        f"Instanz hat SHADOW_FULL_CLOSED waehrend des unavailable-Fensters verlassen (state={state.state}) - "
        f"der Vergleich der gesendeten Hoehe waere dadurch nicht mehr aussagekraeftig"
    )

    assert len(pos_calls) > initial_pos_count, "Enforce Positioning hat nach dem unavailable-Werden keinen neuen Befehl ausgeloest"
    height_after_unavailable, _ = get_cover_position(pos_calls, tilt_calls)

    assert height_after_unavailable == height_with_real_value, (
        f"Nach unavailable-Werden der shadow_shutter_max_height-Entity hat sich die gesendete Hoehe von "
        f"{height_with_real_value}% auf {height_after_unavailable}% geaendert. Erwartet: der zuletzt bekannte "
        f"echte Wert (SC-Hoehe 0, Cover-Position 100) bleibt 'sticky'. Ein Sprung auf Cover-Position 0 "
        f"(entspricht SC-Hoehe 100, dem hartkodierten Upstream-Default) zeigt, dass der Fix in "
        f"_update_input_values() nicht (mehr) greift."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
