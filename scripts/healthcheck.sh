#!/bin/sh
# Healthcheck: bridge process alive?
pgrep -f "bridge.py" >/dev/null 2>&1 || exit 1
