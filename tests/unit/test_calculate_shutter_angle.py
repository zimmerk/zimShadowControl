"""Test the shutter slat angle calculation math."""

import math
from itertools import pairwise
from unittest.mock import MagicMock

import pytest

from custom_components.zimshadow import ShadowControlManager
from custom_components.zimshadow.const import ShutterType


@pytest.fixture
def manager(mock_manager):
    """Bind the real calculation and setup mocks."""
    manager = mock_manager
    manager._calculate_shutter_angle = ShadowControlManager._calculate_shutter_angle.__get__(manager)

    # Mock the stepping helper
    manager._handle_shutter_angle_stepping = MagicMock(side_effect=lambda x: x)

    # Default Config (Standard Slat)
    manager._facade_config.slat_width = 80.0  # 80mm
    manager._facade_config.slat_distance = 70.0  # 70mm
    manager._facade_config.slat_angle_offset = 0.0
    manager._facade_config.slat_min_angle = 0.0
    manager._shadow_config.shutter_max_angle = 100.0
    manager._facade_config.shutter_type = ShutterType.MODE1  # 90 degree total
    manager._facade_config.azimuth = 180.0  # Fassade zeigt nach Süden

    # Inputs: Sonne direkt vor der Fassade (rel. Azimuth = 0°)
    manager._dynamic_config.sun_elevation = 30.0
    manager._dynamic_config.sun_azimuth = 180.0
    manager._effective_elevation = 30.0

    return manager


@pytest.mark.asyncio
class TestCalculateShutterAngle:
    """Test suite for slat trigonometry and mapping."""

    async def test_mode3_returns_zero(self, manager):
        """Mode 3 (no tilt) should always return 0.0."""
        manager._facade_config.shutter_type = ShutterType.MODE3
        assert manager._calculate_shutter_angle() == 0.0

    async def test_standard_math_mode1(self, manager):
        """
        Test Mode 1 mapping (0-90 degrees).
        If math results in 45 degrees, percentage should be 45 / 0.9 = 50%.
        """
        # We manually force effective_elevation to get a known angle
        # For simplicity, let's assume the math results in 45 deg
        manager._effective_elevation = 45.0
        # alpha = 90 - 45 = 45
        # asin_arg = sin(45) * 70 / 80 = 0.707 * 0.875 = 0.618
        # beta = asin(0.618) = 38.2 deg
        # gamma = 180 - 45 - 38.2 = 96.8
        # deg = 90 - 96.8 = -6.8 (will be clamped to 0 or handled by mapping)

        result = manager._calculate_shutter_angle()
        assert isinstance(result, float)

    async def test_mode2_mapping(self, manager):
        """Test Mode 2 (180 degree total range, 50% is horizontal)."""
        manager._facade_config.shutter_type = ShutterType.MODE2
        manager._effective_elevation = 30.0

        result = manager._calculate_shutter_angle()
        # Mode 2: (degrees / 1.8) + 50
        # If degrees is 0, result is 50.0.
        assert result >= 50.0

    async def test_invalid_asin_argument_safety(self, manager):
        """Trigger the warning if distance > width (impossible triangle)."""
        manager._facade_config.slat_distance = 200.0  # Much larger than width 80
        manager._effective_elevation = 5.0  # Low elevation creates large sin(alpha)

        result = manager._calculate_shutter_angle()
        assert result == 0.0
        manager.logger.warning.assert_called()

    async def test_min_max_clamping(self, manager):
        """Ensure result respects slat_min_angle and shutter_max_angle."""
        manager._facade_config.slat_min_angle = 20.0
        manager._shadow_config.shutter_max_angle = 80.0

        # Force a very high result
        manager._effective_elevation = 85.0  # Sun overhead, slats should close
        result = manager._calculate_shutter_angle()
        assert result <= 80.0

        # Force a very low result
        manager._effective_elevation = 5.0
        result = manager._calculate_shutter_angle()
        assert result >= 20.0

    async def test_missing_data_fallback(self, manager):
        """Test the large block of None checks at the start."""
        manager._effective_elevation = None
        result = manager._calculate_shutter_angle()
        assert result == 0.0
        manager.logger.warning.assert_called()

    # ===========================================================================
    # Schraegeinfall: NUR effektive Elevation, KEINE "wirksame Lamellenbreite"
    # (0.14.0+zimshadow.6, 12.09.2026 — s. Kommentar in _calculate_shutter_angle)
    # ===========================================================================

    @staticmethod
    def _effective_elevation(elevation: float, azimuth: float, facade_azimuth: float) -> float:
        """Wie _calculate_effective_elevation() es rechnet."""
        return math.degrees(math.atan(math.tan(math.radians(elevation)) / math.cos(math.radians(abs(azimuth - facade_azimuth)))))

    async def test_zero_relative_azimuth_unchanged(self, manager):
        """Sonne senkrecht zur Fassade: Referenzwert 21 % (wie vor und nach dem Umbau)."""
        result = manager._calculate_shutter_angle()
        assert result == pytest.approx(21.0, abs=2.0), f"Bei rel. Azimuth=0° erwartet ~21%, got {result}%"

    async def test_45_degree_relative_azimuth_only_effective_elevation(self, manager):
        """
        45° rel. Azimut bei 30° Elevation: effektive Elevation 39,2°, volle Lamellenbreite 80 mm.
        alpha 50,8° -> asin_arg = sin(50,8°)*70/80 = 0,678 -> beta 42,7° -> gamma 86,5° -> 4° -> ~4,4 %.
        Upstream lieferte hier ~38 % (Breite auf 56,6 mm geschrumpft) — das war die Doppelkorrektur.
        """
        manager._dynamic_config.sun_azimuth = 225.0
        manager._effective_elevation = self._effective_elevation(30.0, 225.0, 180.0)

        result = manager._calculate_shutter_angle()

        assert result == pytest.approx(4.4, abs=1.5), f"Bei rel. Azimuth=45° erwartet ~4%, got {result}%"
        assert result < 10.0, "Schraegeinfall darf den Winkel nicht steiler machen als der Frontaleinfall"

    async def test_grazing_sun_no_jump(self, manager):
        """
        Suedfassade 165°, Lamellen 95/67 mm, Elevation 31,5° (Nachmittag 12.09.2026):
        Von rel. Azimut 60° bis 80° darf der Winkel nie STEIGEN und ab ~70° muss er 0 sein.
        Upstream: 18 % -> 30 % -> Sprung auf 0 % bei 70° (asin_arg > 1, Rueckfall auf volle Breite).
        """
        manager._facade_config.azimuth = 165.0
        manager._facade_config.slat_width = 95.0
        manager._facade_config.slat_distance = 67.0
        manager._dynamic_config.sun_elevation = 31.5
        results = []
        for rel in range(60, 81):
            azimuth = 165.0 + rel
            manager._dynamic_config.sun_azimuth = azimuth
            manager._effective_elevation = self._effective_elevation(31.5, azimuth, 165.0)
            results.append((rel, manager._calculate_shutter_angle()))
        for (rel_a, a), (rel_b, b) in pairwise(results):
            assert b <= a + 1e-9, f"Winkel steigt bei streifender Sonne: rel {rel_a}° -> {a}%, rel {rel_b}° -> {b}%"
        assert all(v == 0.0 for rel, v in results if rel >= 70), f"Ab 70° rel. Azimut muessen die Lamellen offen bleiben: {results}"

    async def test_real_world_2026_09_12_afternoon_south_facade(self, manager):
        """
        Aufgezeichnete Fahrten 16:17/16:19 (17 %/23 %) und 16:23 (0 %) auf yvette (165°, 95/67 mm).
        Sonnenwerte aus VictoriaMetrics. Nach dem Umbau: alle drei 0 % -> keine der zwoelf Fahrten.
        """
        manager._facade_config.azimuth = 165.0
        manager._facade_config.slat_width = 95.0
        manager._facade_config.slat_distance = 67.0
        for elevation, azimuth in ((32.24, 233.12), (31.71, 234.12), (31.18, 235.11)):
            manager._dynamic_config.sun_elevation = elevation
            manager._dynamic_config.sun_azimuth = azimuth
            manager._effective_elevation = self._effective_elevation(elevation, azimuth, 165.0)
            assert manager._calculate_shutter_angle() == 0.0, f"el {elevation} az {azimuth}: Lamellen muessen offen bleiben"

    async def test_real_world_wohnzimmer_hof(self, manager):
        """
        Upstream-Beispiel 'Wohnzimmer Hof' (200°, 95/67 mm, Sonne 28,6°/167,6°, rel. 32,4°):
        effektive Elevation 32,9° -> alpha 57,1° -> asin_arg 0,592 -> ~3 %. Upstream erwartete 13 %
        aus der Breitenkorrektur; geometrisch reicht die kleine Neigung, der Strahl trifft die Lamelle.
        """
        manager._facade_config.azimuth = 200.0
        manager._facade_config.slat_width = 95.0
        manager._facade_config.slat_distance = 67.0
        manager._dynamic_config.sun_azimuth = 167.6
        manager._dynamic_config.sun_elevation = 28.6
        manager._effective_elevation = self._effective_elevation(28.6, 167.6, 200.0)

        result = manager._calculate_shutter_angle()

        assert result == pytest.approx(3.3, abs=1.5), f"Wohnzimmer Hof: erwartet ~3%, got {result}%"

    async def test_missing_facade_azimuth_returns_zero(self, manager):
        """facade_azimuth None -> sicher 0.0 (None-Check bleibt)."""
        manager._facade_config.azimuth = None
        result = manager._calculate_shutter_angle()
        assert result == 0.0
        manager.logger.warning.assert_called()

    async def test_90_degree_relative_azimuth_no_crash(self, manager):
        """rel. Azimut 90°: kein Crash, kein NaN, kein negativer Wert (keine Division durch die Breite mehr)."""
        manager._dynamic_config.sun_azimuth = 270.0
        manager._effective_elevation = 89.9

        result = manager._calculate_shutter_angle()

        assert isinstance(result, float)
        assert result == 0.0
        assert not math.isnan(result)
