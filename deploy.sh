#!/bin/bash
# deploy.sh - Deploy PicoTracker to Pico via mpremote
# Run from the PicoTracker root directory.
#
# Usage:
#   bash docs/deploy.sh           # Deploy app files only
#   bash docs/deploy.sh --full    # Deploy + delete config.json (fresh start)
#   bash docs/deploy.sh --clean   # Also remove empty track CSV files

set -e

APP="app"
TOOLS="tools"

APP_FILES="debug.py config.py gnss_state.py gnss.py input.py display.py \
           metrics.py activity.py recorder.py menu.py main.py"

echo "=== PicoTracker deploy ==="
echo ""

for f in $APP_FILES; do
    echo "  -> $f"
    mpremote cp "$APP/$f" ":$f"
done

# Remove stale files no longer part of the build
for f in splash.py GNSS_Test.py README.md; do
    mpremote exec "import os
try:
    os.remove('$f')
    print('  -- removed $f')
except:
    pass" 2>/dev/null || true
done

echo ""

if [[ "$*" == *"--full"* ]]; then
    echo "  -- removing config.json (will reset to defaults on boot)"
    mpremote exec "import os
try:
    os.remove('config.json')
    print('  -- removed config.json')
except:
    pass" 2>/dev/null || true
fi

if [[ "$*" == *"--clean"* ]]; then
    echo "  -- cleaning empty track files"
    mpremote run "$TOOLS/cleanup_tracks.py"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Files on Pico:"
mpremote exec "import os; [print(' ', f) for f in sorted(os.listdir('/'))]"
