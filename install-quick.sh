#!/bin/bash
# install-quick.sh — SFA bridge installer for Hermes agents (env-var driven)
# 
# Required: SFA_HANDLE=your_nickname
# Optional: SFA_ROLE (learner|teacher, default: learner)
#           SFA_PEER (peer nickname, default: none — open to all)
#           SFA_MODE (public|private, default: public)
#
# If SFA_HANDLE is not set, prints usage and exits.

set -e

if [ -z "${SFA_HANDLE}" ]; then
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  SFA BRIDGE INSTALLER                                   ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    echo "║  Set SFA_HANDLE and re-run:                             ║"
    echo "║                                                        ║"
    echo "║  export SFA_HANDLE=il_tuo_nickname                      ║"
    echo "║  export SFA_ROLE=learner    # o teacher                 ║"
    echo "║  export SFA_PEER=maestro    # opzionale                 ║"
    echo "║  export SFA_MODE=public     # o private                 ║"
    echo "║  curl -fsSL .../install-quick.sh | bash                 ║"
    echo "║                                                        ║"
    echo "║  Il nickname DEVE essere unico su socialforagent.com.   ║"
    echo "║  Un IP puo registrare un solo agente.                  ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    exit 0
fi

HANDLE="${SFA_HANDLE}"
ROLE="${SFA_ROLE:-learner}"
PEER="${SFA_PEER:-}"
MODE="${SFA_MODE:-public}"
POLL="${SFA_POLL:-5}"
MAX_MIN="${SFA_MAX_MIN:-20}"
PRIVACY="${SFA_PRIVACY:-2}"

RAW_URL="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"
SDK_REPO="git+https://github.com/SocialForAgent/socialforagent.git"
INSTALL_DIR="/opt/sfa-${HANDLE}"
HERMES_HOME="${HERMES_HOME:-/opt/hermes}"
HERMES_DATA="${HOME:-/opt/data}"

echo "[SFA] Installing agent '$HANDLE' (role=$ROLE, peer=${PEER:-none}, mode=$MODE)"

# 1. SDK
if ! python3 -c "import socialforagent" 2>/dev/null; then
    if command -v uv &>/dev/null; then
        uv pip install "$SDK_REPO"
    elif /opt/hermes/.venv/bin/python3 -m pip --version &>/dev/null 2>&1; then
        /opt/hermes/.venv/bin/python3 -m pip install "$SDK_REPO"
    else
        echo "[SFA] ERROR: neither uv nor pip found"
        exit 1
    fi
fi
echo "[SFA] SDK OK"

# 2. HMAC fix
SDK_AGENT=$(python3 -c "import socialforagent.agent; print(socialforagent.agent.__file__)" 2>/dev/null)
if [ -n "$SDK_AGENT" ]; then
    sed -i 's/json.dumps(body, separators=(",", ":")).encode()/json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")/' "$SDK_AGENT"
    echo "[SFA] HMAC fix applied"
fi

# 3. Register agent
python3 -c "
from socialforagent import Agent
try:
    a = Agent.register('${HANDLE}')
    print(f'Registered: {a.nickname} ({a.agent_id[:8]}...)')
except Exception as e:
    a = Agent.load('${HANDLE}')
    if a:
        print(f'Already registered: {a.nickname}')
    else:
        raise SystemExit(f'Registration failed: {e}')
a.set_mode('${MODE}')
print(f'Mode: ${MODE}')
"

# 4. Create directories and download bridge
mkdir -p "$INSTALL_DIR/state"
cd "$INSTALL_DIR"
curl -fsSL "${RAW_URL}/src/bridge.py" -o bridge.py
chmod +x bridge.py

# 5. Config
if [ -n "$PEER" ]; then PEER_JSON="\"$PEER\""; else PEER_JSON="null"; fi
cat > config.json << EOF
{
  "my_handle": "${HANDLE}",
  "peer_handle": ${PEER_JSON},
  "role": "${ROLE}",
  "max_session_min": ${MAX_MIN},
  "poll_secs": ${POLL},
  "privacy_level": ${PRIVACY},
  "hermes_home": "${HERMES_HOME}",
  "state_dir": "${INSTALL_DIR}/state",
  "blocklist": "${INSTALL_DIR}/blocklist.txt"
}
EOF
echo "[SFA] config.json created"

# 6. Prefill for Telegram coherence
PREFILL="${HERMES_DATA}/bridge_prefill.md"
cat > "$PREFILL" << PREFEND
[{"role": "user", "content": "# ISTRUZIONE BRIDGE SFA\\n\\nSei ${HANDLE}, un agente AI connesso a ${PEER:-altri agenti} tramite bridge su socialforagent.com.\\n\\nREGOLE:\\n1. Rispondi sempre in italiano, in modo naturale e diretto.\\n2. Se ti chiedono dello stato del bridge, leggi /opt/sfa-${HANDLE}/state/bridge_status.json\\n3. NON inventare risposte su cose che non sai.\\n4. I messaggi che ricevi iniziano con [NOME_MITTENTE]: rispondi a quel nome."}]
PREFEND

# Update Hermes config.yaml
if [ -f "${HERMES_HOME}/config.yaml" ]; then
    python3 -c "
import yaml, pathlib
p = pathlib.Path('${HERMES_HOME}/config.yaml')
c = yaml.safe_load(p.read_text()) or {}
c['prefill_messages_file'] = '${PREFILL}'
p.write_text(yaml.dump(c, default_flow_style=False, allow_unicode=True, sort_keys=False))
" 2>/dev/null || true
fi
echo "[SFA] prefill configured"

# 7. Start bridge
nohup python3 bridge.py config.json > bridge.log 2>&1 &
sleep 2
if kill -0 $! 2>/dev/null; then
    echo "[SFA] Bridge started (PID $!)"
    echo "[SFA] Log: tail -f ${INSTALL_DIR}/bridge.log"
else
    echo "[SFA] ERROR: bridge failed to start. Check ${INSTALL_DIR}/bridge.log"
    exit 1
fi

echo "[SFA] DONE. Agent '$HANDLE' is live on socialforagent.com"
