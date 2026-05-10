# main.py - PicoTracker orchestrator
# Wires modules together and runs the cooperative main loop.
# No business logic lives here.
# ASCII-clean.

import utime
import debug

from config     import ConfigManager
from gnss_state import GNSSState
from gnss       import GNSSService
from input      import InputController, AB_SHORT, B_LONG, A_LONG
from metrics    import MetricsManager
from activity   import ActivityManager
from recorder   import Recorder
from display    import DisplayController
from menu       import MenuController, MODE_SCREEN, ITEM_CONFIRM, ITEM_ACTION

# ---------------------------------------------------------------------------
# Screen definitions (screens shown in SCREEN mode, cycled by A_SHORT)
# Screens are rendered by _render_screen() below.
# ---------------------------------------------------------------------------
SCREENS = [
    "position",       # Lat / Lon
    "motion",         # Speed / Heading
    "altitude",       # Altitude
    "fix",            # Fix mode / sats / DOP
    "constellation",  # Per-constellation signal metrics
    "status",         # Time / Date / Recording state
    "storage",        # Flash used / free
]


def _fmt_lat(lat):
    """Format decimal degrees latitude to exactly 5dp. e.g. 55.02980N"""
    hemi = "N" if lat >= 0 else "S"
    val  = abs(lat)
    # Multiply to shift 5dp into integer, format, then reinsert decimal
    whole = int(val)
    frac  = int(round((val - whole) * 100000))
    return "{}.{:05d}{}".format(whole, frac, hemi)


def _fmt_lon(lon):
    """Format decimal degrees longitude to exactly 5dp. e.g. 1.54950W"""
    hemi = "E" if lon >= 0 else "W"
    val  = abs(lon)
    whole = int(val)
    frac  = int(round((val - whole) * 100000))
    return "{}.{:05d}{}".format(whole, frac, hemi)


def _heading_str(deg):
    """Convert heading degrees to compass abbreviation."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx  = int((deg + 22.5) / 45.0) % 8
    return dirs[idx]


def _render_screen(disp, snap, screen_name, recording, activity_name, rec=None):
    """Render one info screen to the display framebuffer."""
    import os
    disp.clear()

    # Status bar (top 9 pixels)
    # "FR S:06 2.1H" — fix, rec, sats, hdop
    fix_char = "F" if (snap and snap.fix_valid) else "-"
    rec_char = "R" if recording else "-"
    sats     = snap.sats_used if snap else 0
    hdop     = snap.hdop if snap else 0.0
    disp.text("{}{} S:{:02d} {:3.1f}H".format(
        fix_char, rec_char, sats, hdop), 0, 0, 1)
    disp.hline(0, 9, 128, 1)

    # Body (y=12 to y=63, 6 usable rows at 8px + 1px gap each = y 12,21,30,39,48,57)
    R = [12, 21, 30, 39, 48, 57]   # row y positions

    if snap is None or not snap.valid:
        disp.text("No Fix", 36, 30, 1)
        return

    if screen_name == "position":
        # Full-width rows — coords need 9+ chars
        disp.text("LAT " + _fmt_lat(snap.lat), 0, R[0], 1)
        disp.text("LON " + _fmt_lon(snap.lon), 0, R[2], 1)
        # Alt on bottom row right-aligned area
        disp.text("ALT {:.0f}m".format(snap.alt), 0, R[4], 1)

    elif screen_name == "motion":
        spd_kmh = snap.speed * 3.6
        # Left col: speed, Right col: heading
        disp.text("SPD", 0, R[0], 1)
        disp.text("{:.1f}".format(spd_kmh), 0, R[1], 1)
        disp.text("km/h", 0, R[2], 1)
        disp.text("HDG", 68, R[0], 1)
        disp.text("{:.0f}".format(snap.heading), 68, R[1], 1)
        disp.text(_heading_str(snap.heading), 68, R[2], 1)
        # Bottom row: activity and recording state
        disp.text("{:<8}{}".format(
            activity_name[:8], "REC" if recording else "   "), 0, R[4], 1)

    elif screen_name == "altitude":
        disp.text("ALT", 0, R[0], 1)
        disp.text("{:.1f}m".format(snap.alt), 0, R[1], 1)
        disp.text("VDOP {:.1f}".format(snap.vdop), 0, R[3], 1)
        disp.text("PDOP {:.1f}".format(snap.pdop), 0, R[4], 1)

    elif screen_name == "fix":
        mode_str = {0: "NO FIX", 2: "2D", 3: "3D"}.get(snap.fix_mode, "?")
        # Left col: fix mode + sats, Right col: DOP values
        disp.text("FIX  " + mode_str,              0, R[0], 1)
        disp.text("SAT  {:02d}/{:02d}".format(
            snap.sats_used, snap.sats_in_view),     0, R[1], 1)
        disp.text("HDOP {:.1f}".format(snap.hdop),  0, R[3], 1)
        disp.text("VDOP {:.1f}".format(snap.vdop),  0, R[4], 1)
        disp.text("PDOP {:.1f}".format(snap.pdop),  0, R[5], 1)

    elif screen_name == "status":
        d = snap.date
        t = snap.utc_time
        date_str = "{}/{}/{}".format(d[0:2], d[2:4], d[4:6]) if len(d) >= 6 else "--/--/--"
        time_str = "{}:{}:{}".format(t[0:2], t[2:4], t[4:6]) if len(t) >= 6 else "--:--:--"
        disp.text(date_str,                              0, R[0], 1)
        disp.text(time_str,                              0, R[1], 1)
        disp.text("ACT  " + activity_name[:8],           0, R[3], 1)
        disp.text("REC  " + ("ON " if recording else "OFF"), 0, R[4], 1)

    elif screen_name == "constellation":
        # Header: short enough to fit 128px
        disp.text("    used avg max", 0, R[0], 1)
        # Short label map
        short = {"GPS": "GP", "BDS": "BD", "GLO": "GL"}
        const_order = ["GPS", "BDS", "GLO"]
        row = 1
        for name in const_order:
            c = snap.constellations.get(name)
            lbl = short[name]
            if c:
                disp.text("{} {:2d}/{:2d}  {:2d}  {:2d}".format(
                    lbl,
                    c["used"], c["in_view"],
                    c["snr_avg"], c["snr_max"]), 0, R[row], 1)
            else:
                disp.text("{}  --/--  --  --".format(lbl), 0, R[row], 1)
            row += 1
        # Two-row DOP footer — fits cleanly
        disp.text("HDOP {:.1f}".format(snap.hdop), 0, R[4], 1)
        disp.text("PDOP {:.1f}".format(snap.pdop), 0, R[5], 1)

    elif screen_name == "storage":
        try:
            stats     = os.statvfs("/")
            blk       = stats[0]
            total_kb  = (stats[2] * blk) // 1024
            free_kb   = (stats[3] * blk) // 1024
            used_kb   = total_kb - free_kb
            pct       = (used_kb * 100) // total_kb if total_kb else 0
        except Exception:
            total_kb = free_kb = used_kb = pct = 0

        track_kb = rec.bytes_written // 1024 if rec else 0

        disp.text("FLASH",                                0, R[0], 1)
        disp.text("TOT  {:4d}KB".format(total_kb),        0, R[1], 1)
        disp.text("USED {:4d}KB {}%".format(used_kb, pct), 0, R[2], 1)
        disp.text("FREE {:4d}KB".format(free_kb),          0, R[3], 1)
        if rec and rec.is_recording:
            disp.text("TRK  {:4d}KB".format(track_kb),    0, R[5], 1)


def _render_menu(disp, menu):
    """Render the current menu level."""
    disp.clear()
    items, cursor = menu.menu_items()
    disp.text("MENU", 40, 0, 1)
    disp.hline(0, 10, 128, 1)
    for i, item in enumerate(items[:5]):   # Show up to 5 items
        y = 14 + i * 10
        prefix = ">" if i == cursor else " "
        label  = item["label"][:14]         # Clip to display width
        disp.text("{}{}".format(prefix, label), 0, y, 1)


def _render_edit(disp, menu):
    """Render the EDIT mode screen."""
    disp.clear()
    item, value = menu.edit_state()
    if item is None:
        return
    disp.text("EDIT", 40, 0, 1)
    disp.hline(0, 10, 128, 1)
    disp.text(item["label"][:16], 0, 14, 1)
    disp.text("< {} >".format(str(value)[:10]), 10, 32, 1)
    disp.text("B:OK  Blong:CANCEL", 0, 54, 1)


def _render_confirm(disp, menu):
    """Render the CONFIRM mode screen."""
    disp.clear()
    item, yes = menu.confirm_state()
    if item is None:
        return
    disp.text("CONFIRM?", 20, 0, 1)
    disp.hline(0, 10, 128, 1)
    disp.text(item["label"][:16], 0, 14, 1)
    # Show detail line (e.g. file size) if present
    detail = item.get("detail")
    if detail:
        disp.text(detail[:16], 0, 24, 1)
    disp.text("A:toggle  B:confirm", 0, 54, 1)
    if yes:
        disp.fill_rect(66, 36, 38, 12, 1)
        disp.text("[NO]",  10, 38, 1)
        disp.text("[YES]", 68, 38, 0)
    else:
        disp.fill_rect(6, 36, 38, 12, 1)
        disp.text("[NO]",  8, 38, 0)
        disp.text("[YES]", 70, 38, 1)


# ---------------------------------------------------------------------------
# Boot and main loop
# ---------------------------------------------------------------------------

def main():
    debug.info("picotracker: boot")

    # 1. Configuration
    cfg = ConfigManager().load()

    # 2. GNSS state (shared read-only object)
    state = GNSSState()

    # 3. Hardware modules
    gnss    = GNSSService(cfg, state)
    inp     = InputController(cfg)
    disp    = DisplayController(cfg)
    splash_ms = cfg.get("display", "splash_s") * 1000
    disp.show_splash(duration_ms=splash_ms)
    metrics = MetricsManager(state,
                             interval_ms=1000)  # Updated by activity selection
    act     = ActivityManager(cfg)
    rec     = Recorder(cfg)
    menu    = MenuController(cfg, act)

    # 4. Wire action callbacks into menu
    def _do_factory_reset():
        cfg.factory_reset()
        menu.config_changed = True

    def _load_track_items():
        """Build dynamic menu of track CSV files for deletion."""
        import os
        items = []
        try:
            files = sorted([f for f in os.listdir("/")
                           if f.startswith("track_") and f.endswith(".csv")])
        except Exception:
            files = []

        for f in files:
            try:
                size_bytes = os.stat(f)[6]
            except Exception:
                size_bytes = 0
            # Skip currently recording file
            if rec.is_recording and rec._filename == f:
                continue
            label  = f[6:-4]           # strip "track_" and ".csv"
            detail = "{:.1f}KB  {}pts".format(
                size_bytes / 1024,
                max(0, (size_bytes - 55) // 55))   # rough point estimate
            items.append({
                "label":  label[:16],
                "type":   ITEM_CONFIRM,
                "action": (lambda fn=f: os.remove(fn)),
                "detail": detail,
            })

        if not items:
            items.append({
                "label": "No tracks",
                "type":  ITEM_ACTION,
                "action": None,
            })
        return items

    menu.set_screen_count(len(SCREENS))
    menu.set_action("Recording/Start-Stop",  rec.toggle)
    menu.set_action("System/Save config",    cfg.save)
    menu.set_action("System/Factory reset",  _do_factory_reset)
    menu.set_loader("System/Manage tracks",  _load_track_items)

    # Reboot action (import late to avoid top-level machine import issues)
    try:
        import machine
        menu.set_action("System/Reboot", machine.reset)
    except Exception:
        pass

    # Sync recorder with default activity profile
    rec.set_profile(act.current)

    debug.info("picotracker: entering main loop")

    def _mark_waypoint():
        """Append current position to waypoints.csv and flash confirmation."""
        snap = metrics.get_snapshot()
        if snap is None or not snap.fix_valid:
            debug.warn("waypoint: no fix")
            return
        try:
            first = False
            try:
                import os
                os.stat("waypoints.csv")
            except OSError:
                first = True
            with open("waypoints.csv", "a") as f:
                if first:
                    f.write("utc_time,date,lat,lon,alt,hdop\r\n")
                f.write("{},{},{:.7f},{:.7f},{:.2f},{:.1f}\r\n".format(
                    snap.utc_time, snap.date,
                    snap.lat, snap.lon, snap.alt, snap.hdop))
            debug.info("waypoint: marked {:.5f},{:.5f}".format(snap.lat, snap.lon))
            # Brief confirmation flash — invert display for 200ms
            disp.set_brightness(255)
            utime.sleep_ms(200)
            disp.set_brightness(cfg.get("display", "brightness"))
        except Exception as e:
            debug.error("waypoint: failed ({})".format(e))

    # 5. Cooperative main loop
    while True:
        now = utime.ticks_ms()

        # --- Poll hardware ---
        gnss.poll()
        inp.poll()

        # --- Process input events ---
        event = inp.get_event()
        if event is not None:
            disp.touch()   # Reset screen timeout on any interaction

            # Global: AB_SHORT toggles recording
            if event == AB_SHORT:
                rec.toggle()
                rec.set_profile(act.current)
                debug.info("main: recording toggled -> {}".format(rec.is_recording))
            # Global: A_LONG marks waypoint (only in SCREEN mode)
            elif event == A_LONG and menu.mode == MODE_SCREEN:
                _mark_waypoint()
            else:
                changed = menu.handle(event)
                if changed:
                    rec.set_profile(act.current)
                # Apply display settings only when a config value was committed
                if menu.config_changed:
                    menu.config_changed = False
                    disp.set_rotation(cfg.get("display", "rotation"))
                    disp.set_timeout(cfg.get("display", "timeout_s"))
                    disp.set_brightness(cfg.get("display", "brightness"))

        # --- Metrics snapshot ---
        metrics.set_interval(act.current.interval_ms)
        new_snap = metrics.poll()
        snap = metrics.get_snapshot()

        # --- Recording ---
        if new_snap:
            rec.maybe_record(snap)

        # --- Display ---
        disp.check_timeout()

        mode = menu.mode
        if mode == MODE_SCREEN:
            _render_screen(disp, snap, SCREENS[menu.screen_index],
                           rec.is_recording, act.current.name, rec)
        elif mode == "MENU":
            _render_menu(disp, menu)
        elif mode == "EDIT":
            _render_edit(disp, menu)
        elif mode == "CONFIRM":
            _render_confirm(disp, menu)

        disp.refresh()

        # --- Yield ---
        utime.sleep_ms(10)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
