"""Espresso shot detection from pressure (Espresso Monitor) and weight (scale).

Pure Python on purpose (no Home Assistant imports) so it can be unit tested.
Feed one sample per tick via ShotDetector.feed(); a finished, valid shot is
returned as a dict, everything else returns None.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any

# --- pressure mode ---------------------------------------------------------
PRESSURE_START = 1.0  # bar, starts a shot
PRESSURE_ONSET = 0.3  # bar, shot start is backdated to where pressure left idle
PRESSURE_MAIN = 4.0  # bar, reaching this switches preinfusion -> main phase
PRESSURE_END = 0.5  # bar, below this counts as "no pressure"
PRESSURE_END_HOLD = 3.0  # s below PRESSURE_END (main phase) ends the shot
PRESSURE_END_HOLD_MAX = 10.0  # s, end even if the scale still reports drips
PRESSURE_END_MAX_FLOW = 0.3  # g/s, scale must agree the shot is over
PREINFUSION_PAUSE_MAX = 25.0  # s of low pressure tolerated before main phase
PREINFUSION_MAX = 60.0  # s without reaching main phase -> discard
PRESSURE_MIN_PEAK = 3.0  # bar
PREBUFFER = 15.0  # s of samples kept while idle (covers a slow monitor connect)

# --- weight mode (no monitor) ------------------------------------------------
WEIGHT_TARED = 2.0  # g, |weight| below this counts as tared
WEIGHT_TARED_WINDOW = 5.0  # s, rise must start this soon after being tared
WEIGHT_RISE_MIN = 0.3  # g
WEIGHT_RISE_MAX = 100.0  # g
FLOW_RISE_MIN = 0.3  # g/s
FLOW_RISE_MAX = 8.0  # g/s
WEIGHT_RISE_HOLD = 2.0  # s of steady rise starts a shot
FLOW_END = 0.1  # g/s
FLOW_END_HOLD = 4.0  # s below FLOW_END ends the shot

# --- weight filtering ----------------------------------------------------------
WEIGHT_SPIKE = 15.0  # g per tick, larger jumps are handling noise
WEIGHT_SPIKE_CONFIRM = 3  # consecutive samples at a new level accept it
CUP_REMOVED = 20.0  # g drop of the accepted level during a shot
FLOW_WINDOW = 1.0  # s

# --- general -------------------------------------------------------------------
SHOT_MIN_DURATION = 12.0  # s
SHOT_MAX_DURATION = 120.0  # s

SOURCE_PRESSURE = "pressure"
SOURCE_WEIGHT = "weight"


class WeightFilter:
    """Reject handling spikes, smooth with a median of 3 and derive flow."""

    def __init__(self) -> None:
        self._level: float | None = None
        self._pending: list[float] = []
        self._recent: deque[float] = deque(maxlen=3)
        self._history: deque[tuple[float, float]] = deque()

    def update(self, t: float, raw: float | None) -> tuple[float | None, float | None, float]:
        """Return (smoothed weight, flow, level jump) for this tick.

        The level jump is non-zero only on the tick a new weight level (cup
        placed/removed) got accepted.
        """
        if raw is None:
            self.__init__()
            return None, None, 0.0

        jump = 0.0
        if self._level is not None and abs(raw - self._level) > WEIGHT_SPIKE:
            self._pending.append(raw)
            if len(self._pending) < WEIGHT_SPIKE_CONFIRM or (
                max(self._pending) - min(self._pending) > WEIGHT_SPIKE
            ):
                if len(self._pending) >= WEIGHT_SPIKE_CONFIRM:
                    self._pending.pop(0)
                return self.smoothed, None, 0.0
            # stable at a new level: accept it and start fresh
            jump = raw - self._level
            self._recent.clear()
            self._history.clear()
        self._pending.clear()
        self._level = raw
        self._recent.append(raw)
        smoothed = self.smoothed
        assert smoothed is not None

        self._history.append((t, smoothed))
        while self._history and t - self._history[0][0] > FLOW_WINDOW + 0.25:
            self._history.popleft()
        flow = None
        t_old, w_old = self._history[0]
        if t - t_old >= FLOW_WINDOW * 0.8:
            flow = max(0.0, (smoothed - w_old) / (t - t_old))
        return smoothed, flow, jump

    @property
    def smoothed(self) -> float | None:
        """Median of the last accepted weights."""
        return median(self._recent) if self._recent else None


@dataclass
class _Sample:
    t: float
    weight: float | None
    flow: float | None
    pressure: float | None


@dataclass
class _Recording:
    source: str
    t0: float
    samples: list[_Sample] = field(default_factory=list)
    main_phase: bool = False
    peak: float = 0.0
    low_since: float | None = None
    end_weight: float | None = None
    weight_lost: bool = False


class ShotDetector:
    """State machine: idle -> recording -> idle."""

    def __init__(self) -> None:
        self._filter = WeightFilter()
        self._prebuffer: deque[_Sample] = deque()
        self._rec: _Recording | None = None
        self._last_timer: float | None = None
        self._last_tared: float | None = None
        self._last_empty: float | None = None
        self._rise_start: float | None = None

    @property
    def recording(self) -> bool:
        """Return True while a shot is being recorded."""
        return self._rec is not None

    @property
    def source(self) -> str | None:
        """Return the trigger source of the running shot."""
        return self._rec.source if self._rec else None

    @property
    def started(self) -> float | None:
        """Return the start time of the running shot."""
        return self._rec.t0 if self._rec else None

    def reset(self) -> None:
        """Drop any running recording."""
        self.__init__()

    def feed(
        self,
        t: float,
        weight: float | None,
        pressure: float | None,
        timer: float | None = None,
    ) -> dict[str, Any] | None:
        """Process one sample; return a finished shot or None."""
        w, flow, jump = self._filter.update(t, weight)
        sample = _Sample(t, w, flow, pressure)

        timer_started = bool(timer) and not self._last_timer
        self._last_timer = timer

        if self._rec is None:
            return self._feed_idle(sample, timer_started)

        rec = self._rec
        if jump < -CUP_REMOVED:
            # Cup lifted: freeze the yield, ignore weight from here on.
            rec.weight_lost = True
        if rec.weight_lost:
            sample.weight = sample.flow = None
        else:
            if sample.weight is not None:
                rec.end_weight = sample.weight
        rec.samples.append(sample)

        if t - rec.t0 > SHOT_MAX_DURATION:
            self._rec = None
            return None
        if rec.source == SOURCE_WEIGHT and sample.pressure is not None:
            # Monitor connected mid-shot: pressure is the better end signal.
            rec.source = SOURCE_PRESSURE
            rec.low_since = None
        if rec.source == SOURCE_PRESSURE:
            return self._feed_pressure(rec, sample)
        return self._feed_weight(rec, sample)

    # -- idle --------------------------------------------------------------

    def _feed_idle(self, s: _Sample, timer_started: bool) -> None:
        self._prebuffer.append(s)
        while self._prebuffer and s.t - self._prebuffer[0].t > PREBUFFER:
            self._prebuffer.popleft()

        if s.pressure is not None:
            self._rise_start = None
            if s.pressure >= PRESSURE_START:
                buf = list(self._prebuffer)
                onset = len(buf) - 1
                while onset > 0 and (buf[onset - 1].pressure or 0) > PRESSURE_ONSET:
                    onset -= 1
                self._start(SOURCE_PRESSURE, buf[self._late_monitor_onset(buf, onset):])
            return None

        if s.weight is None:
            self._rise_start = None
            return None
        if abs(s.weight) < WEIGHT_TARED:
            self._last_tared = s.t
        if abs(s.weight) < WEIGHT_RISE_MIN:
            self._last_empty = s.t

        if timer_started:
            self._start(SOURCE_WEIGHT, [s])
            return None

        rising = (
            WEIGHT_RISE_MIN <= s.weight <= WEIGHT_RISE_MAX
            and s.flow is not None
            and FLOW_RISE_MIN <= s.flow <= FLOW_RISE_MAX
        )
        if not rising:
            self._rise_start = None
            return None
        if self._rise_start is None:
            tared_recently = (
                self._last_tared is not None
                and s.t - self._last_tared <= WEIGHT_TARED_WINDOW
            )
            if not tared_recently:
                return None
            self._rise_start = s.t
        if s.t - self._rise_start >= WEIGHT_RISE_HOLD:
            # Backdate to where the weight left zero so the curve starts at 0 g.
            t0 = self._last_empty if self._last_empty is not None else self._rise_start
            self._start(SOURCE_WEIGHT, [x for x in self._prebuffer if x.t >= t0])
        return None

    @staticmethod
    def _late_monitor_onset(buf: list[_Sample], onset: int) -> int:
        """Backdate a shot the monitor only joined after it had begun.

        If the monitor connected while coffee was already flowing (pressure
        unknown before, weight rising since it last read empty), the shot
        really started where the weight left zero.
        """
        if onset == 0 or buf[onset - 1].pressure is not None:
            return onset
        latest = buf[-1].weight
        if latest is None or latest < WEIGHT_RISE_MIN:
            return onset
        for i in range(onset - 1, -1, -1):
            w = buf[i].weight
            if w is None:
                break
            if abs(w) < WEIGHT_RISE_MIN:
                return i
        return onset

    def _start(self, source: str, samples: list[_Sample]) -> None:
        self._rec = _Recording(source=source, t0=samples[0].t, samples=samples)
        for x in samples:
            if x.pressure is not None:
                self._rec.peak = max(self._rec.peak, x.pressure)
            if x.weight is not None:
                self._rec.end_weight = x.weight
        self._prebuffer.clear()
        self._rise_start = None

    # -- recording ---------------------------------------------------------

    def _feed_pressure(self, rec: _Recording, s: _Sample) -> dict[str, Any] | None:
        p = s.pressure
        if p is None:
            # Monitor went away mid-shot: finish with what we have.
            return self._finish(rec, s.t)
        rec.peak = max(rec.peak, p)
        if p >= PRESSURE_MAIN:
            rec.main_phase = True

        if p < PRESSURE_END:
            if rec.low_since is None:
                rec.low_since = s.t
        else:
            rec.low_since = None

        if not rec.main_phase:
            # Preinfusion: holds and blooms at low pressure are expected.
            paused_too_long = (
                rec.low_since is not None and s.t - rec.low_since > PREINFUSION_PAUSE_MAX
            )
            if paused_too_long or s.t - rec.t0 > PREINFUSION_MAX:
                self._rec = None
            return None

        if rec.low_since is None:
            return None
        low_for = s.t - rec.low_since
        still_dripping = s.flow is not None and s.flow >= PRESSURE_END_MAX_FLOW
        if low_for >= PRESSURE_END_HOLD_MAX or (low_for >= PRESSURE_END_HOLD and not still_dripping):
            return self._finish(rec, rec.low_since)
        return None

    def _feed_weight(self, rec: _Recording, s: _Sample) -> dict[str, Any] | None:
        if rec.weight_lost or s.weight is None:
            return self._finish(rec, s.t)
        if s.flow is not None and s.flow < FLOW_END:
            if rec.low_since is None:
                rec.low_since = s.t
            if s.t - rec.low_since >= FLOW_END_HOLD:
                return self._finish(rec, rec.low_since)
        else:
            rec.low_since = None
        return None

    def _finish(self, rec: _Recording, t_end: float) -> dict[str, Any] | None:
        self._rec = None
        duration = t_end - rec.t0
        if duration < SHOT_MIN_DURATION:
            return None
        if rec.source == SOURCE_PRESSURE and rec.peak < PRESSURE_MIN_PEAK:
            return None

        def rnd(v: float | None, digits: int) -> float | None:
            return None if v is None else round(v, digits)

        return {
            "start": rec.t0,
            "source": rec.source,
            "duration": round(duration, 1),
            "yield_g": rnd(rec.end_weight, 1),
            "peak_bar": round(rec.peak, 2) if rec.source == SOURCE_PRESSURE else None,
            "samples": {
                "t": [round(x.t - rec.t0, 2) for x in rec.samples],
                "w": [rnd(x.weight, 1) for x in rec.samples],
                "f": [rnd(x.flow, 2) for x in rec.samples],
                "p": [rnd(x.pressure, 2) for x in rec.samples],
            },
        }
