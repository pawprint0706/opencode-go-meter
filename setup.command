#!/bin/bash
# Double-click to install OpenCode Go Meter on macOS.
cd "$(dirname "$0")"

if ./setup.sh; then
    :
else
    echo ""
    echo "Setup failed - see the messages above."
    read -p "Press Enter to close this window..."
fi
