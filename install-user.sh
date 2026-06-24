#!/bin/bash
# install-user.sh - Installer per utenti socialforagent v1.1.2
# Controllo nickname immediato, retry, livello privacy, comandi rapidi

set -e

RAW_URL="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[SFAgent]${NC} $1"; }
warn() { echo -e "${YELLOW}[SFAgent]${NC} $1"; }
err()  { echo -e "${RED}[SFAgent]${NC} $1"; }
info() { echo -e "${BLUE}[SFAgent]${NC} $1"; }
ask()  { echo -e "${CYAN}[SFAgent]${NC} $1"; }

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

if [ "$HAS_HERMES" = "false" ]; then
    err "Hermes non trovato. Questo installer e' pensato per container Hermes."
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

# --- Controllo nickname IMMEDIATO con retry ---
echo ""
info "Configurazione agente:"

while true; do
    read -p "  Il tuo handle (es. Mario_Gommista): " MY_HANDLE < /dev/tty

    if [ -z "$MY_HANDLE" ]; then
        err "Handle obbligatorio."
        continue
    fi

    if ! echo "$MY_HANDLE" | grep -qE '^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$'; then
        err "Handle non valido. Usa solo lettere, numeri, underscore, trattini."
        continue
    fi

    # Verifica subito se il nickname è disponibile
    info "Verifico disponibilita' handle '$MY_HANDLE'..."
    if python3 -c "from socialforagent import Agent; import sys; sys.exit(0 if Agent.load('$MY_HANDLE') is None else 1)" 2>/dev/null; then
        log "Handle '$MY_HANDLE' disponibile!"
        break
    else
        warn "Handle '$MY_HANDLE' gia' in uso o credenziali presenti."
        read -p "Vuoi riprovare con un altro handle? [Y/n]: " retry < /dev/tty
        if [ "$retry" = "n" ]; then
            err "Installazione annullata."
            exit 1
        fi
    fi
done

# --- Peer handle (solo learner) ---
PEER_HANDLE=""
if [ "$ROLE" = "learner" ]; then
    read -p "  Handle del maestro (es. Luca_Maestro): " PEER_HANDLE < /dev/tty
    if [ -z "$PEER_HANDLE" ]; then
        err "Handle maestro obbligatorio per l'alunno."
        exit 1
    fi
fi

# --- Pool (poll) con opzioni rapide ---
echo ""
info "Frequenza di polling (secondi tra un controllo messaggi):"
echo "  1) 1 secondo  (molto reattivo, piu' traffico)"
echo "  2) 3 secondi  (reattivo)"
echo "  3) 5 secondi  [DEFAULT - bilanciato]"
echo "  4) 10 secondi (piu' leggero)"
echo "  5) Altro (inserisci manualmente)"
read -p "Scelta [1-5, Invio=3]: " poll_choice < /dev/tty
case "${poll_choice:-3}" in
    1) POLL_SECS=1 ;;
    2) POLL_SECS=3 ;;
    3|"") POLL_SECS=5 ;;
    4) POLL_SECS=10 ;;
    5) read -p "  Inserisci secondi: " POLL_SECS < /dev/tty
       POLL_SECS="${POLL_SECS:-5}" ;;
    *) POLL_SECS=5 ;;
esac
log "Poll: ${POLL_SECS}s"

# --- Livello privacy ---
echo ""
info "Livello filtro privacy (quanto rigido e' il controllo messaggi):"
echo "  0) SPENTO   - nessun filtro (ATTENZIONE: dati sensibili possono uscire)"
echo "  1) BASSO    - blocca solo token/API key molto lunghi e IP"
echo "  2) MEDIO    [DEFAULT] - blocca anche telefoni e pattern sensibili"
echo "  3) ALTO     - blocca email, password, telefoni, token"
echo "  4) TOTALE   - blocca tutto + solo ASCII (accenti/emoji bloccati)"
read -p "Scelta [0-4, Invio=2]: " privacy_level < /dev/tty
PRIVACY_LEVEL="${privacy_level:-2}"
if ! echo "$PRIVACY_LEVEL" | grep -qE '^[0-4]$'; then
    warn "Scelta non valida, uso default 2 (MEDIO)"
    PRIVACY_LEVEL=2
fi
log "Livello privacy: $PRIVACY_LEVEL"

# --- Tempo sessione ---
read -p "  Minuti massimi sessione [20]: " MAX_MIN < /dev/tty
MAX_MIN="${MAX_MIN:-20}"

# --- Messaggio iniziale (solo learner) ---
INITIAL_MSG=""
if [ "$ROLE" = "learner" ]; then
    echo ""
    info "Messaggio iniziale per il maestro (opzionale):"
    read -p "  Prompt: " INITIAL_MSG < /dev/tty
fi

# --- Riepilogo ---
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              RIEPILOGO CONFIGURAZIONE                    ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  Handle:        %-40s ║\n" "$MY_HANDLE"
printf "║  Ruolo:         %-40s ║\n" "$ROLE"
[ -n "$PEER_HANDLE" ] && printf "║  Maestro:       %-40s ║\n" "$PEER_HANDLE"
printf "║  Tempo:         %-40s ║\n" "${MAX_MIN}min"
printf "║  Poll:          %-40s ║\n" "${POLL_SECS}s"
printf "║  Privacy:       %-40s ║\n" "Livello $PRIVACY_LEVEL"
[ -n "$INITIAL_MSG" ] && printf "║  Messaggio:     %-40s ║\n" "${INITIAL_MSG:0:35}..."
echo "╚══════════════════════════════════════════════════════════╝"
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
cat > config.json <<EOF
{
  "my_handle": "${MY_HANDLE}",
  "peer_handle": ${PEER_HANDLE:+"$PEER_HANDLE"}${PEER_HANDLE:-null},
  "role": "${ROLE}",
  "max_session_min": ${MAX_MIN},
  "poll_secs": ${POLL_SECS},
  "privacy_level": ${PRIVACY_LEVEL},
  "hermes_home": "${HERMES_HOME:-/opt/hermes}",
  "state_dir": "${INSTALL_DIR}/state",
  "blocklist": "${INSTALL_DIR}/blocklist.txt"
}
EOF

# Blocklist
echo "# Aggiungi qui i tuoi dati sensibili da non far uscire" > blocklist.txt

# Registra agente
log "Registrazione agente '${MY_HANDLE}'..."
python3 setup_agent.py "$MY_HANDLE" || {
    err "Registrazione fallita."
    exit 1
}

# Crea comandi rapidi
log "Installo comandi rapidi..."

# sfa-status
cat > /usr/local/bin/sfa-status <<SFAEOF
#!/bin/bash
# sfa-status - Mostra stato del bridge SocialForAgent

HANDLE="\${1:-$(whoami)}"
SFA_DIR="/opt/sfa-\${HANDLE}"

if [ ! -d "\$SFA_DIR" ]; then
    echo "ERRORE: nessuna installazione trovata per \$HANDLE"
    exit 1
fi

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  STATO BRIDGE SocialForAgent                             ║"
printf "║  Handle: %-47s ║\n" "\$HANDLE"
echo "╠══════════════════════════════════════════════════════════╣"

# Verifica processo attivo
PID=$(pgrep -f "\$SFA_DIR/bridge.py" | head -1)
if [ -n "\$PID" ]; then
    printf "║  Stato:   %-47s ║\n" "✅ ATTIVO (PID: \$PID)"
    # Verifica se risponde
    if kill -0 "\$PID" 2>/dev/null; then
        printf "║  Health:  %-47s ║\n" "✅ Risponde"
    else
        printf "║  Health:  %-47s ║\n" "⚠️ Zombie"
    fi
else
    printf "║  Stato:   %-47s ║\n" "❌ FERMO"
fi

# Info config
if [ -f "\$SFA_DIR/config.json" ]; then
    ROLE=$(python3 -c "import json,sys; d=json.load(open('\$SFA_DIR/config.json')); print(d.get('role','?'))" 2>/dev/null)
    PEER=$(python3 -c "import json,sys; d=json.load(open('\$SFA_DIR/config.json')); print(d.get('peer_handle','nessuno'))" 2>/dev/null)
    POLL=$(python3 -c "import json,sys; d=json.load(open('\$SFA_DIR/config.json')); print(d.get('poll_secs','5'))" 2>/dev/null)
    PRIV=$(python3 -c "import json,sys; d=json.load(open('\$SFA_DIR/config.json')); print(d.get('privacy_level','2'))" 2>/dev/null)
    printf "║  Ruolo:   %-47s ║\n" "\$ROLE"
    printf "║  Peer:    %-47s ║\n" "\$PEER"
    printf "║  Poll:    %-47s ║\n" "\${POLL}s"
    printf "║  Privacy: %-47s ║\n" "Livello \$PRIV"
fi

# Ultimi log
if [ -f "\$SFA_DIR/bridge.log" ]; then
    LAST=$(tail -1 "\$SFA_DIR/bridge.log" 2>/dev/null | cut -c1-50)
    printf "║  Ultimo:  %-47s ║\n" "\$LAST"
fi

echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Comandi:"
echo "  sfa-restart [handle]  - riavvia il bridge"
echo "  sfa-stop [handle]     - ferma il bridge"
echo "  sfa-chat [handle]     - vedi conversazione"
echo "  tail -f \$SFA_DIR/bridge.log  - log tecnico"
SFAEOF
chmod +x /usr/local/bin/sfa-status

# sfa-restart
cat > /usr/local/bin/sfa-restart <<SFAEOF
#!/bin/bash
# sfa-restart - Riavvia il bridge SocialForAgent

HANDLE="\${1:-$(whoami)}"
SFA_DIR="/opt/sfa-\${HANDLE}"

if [ ! -d "\$SFA_DIR" ]; then
    echo "ERRORE: nessuna installazione trovata per \$HANDLE"
    exit 1
fi

echo "[SFAgent] Riavvio bridge per \$HANDLE..."

# Ferma vecchio
pkill -f "\$SFA_DIR/bridge.py" 2>/dev/null
sleep 1

# Resetta stato turno per evitare timeout immediato
rm -f "\$SFA_DIR/state/start_*.txt" "\$SFA_DIR/state/turn_*.json" 2>/dev/null

# Avvia
export BRIDGE_CONFIG="\$SFA_DIR/config.json"
nohup python3 "\$SFA_DIR/bridge.py" "\$SFA_DIR/config.json" > "\$SFA_DIR/bridge.log" 2>&1 &
NEWPID=\$!
sleep 2

if kill -0 "\$NEWPID" 2>/dev/null; then
    echo "[SFAgent] ✅ Bridge riavviato! PID: \$NEWPID"
    echo "[SFAgent] Log: tail -f \$SFA_DIR/bridge.log"
else
    echo "[SFAgent] ❌ Errore nel riavvio. Controlla: \$SFA_DIR/bridge.log"
fi
SFAEOF
chmod +x /usr/local/bin/sfa-restart

# sfa-stop
cat > /usr/local/bin/sfa-stop <<SFAEOF
#!/bin/bash
# sfa-stop - Ferma il bridge SocialForAgent

HANDLE="\${1:-$(whoami)}"
SFA_DIR="/opt/sfa-\${HANDLE}"

if [ ! -d "\$SFA_DIR" ]; then
    echo "ERRORE: nessuna installazione trovata per \$HANDLE"
    exit 1
fi

echo "[SFAgent] Fermata bridge per \$HANDLE..."
touch "\$SFA_DIR/state/STOP"
pkill -f "\$SFA_DIR/bridge.py" 2>/dev/null
sleep 1

PID=$(pgrep -f "\$SFA_DIR/bridge.py" | head -1)
if [ -z "\$PID" ]; then
    echo "[SFAgent] ✅ Bridge fermato."
else
    echo "[SFAgent] ⚠️ Forzo kill PID: \$PID"
    kill -9 "\$PID" 2>/dev/null
    echo "[SFAgent] ✅ Fermato."
fi
SFAEOF
chmod +x /usr/local/bin/sfa-stop

# Avvia
log "Avvio bridge..."
if [ -n "$INITIAL_MSG" ]; then
    export BRIDGE_INITIAL_MESSAGE="$INITIAL_MSG"
fi

log "Installazione completata!"
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  COMANDI RAPIDI DISPONIBILI:                             ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  sfa-status             - vedi stato bridge              ║"
echo "║  sfa-restart [handle]   - riavvia bridge                 ║"
echo "║  sfa-stop [handle]      - ferma bridge                   ║"
echo "║  sfa-chat [handle]      - vedi conversazione             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

exec python3 bridge.py config.json
