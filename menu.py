# menu.py - MenuController for PicoTracker
# Table-driven state machine. Consumes semantic events from InputController.
# ASCII-clean.

import debug
from input import A_SHORT, B_SHORT, A_LONG, B_LONG, AB_SHORT

# ---------------------------------------------------------------------------
# UI modes
# ---------------------------------------------------------------------------
MODE_SCREEN  = "SCREEN"
MODE_MENU    = "MENU"
MODE_EDIT    = "EDIT"
MODE_CONFIRM = "CONFIRM"

# ---------------------------------------------------------------------------
# Menu item types
# ---------------------------------------------------------------------------
ITEM_SUBMENU = "submenu"   # Enters a child menu
ITEM_EDIT    = "edit"      # Enters EDIT mode for a value
ITEM_ACTION  = "action"    # Triggers a callback immediately
ITEM_CONFIRM = "confirm"   # Requires confirmation before action
ITEM_DYNAMIC = "dynamic"   # Submenu built at navigation time via loader()


# ---------------------------------------------------------------------------
# Menu structure
# ---------------------------------------------------------------------------

def _build_menu(activity_manager, config):
    """Build the static menu tree. Returns root item list."""

    activity_items = [
        {"label": name, "type": ITEM_ACTION,
         "action": lambda k=key: activity_manager.select(k)}
        for key, name in activity_manager.profile_names()
    ]

    return [
        {
            "label": "Activity",
            "type": ITEM_SUBMENU,
            "children": activity_items,
        },
        {
            "label": "Recording",
            "type": ITEM_SUBMENU,
            "children": [
                {"label": "Start-Stop",  "type": ITEM_ACTION, "action": None},  # wired in main
                {"label": "Auto-pause",    "type": ITEM_EDIT,
                 "key": ("activity", "default")},   # placeholder - needs elaboration
            ],
        },
        {
            "label": "Display",
            "type": ITEM_SUBMENU,
            "children": [
                {"label": "Rotation",
                 "type": ITEM_EDIT,
                 "options": [0, 180],
                 "cfg_section": "display", "cfg_key": "rotation"},
                {"label": "Splash secs",
                 "type": ITEM_EDIT,
                 "min": 0, "max": 15, "step": 1,
                 "cfg_section": "display", "cfg_key": "splash_s"},
                {"label": "Units",
                 "type": ITEM_EDIT,
                 "options": ["metric", "imperial"],
                 "cfg_section": "display", "cfg_key": "units"},
                {"label": "Brightness",
                 "type": ITEM_EDIT,
                 "min": 0, "max": 255, "step": 16,
                 "cfg_section": "display", "cfg_key": "brightness"},
                {"label": "Timeout",
                 "type": ITEM_EDIT,
                 "min": 0, "max": 300, "step": 10,
                 "cfg_section": "display", "cfg_key": "timeout_s"},
            ],
        },
        {
            "label": "System",
            "type": ITEM_SUBMENU,
            "children": [
                {"label": "Save config",    "type": ITEM_CONFIRM, "action": None},
                {"label": "Factory reset",  "type": ITEM_CONFIRM, "action": None},
                {"label": "Manage tracks",  "type": ITEM_DYNAMIC, "loader": None},
                {"label": "Reboot",         "type": ITEM_CONFIRM, "action": None},
            ],
        },
    ]


class MenuController:
    """
    Implements the four-mode UI state machine described in the project spec.
    Caller must set action callbacks after construction:
        menu.set_action("Recording/Start / Stop", recorder.toggle)
        menu.set_action("System/Save config", config.save)
        menu.set_action("System/Reboot", machine.reset)
    """

    def __init__(self, config, activity_manager):
        self._config   = config
        self._mode     = MODE_SCREEN
        self._screen_index = 0
        self._screen_count = 0   # Set by DisplayController

        self._menu_stack = []   # Stack of (item_list, cursor_index)
        self._root_items = _build_menu(activity_manager, config)

        # EDIT state
        self._edit_item      = None
        self._edit_value     = None
        self._edit_orig      = None

        # CONFIRM state
        self._confirm_item   = None
        self._confirm_yes    = False

        self.config_changed  = False   # Set True when any config value is committed

        debug.info("menu: init mode={}".format(self._mode))

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_screen_count(self, n):
        """Inform the menu how many info screens exist."""
        self._screen_count = n

    def set_loader(self, path, loader):
        """Wire a loader callback to an ITEM_DYNAMIC item by path."""
        parts = path.split("/")
        items = self._root_items
        for part in parts:
            match = next((i for i in items if i["label"] == part), None)
            if match is None:
                debug.warn("menu: path not found: {}".format(path))
                return
            if "children" in match:
                items = match["children"]
            else:
                match["loader"] = loader
                return

    def set_action(self, path, callback):
        """Wire a callback to a menu item by path string e.g. 'System/Reboot'."""
        parts = path.split("/")
        items = self._root_items
        for part in parts:
            match = next((i for i in items if i["label"] == part), None)
            if match is None:
                debug.warn("menu: path not found: {}".format(path))
                return
            if "children" in match:
                items = match["children"]
            else:
                match["action"] = callback
                return

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def mode(self):
        return self._mode

    @property
    def screen_index(self):
        return self._screen_index

    def menu_items(self):
        """Return the current item list and cursor index for rendering."""
        if self._menu_stack:
            items, cursor, _ = self._menu_stack[-1]
            return items, cursor
        return self._root_items, 0

    def edit_state(self):
        """Return (item, current_value) for EDIT mode rendering."""
        return self._edit_item, self._edit_value

    def confirm_state(self):
        """Return (item, yes_selected) for CONFIRM mode rendering."""
        return self._confirm_item, self._confirm_yes

    # ------------------------------------------------------------------
    # Event handler - call once per main loop iteration
    # ------------------------------------------------------------------

    def handle(self, event):
        """Process one semantic event. Returns True if display needs updating."""
        if event is None:
            return False

        # Global transitions (any mode)
        if event == AB_SHORT:
            # AB_SHORT handled by caller for recording toggle
            # Return to SCREEN if in menu
            if self._mode != MODE_SCREEN:
                self._exit_to_screen()
                return True
            return False   # Caller handles recording toggle

        if event == B_LONG and self._mode == MODE_SCREEN:
            self._enter_menu()
            return True

        # Mode-specific handlers
        if self._mode == MODE_SCREEN:
            return self._handle_screen(event)
        elif self._mode == MODE_MENU:
            return self._handle_menu(event)
        elif self._mode == MODE_EDIT:
            return self._handle_edit(event)
        elif self._mode == MODE_CONFIRM:
            return self._handle_confirm(event)

        return False

    # ------------------------------------------------------------------
    # Per-mode handlers
    # ------------------------------------------------------------------

    def _handle_screen(self, event):
        if event == A_SHORT:
            if self._screen_count > 0:
                self._screen_index = (self._screen_index + 1) % self._screen_count
            return True
        if event == B_SHORT:
            if self._screen_count > 0:
                self._screen_index = (self._screen_index - 1) % self._screen_count
            return True
        return False

    def _handle_menu(self, event):
        items, cursor, loader = self._menu_stack[-1] if self._menu_stack else (self._root_items, 0, None)

        if event == A_SHORT:
            cursor = (cursor + 1) % len(items)
            self._set_cursor(cursor)
            return True

        if event == A_LONG:
            cursor = (cursor - 1) % len(items)
            self._set_cursor(cursor)
            return True

        if event == B_SHORT:
            item = items[cursor]
            if item["type"] == ITEM_SUBMENU:
                self._menu_stack.append((item["children"], 0, None))
            elif item["type"] == ITEM_DYNAMIC:
                loader = item.get("loader")
                if loader:
                    dynamic_items = loader()
                    self._menu_stack.append((dynamic_items, 0, loader))
                    debug.info("menu: dynamic {} items loaded".format(
                        len(dynamic_items)))
            elif item["type"] == ITEM_EDIT:
                self._start_edit(item)
            elif item["type"] == ITEM_ACTION:
                if item.get("action"):
                    item["action"]()
                self._exit_to_screen()
            elif item["type"] == ITEM_CONFIRM:
                self._confirm_item = item
                self._confirm_yes  = False
                self._mode = MODE_CONFIRM
            return True

        if event == B_LONG:
            if len(self._menu_stack) > 1:
                self._menu_stack.pop()
            else:
                self._exit_to_screen()
            return True

        return False

    def _handle_edit(self, event):
        item = self._edit_item
        if item is None:
            return False

        if event == A_SHORT:
            self._edit_value = self._edit_step(item, +1)
            return True

        if event == A_LONG:
            self._edit_value = self._edit_step(item, -1)
            return True

        if event == B_SHORT:
            # Confirm - write value back to config
            self._commit_edit()
            self._mode = MODE_MENU
            return True

        if event == B_LONG:
            # Cancel - discard
            self._edit_value = self._edit_orig
            self._mode = MODE_MENU
            return True

        if event == AB_SHORT:
            # Restore default
            key = item.get("cfg_key")
            section = item.get("cfg_section")
            if key and section:
                default = self._config._deep_copy(
                    self._config._cfg[section][key])  # noqa: accessing internal for default
                self._edit_value = default
            return True

        return False

    def _handle_confirm(self, event):
        if event == A_SHORT:
            self._confirm_yes = not self._confirm_yes
            return True

        if event == B_SHORT:
            if self._confirm_yes and self._confirm_item:
                action = self._confirm_item.get("action")
                if action:
                    action()
                # If parent level is a dynamic list, reload it now
                if self._menu_stack:
                    items, cursor, loader = self._menu_stack[-1]
                    if loader:
                        fresh = loader()
                        # Clamp cursor in case list shrank
                        cursor = min(cursor, max(0, len(fresh) - 1))
                        self._menu_stack[-1] = (fresh, cursor, loader)
                        debug.info("menu: dynamic list reloaded {} items".format(
                            len(fresh)))
            self._mode = MODE_MENU
            return True

        if event in (B_LONG, AB_SHORT):
            self._confirm_yes  = False
            self._confirm_item = None
            self._mode = MODE_MENU
            return True

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enter_menu(self):
        self._menu_stack = [(self._root_items, 0, None)]
        self._mode = MODE_MENU
        debug.info("menu: entered MENU")

    def _exit_to_screen(self):
        self._menu_stack = []
        self._mode = MODE_SCREEN
        debug.info("menu: returned to SCREEN")

    def _set_cursor(self, cursor):
        if self._menu_stack:
            items, _, loader = self._menu_stack[-1]
            self._menu_stack[-1] = (items, cursor, loader)
        debug.debug("menu: cursor={}".format(cursor))

    def _start_edit(self, item):
        self._edit_item = item
        section = item.get("cfg_section")
        key     = item.get("cfg_key")
        if section and key:
            self._edit_value = self._config.get(section, key)
            self._edit_orig  = self._edit_value
        elif "options" in item:
            self._edit_value = item["options"][0]
            self._edit_orig  = self._edit_value
        self._mode = MODE_EDIT
        debug.info("menu: EDIT {} = {}".format(item["label"], self._edit_value))

    def _edit_step(self, item, direction):
        val = self._edit_value
        if "options" in item:
            opts = item["options"]
            idx  = opts.index(val) if val in opts else 0
            return opts[(idx + direction) % len(opts)]
        # Numeric
        step = item.get("step", 1)
        mn   = item.get("min", 0)
        mx   = item.get("max", 255)
        return max(mn, min(mx, val + direction * step))

    def _commit_edit(self):
        item    = self._edit_item
        section = item.get("cfg_section")
        key     = item.get("cfg_key")
        if section and key:
            self._config.set(section, key, self._edit_value)
            self.config_changed = True
            debug.info("menu: set {}/{} = {}".format(
                section, key, self._edit_value))
