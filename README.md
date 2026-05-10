# PicoTracker

A GNSS activity tracker built on the Raspberry Pi Pico 2W. Records GPS tracks to CSV on flash, displays live position and signal data on an OLED screen, and supports multiple activity profiles.

By Andy Holmes

---

## Hardware

| Component | Part |
|-----------|------|
| MCU | Raspberry Pi Pico 2W |
| GNSS | Waveshare Pico-GPS-L76K (AT6558R / CASIC chipset) |
| Display | Waveshare Pico-OLED-1.3 (SH1107, 128×64, SPI) |

The three boards stack directly — Pico 2W at the bottom, GPS HAT in the middle, OLED HAT on top. No wiring required beyond the stacked headers.

### Pin assignments

| Signal | GPIO | Notes |
|--------|------|-------|
| GNSS TX → Pico RX | GP0 | UART0 |
| GNSS RX ← Pico TX | GP1 | UART0 |
| OLED DC | GP8 | SPI1 |
| OLED CS | GP9 | SPI1 |
| OLED CLK | GP10 | SPI1 |
| OLED DIN | GP11 | SPI1 |
| OLED RST | GP12 | |
| Button A (top) | GP17 | Active low, pull-up |
| Button B (bottom) | GP15 | Active low, pull-up |

---

## Features

- Live GNSS data — position, speed, heading, altitude
- 7 information screens cycled with buttons
- Per-constellation signal metrics (GPS, BeiDou, GLONASS)
- Four activity profiles — Walk, Run, Ride, Car — each with configurable recording interval, distance filter, speed threshold and auto-pause
- CSV track recording to internal flash with distance and speed filtering
- Waypoint marking — single button press saves current position to `waypoints.csv`
- Flash storage monitoring with used/free display
- Track file management — list and delete tracks from the device menu
- Configurable display rotation, brightness and screen timeout
- Splash screen on boot with scroll animation
- Settings persist across reboots via `config.json`
- Factory reset option

---

## Button controls

### Info screen mode

| Press | Action |
|-------|--------|
| A short | Next screen |
| B short | Previous screen |
| A long (~0.7s) | Mark waypoint |
| B long (~0.7s) | Open menu |
| A + B together | Toggle recording on/off |

### Menu mode

| Press | Action |
|-------|--------|
| A short | Cursor down |
| A long | Cursor up |
| B short | Select / confirm |
| B long | Back / exit |
| A + B together | Exit to info screen |

### Edit mode

| Press | Action |
|-------|--------|
| A short | Increment value |
| A long | Decrement value |
| B short | Confirm |
| B long | Cancel |

### Confirm dialog

| Press | Action |
|-------|--------|
| A short | Toggle YES / NO |
| B short | Execute selection |
| B long | Cancel |

---

## Info screens

Cycle through screens with A short (forward) and B short (backward).

| # | Screen | Shows |
|---|--------|-------|
| 1 | Position | Latitude, longitude, altitude |
| 2 | Motion | Speed (km/h), heading, compass direction, activity |
| 3 | Altitude | Altitude, VDOP, PDOP |
| 4 | Fix | Fix mode (2D/3D), satellites used/in-view, DOP values |
| 5 | Constellation | Per-constellation metrics — used/in-view, avg SNR, max SNR, HDOP, PDOP |
| 6 | Status | Date, time (UTC), activity, recording state |
| 7 | Storage | Flash total, used, free; current track size if recording |

### Status bar (all screens)

```
FR S:06 2.1H
```

| Character | Meaning |
|-----------|---------|
| `F` / `-` | Fix acquired / no fix |
| `R` / `-` | Recording active / inactive |
| `S:06` | Satellites used in fix |
| `2.1H` | HDOP value |

---

## Activity profiles

| Profile | Interval | Min distance | Min speed | Auto-pause |
|---------|----------|-------------|-----------|------------|
| Walk | 5s | 5m | 0.3 m/s | Yes |
| Run | 2s | 5m | 0.5 m/s | Yes |
| Ride | 2s | 10m | 0.8 m/s | Yes |
| Car | 10s | 20m | 1.0 m/s | No |

Select the active profile via **Menu → Activity**.

---

## Menu structure

```
Activity
  Walk / Run / Ride / Car

Recording
  Start-Stop

Display
  Rotation        (0 or 180 degrees)
  Splash secs     (0–15 seconds)
  Units           (metric / imperial)
  Brightness      (0–255)
  Timeout         (0–300 seconds)

System
  Save config
  Factory reset
  Manage tracks
  Reboot
```

---

## Output files

All files are written to the Pico's internal flash (2MB total).

### Track files — `track_<ticks>.csv`

One file per recording session. Columns:

```
utc_time, date, lat, lon, alt, speed, heading, hdop, sats_used
```

- `utc_time` — HHMMSS.sss
- `date` — DDMMYY
- `lat` / `lon` — decimal degrees (positive = N/E)
- `alt` — metres above MSL
- `speed` — m/s
- `heading` — degrees true
- `hdop` — horizontal dilution of precision
- `sats_used` — satellites contributing to fix

### Waypoints file — `waypoints.csv`

Appended to each time A long is pressed with a valid fix. Columns:

```
utc_time, date, lat, lon, alt, hdop
```

---

## Configuration

Settings are stored in `config.json` on flash. If the file is missing, defaults are used and a warning is logged. Save settings via **Menu → System → Save config**.

Key configurable values:

| Setting | Default | Description |
|---------|---------|-------------|
| `display.rotation` | 180 | 0 = buttons left, 180 = buttons right |
| `display.brightness` | 111 | 0–255 |
| `display.timeout_s` | 30 | Screen off after idle seconds (0 = never) |
| `display.splash_s` | 5 | Splash screen duration (0 = skip) |
| `recorder.max_file_size_kb` | 512 | Max track file size before auto-stop |
| `gnss.update_hz` | 1 | Fix rate (1, 2, 5, or 10 Hz) |
| `gnss.constellation` | 7 | Bitfield: 1=GPS, 2=BDS, 4=GLO, 7=all |
| `debug.level` | INFO | DEBUG / INFO / WARN / ERROR |

To factory reset all settings: **Menu → System → Factory reset → YES**.

---

## Project structure

```
PicoTracker/
  app/
    main.py          Orchestrator and main loop
    config.py        ConfigManager — load, save, validate
    debug.py         Centralised logging with levels
    gnss_state.py    GNSSState shared data object
    gnss.py          GNSSService — UART driver and NMEA parser
    input.py         InputController — debounce and event state machine
    display.py       DisplayController — SH1107 SPI driver and framebuffer
    metrics.py       MetricsManager — timed snapshots of GNSS state
    activity.py      ActivityManager and ActivityProfile
    recorder.py      CSV recorder with distance/speed filtering
    menu.py          MenuController — four-mode UI state machine
  tools/
    cleanup_tracks.py  Remove empty track files from flash
  docs/
    README.md
    deploy.sh
```

---

## Deployment

### Prerequisites

- Python 3 with `mpremote` installed: `pip install mpremote`
- MicroPython firmware on the Pico 2W

### Deploy

From the `PicoTracker` directory:

```bash
# Standard deploy — update all app files
bash docs/deploy.sh

# Full deploy — also delete config.json (resets to defaults on next boot)
bash docs/deploy.sh --full

# Full deploy and remove empty track files
bash docs/deploy.sh --full --clean
```

After a full deploy, boot the Pico and go to **Menu → System → Save config** to write a fresh `config.json`.

### Verify files on Pico

```python
import os
print(sorted(os.listdir('/')))
```

Expected files:
```
activity.py, config.json, config.py, debug.py, display.py,
gnss.py, gnss_state.py, input.py, main.py, menu.py,
metrics.py, recorder.py
```

### Remove track files from Pico

```bash
mpremote run tools/cleanup_tracks.py
```

---

## GNSS module notes

The AT6558R (CASIC chipset) uses PCAS proprietary commands rather than the more common PMTK command set. Key differences:

- Baud rate: `$PCAS01`
- Update rate: `$PCAS02`
- NMEA mask: `$PCAS03`
- Constellation: `$PCAS04`

The module outputs `GPTXT` startup messages which are silently filtered by the driver. A baud rate normalisation sequence runs on every boot to handle cases where the module was left at a non-default baud rate.

Constellation identification in the fix screen uses PRN number ranges since the module uses the `GN` (multi-constellation) talker for all GSA sentences:

| Constellation | PRN range |
|---------------|-----------|
| GPS | 1–32 |
| BeiDou | 33–64, 201–237 |
| GLONASS | 65–96 |

---

## Known limitations

- Altitude accuracy is poor indoors or near buildings (typical for consumer GNSS)
- Imperial units setting is stored but not yet applied to the display
- Track file listing shows estimated point count based on file size
- No GPX export — tracks are CSV only
- Waypoints file is not included in the Manage Tracks menu (intentional — it persists across sessions)
