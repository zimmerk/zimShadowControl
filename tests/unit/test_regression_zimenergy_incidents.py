"""Regression tests for the zim-ha-config "Jalousie faehrt hoch" incidents.

Background (siehe Memory shadow_control_jalousien.md, Updates 2026-07-10/12/13):
mehrfach sind Rolladen unerwartet aufgefahren, obwohl alle 13 Hausinstanzen
auf `movement_restriction_height: only_close` + `shadow_shutter_max_height: 0`
konfiguriert sind (Hoehe soll strukturell nie ueber 0 hinaus bewegt werden).

Drei unabhaengig bestaetigte Mechanismen:

1. `_should_output_be_updated()` (Zeile ~4820) gibt `new_value` unconditional
   zurueck, sobald `previous_value` `None` ist - die only_close/only_open-
   Restriktion greift dann ueberhaupt nicht. Fuer `_should_output_be_updated`
   existiert im kompletten Upstream-Testsuite (Stand f3c26ca) KEIN einziger
   Test, der previous_value=None ueberhaupt abdeckt (test_position_shutter.py
   mockt die Funktion komplett weg).

2. `_calculate_shutter_height()` faellt bei `light_strip_width == 0` auf
   `shadow_max_height_percent` zurueck - dieser Wert kommt aus einer
   `number.*`-HA-Entity und wird bei Unavailability (z.B. waehrend eines
   Config-Entry-Reloads, bevor die number-Plattform fertig restauriert hat)
   durch den hartkodierten Upstream-Default SCDefaults.SHADOW_SHUTTER_MAX_HEIGHT_VALUE
   (=100) ersetzt - unabhaengig vom real konfigurierten Wert (in diesem Haus: 0).

3. NEU (gefixt 2026-07-23): `_position_shutter()`s Phase-2.5-Startup-Guard
   (`if not self._startup_restore_complete: ...`) hat previous_value=None
   *bereits gekannten* previous_value nicht geholfen - er hat physischen
   Output waehrend eines Config-Entry-Reloads bislang UNBEDINGT durchgelassen
   (`and not self.hass.is_running` als Ausnahme), sobald `hass.is_running`
   True war - was bei jedem Reload sofort der Fall ist. Der von
   `_async_register_listeners()` beim Reload sofort geschedulte Task
   (`_async_home_assistant_started(None)`) setzte `_startup_restore_complete`
   dabei VOR der eigenen Neuberechnung auf True, oft bevor
   `async_forward_entry_setups()` in `async_setup_entry()` ueberhaupt
   zurueckgekehrt war - also bevor die eigenen number/switch/select-Entities
   (z.B. `shadow_shutter_max_height_manual`) ueberhaupt lesbar waren. Traf
   dieses Zeitfenster mit Mechanismus #2 zusammen (Entity liest noch den
   hartkodierten Default 100 statt der echten 0), war `previous_value` (echte
   physische Position, hier: 0) bereits BEKANNT - only_close haette also
   theoretisch greifen sollen, tat es aber nicht: `100 > 0` erfuellt
   only_close's Ratchet-Bedingung (`new_value > previous_value`), da only_close
   laut Konvention "groesserer Wert = geschlossener" bedeutet, hier aber ein
   falscher, zu grosser Defaultwert als "mehr geschlossen" fehlinterpretiert
   wird. Der Guard ist jetzt unconditional (kein `hass.is_running`-Ausnahme
   mehr); `_startup_restore_complete` wird beim Reload stattdessen erst in
   `async_setup_entry()` gesetzt, NACHDEM `async_forward_entry_setups()`
   zurueckgekehrt ist (s. test_position_shutter.py::
   test_blocks_physical_output_during_reload_even_if_ha_running sowie die
   TestReloadRaceStartupRestoreComplete-Klasse unten).

WICHTIG zur vorherigen Analyse-Korrektur: "Enforce Positioning umgeht
only_close" ist als eigenstaendige Behauptung UNGENAU. _position_shutter()
ruft _should_output_be_updated() VOR dem Enforce-Override auf; self.used_shutter_height
ist bereits der (ggf. only_close-geschuetzte) Rueckgabewert. Enforce
erzwingt nur send_height_command=True (SENDEN), nicht einen anderen WERT.
Der tatsaechliche Bypass passiert nur, wenn _should_output_be_updated()
selbst schon einen ungeschuetzten Wert zurueckgibt - genau der Fall #1 oben.
test_regression_enforce_after_stale_tracking_bypasses_only_close unten
reproduziert das kombinierte Verhalten, das vermutlich den West-Vorfall
vom 2026-07-13 erklaert: ein Config-Entry-Reload/Reaktivierung, bei der
SC's eigenes previous_shutter_height (noch) None ist, kombiniert mit dem
Enforce-Positioning-Button.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.cover import CoverEntityFeature

from custom_components.shadow_control import ShadowControlManager
from custom_components.shadow_control.const import LockState, MovementRestricted, ShutterType


@pytest.mark.asyncio
class TestOnlyCloseBypassedWhenPreviousValueUnknown:
    """_should_output_be_updated(): previous_value=None schaltet only_close/only_open komplett ab."""

    @pytest.fixture
    def manager(self):
        instance = MagicMock()
        instance._should_output_be_updated = ShadowControlManager._should_output_be_updated.__get__(instance)
        return instance

    async def test_only_close_blocks_opening_when_previous_is_known(self, manager):
        """Sanity check: mit bekanntem previous_value blockt only_close korrekt eine Oeffnung."""
        result = manager._should_output_be_updated(
            config_value=MovementRestricted.ONLY_CLOSE,
            new_value=0.0,  # wuerde die Jalousie oeffnen
            previous_value=100.0,  # aktuell voll geschlossen
        )
        assert result == 100.0, "only_close muss die Jalousie geschlossen halten"

    async def test_only_close_bypassed_when_previous_value_is_none(self, manager):
        """BUG: mit unbekanntem (None) previous_value - z.B. direkt nach Manager-Konstruktion,
        bevor die reale Position getrackt wurde - wird only_close stillschweigend umgangen
        und die Jalousie kann geoeffnet werden."""
        result = manager._should_output_be_updated(
            config_value=MovementRestricted.ONLY_CLOSE,
            new_value=0.0,  # wuerde die Jalousie oeffnen
            previous_value=None,
        )
        assert result != 0.0, (
            "only_close darf nicht umgangen werden, nur weil die vorherige Position noch "
            "nicht getrackt ist - genau dieses Fenster tritt bei jedem Reload/Reaktivierung auf, "
            "bevor der Manager seine erste echte Position kennt."
        )

    async def test_only_open_bypassed_when_previous_value_is_none(self, manager):
        """Gleicher Bug spiegelverkehrt fuer only_open (Vollstaendigkeit)."""
        result = manager._should_output_be_updated(
            config_value=MovementRestricted.ONLY_OPEN,
            new_value=100.0,  # wuerde die Jalousie schliessen
            previous_value=None,
        )
        assert result != 100.0, "only_open darf nicht umgangen werden, nur weil previous_value None ist"


@pytest.mark.asyncio
class TestUnavailableConfigEntityProducesSilentlyWrongHeight:
    """_calculate_shutter_height(): reine Berechnungsfunktion, die _shadow_config.shutter_max_height
    unveraendert durchreicht, wenn light_strip_width==0 ist (Fassaden-Geometrie aller betroffenen
    Hausinstanzen). Der eigentliche "unavailable-Entity wird durch hartkodierten Upstream-Default
    (100) ersetzt"-Bugfix sitzt NICHT hier, sondern in der Zuweisungslogik in _update_input_values(),
    die von keinem Unit-Test aufgerufen wird (ueberall via AsyncMock() weggemockt). Der End-to-End-
    Regressionstest dafuer lebt deshalb auf Integrationsebene:
    tests/integration/test_regression_sticky_shutter_max_height.py::test_sticky_default_survives_entity_unavailable
    Diese Klasse behaelt nur den Sanity-Check der puren Berechnung."""

    @pytest.fixture
    def manager(self, mock_manager):
        manager = mock_manager
        manager._calculate_shutter_height = ShadowControlManager._calculate_shutter_height.__get__(manager)
        manager._handle_shutter_height_stepping = MagicMock(side_effect=lambda x: x)

        # Fassaden-Geometrie wie in den betroffenen Hausinstanzen: light_strip_width
        # kommt bei diesem Sonnenwinkel/dieser Fassade auf 0 - der width==0-Zweig wird
        # unconditional genommen, unabhaengig davon ob shutter_max_height echt oder
        # defaulted ist.
        manager._facade_config.light_strip_width = 0
        manager._facade_config.shutter_height = 200.0
        manager._dynamic_config.sun_elevation = 45.0
        return manager

    async def test_real_configured_value_is_used_when_available(self, manager):
        """Sanity check: mit verfuegbarer Entity wird der echte konfigurierte Wert (hier: 0,
        wie in allen 13 Hausinstanzen per only_close-Philosophie) korrekt durchgereicht."""
        manager._shadow_config.shutter_max_height = 0.0  # echter Wert dieses Hauses

        result = manager._calculate_shutter_height()

        assert result == 0.0


@pytest.mark.asyncio
class TestEnforcePositioningAfterStaleTracking:
    """_position_shutter(): kombiniert Mechanismus 1 (previous_value=None) mit dem
    Enforce-Positioning-Button - die vermutete Erklaerung fuer den West-Vorfall vom
    2026-07-13 (Positioniere-Button gedrueckt kurz nach einer Reaktivierung, bevor
    der Manager seine erste echte Position getrackt hatte)."""

    @pytest.fixture
    def manager(self):
        instance = MagicMock(spec=ShadowControlManager)
        instance.logger = MagicMock()
        instance.hass = MagicMock()
        instance.name = "Test Manager"
        instance._config = MagicMock()
        instance._dynamic_config = MagicMock()
        instance._facade_config = MagicMock()

        instance._is_initial_run = False
        instance._startup_restore_complete = True
        instance.current_lock_state = LockState.UNLOCKED
        instance._target_cover_entity_id = ["cover.test"]

        instance._facade_config.shutter_type = ShutterType.MODE1

        # Kernszenario: SC hat NOCH KEINE reale Position getrackt (frischer Manager
        # nach Reload/Reaktivierung), aber die physische Jalousie ist tatsaechlich
        # bereits geschlossen (z.B. durch die Normalzustand-Szene).
        instance._previous_shutter_height = None
        instance._previous_shutter_angle = None

        instance._dynamic_config.movement_restriction_height = MovementRestricted.ONLY_CLOSE
        instance._dynamic_config.movement_restriction_angle = MovementRestricted.NO_RESTRICTION

        instance._should_output_be_updated = ShadowControlManager._should_output_be_updated.__get__(instance)

        instance._timer = None
        instance._last_positioning_time = None
        instance._last_calculated_height = 0.0
        instance._last_calculated_angle = 0.0
        instance._last_unlock_time = None
        instance._last_reported_height = None
        instance._last_reported_angle = None

        instance._cancel_timer = MagicMock()
        instance._update_extra_state_attributes = MagicMock()
        instance._convert_shutter_angle_percent_to_degrees = MagicMock(return_value=0.0)

        instance.hass.states.get = MagicMock(
            return_value=MagicMock(
                attributes={"supported_features": (CoverEntityFeature.SET_POSITION | CoverEntityFeature.SET_TILT_POSITION)}
            )
        )
        instance.hass.is_running = True
        instance.hass.services.has_service = MagicMock(return_value=True)

        async def mock_async_call(domain, service, service_data, blocking=False):
            return

        instance.hass.services.async_call = AsyncMock(side_effect=mock_async_call)

        instance._position_shutter = ShadowControlManager._position_shutter.__get__(instance)

        return instance

    async def test_enforce_sends_opening_command_despite_only_close(self, manager):
        """BUG: 'Positioniere' (enforce_positioning_manual) presst 20s nach einer
        Reaktivierung - der Manager kennt seine reale Vorposition noch nicht
        (previous_shutter_height=None). only_close ist konfiguriert, die Jalousie
        soll strukturell nie ueber Hoehe 0 hinausgehen. Trotzdem wird ein
        set_cover_position-Befehl gesendet, der die Jalousie oeffnet (Cover-Skala:
        100 - height)."""
        manager._enforce_position_update = True

        # Frische Berechnung ergibt height=0 (SC-Skala) - strukturell korrekt fuer
        # dieses Haus (b06_max_hohe=0 ueberall), aber unrestricted gesendet, weil
        # previous_value=None die only_close-Pruefung umgeht.
        await manager._position_shutter(0.0, 0.0, stop_timer=False)

        position_calls = [c for c in manager.hass.services.async_call.call_args_list if c[0][1] == "set_cover_position"]
        assert position_calls, "Es wurde ueberhaupt kein set_cover_position-Befehl gesendet - Testaufbau pruefen"

        sent_position = position_calls[0][0][2]["position"]
        assert sent_position != 100, (
            f"Enforce Positioning hat Cover-Position {sent_position} (=offen) gesendet, obwohl "
            f"only_close konfiguriert ist. Ursache: previous_shutter_height war None (Manager "
            f"kennt seine reale Vorposition nach Reload/Reaktivierung noch nicht), wodurch "
            f"_should_output_be_updated() die only_close-Restriktion nicht anwenden konnte. "
            f"Reproduziert den West-Vorfall vom 2026-07-13."
        )


@pytest.mark.asyncio
class TestReloadRaceStartupRestoreComplete:
    """Mechanismus #3 (s. Modul-Docstring): _async_home_assistant_started() darf beim
    Config-Entry-Reload NICHT mehr selbst `_startup_restore_complete` setzen, bevor
    async_forward_entry_setups() zurueckgekehrt ist. Diese Klasse testet direkt den
    genau von _async_register_listeners() beim Reload geschedulten Aufruf
    (`_async_home_assistant_started(None, mark_complete=False)`) end-to-end gegen die
    echte _position_shutter()-Methode - inklusive der exakten Symptomatik (Hoehen-Befehl
    wird/wird nicht gesendet), nicht nur gegen die interne Flag-Buchhaltung isoliert."""

    @pytest.fixture
    def manager(self):
        instance = MagicMock(spec=ShadowControlManager)
        instance.logger = MagicMock()
        instance.hass = MagicMock()
        instance.name = "Test Manager"
        instance._config = MagicMock()
        instance._dynamic_config = MagicMock()
        instance._facade_config = MagicMock()

        instance._is_initial_run = False  # async_start() has already flipped this by the time
        # the reload-scheduled task actually runs, exactly as in production (s. Diagnose Punkt 2/3).
        instance._startup_restore_complete = False  # not yet set - forward_entry_setups() has not returned yet
        instance.current_lock_state = LockState.UNLOCKED
        instance._target_cover_entity_id = ["cover.test"]
        instance._enforce_position_update = False

        instance._facade_config.shutter_type = ShutterType.MODE1

        # Reale physische Vorposition bereits bekannt (durch den allerersten, durch Phase 2
        # geschuetzten Initial-Run-Aufruf in async_start() seeded) - only_close/nur previous_value
        # ist hier explizit NICHT None, um Mechanismus #1 (previous_value=None) sauber von
        # Mechanismus #3 (hass.is_running-Ausnahme) zu isolieren.
        instance._previous_shutter_height = 0.0
        instance._previous_shutter_angle = 0.0

        instance._timer = None
        instance._last_positioning_time = None
        instance._last_calculated_height = 0.0
        instance._last_calculated_angle = 0.0
        instance._last_unlock_time = None
        instance._last_reported_height = None
        instance._last_reported_angle = None

        instance._cancel_timer = MagicMock()
        instance._update_extra_state_attributes = MagicMock()
        instance._convert_shutter_angle_percent_to_degrees = MagicMock(return_value=0.0)

        instance._dynamic_config.movement_restriction_height = MovementRestricted.ONLY_CLOSE
        instance._dynamic_config.movement_restriction_angle = MovementRestricted.NO_RESTRICTION
        instance._should_output_be_updated = ShadowControlManager._should_output_be_updated.__get__(instance)

        instance.hass.states.get = MagicMock(
            return_value=MagicMock(
                attributes={"supported_features": (CoverEntityFeature.SET_POSITION | CoverEntityFeature.SET_TILT_POSITION)}
            )
        )
        instance.hass.is_running = True  # reload: HA itself is already fully running
        instance.hass.services.has_service = MagicMock(return_value=True)

        async def mock_async_call(domain, service, service_data, blocking=False):
            return

        instance.hass.services.async_call = AsyncMock(side_effect=mock_async_call)

        instance._position_shutter = ShadowControlManager._position_shutter.__get__(instance)
        instance._async_home_assistant_started = ShadowControlManager._async_home_assistant_started.__get__(instance)

        # Simulates _calculate_shutter_height() reading the manager's OWN config number entity
        # (shadow_shutter_max_height_manual) while it's still unavailable/defaulted right after
        # a reload, before async_forward_entry_setups() has restored it - the hardcoded upstream
        # default SHADOW_SHUTTER_MAX_HEIGHT_VALUE=100 flows in, instead of the real, configured 0.
        async def defaulted_calc(event):
            await instance._position_shutter(100.0, 0.0, stop_timer=True)

        instance.async_calculate_and_apply_cover_position = AsyncMock(side_effect=defaulted_calc)

        return instance

    async def test_reload_scheduled_task_does_not_mark_restore_complete_itself(self, manager):
        """Kern-Regressionstest fuer Mechanismus #3: der beim Reload sofort geschedulte Task
        (mark_complete=False) darf _startup_restore_complete NICHT selbst setzen - das muss
        erst async_setup_entry() tun, NACHDEM async_forward_entry_setups() zurueckgekehrt ist."""
        assert manager._startup_restore_complete is False

        await manager._async_home_assistant_started(None, mark_complete=False)

        assert manager._startup_restore_complete is False, (
            "_async_home_assistant_started(mark_complete=False) hat _startup_restore_complete "
            "trotzdem gesetzt - genau das war der Reload-Race-Bug: die eigenen Config-Entities "
            "sind zu diesem Zeitpunkt (async_forward_entry_setups() noch nicht zurueckgekehrt) "
            "moeglicherweise noch nicht lesbar."
        )

    async def test_reload_scheduled_task_produces_no_physical_output_before_platforms_loaded(self, manager):
        """End-to-end-Symptom-Check: der Task ruft (wie in Produktion via
        async_calculate_and_apply_cover_position -> ... -> _position_shutter) mit dem
        hartkodiert-defaulteten Zielwert (100, statt real konfigurierter 0) auf. Trotz bekanntem
        previous_value=0.0 UND konfiguriertem only_close darf KEIN Hoehen-Befehl gesendet werden,
        weil _startup_restore_complete noch False ist - das ist die eigentliche Absicherung
        gegen genau dieses Zeitfenster."""
        await manager._async_home_assistant_started(None, mark_complete=False)

        manager.hass.services.async_call.assert_not_called()
        assert manager.calculated_shutter_height == 100.0, (
            "Sanity check: der (fehlerhaft defaultete) Zielwert sollte intern zwar berechnet/"
            "getrackt, aber NICHT physisch ausgegeben werden."
        )

    async def test_real_cold_boot_path_still_marks_restore_complete_and_positions(self, manager):
        """Positiv-Kontrolle (darf durch den Fix NICHT kaputtgehen): der echte, ueber
        EVENT_HOMEASSISTANT_STARTED getriggerte Kaltstart-Pfad ruft OHNE mark_complete=False auf
        (Default bleibt True) und muss weiterhin ganz normal positionieren."""
        manager._startup_restore_complete = False

        # Kaltstart: previous_value bereits 0.0 (Initial-Run-Seed) und der jetzt tatsaechlich
        # korrekt gelesene Zielwert (hier simuliert: 0.0, wie in allen 13 Hausinstanzen).
        async def real_calc(event):
            await manager._position_shutter(0.0, 0.0, stop_timer=True)

        manager.async_calculate_and_apply_cover_position = AsyncMock(side_effect=real_calc)

        await manager._async_home_assistant_started(None)  # mark_complete default = True

        assert manager._startup_restore_complete is True
        manager._update_extra_state_attributes.assert_called()


@pytest.mark.asyncio
class TestMovementRestrictionStickyFallback:
    """_handle_movement_restriction(): Fallback bei unavailable/unknown-Entity muss - analog zum
    bestehenden shutter_max_height-Fix (f6c68c6) - der Manager-eigene, zuletzt bekannte Wert
    sein, NICHT der hartkodierte NO_RESTRICTION-Default. NO_RESTRICTION deaktiviert die
    Bewegungsrestriktion (z.B. only_close) komplett - genau in dem Zeitfenster (Entity
    unavailable, z.B. waehrend eines Config-Entry-Reloads, bevor die eigene select-Plattform
    fertig restauriert hat), in dem die Absicherung am wichtigsten ist."""

    @pytest.fixture
    def manager(self):
        instance = MagicMock()
        instance.logger = MagicMock()
        instance.hass = MagicMock()
        instance._config = MagicMock()
        instance._dynamic_config = MagicMock()

        # Kein externes Entity konfiguriert -> interner Entity-Pfad wird genommen (Standardfall
        # aller 13 Hausinstanzen).
        instance._config.get = MagicMock(return_value=None)
        instance.get_internal_entity_id = MagicMock(
            side_effect=lambda internal_enum: f"select.{internal_enum.value}"
        )

        instance._get_movement_restricted_from_state = ShadowControlManager._get_movement_restricted_from_state.__get__(instance)
        instance._handle_movement_restriction = ShadowControlManager._handle_movement_restriction.__get__(instance)

        return instance

    async def test_height_restriction_stays_sticky_when_entity_unavailable(self, manager):
        """Manager hat only_close bereits einmal real gelesen (Sticky-Ausgangswert) - die Entity
        wird danach unavailable (Reload-Race) - only_close darf NICHT auf NO_RESTRICTION
        zurueckfallen."""
        manager._dynamic_config.movement_restriction_height = MovementRestricted.ONLY_CLOSE
        manager.hass.states.get = MagicMock(return_value=MagicMock(state="unavailable"))

        manager._handle_movement_restriction()

        assert manager._dynamic_config.movement_restriction_height == MovementRestricted.ONLY_CLOSE, (
            "Movement restriction height ist trotz vorher bekanntem only_close auf "
            "NO_RESTRICTION zurueckgefallen, nur weil die Entity gerade unavailable ist - "
            "das deaktiviert die Bewegungsrestriktion genau im gefaehrlichsten Zeitfenster "
            "(Config-Entry-Reload-Race)."
        )

    async def test_angle_restriction_stays_sticky_when_entity_unknown(self, manager):
        """Gleicher Mechanismus fuer die Winkel-Restriktion, Zustand 'unknown' statt
        'unavailable' (beide werden identisch behandelt)."""
        manager._dynamic_config.movement_restriction_angle = MovementRestricted.ONLY_OPEN
        manager.hass.states.get = MagicMock(return_value=MagicMock(state="unknown"))

        manager._handle_movement_restriction()

        assert manager._dynamic_config.movement_restriction_angle == MovementRestricted.ONLY_OPEN

    async def test_height_restriction_stays_sticky_when_entity_id_not_resolvable(self, manager):
        """Randfall: get_internal_entity_id() liefert (noch) gar keine Entity-ID (z.B. Registry
        noch nicht bereit) - auch hier muss der zuletzt bekannte Wert erhalten bleiben statt
        auf NO_RESTRICTION zurueckzufallen."""
        manager._dynamic_config.movement_restriction_height = MovementRestricted.ONLY_CLOSE
        manager.get_internal_entity_id = MagicMock(return_value=None)

        manager._handle_movement_restriction()

        assert manager._dynamic_config.movement_restriction_height == MovementRestricted.ONLY_CLOSE

    async def test_fresh_manager_still_defaults_to_no_restriction(self, manager):
        """Sanity check (spiegelt f6c68c6's 'no-op auf frischem Manager'): wenn noch NIE ein
        echter Wert gelesen wurde (DynamicConfig-Default), bleibt der sticky Fallback bei
        NO_RESTRICTION - das Verhalten fuer einen wirklich frischen Manager aendert sich also
        nicht."""
        manager._dynamic_config.movement_restriction_height = MovementRestricted.NO_RESTRICTION
        manager.hass.states.get = MagicMock(return_value=MagicMock(state="unavailable"))

        manager._handle_movement_restriction()

        assert manager._dynamic_config.movement_restriction_height == MovementRestricted.NO_RESTRICTION

    async def test_real_available_value_is_still_used_normally(self, manager):
        """Sanity check: mit verfuegbarer Entity wird weiterhin ganz normal der echte Zustand
        gelesen (kein Sticky-Verhalten faelschlich erzwungen)."""
        manager._dynamic_config.movement_restriction_height = MovementRestricted.NO_RESTRICTION
        manager.hass.states.get = MagicMock(return_value=MagicMock(state="only_close"))

        manager._handle_movement_restriction()

        assert manager._dynamic_config.movement_restriction_height == MovementRestricted.ONLY_CLOSE
