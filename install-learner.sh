#!/bin/sh
# Learner one-liner: curl ... | bash -s -- "Allievo" "Maestro" "Ciao"
set -e
HANDLE="${1:?Usage: $0 <handle> <teacher_handle> [initial_message]}"
PEER="${2:?Usage: $0 <handle> <teacher_handle> [initial_message]}"
MSG="${3:-}"
exec bash -c "$(curl -fsSL https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main/install.sh)" - learner "$HANDLE" "$PEER" "$MSG"
