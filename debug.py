# debug.py - Centralised logging for PicoTracker
# Replaces all raw print() calls in modules.
# Dependency-free, MicroPython-safe, ASCII-clean.

import utime

DEBUG = 0
INFO  = 1
WARN  = 2
ERROR = 3

_LEVEL_NAMES = ("DEBUG", "INFO ", "WARN ", "ERROR")

_level = INFO  # Default runtime level


def set_level(level):
    global _level
    _level = level


def get_level():
    return _level


def _log(level, msg):
    if level >= _level:
        ms = utime.ticks_ms()
        print("[{}] {:8d} {}".format(_LEVEL_NAMES[level], ms, msg))


def debug(msg):
    _log(DEBUG, msg)


def info(msg):
    _log(INFO, msg)


def warn(msg):
    _log(WARN, msg)


def error(msg):
    _log(ERROR, msg)
