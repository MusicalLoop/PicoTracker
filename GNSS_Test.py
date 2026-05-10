# GNSS_Test.py - Self-contained test harness for GNSSService / _L76K
# Run standalone on the Pico. Uses blocking read strategies (2 and 3).
# ASCII-clean.

import utime
import debug

from config     import ConfigManager
from gnss_state import GNSSState
from gnss       import (GNSSService, _L76K,
                        PCAS_RATE_1HZ, PCAS_RATE_2HZ, PCAS_RATE_5HZ,
                        PCAS_BAUD_9600, PCAS_BAUD_115200,
                        PCAS_NMEA_MASK, PCAS_CONST_ALL)

debug.set_level(debug.DEBUG)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _separator(title=""):
    print()
    print("=" * 40)
    if title:
        print("  " + title)
        print("=" * 40)


def _measure_rate(gnss, duration_s, label):
    """Count sentences received over duration_s seconds."""
    print("Measuring {} for {}s...".format(label, duration_s))
    count = 0
    start = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), start) < duration_s * 1000:
        s = gnss.read_sentence(timeout_ms=2000)
        if s:
            count += 1
    elapsed = utime.ticks_diff(utime.ticks_ms(), start) / 1000.0
    rate = count / elapsed if elapsed > 0 else 0
    print("  {} sentences in {:.1f}s = {:.2f} Hz".format(count, elapsed, rate))
    return rate


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_sentence_read(gnss, cfg):
    _separator("Test 1: Sentence reading")
    dur = cfg.get("tests", "duration_s") or 10
    count = 0
    start = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), start) < dur * 1000:
        s = gnss.read_sentence(timeout_ms=2000)
        if s:
            count += 1
            print("  [{}] {}".format(count, s[:60]))
    print("  Total: {} sentences in {}s".format(count, dur))


def test_update_rates(gnss):
    _separator("Test 2: Update rates")
    for rate_cmd, label in [
        (PCAS_RATE_1HZ,  "1 Hz"),
        (PCAS_RATE_2HZ,  "2 Hz"),
        (PCAS_RATE_5HZ,  "5 Hz"),
    ]:
        gnss.send_command(rate_cmd)
        utime.sleep_ms(300)
        _measure_rate(gnss, 5, label)

    # Restore 1 Hz
    gnss.send_command(PCAS_RATE_1HZ)
    utime.sleep_ms(300)


def test_nmea_mask(gnss):
    _separator("Test 3: NMEA output mask")
    gnss.send_command(PCAS_NMEA_MASK)
    utime.sleep_ms(300)
    print("Mask applied. Listening for 5s:")
    start = utime.ticks_ms()
    seen = set()
    while utime.ticks_diff(utime.ticks_ms(), start) < 5000:
        s = gnss.read_sentence(timeout_ms=1500)
        if s:
            stype = s[3:6] if len(s) > 6 else "???"
            if stype not in seen:
                print("  Sentence type: {}".format(stype))
                seen.add(stype)
    print("  Types seen: {}".format(", ".join(sorted(seen))))


def test_baud_change(gnss):
    _separator("Test 4: Baud rate change 9600 <-> 115200")
    print("Switching to 115200...")
    gnss._gps.set_baud(115200)
    utime.sleep_ms(200)
    s = gnss.read_sentence(timeout_ms=2000)
    if s:
        print("  OK at 115200: {}".format(s[:50]))
    else:
        print("  WARN: no sentence at 115200")

    print("Switching back to 9600...")
    gnss._gps.set_baud(9600)
    utime.sleep_ms(200)
    s = gnss.read_sentence(timeout_ms=2000)
    if s:
        print("  OK at 9600: {}".format(s[:50]))
    else:
        print("  WARN: no sentence at 9600")


def test_constellation(gnss):
    _separator("Test 5: Constellation mode")
    gnss.send_command(PCAS_CONST_ALL)
    utime.sleep_ms(300)
    print("All constellations enabled. Listening 5s for talker IDs:")
    start = utime.ticks_ms()
    talkers = set()
    while utime.ticks_diff(utime.ticks_ms(), start) < 5000:
        s = gnss.read_sentence(timeout_ms=1500)
        if s and s.startswith("$"):
            talker = s[1:3]
            if talker not in talkers:
                print("  Talker: {}".format(talker))
                talkers.add(talker)
    print("  Talkers seen: {}".format(", ".join(sorted(talkers))))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all():
    _separator("GNSS Test Harness")

    cfg   = ConfigManager().load()
    state = GNSSState()
    gnss  = GNSSService(cfg, state)

    print("Waiting 2s for module to settle...")
    utime.sleep_ms(2000)

    try:
        test_sentence_read(gnss, cfg)
        test_update_rates(gnss)
        test_nmea_mask(gnss)
        test_baud_change(gnss)
        test_constellation(gnss)
    except Exception as e:
        debug.error("test harness exception: {}".format(e))
        raise

    _separator("All tests complete")


run_all()
