#!/bin/bash
# Uninstall OpenCode Go Meter (macOS / Linux): stop the app, remove the
# autostart entry, and delete the app data folder and the virtualenv.
set -e
cd "$(dirname "$0")"

echo "This will stop OpenCode Go Meter and remove:"
echo "  - the Start-at-Login entry (LaunchAgent / autostart)"
echo "  - app data in ~/.opencode-go-meter (including the saved login cookie)"
echo "  - the .venv folder in this project"
read -p "Continue? [y/N] " CONFIRM
case "$CONFIRM" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Cancelled."; exit 0 ;;
esac

echo ""
echo "Stopping any running instance and removing autostart..."
# Preferred path: let the app stop itself and unregister autostart (the
# disable() also unloads the LaunchAgent via launchctl bootout).
if [ -x .venv/bin/python ]; then
    .venv/bin/python launch.py --stop >/dev/null 2>&1 || true
    .venv/bin/python -c "from go_meter import autostart; autostart.disable()" >/dev/null 2>&1 || true
fi

# Fallback: remove the autostart entries directly, in case the .venv is
# already gone (so the Python path above could not run).
LABEL="local.opencode-go-meter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST"
fi
rm -f "$HOME/.config/autostart/opencode-go-meter.desktop"

echo "Removing app data (~/.opencode-go-meter) and .venv..."
rm -rf "$HOME/.opencode-go-meter"
rm -rf .venv

echo ""
echo "Done. OpenCode Go Meter has been removed."
echo "You can now delete this project folder if you want: $(pwd)"
read -p "Press Enter to close this window..."
