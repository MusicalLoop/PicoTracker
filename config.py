# config.py - ConfigManager for PicoTracker
# Single source of truth for all hardware configuration.
# Loads config.json from flash; validates GPIO assignments; provides safe defaults.
# ASCII-clean. No UTF-8 outside comments.

import json
import debug

CONFIG_FILE = "config.json"

# ---------------------------------------------------------------------------
# Safe hardware defaults - matched to actual build:
#   GNSS : Waveshare Pico-GPS-L76K (AT6558R) via UART0 GP0/GP1
#   OLED : Waveshare Pico-OLED-1.3 (SH1107) via SPI1 GP8-GP12
#   Keys : GP15 (KEY0 / button A), GP17 (KEY1 / button B)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "version": 1,

    "gnss": {
        "uart_id":      0,
        "tx_pin":       0,       # GP0 - Pico TX -> module RX
        "rx_pin":       1,       # GP1 - Pico RX <- module TX
        "baud":         9600,    # AT6558R default; raised to 115200 after init
        "update_hz":    1,       # 1 Hz default; max 10 Hz
        "constellation": 7       # 7 = GPS+GLONASS+BDS (PCAS04)
    },

    "display": {
        "spi_id":       1,       # SPI1
        "dc_pin":       8,       # GP8  OLED_DC
        "cs_pin":       9,       # GP9  OLED_CS
        "clk_pin":      10,      # GP10 OLED_CLK
        "din_pin":      11,      # GP11 OLED_DIN (MOSI)
        "rst_pin":      12,      # GP12 OLED_RST
        "width":        128,
        "height":       64,
        "rotation":     180,     # 0 = buttons left, 180 = buttons right
        "brightness":   111,     # 0-255, Waveshare default 0x6F=111
        "splash_s":     5,       # Splash screen duration in seconds (0 = skip)
        "timeout_s":    30,
        "units":        "metric" # "metric" or "imperial"
    },

    "input": {
        "button_a_pin":  17,     # GP17 KEY1 (top button with 180° rotation)
        "button_b_pin":  15,     # GP15 KEY0 (bottom button with 180° rotation)
        "active_low":    True,   # Buttons pull to GND when pressed
        "debounce_ms":   40,
        "long_press_ms": 700,
        "ab_window_ms":  80
    },

    "activity": {
        "default": "walk",
        "profiles": {
            "walk":  {"interval_ms": 5000,  "min_distance_m": 5,  "min_speed_mps": 0.3, "auto_pause": True,  "smoothing": True},
            "run":   {"interval_ms": 2000,  "min_distance_m": 5,  "min_speed_mps": 0.5, "auto_pause": True,  "smoothing": True},
            "ride":  {"interval_ms": 2000,  "min_distance_m": 10, "min_speed_mps": 0.8, "auto_pause": True,  "smoothing": False},
            "car":   {"interval_ms": 10000, "min_distance_m": 20, "min_speed_mps": 1.0, "auto_pause": False, "smoothing": False}
        }
    },

    "recorder": {
        "format":              "csv",
        "storage":             "internal",
        "max_file_size_kb":    512,
        "flush_every_n_points": 10
    },

    "tests": {
        "duration_s": 20
    },

    "debug": {
        "level": "INFO"   # DEBUG / INFO / WARN / ERROR
    }
}

# ---------------------------------------------------------------------------
# Known UART pin pairs - used for conflict detection
# Each entry: (uart_id, tx, rx)
# ---------------------------------------------------------------------------
_UART_PINS = [
    (0, 0, 1),
    (0, 12, 13),
    (0, 16, 17),
    (1, 4, 5),
    (1, 8, 9),
]

# ---------------------------------------------------------------------------
# Known SPI pin sets - used for conflict detection
# Each entry: (spi_id, clk, mosi)
# ---------------------------------------------------------------------------
_SPI_PINS = [
    (0, 2, 3),
    (0, 6, 7),
    (0, 18, 19),
    (1, 10, 11),
    (1, 14, 15),
]


class ConfigManager:

    def __init__(self):
        self._cfg = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self):
        """Load config.json from flash. Falls back to DEFAULTS on any error."""
        try:
            with open(CONFIG_FILE, "r") as f:
                loaded = json.load(f)
            self._cfg = self._merge(DEFAULTS, loaded)
            debug.info("config: loaded from {}".format(CONFIG_FILE))
        except OSError:
            debug.warn("config: {} not found - using defaults".format(CONFIG_FILE))
            self._cfg = self._deep_copy(DEFAULTS)
        except Exception as e:
            debug.error("config: parse error ({}) - using defaults".format(e))
            self._cfg = self._deep_copy(DEFAULTS)

        self._apply_debug_level()
        self._validate()
        return self

    def save(self):
        """Write current config to config.json."""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._cfg, f)
            debug.info("config: saved to {}".format(CONFIG_FILE))
            return True
        except Exception as e:
            debug.error("config: save failed ({})".format(e))
            return False

    def factory_reset(self):
        """Restore defaults and save."""
        self._cfg = self._deep_copy(DEFAULTS)
        debug.warn("config: factory reset")
        return self.save()

    def get(self, section, key=None):
        """Return a config section dict, or a single value within a section."""
        section_data = self._cfg.get(section, {})
        if key is None:
            return section_data
        return section_data.get(key)

    def set(self, section, key, value):
        """Update a single value. Does not save to flash automatically."""
        if section not in self._cfg:
            self._cfg[section] = {}
        self._cfg[section][key] = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_debug_level(self):
        level_map = {"DEBUG": debug.DEBUG, "INFO": debug.INFO,
                     "WARN": debug.WARN, "ERROR": debug.ERROR}
        level_str = self._cfg.get("debug", {}).get("level", "INFO")
        debug.set_level(level_map.get(level_str, debug.INFO))

    def _validate(self):
        """Basic conflict detection. Logs warnings; does not abort boot."""
        errors = []
        gnss = self._cfg.get("gnss", {})
        disp = self._cfg.get("display", {})
        inp  = self._cfg.get("input", {})

        uart_pins = {gnss.get("tx_pin"), gnss.get("rx_pin")}
        spi_pins  = {disp.get("clk_pin"), disp.get("din_pin"),
                     disp.get("cs_pin"),  disp.get("dc_pin"), disp.get("rst_pin")}
        btn_pins  = {inp.get("button_a_pin"), inp.get("button_b_pin")}

        # Check UART vs SPI
        conflict = uart_pins & spi_pins
        if conflict:
            errors.append("UART/SPI pin conflict: GP{}".format(conflict))

        # Check UART vs buttons
        conflict = uart_pins & btn_pins
        if conflict:
            errors.append("UART/button pin conflict: GP{}".format(conflict))

        # Check SPI vs buttons
        conflict = spi_pins & btn_pins
        if conflict:
            errors.append("SPI/button pin conflict: GP{}".format(conflict))

        for e in errors:
            debug.error("config: " + e)

        if not errors:
            debug.info("config: pin validation OK")

    def _merge(self, base, override):
        """Recursively merge override into base. Returns new dict."""
        result = self._deep_copy(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._merge(result[k], v)
            else:
                result[k] = v
        return result

    def _deep_copy(self, obj):
        if isinstance(obj, dict):
            return {k: self._deep_copy(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._deep_copy(i) for i in obj]
        return obj
