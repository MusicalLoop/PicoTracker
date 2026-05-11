# gpx.py - CSV to GPX converter for PicoTracker
# Reads a track CSV file and writes a GPX 1.1 file.
# ASCII-clean. No UTF-8 outside comments.

import os
import debug

# GPX template parts — built with string concatenation to avoid
# multi-line strings (MicroPython handles them fine but keeps file clean)
_HDR = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<gpx version="1.1" creator="PicoTracker"\r\n'
    '  xmlns="http://www.topografix.com/GPX/1/1"\r\n'
    '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\r\n'
    '  xsi:schemaLocation="http://www.topografix.com/GPX/1/1 '
    'http://www.topografix.com/GPX/1/1/gpx.xsd">\r\n'
)
_TRK_OPEN  = '  <trk>\r\n    <name>{}</name>\r\n    <trkseg>\r\n'
_TRKPT     = ('      <trkpt lat="{}" lon="{}">\r\n'
              '        <ele>{}</ele>\r\n'
              '        <time>{}</time>\r\n'
              '        <speed>{}</speed>\r\n'
              '        <hdop>{}</hdop>\r\n'
              '      </trkpt>\r\n')
_TRK_CLOSE = '    </trkseg>\r\n  </trk>\r\n</gpx>\r\n'


def _parse_iso_time(utc_time, date):
    """
    Convert NMEA utc_time (HHMMSS.sss) and date (DDMMYY) to ISO 8601.
    e.g. '071128.000' + '110526' -> '2026-05-11T07:11:28Z'
    Returns empty string on parse failure.
    """
    try:
        hh = utc_time[0:2]
        mm = utc_time[2:4]
        ss = utc_time[4:6]
        dd = date[0:2]
        mo = date[2:4]
        yy = date[4:6]
        return "20{}-{}-{}T{}:{}:{}Z".format(yy, mo, dd, hh, mm, ss)
    except Exception:
        return ""


def _estimate_gpx_size(csv_size):
    """GPX is ~3x CSV due to XML verbosity. Add 20% margin."""
    return int(csv_size * 3.6)


def convert(csv_filename, gpx_filename=None):
    """
    Convert a track CSV file to GPX 1.1.

    csv_filename : source CSV path on flash
    gpx_filename : output GPX path (default: same name, .gpx extension)

    Returns (True, gpx_filename) on success, (False, error_message) on failure.
    """
    if gpx_filename is None:
        # Replace .csv with .gpx
        if csv_filename.endswith(".csv"):
            gpx_filename = csv_filename[:-4] + ".gpx"
        else:
            gpx_filename = csv_filename + ".gpx"

    # Check source exists
    try:
        csv_size = os.stat(csv_filename)[6]
    except OSError:
        return False, "source not found: {}".format(csv_filename)

    # Check free space
    try:
        stats    = os.statvfs("/")
        free     = stats[0] * stats[3]
        needed   = _estimate_gpx_size(csv_size)
        if free < needed + 51200:   # 50KB safety margin
            return False, "insufficient space ({} needed, {} free)".format(
                needed, free)
    except Exception as e:
        return False, "statvfs error: {}".format(e)

    # Track name from filename (strip path and extension)
    track_name = csv_filename
    if "/" in track_name:
        track_name = track_name.rsplit("/", 1)[1]
    if track_name.endswith(".csv"):
        track_name = track_name[:-4]

    # Convert
    points = 0
    try:
        with open(csv_filename, "r") as src, open(gpx_filename, "w") as dst:
            dst.write(_HDR)
            dst.write(_TRK_OPEN.format(track_name))

            first = True
            for line in src:
                line = line.strip()
                if not line or line.startswith("utc_time"):
                    continue   # Skip header and blank lines

                parts = line.split(",")
                if len(parts) < 8:
                    continue

                utc_time = parts[0]
                date     = parts[1]
                lat      = parts[2]
                lon      = parts[3]
                ele      = parts[4]
                speed    = parts[5]
                # parts[6] = heading (not in GPX standard trkpt)
                hdop     = parts[7]

                iso_time = _parse_iso_time(utc_time, date)
                if not iso_time:
                    continue

                dst.write(_TRKPT.format(lat, lon, ele, iso_time, speed, hdop))
                points += 1

            dst.write(_TRK_CLOSE)

    except Exception as e:
        # Clean up partial file
        try:
            os.remove(gpx_filename)
        except Exception:
            pass
        return False, "conversion error: {}".format(e)

    debug.info("gpx: converted {} -> {} ({} points)".format(
        csv_filename, gpx_filename, points))
    return True, gpx_filename
