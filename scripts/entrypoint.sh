#!/bin/sh
# Entrypoint per container Docker
cd /opt/sfa-bridge
exec python3 bridge.py config.json
