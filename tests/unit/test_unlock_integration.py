"""Tests for async_unlock_integration().

Bug (Fassade faehrt beim Entsperren hoch, Live-Vorfall 2026-07-16, wz_nord):
async_unlock_integration() reset previous_shutter_height/_angle only indirectly,
by handing None (via _height_during_lock_state) to the state-change handlers of
the two lock switches it turns off. Those handlers feed that value straight into
previous_shutter_height/_angle - safe on its own (previous_value=None triggers
_should_output_be_updated()'s safe-boundary protection), but once ONE handler has
already forwarded the None into previous_shutter_height, a second, interleaved
event's handler no longer sees None and the protection never fires - so a
combination that forces an immediate resend could push a stale/incorrect target
straight to the physical cover, bypassing only_close.

Fix: read the cover's REAL physical position up front and anchor
previous_shutter_height/_angle, _last_calculated_height/_angle and (when clearing
an auto-lock) _height_during_lock_state/_angle_during_lock_state to it - so every
downstream consumer works with a value that matches reality, regardless of
interleaving order.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.shadow_control import ShadowControlManager
from custom_components.shadow_control.const import LockState, SCInternal


class TestUnlockIntegration:
    """Test async_unlock_integration()."""

    @pytest.fixture
    def manager(self):
        """Create a mock ShadowControlManager instance with the real unlock method bound."""
        instance = MagicMock(spec=ShadowControlManager)
        instance.logger = MagicMock()
        instance.hass = MagicMock()

        instance._locked_by_auto_lock = True
        instance._height_during_lock_state = None
        instance._angle_during_lock_state = None
        instance._previous_shutter_height = None
        instance._previous_shutter_angle = None
        instance._last_calculated_height = None
        instance._last_calculated_angle = None
        instance._last_unlock_time = None
        instance.current_lock_state = LockState.LOCKED_BY_EXTERNAL_MODIFICATION
        instance.name = "SC Test Instance"

        # Cover physically closed (SC scale: 100 = fully closed) when unlocked.
        instance._get_current_cover_position = AsyncMock(return_value=(100.0, 100.0))
        instance._calculate_lock_state = MagicMock(return_value=LockState.UNLOCKED)

        instance.get_internal_entity_id = MagicMock(
            side_effect=lambda key: {
                SCInternal.LOCK_INTEGRATION_MANUAL: "switch.sc_test_instance_sperren",
                SCInternal.LOCK_INTEGRATION_WITH_POSITION_MANUAL: "switch.sc_test_instance_sperren_mit_zwangsposition",
            }[key]
        )

        # Both switches already "off" - the exact live scenario for wz_nord.
        instance.hass.states.is_state = MagicMock(return_value=False)
        instance.hass.services.async_call = AsyncMock()

        instance.async_unlock_integration = ShadowControlManager.async_unlock_integration.__get__(instance)

        return instance

    async def test_anchors_previous_values_to_physical_position(self, manager):
        """previous_shutter_height/_angle must come from the real physical position, not None."""
        await manager.async_unlock_integration()

        assert manager._previous_shutter_height == 100.0
        assert manager._previous_shutter_angle == 100.0
        assert manager._last_calculated_height == 100.0
        assert manager._last_calculated_angle == 100.0

    async def test_height_during_lock_state_anchored_when_auto_locked(self, manager):
        """Clearing an auto-lock must seed _height_during_lock_state/_angle_during_lock_state
        from the real physical position too, not leave them as None - the
        LOCK_INTEGRATION_MANUAL off-handler feeds this straight into
        previous_shutter_height/_angle on its own."""
        await manager.async_unlock_integration()

        assert manager._height_during_lock_state == 100.0
        assert manager._angle_during_lock_state == 100.0
        assert manager._locked_by_auto_lock is False

    async def test_does_not_overwrite_height_during_lock_state_when_not_auto_locked(self, manager):
        """A genuine manual lock's stored position must survive an unlock call that
        isn't clearing an auto-lock."""
        manager._locked_by_auto_lock = False
        manager._height_during_lock_state = 42.0
        manager._angle_during_lock_state = 17.0

        await manager.async_unlock_integration()

        assert manager._height_during_lock_state == 42.0
        assert manager._angle_during_lock_state == 17.0

    async def test_skips_turn_off_for_already_off_switches(self, manager):
        """No service call should fire for a switch that's already off - the real
        cause of the wz_nord incident was exactly this: turning off an already-off
        switch still re-ran its state-change handler."""
        await manager.async_unlock_integration()

        manager.hass.services.async_call.assert_not_called()

    async def test_turns_off_switches_that_are_genuinely_on(self, manager):
        """Genuinely engaged switches must still be turned off as before."""
        manager.hass.states.is_state = MagicMock(return_value=True)

        await manager.async_unlock_integration()

        assert manager.hass.services.async_call.call_count == 2
        called_entities = {call.args[2]["entity_id"] for call in manager.hass.services.async_call.call_args_list}
        assert called_entities == {
            "switch.sc_test_instance_sperren",
            "switch.sc_test_instance_sperren_mit_zwangsposition",
        }

    async def test_anchors_to_open_position_when_cover_is_open(self, manager):
        """Sanity check the other direction: if the cover really is open when
        unlocked, the anchor must reflect that too (not force it closed)."""
        manager._get_current_cover_position = AsyncMock(return_value=(0.0, 0.0))

        await manager.async_unlock_integration()

        assert manager._previous_shutter_height == 0.0
        assert manager._previous_shutter_angle == 0.0

    async def test_refreshes_lock_state_even_when_no_switch_needed_toggling(self, manager):
        """Regression (found while live-verifying the fix above, 2026-07-16):
        current_lock_state used to only get refreshed as a side effect of a
        switch-off handler's recalculation cycle. Once already-off switches are
        skipped (see test_skips_turn_off_for_already_off_switches), that side
        effect no longer happens - without recomputing it directly here,
        _position_shutter()'s "is_locked" gate (which reads current_lock_state,
        not _locked_by_auto_lock) would keep blocking output, and lock sensors
        would keep showing "locked", until some unrelated event happened to
        trigger a recalculation. Fixture default: both switches report off,
        matching the live wz_nord scenario."""
        await manager.async_unlock_integration()

        manager._calculate_lock_state.assert_called_once()
        assert manager.current_lock_state == LockState.UNLOCKED


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
