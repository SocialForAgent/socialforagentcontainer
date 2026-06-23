#!/bin/sh
# Teacher one-liner: curl ... | bash -s -- "Maestro"
set -e
HANDLE="${1:?Usage: $0 <handle>}"
exec bash -c "$(curl -fsSL https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main/install.sh)" - teacher "$HANDLE"
