# gnss.py - GNSSService for PicoTracker
# Hardware: Waveshare Pico-GPS-L76K (AT6558R chipset, PCAS/CASIC command set)
# Interface: UART0, GP0 (TX), GP1 (RX), default 9600 baud
# ASCII-clean. No UTF-8 outside comments.

import utime
from machine import UART, Pin
import debug
from gnss_state import GNSSState


# ---------------------------------------------------------------------------
# PCAS command constants (AT6558R / CASIC protocol)
# Checksum is XOR of all bytes between $ and * exclusive.
# ---------------------------------------------------------------------------

# Update rate  (PCAS02, param = milliseconds between fixes)
PCAS_RATE_1HZ   = "$PCAS02,1000*2E"
PCAS_RATE_2HZ   = "$PCAS02,500*1A"
PCAS_RATE_5HZ   = "$PCAS02,200*1C"
PCAS_RATE_10HZ  = "$PCAS02,100*1E"

# Baud rate (PCAS01)
PCAS_BAUD_9600   = "$PCAS01,1*1D"
PCAS_BAUD_115200 = "$PCAS01,5*19"

# Constellation (PCAS04, bitfield: bit0=GPS bit1=BDS bit2=GLONASS)
PCAS_CONST_GPS         = "$PCAS04,1*18"
PCAS_CONST_BDS         = "$PCAS04,2*1B"
PCAS_CONST_GPS_BDS     = "$PCAS04,3*18"
PCAS_CONST_GPS_GLONASS = "$PCAS04,5*18"
PCAS_CONST_ALL         = "$PCAS04,7*1E"

# NMEA sentence mask (PCAS03) - enable GGA,RMC,GSA,GSV; disable others
# Fields: GGA,GLL,GSA,GSV,RMC,VTG,ZDA,ANT,DHV,LPS  (1=on 0=off)
PCAS_NMEA_MASK = "$PCAS03,1,0,1,1,1,0,0,0,0,0*02"

# Cold start
PCAS_COLD_START = "$PCAS10,0*1C"


class _L76K:
    """
    Low-level UART driver for the AT6558R module.
    Provides three read strategies; Strategy 1 (drain_to_buf) is used in production.
    """

    def __init__(self, uart_id, tx_pin, rx_pin, baud):
        self._uart = UART(
            uart_id,
            baudrate=baud,
            tx=Pin(tx_pin),
            rx=Pin(rx_pin),
            bits=8,
            parity=None,
            stop=1,
            timeout=0,        # Non-blocking for drain_to_buf
            rxbuf=2048
        )
        self._buf = b""
        debug.info("gnss: UART{} init baud={} TX=GP{} RX=GP{}".format(
            uart_id, baud, tx_pin, rx_pin))

    # ------------------------------------------------------------------
    # Strategy 1 - Non-blocking drain (use this in the main loop)
    # ------------------------------------------------------------------

    def drain_to_buf(self):
        """
        Read all bytes currently waiting in UART RX buffer (never blocks).
        Returns the first complete NMEA sentence found, or None.
        Caller should call repeatedly until None is returned.
        """
        waiting = self._uart.any()
        if waiting:
            self._buf += self._uart.read(waiting)

        # Find a complete sentence
        while True:
            nl = self._buf.find(b"\n")
            if nl == -1:
                # Only discard if buffer is genuinely huge with no sentence boundary.
                # A legitimate 1Hz epoch is ~1100 bytes; guard at 2x that.
                # Smaller thresholds falsely trigger during normal GSV bursts.
                if len(self._buf) > 2200:
                    discarded = len(self._buf)
                    self._buf = b""
                    debug.warn("gnss: no sentence boundary in {}b, cleared".format(discarded))
                return None

            line = self._buf[:nl + 1]
            self._buf = self._buf[nl + 1:]

            # Discard bytes before first '$'
            dollar = line.find(b"$")
            if dollar == -1:
                continue
            line = line[dollar:]

            line = bytes(b for b in line if b < 128)
            sentence = line.decode("ascii").strip()
            if not sentence:
                continue
            # Silently discard proprietary text messages and unknown types
            # Keep only navigation sentences: GGA, RMC, GSA, GSV
            if len(sentence) >= 6:
                stype = sentence[3:6]
                if stype not in ("GGA", "RMC", "GSA", "GSV"):
                    continue
            return sentence

    # ------------------------------------------------------------------
    # Strategy 2 - Flush then read (diagnostics / one-shot)
    # ------------------------------------------------------------------

    def read_latest(self, sentence_type="$GNRMC", timeout_ms=1400):
        """Discard stale data then wait for the next matching sentence."""
        # Flush
        deadline = utime.ticks_add(utime.ticks_ms(), 200)
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            self._uart.read()

        # Read
        deadline = utime.ticks_add(utime.ticks_ms(), timeout_ms)
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            s = self.drain_to_buf()
            if s and s.startswith(sentence_type):
                return s
        return None

    # ------------------------------------------------------------------
    # Strategy 3 - Blocking read (test harness only)
    # ------------------------------------------------------------------

    def read_sentence(self, timeout_ms=2000):
        """Block until a complete sentence arrives or timeout."""
        start = utime.ticks_ms()
        while utime.ticks_diff(utime.ticks_ms(), start) < timeout_ms:
            s = self.drain_to_buf()
            if s:
                return s
            utime.sleep_ms(5)
        return None

    # ------------------------------------------------------------------
    # Command sending
    # ------------------------------------------------------------------

    def send_command(self, cmd):
        """Send a PCAS command string followed by CRLF."""
        self._uart.write((cmd + "\r\n").encode("ascii"))
        debug.debug("gnss: cmd -> {}".format(cmd))

    def set_baud(self, new_baud):
        """Send baud-change command then reinitialise UART at new rate."""
        if new_baud == 115200:
            self.send_command(PCAS_BAUD_115200)
        elif new_baud == 9600:
            self.send_command(PCAS_BAUD_9600)
        else:
            debug.warn("gnss: unsupported baud {}".format(new_baud))
            return
        utime.sleep_ms(100)
        self._uart.init(baudrate=new_baud)
        debug.info("gnss: UART rebaud -> {}".format(new_baud))


# ---------------------------------------------------------------------------
# NMEA parser helpers
# ---------------------------------------------------------------------------

def _nmea_checksum_ok(sentence):
    """Verify NMEA XOR checksum. Returns True if valid or no checksum present."""
    try:
        if "*" not in sentence:
            return True
        body, chk = sentence.rsplit("*", 1)
        body = body.lstrip("$")
        calc = 0
        for c in body:
            calc ^= ord(c)
        return calc == int(chk[:2], 16)
    except Exception:
        return False


def _parse_lat(val, hemi):
    """Convert NMEA DDMM.MMMM to decimal degrees. Negative if S."""
    if not val:
        return 0.0
    try:
        d = int(float(val) / 100)
        m = float(val) - d * 100
        dec = d + m / 60.0
        if hemi == "S":
            dec = -dec
        return dec
    except Exception:
        return 0.0


def _parse_lon(val, hemi):
    """Convert NMEA DDDMM.MMMM to decimal degrees. Negative if W."""
    if not val:
        return 0.0
    try:
        d = int(float(val) / 100)
        m = float(val) - d * 100
        dec = d + m / 60.0
        if hemi == "W":
            dec = -dec
        return dec
    except Exception:
        return 0.0


def _f(val, default=0.0):
    """Safe float conversion."""
    try:
        return float(val) if val else default
    except Exception:
        return default


def _i(val, default=0):
    """Safe int conversion."""
    try:
        return int(val) if val else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# GNSSService
# ---------------------------------------------------------------------------

class GNSSService:
    """
    Owns the L76K driver. Parses NMEA and writes to GNSSState.
    All consumers of GNSSState treat it as read-only.
    """

    STALE_THRESHOLD_MS = 3000   # Declare no-fix if no update in 3 s

    def __init__(self, config, state):
        """
        config : ConfigManager instance
        state  : GNSSState instance (shared, written here only)
        """
        self._state = state
        gnss_cfg = config.get("gnss")

        self._gps = _L76K(
            uart_id = gnss_cfg["uart_id"],
            tx_pin  = gnss_cfg["tx_pin"],
            rx_pin  = gnss_cfg["rx_pin"],
            baud    = gnss_cfg["baud"]
        )

        self._init_module(gnss_cfg)
        self._gsa_count = 0

    def _init_module(self, gnss_cfg):
        """Send startup configuration commands to AT6558R."""
        utime.sleep_ms(500)   # Allow module to settle after power-on

        # --- Baud rate normalisation ---
        # The module stores its baud rate in flash. If a previous session left
        # it at 115200, we must talk to it at 115200 first to bring it back.
        # Strategy: try sending PCAS01,1 (->9600) at 115200, then re-init UART
        # at 9600 regardless. If the module was already at 9600 the command is
        # received as garbage and ignored, but the UART reinit is harmless.
        debug.info("gnss: normalising baud rate")
        self._gps._uart.init(baudrate=115200)
        utime.sleep_ms(50)
        self._gps.send_command(PCAS_BAUD_9600)
        utime.sleep_ms(200)
        self._gps._uart.init(baudrate=9600)
        self._gps._buf = b""
        # Flush for longer to discard AT6558R startup messages (GPTXT etc.)
        utime.sleep_ms(800)
        self._gps._uart.read()   # drain UART FIFO
        self._gps._buf = b""
        debug.info("gnss: baud normalised -> 9600")

        # NMEA sentence mask - enable only what we parse
        self._gps.send_command(PCAS_NMEA_MASK)
        utime.sleep_ms(100)

        # Constellation
        const = gnss_cfg.get("constellation", 7)
        const_cmd = {
            1: PCAS_CONST_GPS,
            3: PCAS_CONST_GPS_BDS,
            5: PCAS_CONST_GPS_GLONASS,
            7: PCAS_CONST_ALL,
        }.get(const, PCAS_CONST_ALL)
        self._gps.send_command(const_cmd)
        utime.sleep_ms(100)

        # Update rate
        hz = gnss_cfg.get("update_hz", 1)
        rate_cmd = {
            1:  PCAS_RATE_1HZ,
            2:  PCAS_RATE_2HZ,
            5:  PCAS_RATE_5HZ,
            10: PCAS_RATE_10HZ,
        }.get(hz, PCAS_RATE_1HZ)
        self._gps.send_command(rate_cmd)
        utime.sleep_ms(100)

        debug.info("gnss: module configured hz={} const={}".format(hz, const))

    # ------------------------------------------------------------------
    # Main loop method
    # ------------------------------------------------------------------

    def poll(self):
        """
        Drain all complete sentences currently in the UART buffer.
        Call once per main loop iteration. Never blocks.
        """
        self._gsa_count = 0   # Reset GSA epoch counter each poll
        while True:
            sentence = self._gps.drain_to_buf()
            if sentence is None:
                break
            if _nmea_checksum_ok(sentence):
                self._dispatch(sentence)
            else:
                debug.warn("gnss: bad checksum: {}".format(sentence[:40]))

        self._check_staleness()

    # ------------------------------------------------------------------
    # Sentence dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, sentence):
        """Route sentence to the correct parser by talker+type."""
        # Strip talker prefix: $GNGGA / $GPGGA / $BDGGA all -> GGA
        if len(sentence) < 6:
            return
        try:
            # sentence_type is chars 3-5 (after $XX)
            stype = sentence[3:6]
        except IndexError:
            return

        if stype == "GGA":
            self._parse_gga(sentence)
        elif stype == "RMC":
            self._parse_rmc(sentence)
        elif stype == "GSA":
            self._parse_gsa(sentence)
        elif stype == "GSV":
            self._parse_gsv(sentence)

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_gga(self, s):
        """
        GGA - Global Positioning System Fix Data
        $xxGGA,hhmmss.ss,lat,N,lon,E,quality,sats,hdop,alt,M,...
        """
        try:
            parts = s.split(",")
            if len(parts) < 10:
                return
            q = _i(parts[6])
            if q == 0:
                return   # No fix - don't overwrite valid position

            self._state.utc_time    = parts[1]
            self._state.lat         = _parse_lat(parts[2], parts[3])
            self._state.lon         = _parse_lon(parts[4], parts[5])
            self._state.fix_quality = q
            self._state.sats_used   = _i(parts[7])
            self._state.hdop        = _f(parts[8], 99.9)
            self._state.alt         = _f(parts[9])
            self._state.mark_updated()
            debug.debug("gnss: GGA lat={:.5f} lon={:.5f} alt={:.1f}".format(
                self._state.lat, self._state.lon, self._state.alt))
        except Exception as e:
            debug.warn("gnss: GGA parse error: {}".format(e))

    def _parse_rmc(self, s):
        """
        RMC - Recommended Minimum Specific GNSS Data
        $xxRMC,hhmmss.ss,A,lat,N,lon,E,speed_kn,heading,DDMMYY,...
        """
        try:
            parts = s.split(",")
            if len(parts) < 10:
                return
            status = parts[2]   # A = active, V = void
            self._state.fix_valid = (status == "A")

            if self._state.fix_valid:
                self._state.utc_time = parts[1]
                self._state.date     = parts[9]
                self._state.lat      = _parse_lat(parts[3], parts[4])
                self._state.lon      = _parse_lon(parts[5], parts[6])
                # Speed: knots -> m/s (1 kn = 0.514444 m/s)
                self._state.speed    = _f(parts[7]) * 0.514444
                self._state.heading  = _f(parts[8])
                self._state.mark_updated()
                debug.debug("gnss: RMC spd={:.2f}m/s hdg={:.1f}".format(
                    self._state.speed, self._state.heading))
        except Exception as e:
            debug.warn("gnss: RMC parse error: {}".format(e))

    def _parse_gsa(self, s):
        """
        GSA - GNSS DOP and Active Satellites
        $xxGSA,auto/manual,fix_mode,sv...(12),pdop,hdop,vdop,[system_id]
        fix_mode: 1=no fix, 2=2D, 3=3D
        This module uses GN talker for all GSA sentences so we cannot
        identify constellation from talker. Instead use PRN ranges:
          GPS:      1-32
          GLONASS: 65-96
          BeiDou: 201-237  (or 33-64 on some firmware)
        """
        try:
            parts = s.split(",")
            if len(parts) < 18:
                return
            self._state.fix_mode = _i(parts[2])
            vdop_raw = parts[17].split("*")[0]
            self._state.pdop = _f(parts[15], 99.9)
            self._state.hdop = _f(parts[16], 99.9)
            self._state.vdop = _f(vdop_raw,  99.9)

            # Reset used counts on first GSA sentence of each poll batch,
            # then accumulate across subsequent GSA sentences in same epoch.
            if self._gsa_count == 0:
                for c in self._state.constellations.values():
                    c["used"] = 0
            self._gsa_count += 1

            # Count used sats per constellation by PRN range
            used = {"GPS": 0, "BDS": 0, "GLO": 0}
            for p in parts[3:15]:
                prn = _i(p.strip()) if p.strip() else 0
                if prn == 0:
                    continue
                if 1 <= prn <= 32:
                    used["GPS"] += 1
                elif 33 <= prn <= 64 or 201 <= prn <= 237:
                    used["BDS"] += 1
                elif 65 <= prn <= 96:
                    used["GLO"] += 1

            # Update used count in constellation dict
            for name, cnt in used.items():
                if cnt > 0:
                    if name not in self._state.constellations:
                        self._state.constellations[name] = {
                            "in_view": 0, "used": 0,
                            "snr_avg": 0, "snr_max": 0}
                    self._state.constellations[name]["used"] += cnt

            debug.debug("gnss: GSA mode={} pdop={:.1f}".format(
                self._state.fix_mode, self._state.pdop))
        except Exception as e:
            debug.warn("gnss: GSA parse error: {}".format(e))

    # Talker -> constellation name mapping for GSV
    _GSV_NAMES = {"GP": "GPS", "BD": "BDS", "GB": "BDS", "GL": "GLO"}

    def _parse_gsv(self, s):
        """
        GSV - GNSS Satellites in View.
        Fields per satellite block: PRN, elevation, azimuth, SNR (4 sats max per sentence).
        SNR blank/0 = satellite visible but not tracked — excluded from signal stats.
        """
        try:
            parts = s.split(",")
            if len(parts) < 4:
                return

            talker       = s[1:3]
            sentence_num = _i(parts[2])
            total_sats   = _i(parts[3])
            name         = self._GSV_NAMES.get(talker)

            # Reset total sats_in_view accumulator on first GPS/GN sentence
            if sentence_num == 1:
                if talker in ("GP", "GN"):
                    self._state.sats_in_view = total_sats
                else:
                    self._state.sats_in_view += total_sats

                # Initialise constellation entry on first sentence of each group
                if name:
                    existing = self._state.constellations.get(name, {})
                    self._state.constellations[name] = {
                        "in_view":    total_sats,
                        "used":       existing.get("used", 0),  # preserved from GSA
                        "snr_avg":    0,
                        "snr_max":    0,
                        "_snr_sum":   0,
                        "_snr_count": 0,
                    }

            # Parse satellite blocks: fields 4,5,6,7 / 8,9,10,11 / 12,13,14,15 / 16,17,18,19
            if name and name in self._state.constellations:
                c = self._state.constellations[name]
                for i in range(4):
                    base = 4 + i * 4
                    if base + 4 > len(parts):   # need 4 fields: prn,el,az,snr
                        break
                    snr_raw = parts[base + 3].split("*")[0].strip()
                    if snr_raw:
                        snr = _i(snr_raw)
                        if snr > 0:
                            c["_snr_sum"]   += snr
                            c["_snr_count"] += 1
                            if snr > c["snr_max"]:
                                c["snr_max"] = snr

                # After last sentence in group, finalise avg
                num_sentences = _i(parts[1])
                if sentence_num == num_sentences:
                    cnt = c["_snr_count"]
                    c["snr_avg"] = c["_snr_sum"] // cnt if cnt else 0
                    debug.debug("gnss: GSV {} in_view={} used={} avg={} max={}".format(
                        name, c["in_view"], c["used"], c["snr_avg"], c["snr_max"]))

        except Exception as e:
            debug.warn("gnss: GSV parse error: {}".format(e))

    def _check_staleness(self):
        """Clear fix flags if no valid sentence has arrived recently."""
        if self._state.fix_valid and self._state.is_stale(self.STALE_THRESHOLD_MS):
            self._state.fix_valid = False
            debug.warn("gnss: fix lost (stale)")

    # ------------------------------------------------------------------
    # Convenience accessors (pass-through to _L76K for test harness)
    # ------------------------------------------------------------------

    def send_command(self, cmd):
        self._gps.send_command(cmd)

    def read_sentence(self, timeout_ms=2000):
        return self._gps.read_sentence(timeout_ms)

    def read_latest(self, sentence_type="$GNRMC", timeout_ms=1400):
        return self._gps.read_latest(sentence_type, timeout_ms)

    @property
    def state(self):
        return self._state