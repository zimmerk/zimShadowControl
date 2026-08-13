"""Tests for _handle_shutter_height_stepping method."""

from unittest.mock import MagicMock

import pytest

from custom_components.zimshadow import ShadowControlManager  # Adjust import


class TestHandleShutterHeightStepping:
    """Test _handle_shutter_height_stepping method."""

    @pytest.fixture
    def zimshadow(self):
        """Create a mock ShadowControl instance."""
        # Mock the minimum required attributes
        instance = MagicMock()
        instance.logger = MagicMock()
        instance._facade_config = MagicMock()

        # Bind the actual method to the mock instance

        instance._handle_shutter_height_stepping = ShadowControlManager._handle_shutter_height_stepping.__get__(instance)

        return instance

    # ========================================================================
    # BRANCH 1: shutter_stepping_percent is None
    # ========================================================================

    def test_stepping_none_returns_original_value(self, zimshadow):
        """Test that None stepping returns original height with warning."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = None
        original_height = 23.5

        # Act
        result = zimshadow._handle_shutter_height_stepping(original_height)

        # Assert
        assert result == original_height  # ✅ Branch 1A: None → return original
        zimshadow.logger.warning.assert_called_once()  # Verify warning logged

    # ========================================================================
    # BRANCH 2A + 3A: stepping != 0 AND remainder != 0 (needs adjustment)
    # ========================================================================

    def test_stepping_with_remainder_rounds_up(self, zimshadow):
        """Test that height with remainder is rounded up to next step."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 23.0  # 23 % 10 = 3 (remainder)

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        # 23 + 10 - 3 = 30
        assert result == 30.0  # ✅ Branch 2A (!=0) + Branch 3A (remainder!=0)
        zimshadow.logger.debug.assert_called()

    def test_stepping_example_from_docstring(self, zimshadow):
        """Test the exact example from the docstring: 23% with 10% stepping."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 23.0

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        assert result == 30.0  # As per docstring example
        # Verify debug log was called with expected values
        debug_calls = zimshadow.logger.debug.call_args_list
        assert len(debug_calls) >= 1

    def test_stepping_small_remainder(self, zimshadow):
        """Test stepping with small remainder (e.g., 21% with 10% stepping)."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 21.0  # 21 % 10 = 1 (small remainder)

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        # 21 + 10 - 1 = 30
        assert result == 30.0  # ✅ Branch 2A + 3A

    def test_stepping_large_remainder(self, zimshadow):
        """Test stepping with large remainder (e.g., 29% with 10% stepping)."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 29.0  # 29 % 10 = 9 (large remainder)

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        # 29 + 10 - 9 = 30
        assert result == 30.0  # ✅ Branch 2A + 3A

    # ========================================================================
    # BRANCH 2A + 3B: stepping != 0 AND remainder == 0 (no adjustment needed)
    # ========================================================================

    def test_stepping_no_remainder_returns_original(self, zimshadow):
        """Test that height already at step boundary returns unchanged."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 30.0  # 30 % 10 = 0 (no remainder)

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        assert result == 30.0  # ✅ Branch 2A (!=0) + Branch 3B (remainder==0)
        # Verify the "fits stepping" debug message
        zimshadow.logger.debug.assert_called()

    def test_stepping_zero_height_no_remainder(self, zimshadow):
        """Test that 0% height with stepping returns 0%."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 0.0  # 0 % 10 = 0

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        assert result == 0.0  # ✅ Branch 2A + 3B

    # ========================================================================
    # BRANCH 2B: stepping == 0 (no stepping configured)
    # ========================================================================

    def test_stepping_zero_returns_original(self, zimshadow):
        """Test that zero stepping returns original height."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 0.0
        calculated_height = 23.5

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        assert result == 23.5  # ✅ Branch 2B: stepping == 0
        zimshadow.logger.debug.assert_called()

    # ========================================================================
    # EDGE CASES
    # ========================================================================

    def test_stepping_with_decimal_values(self, zimshadow):
        """Test stepping with decimal step size."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 2.5
        calculated_height = 23.7  # 23.7 % 2.5 = 1.2

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        # 23.7 + 2.5 - 1.2 = 25.0
        assert result == pytest.approx(25.0, abs=0.01)

    def test_stepping_100_percent(self, zimshadow):
        """Test stepping at maximum height (100%)."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 100.0  # 100 % 10 = 0

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        assert result == 100.0  # Should stay at 100%

    def test_stepping_near_100_percent(self, zimshadow):
        """Test stepping near maximum height."""
        # Arrange
        zimshadow._facade_config.shutter_stepping_height = 10.0
        calculated_height = 97.0  # 97 % 10 = 7

        # Act
        result = zimshadow._handle_shutter_height_stepping(calculated_height)

        # Assert
        # 97 + 10 - 7 = 100
        assert result == 100.0  # Should round up to 100%
