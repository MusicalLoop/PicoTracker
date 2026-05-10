# metrics.py - MetricsManager for PicoTracker
# Produces immutable metric snapshots from GNSSState at configurable intervals.
# ASCII-clean.

import utime
import debug


class MetricsSnapshot:
    """Immutable snapshot of GNSS metrics at a point in time."""

    def __init__(self, state, valid, age_ms):
        self.valid       = valid
        self.age_ms      = age_ms
        self.utc_time    = state.utc_time
        self.date        = state.date
        self.lat         = state.lat
        self.lon         = state.lon
        self.alt         = state.alt
        self.speed       = state.speed
        self.heading     = state.heading
        self.fix_valid   = state.fix_valid
        self.fix_mode    = state.fix_mode
        self.fix_quality = state.fix_quality
        self.hdop        = state.hdop
        self.vdop        = state.vdop
        self.pdop        = state.pdop
        self.sats_used   = state.sats_used
        self.sats_in_view = state.sats_in_view
        self.captured_ms  = utime.ticks_ms()
        # Deep copy constellation dict — strip internal accumulators
        self.constellations = {}
        for name, c in state.constellations.items():
            self.constellations[name] = {
                "in_view": c.get("in_view", 0),
                "used":    c.get("used",    0),
                "snr_avg": c.get("snr_avg", 0),
                "snr_max": c.get("snr_max", 0),
            }


class MetricsManager:
    """
    Reads GNSSState at a configurable interval and produces MetricsSnapshots.
    Consumers call get_snapshot() to get the latest (or None if not yet ready).
    """

    # Supported intervals in milliseconds
    VALID_INTERVALS_MS = (500, 1000, 2000, 5000, 10000)

    def __init__(self, state, interval_ms=1000):
        self._state       = state
        self._snapshot    = None
        self._last_ms     = 0
        self.set_interval(interval_ms)
        debug.info("metrics: interval={}ms".format(self._interval_ms))

    def set_interval(self, interval_ms):
        """Set snapshot interval. Clamps to nearest supported value."""
        closest = min(self.VALID_INTERVALS_MS,
                      key=lambda v: abs(v - interval_ms))
        self._interval_ms = closest

    def poll(self):
        """
        Check if a new snapshot is due. Call once per main loop iteration.
        Returns True if a new snapshot was generated.
        """
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_ms) < self._interval_ms:
            return False

        self._last_ms = now
        age_ms = self._state.age_ms()
        valid  = self._state.fix_valid and not self._state.is_stale()
        self._snapshot = MetricsSnapshot(self._state, valid, age_ms)
        debug.debug("metrics: snapshot valid={} spd={:.2f}".format(
            valid, self._snapshot.speed))
        return True

    def get_snapshot(self):
        """Return the latest MetricsSnapshot, or None if none yet produced."""
        return self._snapshot
