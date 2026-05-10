# recorder.py - Recorder for PicoTracker
# Buffers track points in memory; flushes to CSV on flash.
# ASCII-clean.

import utime
import os
import debug


# CSV column order
_CSV_HEADER = "utc_time,date,lat,lon,alt,speed,heading,hdop,sats_used\r\n"

# Failsafe: record a point after this many ms even if below thresholds
_FAILSAFE_INTERVAL_MS = 60000

# Minimum free flash space before we refuse to start recording (bytes)
_MIN_FREE_BYTES = 51200   # 50 KB


def _free_space_bytes():
    """Return free flash space in bytes. Returns 0 on error."""
    try:
        stats = os.statvfs("/")
        # f_bsize * f_bfree = free bytes
        return stats[0] * stats[3]
    except Exception:
        return 0


class Recorder:
    """
    Records track points filtered by the active ActivityProfile.
    Buffer is kept in RAM; flushed to flash every flush_every_n_points.
    Stops recording gracefully when max_file_size_kb is reached.
    """

    def __init__(self, config):
        rec = config.get("recorder")
        self._max_bytes        = rec.get("max_file_size_kb", 512) * 1024
        self._flush_every      = rec.get("flush_every_n_points", 10)
        self._format           = rec.get("format", "csv")

        self._recording        = False
        self._profile          = None
        self._buffer           = []
        self._point_count      = 0
        self._bytes_written    = 0
        self._last_lat         = None
        self._last_lon         = None
        self._last_record_ms   = 0
        self._filename         = None
        self._file             = None

        debug.info("recorder: init flush_every={} max_kb={}".format(
            self._flush_every, self._max_bytes // 1024))

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def set_profile(self, profile):
        """Update active ActivityProfile (call before start or mid-session)."""
        self._profile = profile

    def start(self, filename=None):
        """Begin a new recording session."""
        if self._recording:
            debug.warn("recorder: already recording")
            return False

        # Check available flash space before starting
        free = _free_space_bytes()
        if free < _MIN_FREE_BYTES:
            debug.error("recorder: insufficient flash space ({} bytes free)".format(free))
            return False

        if filename is None:
            filename = "track_{}.csv".format(utime.ticks_ms())

        self._filename       = filename
        self._point_count    = 0
        self._bytes_written  = 0
        self._last_lat       = None
        self._last_lon       = None
        self._last_record_ms = 0
        self._buffer         = []

        try:
            self._file = open(filename, "w")
            header = _CSV_HEADER
            self._file.write(header)
            self._bytes_written += len(header)
        except Exception as e:
            debug.error("recorder: cannot open {} ({})".format(filename, e))
            return False

        self._recording = True
        debug.info("recorder: started -> {} ({} bytes free)".format(
            filename, free))
        return True

    def stop(self):
        """End the recording session and flush remaining buffer."""
        if not self._recording:
            return
        self._flush()
        try:
            if self._file:
                self._file.close()
        except Exception as e:
            debug.error("recorder: close error ({})".format(e))
        self._file = None
        self._recording = False
        debug.info("recorder: stopped ({} points)".format(self._point_count))

    def toggle(self):
        """Start if stopped; stop if running."""
        if self._recording:
            self.stop()
        else:
            self.start()

    @property
    def is_recording(self):
        return self._recording

    @property
    def point_count(self):
        return self._point_count

    @property
    def bytes_written(self):
        return self._bytes_written

    @property
    def free_space_kb(self):
        return _free_space_bytes() // 1024

    # ------------------------------------------------------------------
    # Main loop method
    # ------------------------------------------------------------------

    def maybe_record(self, snapshot):
        """
        Evaluate a MetricsSnapshot against the active profile filters.
        Call once per main loop iteration (or when a new snapshot is available).
        """
        if not self._recording:
            return
        if snapshot is None or not snapshot.fix_valid:
            return
        if self._profile is None:
            return

        now    = utime.ticks_ms()
        p      = self._profile
        speed  = snapshot.speed

        # Auto-pause: skip if below minimum speed
        if p.auto_pause and speed < p.min_speed_mps:
            debug.debug("recorder: auto-pause spd={:.2f}".format(speed))
            return

        # Distance filter
        if self._last_lat is not None:
            dist = _haversine(self._last_lat, self._last_lon,
                              snapshot.lat, snapshot.lon)
            if dist < p.min_distance_m:
                # Failsafe: record anyway after _FAILSAFE_INTERVAL_MS
                elapsed = utime.ticks_diff(now, self._last_record_ms)
                if elapsed < _FAILSAFE_INTERVAL_MS:
                    debug.debug("recorder: below min_dist {:.1f}m".format(dist))
                    return

        self._commit(snapshot, now)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _commit(self, snap, now):
        """Write a point to the buffer. Stops recording if size limit reached."""
        # Check size limit before committing another point
        if self._bytes_written >= self._max_bytes:
            debug.warn("recorder: size limit {}KB reached, stopping".format(
                self._max_bytes // 1024))
            self.stop()
            return

        row = "{},{},{:.7f},{:.7f},{:.2f},{:.3f},{:.1f},{:.1f},{}\r\n".format(
            snap.utc_time, snap.date,
            snap.lat, snap.lon, snap.alt,
            snap.speed, snap.heading,
            snap.hdop, snap.sats_used
        )
        self._buffer.append(row)
        self._last_lat       = snap.lat
        self._last_lon       = snap.lon
        self._last_record_ms = now
        self._point_count   += 1

        debug.debug("recorder: point #{} lat={:.5f} lon={:.5f}".format(
            self._point_count, snap.lat, snap.lon))

        if len(self._buffer) >= self._flush_every:
            self._flush()

    def _flush(self):
        """Write buffered rows to flash."""
        if not self._buffer or self._file is None:
            return
        try:
            for row in self._buffer:
                self._file.write(row)
                self._bytes_written += len(row)
            self._file.flush()
            debug.debug("recorder: flushed {} rows, total {:.1f}KB".format(
                len(self._buffer), self._bytes_written / 1024))
        except Exception as e:
            debug.error("recorder: flush error ({})".format(e))
        self._buffer = []


# ---------------------------------------------------------------------------
# Haversine distance (metres) - used for distance filter
# ---------------------------------------------------------------------------

import math

def _haversine(lat1, lon1, lat2, lon2):
    """Returns distance in metres between two decimal-degree coordinates."""
    R = 6371000.0   # Earth radius in metres
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))