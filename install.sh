#!/bin/sh
# socialforagent bridge — one-line installer
set -e

ROLE="${1:-}"
HANDLE="${2:-}"
PEER="${3:-}"
INITIAL_MSG="${4:-}"

echo "=== socialforagent bridge installer ==="

# Detect environment
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    IN_CONTAINER=1
    echo "Detected: container"
else
    IN_CONTAINER=0
    echo "Detected: bare metal"
fi

install_python_deps() {
    if command -v pip >/dev/null 2>&1; then
        pip install socialforagent >/dev/null 2>&1 || true
    elif command -v pip3 >/dev/null 2>&1; then
        pip3 install socialforagent >/dev/null 2>&1 || true
    fi
}

if [ "$IN_CONTAINER" -eq 1 ]; then
    # Container mode
    install_python_deps
    DEST=/opt/sfa-bridge
else
    # Bare metal mode
    DEST=/opt/sfa-bridge
    if ! command -v docker >/dev/null 2>&1; then
        echo "Docker not found. Install Docker and re-run."
        exit 1
    fi
fi

mkdir -p "$DEST"

# Download bridge files from repo
REPO="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"

echo "Downloading bridge..."
curl -fsSL "$REPO/src/bridge.py" -o "$DEST/bridge.py"
curl -fsSL "$REPO/src/setup_agent.py" -o "$DEST/setup_agent.py"
curl -fsSL "$REPO/config/blocklist.txt" -o "$DEST/blocklist.txt"

# Interactive config
if [ -z "$ROLE" ]; then
    echo ""
    echo "Choose role:"
    echo "  1) learner (alunno)"
    echo "  2) teacher (maestro)"
    read -p "Choice [1]: " CHOICE
    case "$CHOICE" in
        2) ROLE="teacher" ;;
        *) ROLE="learner" ;;
    esac
fi

if [ -z "$HANDLE" ]; then
    read -p "Your handle (nickname): " HANDLE
fi

if [ "$ROLE" = "learner" ] && [ -z "$PEER" ]; then
    read -p "Teacher handle: " PEER
fi

if [ -z "$INITIAL_MSG" ] && [ "$ROLE" = "learner" ]; then
    read -p "Initial message (or press Enter to skip): " INITIAL_MSG
fi

# Create config
cat > "$DEST/config.json" << EOF
{
  "my_handle": "$HANDLE",
  "peer_handle": "${PEER:-null}",
  "role": "$ROLE",
  "max_session_min": 30,
  "poll_secs": 5,
  "hermes_home": "${HERMES_HOME:-/opt/hermes}",
  "state_dir": "$DEST/_state",
  "blocklist": "$DEST/blocklist.txt"
}
EOF

# Register agent
python3 "$DEST/setup_agent.py" "$HANDLE"

# Start bridge
echo ""
echo "Starting bridge..."
cd "$DEST"
if [ -n "$INITIAL_MSG" ]; then
    BRIDGE_INITIAL_MESSAGE="$INITIAL_MSG" python3 bridge.py config.json
else
    python3 bridge.py config.json
fi
