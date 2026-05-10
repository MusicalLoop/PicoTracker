# input.py - InputController for PicoTracker
# Non-blocking debounce. Emits semantic button events.
# ASCII-clean. No UTF-8 outside comments.

import utime
from machine import Pin
import debug

# ---------------------------------------------------------------------------
# Semantic event constants
# ---------------------------------------------------------------------------
A_SHORT  = "A_SHORT"
B_SHORT  = "B_SHORT"
A_LONG   = "A_LONG"
B_LONG   = "B_LONG"
AB_SHORT = "AB_SHORT"

# Button identifiers
_BTN_A = 0
_BTN_B = 1

# Button states
_UP        = 0   # Not pressed
_DOWN      = 1   # Pressed, debounce pending
_HELD      = 2   # Confirmed pressed, waiting for release or long-press
_LONG_WAIT = 3   # Long press fired, waiting for physical release


class InputController:
    """
    Reads GP15 (KEY0/A) and GP17 (KEY1/B).
    Call poll() once per main loop iteration.
    Retrieve events with get_event() (returns one event string or None).
    """

    def __init__(self, config):
        inp = config.get("input")

        self._debounce_ms   = inp.get("debounce_ms",   40)
        self._long_ms       = inp.get("long_press_ms", 700)
        self._ab_window_ms  = inp.get("ab_window_ms",  80)
        self._active_low    = inp.get("active_low",    True)

        pull = Pin.PULL_UP if self._active_low else Pin.PULL_DOWN

        self._pins = [
            Pin(inp["button_a_pin"], Pin.IN, pull),
            Pin(inp["button_b_pin"], Pin.IN, pull),
        ]

        # Per-button state machine
        self._state      = [_UP, _UP]
        self._down_at    = [0, 0]     # ticks_ms when button first read as pressed
        self._stable_at  = [0, 0]     # ticks_ms when debounce confirmed

        # AB detection
        self._ab_armed   = False
        self._ab_arm_ms  = 0

        self._events = []

        debug.info("input: A=GP{} B=GP{} debounce={}ms long={}ms".format(
            inp["button_a_pin"], inp["button_b_pin"],
            self._debounce_ms, self._long_ms))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll(self):
        """
        Must be called every main loop iteration (ideally < 20 ms apart).
        Advances the state machine and queues any new events.
        """
        now = utime.ticks_ms()
        raw = [self._read(i) for i in range(2)]

        self._update_button(0, raw[0], now)
        self._update_button(1, raw[1], now)
        self._check_ab(now)

    def get_event(self):
        """Return the oldest pending event string, or None if queue is empty."""
        if self._events:
            return self._events.pop(0)
        return None

    def flush(self):
        """Clear all pending events."""
        self._events.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read(self, idx):
        """Return True if button is physically pressed (accounts for active_low)."""
        raw = self._pins[idx].value()
        return (raw == 0) if self._active_low else (raw == 1)

    def _update_button(self, idx, pressed, now):
        state = self._state[idx]

        if state == _UP:
            if pressed:
                self._state[idx] = _DOWN
                self._down_at[idx] = now

        elif state == _DOWN:
            if not pressed:
                # Bounced - reset
                self._state[idx] = _UP
            elif utime.ticks_diff(now, self._down_at[idx]) >= self._debounce_ms:
                # Debounce confirmed
                self._state[idx] = _HELD
                self._stable_at[idx] = now
                self._arm_ab(idx, now)

        elif state == _HELD:
            if not pressed:
                # Released before long-press threshold — emit short press
                self._state[idx] = _UP
                if not self._ab_consumed(idx):
                    self._emit(A_SHORT if idx == _BTN_A else B_SHORT)
            elif utime.ticks_diff(now, self._stable_at[idx]) >= self._long_ms:
                # Long press threshold reached — wait for release before accepting input
                self._state[idx] = _LONG_WAIT
                self._emit(A_LONG if idx == _BTN_A else B_LONG)
                self._disarm_ab()

        elif state == _LONG_WAIT:
            # Long press already fired — just wait for button to be released
            if not pressed:
                self._state[idx] = _UP

    def _arm_ab(self, idx, now):
        """When one button is confirmed down, open the AB detection window."""
        other = 1 - idx
        if self._state[other] == _HELD:
            # Both are already held - fire AB immediately
            self._emit(AB_SHORT)
            # Transition both to _LONG_WAIT so they're suppressed until released
            self._state[0] = _LONG_WAIT
            self._state[1] = _LONG_WAIT
            self._ab_armed = False
        else:
            self._ab_armed  = True
            self._ab_arm_ms = now

    def _disarm_ab(self):
        self._ab_armed = False

    def _ab_consumed(self, idx):
        """True if AB event was fired and this button should be suppressed."""
        return False   # AB resets state directly, so this is always clean

    def _check_ab(self, now):
        """Expire the AB window if it has timed out."""
        if self._ab_armed:
            if utime.ticks_diff(now, self._ab_arm_ms) > self._ab_window_ms:
                self._ab_armed = False

    def _emit(self, event):
        debug.debug("input: event={}".format(event))
        self._events.append(event)
