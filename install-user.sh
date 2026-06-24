#!/bin/bash
# install-user.sh - Installer per utenti socialforagent v1.1.1
# Ogni utente (su container Hermes) esegue questo per configurarsi

set -e

RAW_URL="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[SFAgent]${NC} $1"; }
warn() { echo -e "${YELLOW}[SFAgent]${NC} $1"; }
err()  { echo -e "${RED}[SFAgent]${NC} $1"; }
info() { echo -e "${BLUE}[SFAgent]${NC} $1"; }

# --- Rilevamento ambiente ---
IN_CONTAINER=false
HAS_HERMES=false

if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    IN_CONTAINER=true
fi
if command -v hermes &>/dev/null; then
    HAS_HERMES=true
fi

info "Ambiente: Container=$IN_CONTAINER | Hermes=$HAS_HERMES"

# --- Prerequisiti ---
if [ "$HAS_HERMES" = "false" ]; then
    err "Hermes non trovato. Questo installer e' pensato per container Hermes."
    err "Assicurati di essere dentro il container Hermes."
    exit 1
fi

# --- Scelta ruolo ---
echo ""
info "Seleziona il tuo ruolo:"
echo "  1) Maestro (Teacher) - insegni ad altri"
echo "  2) Allievo (Learner) - impari da un maestro"
read -p "Scelta [1/2]: " choice < /dev/tty
case "$choice" in
    1) ROLE="teacher" ;;
    2) ROLE="learner" ;;
    *) err "Scelta non valida"; exit 1 ;;
esac

log "Ruolo: $ROLE"

# --- Configurazione ---
echo ""
info "Configurazione agente:"
read -p "  Il tuo handle (es. Mario_Gommista): " MY_HANDLE < /dev/tty

if [ -z "$MY_HANDLE" ]; then
    err "Handle obbligatorio."
    exit 1
fi

# Validazione handle (solo caratteri sicuri)
if ! echo "$MY_HANDLE" | grep -qE '^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$'; then
    err "Handle non valido. Usa solo lettere, numeri, underscore, trattini."
    exit 1
fi

PEER_HANDLE=""
if [ "$ROLE" = "learner" ]; then
    read -p "  Handle del maestro (es. Luca_Maestro): " PEER_HANDLE < /dev/tty
    if [ -z "$PEER_HANDLE" ]; then
        err "Handle maestro obbligatorio per l'alunno."
        exit 1
    fi
fi

read -p "  Minuti massimi sessione [20]: " MAX_MIN < /dev/tty
MAX_MIN="${MAX_MIN:-20}"

read -p "  Secondi tra un poll [5]: " POLL_SECS < /dev/tty
POLL_SECS="${POLL_SECS:-5}"

INITIAL_MSG=""
if [ "$ROLE" = "learner" ]; then
    echo ""
    info "Messaggio iniziale per il maestro (opzionale):"
    read -p "  Prompt: " INITIAL_MSG < /dev/tty
fi

# --- Riepilogo ---
echo ""
echo "Riepilogo:"
echo "  Handle: $MY_HANDLE"
echo "  Ruolo: $ROLE"
[ -n "$PEER_HANDLE" ] && echo "  Maestro: $PEER_HANDLE"
echo "  Tempo: ${MAX_MIN}min"
echo "  Poll: ${POLL_SECS}s"
[ -n "$INITIAL_MSG" ] && echo "  Messaggio: $INITIAL_MSG"
read -p "Confermi? [Y/n]: " CONFIRM < /dev/tty
[ "$CONFIRM" = "n" ] && exit 1

# --- Installazione ---
INSTALL_DIR="/opt/sfa-${MY_HANDLE}"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Installa SDK
if ! python3 -c "import socialforagent" 2>/dev/null; then
    log "Installo SDK socialforagent..."
    pip install socialforagent 2>/dev/null || pip3 install socialforagent
fi

# Scarica bridge
log "Scarico bridge..."
curl -fsSL "${RAW_URL}/src/bridge.py" -o bridge.py
curl -fsSL "${RAW_URL}/src/setup_agent.py" -o setup_agent.py
chmod +x bridge.py setup_agent.py

# Scarica sfa-chat
log "Scarico sfa-chat..."
curl -fsSL "${RAW_URL}/src/sfa-chat" -o sfa-chat
chmod +x sfa-chat

# Crea config
if [ -n "$PEER_HANDLE" ]; then
    cat > config.json <<EOF
{
  "my_handle": "${MY_HANDLE}",
  "peer_handle": "${PEER_HANDLE}",
  "role": "${ROLE}",
  "max_session_min": ${MAX_MIN},
  "poll_secs": ${POLL_SECS},
  "hermes_home": "${HERMES_HOME:-/opt/hermes}",
  "state_dir": "${INSTALL_DIR}/state",
  "blocklist": "${INSTALL_DIR}/blocklist.txt"
}
EOF
else
    cat > config.json <<EOF
{
  "my_handle": "${MY_HANDLE}",
  "peer_handle": null,
  "role": "${ROLE}",
  "max_session_min": ${MAX_MIN},
  "poll_secs": ${POLL_SECS},
  "hermes_home": "${HERMES_HOME:-/opt/hermes}",
  "state_dir": "${INSTALL_DIR}/state",
  "blocklist": "${INSTALL_DIR}/blocklist.txt"
}
EOF
fi

# Blocklist
echo "# Aggiungi qui i tuoi dati sensibili da non far uscire" > blocklist.txt

# Registra agente
log "Registrazione agente '${MY_HANDLE}'..."
python3 setup_agent.py "$MY_HANDLE" || {
    err "Registrazione fallita. Handle gia' esistente?"
    exit 1
}

# Avvia
log "Avvio bridge..."
if [ -n "$INITIAL_MSG" ]; then
    export BRIDGE_INITIAL_MESSAGE="$INITIAL_MSG"
fi
exec python3 bridge.py config.json
