"""Tests for async_calculate_and_apply_cover_position method."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event
from homeassistant.util import dt as dt_util

from custom_components.shadow_control import ShadowControlManager
from custom_components.shadow_control.const import (
    SCDawnInput,
    SCDynamicInput,
    SCShadowInput,
    ShutterState,
    ShutterType,
)


class TestAsyncCalculateAndApplyCoverPosition:
    """Test async_calculate_and_apply_cover_position method."""

    @pytest.fixture
    def manager(self):
        """Create a mock ShadowControlManager instance."""
        # Mock the instance with required attributes
        instance = MagicMock(spec=ShadowControlManager)
        instance.logger = MagicMock()
        instance.hass = MagicMock()
        instance._config = MagicMock()
        instance._dynamic_config = MagicMock()
        instance._facade_config = MagicMock()

        # Set default values for lock states
        instance._dynamic_config.lock_integration = False
        instance._dynamic_config.lock_integration_with_position = False

        # Mock internal state variables
        instance._enforce_position_update = False
        instance._previous_shutter_height = 50.0
        instance._previous_shutter_angle = 45.0
        instance._height_during_lock_state = 50.0
        instance._angle_during_lock_state = 45.0

        # Mock methods that are called by async_calculate_and_apply_cover_position
        instance._update_input_values = AsyncMock()
        instance.get_internal_entity_id = MagicMock(return_value="test.entity")
        instance._check_if_facade_is_in_sun = AsyncMock(return_value=True)
        instance._shadow_handling_was_disabled = AsyncMock()
        instance._dawn_handling_was_disabled = AsyncMock()
        instance._force_immediate_positioning = AsyncMock()
        instance._process_shutter_state = AsyncMock()
        instance._handle_external_enforce_trigger = AsyncMock()
        instance._calculate_shutter_height = MagicMock(return_value=50.0)
        instance._calculate_shutter_angle = MagicMock(return_value=45.0)

        instance._last_unlock_time = None

        # Shadow-control enabled/disabled transition tracking (see
        # async_calculate_and_apply_cover_position). Default to "already enabled, no
        # change" so existing tests are unaffected unless a test explicitly changes it.
        instance._shadow_config = MagicMock()
        instance._shadow_config.enabled = True
        instance._previous_shadow_control_enabled = True
        # Dawn mirrors the shadow-control tracking above (its own switch/config, same
        # bypass problem). Same "already enabled, no change" default.
        instance._dawn_config = MagicMock()
        instance._dawn_config.enabled = True
        instance._previous_dawn_control_enabled = True
        instance._last_positioning_time = None
        instance._last_reported_height = None
        instance._last_reported_angle = None
        instance._last_calculated_height = 0.0
        instance._last_calculated_angle = 0.0

        # Grace period attributes
        instance._ha_start_time = datetime.now(tz=UTC) - timedelta(seconds=35)  # Beyond grace period by default
        instance._ha_restart_grace_period_seconds = 30
        instance._startup_restore_complete = True

        # Bind the actual method to the mock instance
        instance.async_calculate_and_apply_cover_position = ShadowControlManager.async_calculate_and_apply_cover_position.__get__(instance)

        # Bind grace period check method
        instance._is_in_ha_restart_grace_period = ShadowControlManager._is_in_ha_restart_grace_period.__get__(instance)

        return instance

    # ========================================================================
    # PHASE 1: EVENT-TYPE BRANCHES
    # ========================================================================

    async def test_initial_run_no_event(self, manager):
        """Test initial run with event=None (Branch 1B)."""
        await manager.async_calculate_and_apply_cover_position(event=None)

        manager._update_input_values.assert_called_once()
        manager._process_shutter_state.assert_called_once()
        manager._shadow_handling_was_disabled.assert_not_called()
        manager._dawn_handling_was_disabled.assert_not_called()
        manager._force_immediate_positioning.assert_not_called()

    async def test_state_changed_event(self, manager):
        """Test with state_changed event (Branch 2A)."""
        event = Event(
            "state_changed",
            {
                "entity_id": "sensor.brightness",
                "old_state": MagicMock(state="1000"),
                "new_state": MagicMock(state="50000"),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)
        manager._update_input_values.assert_called_once()

    async def test_time_changed_event(self, manager):
        """Test with time_changed event (Branch 2B)."""
        event = Event(
            "time_changed",
            {"now": datetime.now(tz=UTC)},
        )

        await manager.async_calculate_and_apply_cover_position(event=event)
        manager._update_input_values.assert_called_once()
        manager._process_shutter_state.assert_called_once()

    async def test_unhandled_event_type(self, manager):
        """Test with unhandled event type (Branch 2C)."""
        event = Event(
            "custom_event",
            {"some": "data"},
        )

        await manager.async_calculate_and_apply_cover_position(event=event)
        manager._update_input_values.assert_called_once()
        manager._process_shutter_state.assert_called_once()

    # ========================================================================
    # PHASE 2: ENTITY-TYPE BRANCHES (Config entities)
    # ========================================================================

    async def test_config_entity_change_with_lock_active(self, manager):
        """Test config entity change when lock is active (Branch 3A + 4A)."""
        manager._dynamic_config.lock_integration = True

        test_entity = "number.max_height"
        manager._config.get = MagicMock(return_value=test_entity)

        event = Event(
            "state_changed",
            {
                "entity_id": test_entity,
                "old_state": MagicMock(state="80"),
                "new_state": MagicMock(state="90"),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        manager._force_immediate_positioning.assert_not_called()
        manager._process_shutter_state.assert_called_once()

    async def test_config_entity_change_without_lock(self, manager):
        """Test config entity change when no lock is active (Branch 3A + 4B)."""
        manager._dynamic_config.lock_integration = False
        manager._dynamic_config.lock_integration_with_position = False

        test_entity = "number.max_height"
        manager._config.get = MagicMock(return_value=test_entity)

        event = Event(
            "state_changed",
            {
                "entity_id": test_entity,
                "old_state": MagicMock(state="80"),
                "new_state": MagicMock(state="90", spec=["state"]),  # Only allow 'state' attribute
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        manager._force_immediate_positioning.assert_called_once()
        manager._process_shutter_state.assert_not_called()

    # ========================================================================
    # PHASE 3: LOCK-LOGIC BRANCHES
    # ========================================================================

    async def test_simple_lock_disabled_no_lock_with_position(self, manager):
        """Test simple lock disabled when lock_with_position is off (Branch 7A1)."""
        manager._dynamic_config.lock_integration_with_position = False
        lock_entity = "switch.lock"

        manager._config.get = MagicMock(side_effect=lambda key: lock_entity if key == SCDynamicInput.LOCK_INTEGRATION_ENTITY.value else None)

        event = Event(
            "state_changed",
            {
                "entity_id": lock_entity,
                "old_state": MagicMock(state=STATE_ON),
                "new_state": MagicMock(state=STATE_OFF),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        assert manager._last_unlock_time is not None
        assert manager._previous_shutter_height == 50.0
        assert manager._previous_shutter_angle == 45.0

    async def test_simple_lock_enabled(self, manager):
        """Test simple lock enabled (Branch 7A3)."""
        lock_entity = "switch.lock"

        manager._config.get = MagicMock(side_effect=lambda key: lock_entity if key == SCDynamicInput.LOCK_INTEGRATION_ENTITY.value else None)

        manager._previous_shutter_height = 60.0
        manager._previous_shutter_angle = 50.0

        event = Event(
            "state_changed",
            {
                "entity_id": lock_entity,
                "old_state": MagicMock(state=STATE_OFF),
                "new_state": MagicMock(state=STATE_ON),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        assert manager._height_during_lock_state == 60.0
        assert manager._angle_during_lock_state == 50.0

    async def test_lock_with_position_disabled_position_differs(self, manager):
        """Test lock with position disabled and position differs (Branch 8A1 + 9A)."""
        manager._dynamic_config.lock_integration = False
        manager._dynamic_config.lock_height = 40.0
        manager._dynamic_config.lock_angle = 30.0

        lock_entity = "switch.lock_with_position"

        manager._config.get = MagicMock(
            side_effect=lambda key: lock_entity if key == SCDynamicInput.LOCK_INTEGRATION_WITH_POSITION_ENTITY.value else None
        )

        manager._calculate_shutter_height = MagicMock(return_value=80.0)
        manager._calculate_shutter_angle = MagicMock(return_value=70.0)
        manager._facade_config.neutral_pos_height = 80.0
        manager._facade_config.neutral_pos_angle = 70.0

        event = Event(
            "state_changed",
            {
                "entity_id": lock_entity,
                "old_state": MagicMock(state=STATE_ON),
                "new_state": MagicMock(state=STATE_OFF),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        assert manager._enforce_position_update is True

    async def test_lock_with_position_disabled_position_same(self, manager):
        """Test lock with position disabled but position is same (Branch 8A1 + 9B)."""
        manager._dynamic_config.lock_integration = False
        manager._dynamic_config.lock_height = 50.0
        manager._dynamic_config.lock_angle = 45.0

        lock_entity = "switch.lock_with_position"

        manager._config.get = MagicMock(
            side_effect=lambda key: lock_entity if key == SCDynamicInput.LOCK_INTEGRATION_WITH_POSITION_ENTITY.value else None
        )

        manager._calculate_shutter_height = MagicMock(return_value=50.0)
        manager._calculate_shutter_angle = MagicMock(return_value=45.0)
        manager._facade_config.neutral_pos_height = 50.0
        manager._facade_config.neutral_pos_angle = 45.0

        event = Event(
            "state_changed",
            {
                "entity_id": lock_entity,
                "old_state": MagicMock(state=STATE_ON),
                "new_state": MagicMock(state=STATE_OFF),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        assert manager._enforce_position_update is False

    # ========================================================================
    # PHASE 4: FINAL EXECUTION BRANCHES
    # ========================================================================

    async def test_shadow_handling_disabled_execution_path(self, manager):
        """Test execution path when shadow handling was disabled (Branch 11A)."""
        shadow_enable_entity = "switch.shadow_enable"

        def config_get(key):
            if key == SCShadowInput.CONTROL_ENABLED_ENTITY.value:
                return shadow_enable_entity
            return None

        manager._config.get = MagicMock(side_effect=config_get)

        event = Event(
            "state_changed",
            {
                "entity_id": shadow_enable_entity,
                "old_state": MagicMock(state=STATE_ON),
                "new_state": MagicMock(state=STATE_OFF),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        manager._shadow_handling_was_disabled.assert_called_once()
        manager._dawn_handling_was_disabled.assert_not_called()
        manager._force_immediate_positioning.assert_not_called()
        manager._process_shutter_state.assert_not_called()

    async def test_dawn_handling_disabled_execution_path(self, manager):
        """Test execution path when dawn handling was disabled (Branch 11B)."""
        dawn_enable_entity = "switch.dawn_enable"

        def config_get(key):
            if key == SCDawnInput.CONTROL_ENABLED_ENTITY.value:
                return dawn_enable_entity
            return None

        manager._config.get = MagicMock(side_effect=config_get)

        event = Event(
            "state_changed",
            {
                "entity_id": dawn_enable_entity,
                "old_state": MagicMock(state=STATE_ON),
                "new_state": MagicMock(state=STATE_OFF),
            },
        )

        await manager.async_calculate_and_apply_cover_position(event=event)

        manager._shadow_handling_was_disabled.assert_not_called()
        manager._dawn_handling_was_disabled.assert_called_once()
        manager._force_immediate_positioning.assert_not_called()
        manager._process_shutter_state.assert_not_called()

    # ========================================================================
    # REGRESSION: switch.py ShadowControlSwitch toggle (event=None) must not
    # leave stale positioning-verification state that triggers a false-positive
    # auto-lock. See memory/shadow_control_jalousien.md / production incident:
    # a periodic "restart all instances" automation toggles
    # switch.shadow_control_<x>_b01_steuerung_aktiv off then on every 30 minutes.
    # ========================================================================

    async def test_switch_toggle_off_on_does_not_false_positive_auto_lock(self, manager):
        """ShadowControlSwitch._notify_integration() (switch.py) calls
        async_calculate_and_apply_cover_position(None) on every toggle - i.e. WITHOUT a
        real Event object - exactly like the periodic production automation that flips the
        "Steuerung aktiv" switch off then on. Before the fix, a stale target height/angle
        from an earlier positioning cycle (_last_calculated_height/_last_calculated_angle/
        _last_positioning_time) survived both toggle calls completely untouched, because the
        `if event:` disable/enable detection inside async_calculate_and_apply_cover_position
        only recognises a real state_changed Event for the *external* CONTROL_ENABLED_ENTITY -
        never event=None, and never the switch's own internal entity. _check_positioning_completed()
        (called unconditionally on every invocation) then compared the shutter's last-reported
        position against that stale target and incorrectly triggered auto-lock ("manual
        intervention detected"). The fix detects the enabled-state transition directly via
        _shadow_config.enabled (populated by _update_input_values() regardless of the event
        shape) and resets the bookkeeping before _check_positioning_completed() runs."""
        # Bind the real positioning-verification methods (the base fixture leaves them as
        # auto-mocks, which would trivially "pass" without exercising the actual bug).
        manager._check_positioning_completed = ShadowControlManager._check_positioning_completed.__get__(manager)
        manager._is_positioning_in_progress = ShadowControlManager._is_positioning_in_progress.__get__(manager)
        manager._activate_auto_lock = AsyncMock()

        manager._facade_config.shutter_type = ShutterType.MODE1
        manager._facade_config.modification_tolerance_height = 2.0
        manager._facade_config.modification_tolerance_angle = 2.0
        manager._facade_config.max_movement_duration = 30.0

        # Pending state from an earlier positioning cycle: timer already expired, and the
        # shutter's last-reported position differs from the stale target well beyond tolerance.
        manager._last_positioning_time = dt_util.utcnow() - timedelta(seconds=40)
        manager._last_calculated_height = 80.0
        manager._last_calculated_angle = 45.0
        manager._last_reported_height = 50.0
        manager._last_reported_angle = 30.0

        # --- Switch toggled OFF (production: periodic restart automation) ---
        manager._shadow_config.enabled = False
        manager._previous_shadow_control_enabled = True  # was enabled before this call

        await manager.async_calculate_and_apply_cover_position(event=None)

        manager._activate_auto_lock.assert_not_called()
        assert manager._last_positioning_time is None, "Stale positioning timer must be cleared on the disable transition"
        assert manager._last_reported_height is None

        # A new stale mismatch accrues (e.g. a leftover positioning cycle settles physically
        # at a different spot than last calculated) before the switch flips back on.
        manager._last_positioning_time = dt_util.utcnow() - timedelta(seconds=40)
        manager._last_calculated_height = 20.0
        manager._last_calculated_angle = 10.0
        manager._last_reported_height = 90.0
        manager._last_reported_angle = 60.0

        # --- Switch toggled back ON ---
        manager._shadow_config.enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        manager._activate_auto_lock.assert_not_called()
        assert manager._last_positioning_time is None, "Stale positioning timer must be cleared on the enable transition"

    async def test_dawn_switch_toggle_off_on_does_not_false_positive_auto_lock(self, manager):
        """Mirror of test_switch_toggle_off_on_does_not_false_positive_auto_lock, but for the
        fully parallel Dawn mechanism: switch.shadow_control_<x>_d01_steuerung_aktiv is backed
        by its own ShadowControlSwitch instance, and its _notify_integration() also calls
        async_calculate_and_apply_cover_position(None) on every toggle - same bypass problem,
        tracked independently via _dawn_config.enabled / _previous_dawn_control_enabled."""
        manager._check_positioning_completed = ShadowControlManager._check_positioning_completed.__get__(manager)
        manager._is_positioning_in_progress = ShadowControlManager._is_positioning_in_progress.__get__(manager)
        manager._activate_auto_lock = AsyncMock()

        manager._facade_config.shutter_type = ShutterType.MODE1
        manager._facade_config.modification_tolerance_height = 2.0
        manager._facade_config.modification_tolerance_angle = 2.0
        manager._facade_config.max_movement_duration = 30.0

        # Pending state from an earlier positioning cycle: timer already expired, and the
        # shutter's last-reported position differs from the stale target well beyond tolerance.
        manager._last_positioning_time = dt_util.utcnow() - timedelta(seconds=40)
        manager._last_calculated_height = 80.0
        manager._last_calculated_angle = 45.0
        manager._last_reported_height = 50.0
        manager._last_reported_angle = 30.0

        # --- Dawn switch toggled OFF ---
        manager._dawn_config.enabled = False
        manager._previous_dawn_control_enabled = True  # was enabled before this call

        await manager.async_calculate_and_apply_cover_position(event=None)

        manager._activate_auto_lock.assert_not_called()
        assert manager._last_positioning_time is None, "Stale positioning timer must be cleared on the dawn disable transition"
        assert manager._last_reported_height is None

        # A new stale mismatch accrues before the switch flips back on.
        manager._last_positioning_time = dt_util.utcnow() - timedelta(seconds=40)
        manager._last_calculated_height = 20.0
        manager._last_calculated_angle = 10.0
        manager._last_reported_height = 90.0
        manager._last_reported_angle = 60.0

        # --- Dawn switch toggled back ON ---
        manager._dawn_config.enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        manager._activate_auto_lock.assert_not_called()
        assert manager._last_positioning_time is None, "Stale positioning timer must be cleared on the dawn enable transition"

    async def test_shadow_flip_resets_stale_calculated_values(self, manager):
        """A shadow-control enable/disable transition must also clear
        _last_calculated_height/_last_calculated_angle, not just
        _last_positioning_time/_last_reported_height/_last_reported_angle. Otherwise a stale
        calculated target survives with _last_positioning_time=None (harmless while None) but
        becomes "armed" again the moment anything later writes a fresh _last_positioning_time
        without also writing a correspondingly fresh calculated value, leaving a window where
        _check_positioning_completed() could still compare against the stale calculated target."""
        manager._last_calculated_height = 80.0
        manager._last_calculated_angle = 45.0

        manager._shadow_config.enabled = False
        manager._previous_shadow_control_enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager._last_calculated_height == 0.0, "Stale calculated height must be reset on the shadow enable/disable transition"
        assert manager._last_calculated_angle == 0.0, "Stale calculated angle must be reset on the shadow enable/disable transition"

    async def test_dawn_flip_resets_stale_calculated_values(self, manager):
        """Same as test_shadow_flip_resets_stale_calculated_values, but for the dawn
        enable/disable transition."""
        manager._last_calculated_height = 80.0
        manager._last_calculated_angle = 45.0

        manager._dawn_config.enabled = False
        manager._previous_dawn_control_enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager._last_calculated_height == 0.0, "Stale calculated height must be reset on the dawn enable/disable transition"
        assert manager._last_calculated_angle == 0.0, "Stale calculated angle must be reset on the dawn enable/disable transition"

    # ========================================================================
    # REGRESSION: switch-driven disable must also force current_shutter_state back to
    # NEUTRAL, not just reset the positioning-verification bookkeeping (fix #1/#2). Live
    # production proof (shadow_control_bad_nord, 2026-07-23 05:12:50, hours after B01 was
    # switched off the previous evening): a routine brightness-triggered recalculation ran
    # with both Shadow and Dawn config confirmed enabled=False in the debug log, yet still
    # produced "Expected: 100.0% / 50.0°" (SCDefaults.SHADOW_SHUTTER_LOOK_THROUGH_ANGLE_VALUE)
    # against "Reported: 100.0% / 0.0°", triggering another false-positive auto-lock.
    #
    # Root cause: _shadow_handling_was_disabled()/_dawn_handling_was_disabled() (which force
    # current_shutter_state back to ShutterState.NEUTRAL and cancel any running timer, see
    # __init__.py) are only invoked from the `if event:` block, gated on a genuine external
    # state_changed event on CONTROL_ENABLED_ENTITY. ShadowControlSwitch._notify_integration()
    # (switch.py) calls async_calculate_and_apply_cover_position with event=None on every
    # toggle, so that block - and therefore these two methods - was never reached from the
    # switch-driven flip-detection added in fix #1/#2. current_shutter_state was thus left
    # parked in whatever non-neutral state it was last in (e.g. SHADOW_HORIZONTAL_NEUTRAL),
    # and every later recalculation cycle keeps "servicing" that stale state via the
    # _state_handlers dispatch in _process_shutter_state() - which computes/re-applies a
    # target for whatever `current_shutter_state` currently is, independent of `enabled`
    # (only the transition *into* a new state re-checks `enabled`, not the fact of already
    # sitting in a non-neutral state) - instead of resetting to NEUTRAL immediately.
    # ========================================================================

    async def test_shadow_disable_transition_forces_current_shutter_state_to_neutral(self, manager):
        """Reproduces the live incident for the Shadow mechanism.

        Without the fix, the switch-driven disable transition only resets the positioning-
        verification bookkeeping (fix #1/#2) but never touches current_shutter_state, so it
        survives the disable untouched. With the fix, the disable transition also invokes the
        real _shadow_handling_was_disabled() (mirroring the genuine external-event path),
        forcing current_shutter_state back to NEUTRAL immediately - closing the window in
        which a later cycle could still compute/compare a stale non-neutral target.
        """
        # Bind the REAL _shadow_handling_was_disabled (the base fixture auto-mocks it, which
        # would trivially "pass" without proving it actually resets the state machine).
        manager._shadow_handling_was_disabled = ShadowControlManager._shadow_handling_was_disabled.__get__(manager)

        # FSM was left mid-cycle in a non-neutral shadow state (look-through / horizontal
        # neutral) with a stale look-through target, exactly like the live incident.
        manager.current_shutter_state = ShutterState.SHADOW_HORIZONTAL_NEUTRAL
        manager._last_calculated_height = 100.0
        manager._last_calculated_angle = 50.0  # SCDefaults.SHADOW_SHUTTER_LOOK_THROUGH_ANGLE_VALUE

        # --- Switch toggled OFF (event=None, exactly like ShadowControlSwitch) ---
        manager._shadow_config.enabled = False
        manager._previous_shadow_control_enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager.current_shutter_state == ShutterState.NEUTRAL, (
            "current_shutter_state must be forced back to NEUTRAL on the switch-driven disable "
            "transition, exactly like the genuine external-event path does via "
            "_shadow_handling_was_disabled()"
        )

        # --- Hours pass: a routine recalculation cycle runs, enabled is still False (no
        # further flip) - current_shutter_state must stay at NEUTRAL, not drift back. ---
        manager._previous_shadow_control_enabled = manager._shadow_config.enabled  # already set by the call above
        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager.current_shutter_state == ShutterState.NEUTRAL
        assert manager._last_calculated_height == 0.0
        assert manager._last_calculated_angle == 0.0

    async def test_dawn_disable_transition_forces_current_shutter_state_to_neutral(self, manager):
        """Mirror of test_shadow_disable_transition_forces_current_shutter_state_to_neutral,
        but for the fully parallel Dawn mechanism (switch.shadow_control_<x>_d01_steuerung_aktiv
        / _dawn_handling_was_disabled())."""
        manager._dawn_handling_was_disabled = ShadowControlManager._dawn_handling_was_disabled.__get__(manager)

        manager.current_shutter_state = ShutterState.DAWN_HORIZONTAL_NEUTRAL
        manager._last_calculated_height = 100.0
        manager._last_calculated_angle = 50.0  # SCDefaults.DAWN_SHUTTER_LOOK_THROUGH_ANGLE_VALUE

        # --- Switch toggled OFF (event=None, exactly like ShadowControlSwitch) ---
        manager._dawn_config.enabled = False
        manager._previous_dawn_control_enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager.current_shutter_state == ShutterState.NEUTRAL, (
            "current_shutter_state must be forced back to NEUTRAL on the switch-driven dawn "
            "disable transition, exactly like the genuine external-event path does via "
            "_dawn_handling_was_disabled()"
        )

        manager._previous_dawn_control_enabled = manager._dawn_config.enabled
        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager.current_shutter_state == ShutterState.NEUTRAL
        assert manager._last_calculated_height == 0.0
        assert manager._last_calculated_angle == 0.0

    async def test_shadow_disable_transition_does_not_touch_state_when_already_neutral(self, manager):
        """Sanity check: _shadow_handling_was_disabled() is a no-op when already NEUTRAL, so
        the new call added for the disable transition must not raise or otherwise misbehave
        when there was nothing stale to reset."""
        manager._shadow_handling_was_disabled = ShadowControlManager._shadow_handling_was_disabled.__get__(manager)
        manager.current_shutter_state = ShutterState.NEUTRAL

        manager._shadow_config.enabled = False
        manager._previous_shadow_control_enabled = True

        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager.current_shutter_state == ShutterState.NEUTRAL

    async def test_shadow_enable_transition_does_not_force_neutral(self, manager):
        """Symmetry check: unlike the disable direction, there is no "_shadow_handling_was_
        enabled()" counterpart anywhere in the production code (the genuine external-event
        path doesn't force anything special on enable either - it just lets the normal
        _process_shutter_state() dispatch pick up from wherever current_shutter_state is).
        The fix must therefore only force NEUTRAL on the disable transition, not on enable."""
        manager._shadow_handling_was_disabled = ShadowControlManager._shadow_handling_was_disabled.__get__(manager)
        manager.current_shutter_state = ShutterState.SHADOW_HORIZONTAL_NEUTRAL

        manager._shadow_config.enabled = True
        manager._previous_shadow_control_enabled = False

        await manager.async_calculate_and_apply_cover_position(event=None)

        assert manager.current_shutter_state == ShutterState.SHADOW_HORIZONTAL_NEUTRAL, (
            "Enabling shadow control must not force current_shutter_state to NEUTRAL - only "
            "the disable transition does that, mirroring that no _shadow_handling_was_enabled() "
            "equivalent exists in the production code"
        )
