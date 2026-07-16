"""Regression test: Fassade faehrt beim Neustart hoch, wenn sie "in der Sonne" steht.

Live-Vorfaelle: 2026-07-10, -12, -13, -14, -16 (zuletzt: Neustart-Test fuer die
zimENERGY-Migration, 7 von 13 Jalousien-Instanzen betroffen, s. Commit-Historie
packages/shadow_control.yaml). Nur Fassaden, die zum Neustart-Zeitpunkt "in der
Sonne" stehen, sind betroffen.

Root Cause: Bei jeder frischen Manager-Konstruktion (echter Neustart, nicht nur
das Ziel-Cover wird kurz unavailable) gilt current_shutter_state=NEUTRAL,
_is_initial_run=True, _previous_shutter_height/_previous_shutter_angle=None.
_async_home_assistant_started() ruft die erste echte Neuberechnung auf, WAEHREND
_is_initial_run noch True ist (der Reset ist im Code sogar auskommentiert,
s. _position_shutter() Phase 2). Diese "stille" erste Positionierung setzt
_previous_shutter_height/_angle unconditional auf den frisch berechneten
Zielwert (z.B. 0, neutral) - VORBEI an _should_output_be_updated(). Fuer
Fassaden, die "in der Sonne" stehen, loest die State-Machine eine echte
zweite Transition/Neuberechnung aus, die dann (mit _is_initial_run=False)
tatsaechlich einen Befehl sendet - aber previous_value ist zu diesem
Zeitpunkt bereits vergiftet (nicht mehr None), sodass der fuer genau diesen
Fall gebaute previous_value=None-Safe-Boundary-Schutz in
_should_output_be_updated() nie greift.

Fix: Phase 2 seedet _previous_shutter_height/_angle beim ALLERERSTEN Aufruf
aus der REALEN physischen Cover-Position (_get_current_cover_position(),
vorher bereits vorhanden aber nie aufgerufen), statt aus dem berechneten
Ziel.

Testmethodik: Direkter Zugriff auf das Manager-Objekt (analog zur bereits
etablierten Praxis in diesem Repo, echte Methoden auf einer echten
hass/Entity-Registry aufzurufen statt komplett zu mocken, s.
test_regression_sticky_shutter_max_height.py) statt ueber die volle
Config-Entry-Reload-Choreographie zu gehen, die dieser Testsuite fehlt. Das
Auto-Lock-Setup-Artefakt (setup_instance()s eigene initiale
Enforce-Positionierung wuerde bei only_close+previous_value=None den
Safe-Boundary-Wert 100/100 senden, was mit einer danach manuell gesetzten
abweichenden physischen Startposition kollidiert und faelschlich Auto-Lock
ausloest) wird dadurch umgangen: der Manager-Restart-Zustand wird nach
normalem Setup gezielt zurueckgesetzt, GENAU wie ihn eine frische
Manager-Konstruktion vorfinden wuerde.
"""

import logging

import pytest
from homeassistant.core import HomeAssistant

from custom_components.shadow_control import ShutterState
from custom_components.shadow_control.const import DOMAIN, DOMAIN_DATA_MANAGERS
from tests.integration.conftest import (
    get_cover_position,
    set_sun_position,
    setup_instance,
)

_LOGGER = logging.getLogger(__name__)

# Facade "in der Sonne": Azimut 180, Fenster -80/+80 -> 100..260 abgedeckt.
TEST_CONFIG = {
    DOMAIN: [
        {
            "name": "SC Test Instance",
            "debug_enabled": False,
            "target_cover_entity": ["cover.sc_dummy"],
            "facade_shutter_type_static": "mode1",
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
            "facade_light_strip_width_static": 0,
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
                # Produktions-Default (s. packages/shadow_control.yaml), NICHT
                # no_restriction wie in test_ha_restart_no_movement_final.py.
                "movement_restriction_height_manual": "only_close",
                "movement_restriction_angle_manual": "only_close",
                "facade_neutral_pos_height_manual": 0,
                "facade_neutral_pos_angle_manual": 0,
                "enforce_positioning_manual": False,
                "shadow_control_enabled_manual": True,
                "shadow_brightness_threshold_winter_manual": 50000,
                "shadow_after_seconds_manual": 10,
                # Wie in Produktion: Hoehe strukturell immer unten (nur der
                # Winkel beschattet), s. packages/shadow_control.yaml.
                "shadow_shutter_max_height_manual": 0,
                "shadow_shutter_max_angle_manual": 90,
                "shadow_shutter_look_through_seconds_manual": 10,
                "shadow_shutter_open_seconds_manual": 10,
                "shadow_shutter_look_through_angle_manual": 54,
                "shadow_height_after_sun_manual": 0,
                "shadow_angle_after_sun_manual": 0,
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


async def test_no_open_movement_after_restart_while_in_sun(
    hass: HomeAssistant,
    setup_from_user_config,
    time_travel,
    caplog,
):
    """Fassade steht beim (simulierten) Neustart in der Sonne + only_close -> darf nicht aufmachen."""
    # Normales Setup ohne die eigene initiale Enforce-Positionierung (die wuerde mit
    # previous_value=None + only_close den Safe-Boundary-Wert 100/100 senden und mit der
    # unten gesetzten physischen Startposition kollidieren -> faelschliches Auto-Lock).
    pos_calls, tilt_calls = await setup_instance(caplog, hass, setup_from_user_config, TEST_CONFIG, time_travel, enforce_positioning=False)

    manager = hass.data[DOMAIN_DATA_MANAGERS]["test_entry_id"]

    # Reale physische Ausgangsposition VOR dem (simulierten) Neustart: geschlossen,
    # Lamellen in aktiver Teil-Beschattung (wie live bei wz_west vor dem Neustart).
    initial_position = 0  # HA: 0 = geschlossen
    initial_tilt = 50

    # Manager glaubt (wie nach einer normalen Betriebsphase VOR dem Neustart), genau
    # diese Position zuletzt selbst gesendet zu haben - sonst wuerde das folgende
    # hass.states.async_set() vom Cover-State-Change-Listener faelschlich als manuelle
    # Bewegung gegen die uninitialisierten 0.0/0.0-Defaults gewertet und Auto-Lock
    # ausloesen, bevor der eigentliche Testfall (der simulierte Neustart) ueberhaupt
    # beginnt. SC-Skala: height/angle = 100 - HA-Wert.
    manager._last_calculated_height = 100.0 - initial_position  # noqa: SLF001
    manager._last_calculated_angle = 100.0 - initial_tilt  # noqa: SLF001

    hass.states.async_set(
        "cover.sc_dummy",
        "closed",
        {"current_position": initial_position, "current_tilt_position": initial_tilt, "supported_features": 255},
    )
    await hass.async_block_till_done()

    # Facade "in der Sonne" (Azimut 180, Fenster 100..260), aber Helligkeit UNTER der
    # Beschattungsschwelle (wie live bei wz_west zum tatsaechlichen Neustart-Zeitpunkt: 1782 lux vs.
    # Schwellwert 21466 -- Abend, Beschattung nicht mehr aktiv). Zielposition ist damit NEUTRAL
    # (facade_neutral_pos_height/angle_manual = 0/0 SC-Skala = HA position/tilt 100/100 = offen).
    await set_sun_position(hass, elevation=45, azimuth=180, brightness=1000)
    await hass.async_block_till_done()

    # === Frische Manager-Konstruktion simulieren ===================================================
    # Exakt der Zustand, den ein echter Neustart vorfinden wuerde (s. __init__.py Zeilen
    # 875/892-893) - im Gegensatz zu einem blossen "Ziel-Cover wird kurz unavailable", das die
    # bestehende Testsuite (test_ha_restart_no_movement_final.py) abdeckt, aber den Manager selbst
    # nie neu konstruiert und deshalb diesen Bug nicht erreichen kann.
    manager.current_shutter_state = ShutterState.NEUTRAL
    manager._is_initial_run = True  # noqa: SLF001
    manager._previous_shutter_height = None  # noqa: SLF001
    manager._previous_shutter_angle = None  # noqa: SLF001
    manager._startup_restore_complete = False  # noqa: SLF001

    initial_pos_count = len(pos_calls)
    initial_tilt_count = len(tilt_calls)

    # Wie _async_home_assistant_started(): startup_restore_complete VOR der Berechnung setzen,
    # dann die erste (stille) Neuberechnung ausloesen, dann _is_initial_run zuruecksetzen - exakt
    # die im Code dokumentierte Reihenfolge (__init__.py Zeilen 1445-1459). Passiert in der Realitaet
    # kurz nach dem echten HA-Start (innerhalb der 30s-Grace-Period).
    manager._startup_restore_complete = True  # noqa: SLF001
    await manager.async_calculate_and_apply_cover_position(None)
    if manager._is_initial_run:  # noqa: SLF001
        manager._is_initial_run = False  # noqa: SLF001
    await hass.async_block_till_done()

    # Ueber die 30s HA-Restart-Grace-Period hinaus vorspulen (_is_in_ha_restart_grace_period()),
    # BEVOR die zweite, tatsaechlich sendende Neuberechnung ausgeloest wird - in Produktion ist
    # shadow_after_seconds_manual=180s, weit ausserhalb der 30s-Grace-Period, der reale Vorfall
    # passiert also regulaer erst danach.
    await time_travel(seconds=35)
    await hass.async_block_till_done()

    # Zweite Neuberechnung, jetzt mit _is_initial_run=False UND ausserhalb der Grace-Period -
    # simuliert z.B. restaurierte Config-Entities, die per externer Enforce-Entity eine sofortige
    # Neupositionierung ausloesen (__init__.py Zeile 2236-2243/4865), analog zu einem echten
    # HA-Neustart, bei dem restaurierte number/select-Entities denselben Effekt haben koennen.
    manager._enforce_position_update = True  # noqa: SLF001
    await manager.async_calculate_and_apply_cover_position(None)
    await hass.async_block_till_done()

    # Zeit vorspulen, damit der Korrektur-Timer feuert und die eigentliche (fehlerhafte)
    # Positionierung stattfindet.
    await time_travel(seconds=12)
    await hass.async_block_till_done()

    # === Assertions ================================================================================
    height_after, angle_after = get_cover_position(pos_calls, tilt_calls)
    new_pos_calls = pos_calls[initial_pos_count:]
    new_tilt_calls = tilt_calls[initial_tilt_count:]

    _LOGGER.info(
        "After simulated restart: new height commands=%s, new tilt commands=%s, last reported height=%s angle=%s",
        [c.data.get("position") for c in new_pos_calls],
        [c.data.get("tilt_position") for c in new_tilt_calls],
        height_after,
        angle_after,
    )

    for call in new_pos_calls:
        sent_position = call.data.get("position")
        assert sent_position is not None and sent_position <= initial_position, (
            f"BUG: Hoehen-Kommando nach simuliertem Neustart oeffnet die Fassade trotz only_close! "
            f"Ausgangsposition={initial_position}, gesendet={sent_position}. "
            f"Fassade war beim Neustart 'in der Sonne' -- Restart-Hochfahren-Bug (s. Modul-Docstring)."
        )

    for call in new_tilt_calls:
        sent_tilt = call.data.get("tilt_position")
        assert sent_tilt is not None and sent_tilt <= initial_tilt, (
            f"BUG: Lamellen-Kommando nach simuliertem Neustart oeffnet die Fassade trotz only_close! "
            f"Ausgangswinkel={initial_tilt}, gesendet={sent_tilt}. "
            f"Fassade war beim Neustart 'in der Sonne' -- Restart-Hochfahren-Bug (s. Modul-Docstring)."
        )

    _LOGGER.info("SUCCESS: Keine oeffnende Bewegung nach simuliertem Neustart trotz 'in der Sonne'")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
