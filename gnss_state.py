# gnss_state.py - GNSSState data container for PicoTracker
# Written only by GNSSService. All other modules treat this as read-only.
# ASCII-clean. No external dependencies.

import utime


class GNSSState:
    """Holds the latest parsed GNSS data. Written only by GNSSService."""

    def __init__(self):
        # Time / date
        self.utc_time       = ""      # HHMMSS.ss from NMEA
        self.date           = ""      # DDMMYY from RMC

        # Position
        self.lat            = 0.0     # Decimal degrees (positive = N)
        self.lon            = 0.0     # Decimal degrees (positive = E)
        self.alt            = 0.0     # Metres above MSL

        # Motion
        self.speed          = 0.0     # Metres per second
        self.heading        = 0.0     # Degrees true (0-359.9)

        # Fix quality
        self.fix_valid      = False   # RMC status: True = A (active), False = V (void)
        self.fix_mode       = 0       # 0 = no fix, 2 = 2D, 3 = 3D  (from GSA)
        self.fix_quality    = 0       # GGA quality indicator (0=invalid,1=GPS,2=DGPS)

        # Dilution of precision
        self.hdop           = 99.9
        self.vdop           = 99.9
        self.pdop           = 99.9

        # Satellite count
        self.sats_used      = 0       # Satellites used in fix (GGA)
        self.sats_in_view   = 0       # Satellites in view (GSV)

        # Staleness
        self.last_update_ms = 0       # utime.ticks_ms() at last successful parse

        # Per-constellation metrics — keyed by short name: GPS, BDS, GLO
        # Each entry: {in_view, used, snr_avg, snr_max}
        self.constellations = {}

    def mark_updated(self):
        """Call after writing any field to record timestamp."""
        self.last_update_ms = utime.ticks_ms()

    def age_ms(self):
        """Milliseconds since last update."""
        return utime.ticks_diff(utime.ticks_ms(), self.last_update_ms)

    def is_stale(self, threshold_ms=3000):
        """True if no update received within threshold_ms."""
        return self.age_ms() > threshold_ms

    def __repr__(self):
        return (
            "GNSSState(fix={} mode={} lat={:.6f} lon={:.6f} "
            "alt={:.1f} spd={:.2f} hdg={:.1f} sats={}/{})".format(
                self.fix_valid, self.fix_mode,
                self.lat, self.lon, self.alt,
                self.speed, self.heading,
                self.sats_used, self.sats_in_view
            )
        )
