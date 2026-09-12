"""Test the SHADOW_HORIZONTAL_NEUTRAL state handler."""

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
    manager._handle_state_shadow_horizontal_neutral = ShadowControlManager._handle_state_shadow_horizontal_neutral.__get__(manager)
    # Wiederschliess-Daempfung (0.14.0+zimshadow.6): echte Helfer, Standard ohne Verzoegerung
    manager._reclose_delay_elapsed = ShadowControlManager._reclose_delay_elapsed.__get__(manager)
    manager._reset_reclose_pending = ShadowControlManager._reset_reclose_pending.__get__(manager)
    manager._reclose_pending_since = None

    # Dependencies specific to this handler
    manager._check_if_facade_is_in_sun = AsyncMock(return_value=True)
    manager._is_zimshadow_enabled = AsyncMock(return_value=True)
    manager._get_current_brightness = MagicMock(return_value=10000)
    manager._start_timer = AsyncMock()
    manager._calculate_shutter_height = MagicMock(return_value=50.0)
    manager._calculate_shutter_angle = MagicMock(return_value=0.0)
    manager._position_shutter = AsyncMock()

    # Config mocks
    manager._shadow_config = MagicMock()
    manager._shadow_config.after_seconds = 0  # b05: 0 = kein Wiederschliess-Delay (Upstream-Verhalten)
    manager._facade_config = MagicMock()

    return manager


@pytest.mark.asyncio
class TestHandleStateShadowHorizontalNeutral:
    """Test branches of the SHADOW_HORIZONTAL_NEUTRAL handler."""

    async def test_brightness_spikes_recloses_shutter(self, manager):
        """Test returning to SHADOW_FULL_CLOSED when brightness increases."""
        manager._get_current_brightness.return_value = 60000
        manager.brightness_threshold = 50000

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_FULL_CLOSED
        manager._position_shutter.assert_called_once_with(50.0, 0.0, stop_timer=True)

    async def test_brightness_low_starts_open_timer(self, manager):
        """Test starting the timer to fully open shutters (NEUTRAL)."""
        manager._get_current_brightness.return_value = 10000
        manager.brightness_threshold = 50000
        manager._shadow_config.shutter_open_seconds = 600

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_NEUTRAL_TIMER_RUNNING
        manager._start_timer.assert_called_once_with(600)

    async def test_sun_gone_returns_to_neutral_pos(self, manager):
        """Test immediate transition to NEUTRAL when sun leaves the facade."""
        manager._check_if_facade_is_in_sun.return_value = False
        manager._facade_config.neutral_pos_height = 100.0
        manager._facade_config.neutral_pos_angle = 0.0

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.NEUTRAL
        manager._position_shutter.assert_called_once_with(100.0, 0.0, stop_timer=True)

    async def test_missing_open_delay_warning(self, manager):
        """Test behavior when open shutter delay is not configured."""
        manager._get_current_brightness.return_value = 10000
        manager._shadow_config.shutter_open_seconds = None

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_HORIZONTAL_NEUTRAL
        assert manager.logger.warning.called

    async def test_calculation_error_stays_in_state(self, manager):
        """Test handling case where calculation returns None during re-closing."""
        manager._get_current_brightness.return_value = 60000
        manager.brightness_threshold = 50000
        manager._calculate_shutter_height.return_value = None

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_HORIZONTAL_NEUTRAL
        manager._position_shutter.assert_not_called()

    # ------------------------------------------------------------------
    # Wiederschliess-Daempfung (0.14.0+zimshadow.6, 12.09.2026 17:08/17:10)
    # ------------------------------------------------------------------

    async def test_reclose_waits_for_after_seconds(self, manager):
        """Erster helle Wert nach der Durchsicht: bleiben, nichts fahren, Daempfung startet."""
        manager._get_current_brightness.return_value = 60000
        manager.brightness_threshold = 50000
        manager._shadow_config.after_seconds = 480

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_HORIZONTAL_NEUTRAL
        manager._position_shutter.assert_not_called()
        manager._start_timer.assert_not_called()
        assert manager._reclose_pending_since is not None

    async def test_reclose_after_damping_elapsed(self, manager):
        """Helligkeit liegt b05 lang ueber der Schwelle: jetzt Beschattung wie bisher."""
        manager._get_current_brightness.return_value = 60000
        manager.brightness_threshold = 50000
        manager._shadow_config.after_seconds = 480
        manager._reclose_pending_since = dt_util.utcnow() - timedelta(seconds=481)

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_FULL_CLOSED
        manager._position_shutter.assert_called_once_with(50.0, 0.0, stop_timer=True)
        assert manager._reclose_pending_since is None

    async def test_reclose_damping_not_yet_elapsed(self, manager):
        """Nach 200 von 480 s: weiter warten."""
        manager._get_current_brightness.return_value = 60000
        manager.brightness_threshold = 50000
        manager._shadow_config.after_seconds = 480
        manager._reclose_pending_since = dt_util.utcnow() - timedelta(seconds=200)

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_HORIZONTAL_NEUTRAL
        manager._position_shutter.assert_not_called()

    async def test_brightness_drop_resets_reclose_damping(self, manager):
        """Faellt die Helligkeit waehrend der Wartezeit, startet die Daempfung spaeter von vorn."""
        manager._get_current_brightness.return_value = 10000
        manager.brightness_threshold = 50000
        manager._shadow_config.after_seconds = 480
        manager._shadow_config.shutter_open_seconds = 600
        manager._reclose_pending_since = dt_util.utcnow() - timedelta(seconds=200)

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.SHADOW_NEUTRAL_TIMER_RUNNING
        assert manager._reclose_pending_since is None

    async def test_sun_gone_resets_reclose_damping(self, manager):
        """Verlaesst die Sonne die Fassade, bleibt keine anhaengige Daempfung zurueck."""
        manager._check_if_facade_is_in_sun.return_value = False
        manager._facade_config.neutral_pos_height = 100.0
        manager._facade_config.neutral_pos_angle = 0.0
        manager._reclose_pending_since = dt_util.utcnow()

        result = await manager._handle_state_shadow_horizontal_neutral()

        assert result == ShutterState.NEUTRAL
        assert manager._reclose_pending_since is None
