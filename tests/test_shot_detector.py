"""Tests for the shot detector (pure Python, no Home Assistant needed)."""

import importlib.util
from pathlib import Path
import sys

_PATH = Path(__file__).parents[1] / "custom_components" / "bookoo" / "shot_detector.py"
_spec = importlib.util.spec_from_file_location("shot_detector", _PATH)
sd = importlib.util.module_from_spec(_spec)
sys.modules["shot_detector"] = sd
_spec.loader.exec_module(sd)

TICK = 0.2


def run(profile, detector=None):
    """Feed (duration, weight_fn, pressure_fn[, timer_fn]) segments, return shots."""
    det = detector or sd.ShotDetector()
    shots = []
    t = 1000.0
    for seg in profile:
        duration, weight_fn, pressure_fn = seg[:3]
        timer_fn = seg[3] if len(seg) > 3 else (lambda x: 0)
        steps = round(duration / TICK)
        for i in range(steps):
            x = i * TICK
            shot = det.feed(t, weight_fn(x), pressure_fn(x), timer_fn(x))
            if shot:
                shots.append(shot)
            t += TICK
    return shots


def const(v):
    return lambda x: v


def ramp(a, b, duration):
    return lambda x: a + (b - a) * min(x / duration, 1.0)


def test_simple_pressure_shot():
    shots = run(
        [
            (5, const(0.0), const(0.05)),
            (3, ramp(0, 2, 3), ramp(0.05, 9, 3)),
            (25, ramp(2, 36, 25), const(9.0)),
            (8, const(36.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    shot = shots[0]
    assert shot["source"] == "pressure"
    assert 27 <= shot["duration"] <= 30
    assert shot["peak_bar"] == 9.0
    assert 35 <= shot["yield_g"] <= 36.5
    assert shot["samples"]["t"][0] == 0


def test_preinfusion_hold_and_bloom_is_one_shot():
    shots = run(
        [
            (5, const(0.0), const(0.05)),
            (8, const(0.0), const(2.0)),  # preinfusion hold
            (6, const(1.0), const(0.1)),  # bloom, pressure drops away
            (3, ramp(1, 3, 3), ramp(0.1, 9, 3)),
            (22, ramp(3, 38, 22), const(9.0)),
            (8, const(38.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    assert shots[0]["duration"] >= 38


def test_preinfusion_without_main_phase_is_discarded():
    det = sd.ShotDetector()
    shots = run(
        [
            (5, const(0.0), const(0.05)),
            (8, const(0.0), const(2.0)),
            (30, const(0.0), const(0.05)),
        ],
        det,
    )
    assert shots == []
    assert not det.recording


def test_flush_is_discarded():
    shots = run(
        [
            (5, const(None), const(0.05)),
            (4, const(None), const(1.5)),
            (30, const(None), const(0.05)),
        ]
    )
    assert shots == []


def test_backflush_pulses_are_discarded():
    pulse = [(8, const(None), const(10.0)), (6, const(None), const(0.05))]
    shots = run([(5, const(None), const(0.05))] + pulse * 5)
    assert shots == []


def test_pressure_shot_end_waits_for_dripping_to_stop():
    shots = run(
        [
            (5, const(0.0), const(0.05)),
            (25, ramp(0, 36, 25), const(9.0)),
            (5, ramp(36, 38, 5), const(0.05)),  # still dripping ~0.4 g/s
            (8, const(38.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    assert shots[0]["yield_g"] >= 37.5


def spiky(fn, spikes):
    """Add single-sample handling spikes at the given offsets."""
    return lambda x: fn(x) + next((v for at, v in spikes if abs(x - at) < TICK / 2), 0)


def test_weight_only_shot_with_spikes():
    shots = run(
        [
            (3, spiky(const(0.0), [(1.0, 180), (1.4, -90)]), const(None)),
            (28, spiky(ramp(0, 36, 28), [(10.0, 60)]), const(None)),
            (8, const(36.0), const(None)),
        ]
    )
    assert len(shots) == 1
    shot = shots[0]
    assert shot["source"] == "weight"
    assert 26 <= shot["duration"] <= 31
    assert 35 <= shot["yield_g"] <= 36.5
    assert shot["peak_bar"] is None


def test_weight_shot_ends_when_cup_removed():
    shots = run(
        [
            (3, const(0.0), const(None)),
            (25, ramp(0, 36, 25), const(None)),
            (5, const(-140.0), const(None)),  # cup lifted off the scale
        ]
    )
    assert len(shots) == 1
    assert 35 <= shots[0]["yield_g"] <= 36.5


def test_cup_placement_does_not_start_shot():
    shots = run(
        [
            (3, const(0.0), const(None)),
            (10, const(250.0), const(None)),
            (10, const(0.0), const(None)),
        ]
    )
    assert shots == []


def test_scale_timer_starts_shot():
    shots = run(
        [
            (3, const(5.0), const(None)),  # not tared, rise alone would not trigger
            (25, ramp(5, 40, 25), const(None), lambda x: x + 0.2),
            (8, const(40.0), const(None), const(25)),
        ]
    )
    assert len(shots) == 1


def test_monitor_connecting_mid_shot_is_backdated_to_tare():
    # The monitor needs a while to connect; the shot is already running.
    shots = run(
        [
            (3, const(0.0), const(None)),
            (1.6, ramp(0, 1.4, 1.6), const(None)),  # flowing, rise hold not reached
            (25, ramp(1.4, 38, 25), const(10.0)),  # monitor connected at 10 bar
            (8, const(38.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    shot = shots[0]
    assert shot["source"] == "pressure"
    assert shot["samples"]["w"][0] < 2.0  # starts at the tared cup, not mid-shot
    assert shot["samples"]["p"][0] is None
    assert shot["duration"] >= 26


def test_weight_shot_switches_to_pressure_when_monitor_connects():
    shots = run(
        [
            (3, const(0.0), const(None)),
            (8, ramp(0, 8, 8), const(None)),  # weight mode already recording
            (20, ramp(8, 38, 20), const(9.0)),
            (8, const(38.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    assert shots[0]["source"] == "pressure"
    assert shots[0]["peak_bar"] == 9.0
    assert shots[0]["duration"] >= 27


def test_monitor_connecting_late_with_several_grams_in_cup():
    shots = run(
        [
            (3, const(0.0), const(None)),
            (6, ramp(0, 6, 6), const(None)),  # weight mode picks this up first
            (20, ramp(6, 38, 20), const(9.5)),
            (8, const(38.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    assert shots[0]["samples"]["w"][0] < 0.5
    assert shots[0]["duration"] >= 25


def test_late_monitor_without_prior_tare_is_not_backdated():
    # Cup sat on an untared scale: no evidence when the shot began.
    shots = run(
        [
            (5, const(120.0), const(None)),
            (25, ramp(120, 156, 25), const(9.0)),
            (8, const(156.0), const(0.05)),
        ]
    )
    assert len(shots) == 1
    assert shots[0]["samples"]["p"][0] == 9.0
