# activity.py - ActivityProfile and ActivityManager for PicoTracker
# ASCII-clean.

import debug


class ActivityProfile:
    """Immutable activity configuration loaded from config."""

    def __init__(self, name, interval_ms, min_distance_m,
                 min_speed_mps, auto_pause, smoothing):
        self.name           = name
        self.interval_ms    = interval_ms
        self.min_distance_m = min_distance_m
        self.min_speed_mps  = min_speed_mps
        self.auto_pause     = auto_pause
        self.smoothing      = smoothing

    def __repr__(self):
        return "ActivityProfile({} {}ms min_d={} min_spd={})".format(
            self.name, self.interval_ms,
            self.min_distance_m, self.min_speed_mps)


# Display names for menu rendering
PROFILE_DISPLAY_NAMES = {
    "walk":  "Walk",
    "run":   "Run",
    "ride":  "Ride",
    "car":   "Car",
}


class ActivityManager:
    """Loads activity profiles from config. Tracks current selection."""

    def __init__(self, config):
        act_cfg  = config.get("activity")
        profiles_raw = act_cfg.get("profiles", {})

        self._profiles = {}
        for key, p in profiles_raw.items():
            self._profiles[key] = ActivityProfile(
                name           = PROFILE_DISPLAY_NAMES.get(key, key),
                interval_ms    = p.get("interval_ms",    5000),
                min_distance_m = p.get("min_distance_m", 5),
                min_speed_mps  = p.get("min_speed_mps",  0.3),
                auto_pause     = p.get("auto_pause",      True),
                smoothing      = p.get("smoothing",       True),
            )

        self._keys = list(self._profiles.keys())
        default    = act_cfg.get("default", "walk")
        self._current_key = default if default in self._profiles else self._keys[0]

        debug.info("activity: loaded {} profiles, default={}".format(
            len(self._profiles), self._current_key))

    @property
    def current(self):
        """Return the active ActivityProfile."""
        return self._profiles[self._current_key]

    @property
    def current_key(self):
        return self._current_key

    def select(self, key):
        """Set active profile by key. Returns True if key is valid."""
        if key in self._profiles:
            self._current_key = key
            debug.info("activity: selected {}".format(key))
            return True
        debug.warn("activity: unknown profile key: {}".format(key))
        return False

    def next_profile(self):
        """Cycle to the next profile. Returns the new profile."""
        idx = self._keys.index(self._current_key)
        self._current_key = self._keys[(idx + 1) % len(self._keys)]
        debug.info("activity: cycled to {}".format(self._current_key))
        return self.current

    def profile_names(self):
        """Return list of (key, display_name) tuples for menu rendering."""
        return [(k, self._profiles[k].name) for k in self._keys]
