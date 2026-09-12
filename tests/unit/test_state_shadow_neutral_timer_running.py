"""Test the SHADOW_NEUTRAL_TIMER_RUNNING state handler."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.zimshadow import ShadowControlManager
from custom_components.zimshadow.const import ShutterState


@pytest.fixture
def manager(mock_manager):
    """Bind the real handler to the mock manager."""
    manager = mock_manager
    manager._handle_state_shadow_neutral_timer_running = ShadowControlManager._handle_state_shadow_neutral_timer_running.__get__(manager)
    # Wiederschliess-Daempfung (0.14.0+zimshadow.6): echte Helfer, Standard ohne Verzoegerung
    manager._reclose_delay_elapsed = ShadowControlManager._reclose_delay_elapsed.__get__(manager)
    manager._reset_reclose_pending = ShadowControlManager._reset_reclose_pending.__get__(manager)
    manager._reclose_pending_since = None

    # Dependencies specific to this handler
    manager._check_if_facade_is_in_sun = AsyncMock(return_value=True)
    manager._is_zimshadow_enabled = AsyncMock(return_value=True)
    manager._get_current_brightness = MagicMock(return_value=10000)
    manager._is_timer_finished = MagicMock(return_value=False)
    manager._position_shutter = AsyncMock()
    manager._cancel_timer = MagicMock()

    # Config mocks
    manager._shadow_config = MagicMock()
    manager.brightness_threshold = 50000
    manager._shadow_config.height_after_sun = 20.0
    manager._shadow_config.angle_after_sun = 10.0
    manager._shadow_config.after_seconds = 0  # b05: 0 = kein Wiederschliess-Delay (Upstream-Verhalten)
    manager._facade_config = MagicMock()

    return manager


@pytest.mark.asyncio
class TestHandleStateShadowNeutralTimerRunning:
    """Test branches of the SHADOW_NEUTRAL_TIMER_RUNNING handler."""

    async def test_brightness_recovery_cancels_timer(self, manager):
        """Test that brightness spike returns to FULL_CLOSED immediately."""
        manager._get_current_brightness.return_value = 60000
        manager.brightness_threshold = 50000

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_FULL_CLOSED
        manager._cancel_timer.assert_called_once()
        manager._position_shutter.assert_not_called()

    async def test_timer_finishes_moves_to_after_sun_pos(self, manager):
        """Test moving to the 'after sun' position when timer expires."""
        manager._is_timer_finished.return_value = True
        manager._shadow_config.height_after_sun = 20.0
        manager._shadow_config.angle_after_sun = 10.0

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_NEUTRAL
        manager._position_shutter.assert_called_once_with(20.0, 10.0, stop_timer=True)

    async def test_waiting_for_timer(self, manager):
        """Test staying in state while timer is active and brightness is low."""
        manager._is_timer_finished.return_value = False

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_NEUTRAL_TIMER_RUNNING
        manager._position_shutter.assert_not_called()

    async def test_unconfigured_after_sun_warning(self, manager):
        """Test behavior when after-sun positions are missing from config."""
        manager._is_timer_finished.return_value = True
        manager._shadow_config.height_after_sun = None

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_NEUTRAL_TIMER_RUNNING
        assert manager.logger.warning.called

    async def test_sun_gone_emergency_neutral(self, manager):
        """Test exit to NEUTRAL if sun is no longer on facade."""
        manager._check_if_facade_is_in_sun.return_value = False
        manager._facade_config.neutral_pos_height = 100.0
        manager._facade_config.neutral_pos_angle = 0.0

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.NEUTRAL
        manager._position_shutter.assert_called_once_with(100.0, 0.0, stop_timer=True)

    # ------------------------------------------------------------------
    # Wiederschliess-Daempfung (0.14.0+zimshadow.6, 12.09.2026 17:08/17:10)
    # ------------------------------------------------------------------

    async def test_reclose_waits_and_keeps_open_timer(self, manager):
        """Erster helle Wert: b10-Timer laeuft weiter, nichts wird abgebrochen."""
        manager._get_current_brightness.return_value = 60000
        manager._shadow_config.after_seconds = 480

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_NEUTRAL_TIMER_RUNNING
        manager._cancel_timer.assert_not_called()
        manager._position_shutter.assert_not_called()
        assert manager._reclose_pending_since is not None

    async def test_reclose_after_damping_elapsed_cancels_timer(self, manager):
        """b05 lang hell: zurueck in die Beschattung, b10-Timer wird abgebrochen."""
        manager._get_current_brightness.return_value = 60000
        manager._shadow_config.after_seconds = 480
        manager._reclose_pending_since = dt_util.utcnow() - timedelta(seconds=481)

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_FULL_CLOSED
        manager._cancel_timer.assert_called_once()
        assert manager._reclose_pending_since is None

    async def test_brightness_drop_resets_damping_and_timer_can_finish(self, manager):
        """Wird es wieder dunkel, faellt die Daempfung weg und der Timer darf regulaer ablaufen."""
        manager._get_current_brightness.return_value = 10000
        manager._shadow_config.after_seconds = 480
        manager._reclose_pending_since = dt_util.utcnow() - timedelta(seconds=200)
        manager._is_timer_finished.return_value = True

        result = await manager._handle_state_shadow_neutral_timer_running()

        assert result == ShutterState.SHADOW_NEUTRAL
        assert manager._reclose_pending_since is None
        manager._position_shutter.assert_called_once_with(20.0, 10.0, stop_timer=True)
