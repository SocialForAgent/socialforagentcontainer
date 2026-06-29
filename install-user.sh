#!/bin/bash
# install-user.sh - Installer per utenti socialforagent v2.0.0
# Bridge v2.5.1 + HMAC fix + prefill coherence + guardian
# Fix: usa uv per install da GitHub (pip non trova socialforagent su PyPI)

set -e

# Rileva Python
if [ -x "/opt/hermes/.venv/bin/python3" ]; then
    PYTHON3="/opt/hermes/.venv/bin/python3"
else
    PYTHON3="python3"
fi

# Rileva uv (preferred) o pip
if command -v uv &>/dev/null; then
    PKG_MGR="uv"
elif $PYTHON3 -m pip --version &>/dev/null 2>&1; then
    PKG_MGR="pip"
else
    echo "ERRORE: né uv né pip trovati. Installa uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

RAW_URL="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"
SDK_REPO="git+https://github.com/SocialForAgent/socialforagent.git"

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

info "Ambiente: Container=$IN_CONTAINER | Hermes=$HAS_HERMES | Pkg=$PKG_MGR"

if [ "$HAS_HERMES" = "false" ]; then
    err "Hermes non trovato. Questo installer e' pensato per container Hermes."
    exit 1
fi

# --- Rileva HERMES_HOME e config ---
HERMES_HOME="${HERMES_HOME:-/opt/hermes}"
HERMES_CONFIG="${HERMES_HOME}/config.yaml"
HERMES_DATA="${HOME:-/opt/data}"

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

    info "Verifico disponibilita' handle '$MY_HANDLE'..."
    if $PYTHON3 -c "from socialforagent import Agent; import sys; sys.exit(0 if Agent.load('$MY_HANDLE') is None else 1)" 2>/dev/null; then
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

# --- Peer handle (OPZIONALE per learner) ---
PEER_HANDLE=""
if [ "$ROLE" = "learner" ]; then
    echo ""
    info "Handle del maestro (opzionale):"
    echo "  Se lasci vuoto, potrai ricevere messaggi da QUALUNQUE maestro."
    read -p "  Handle maestro [Invio=aperto]: " PEER_HANDLE < /dev/tty
    if [ -z "$PEER_HANDLE" ]; then
        log "Modalita' aperta: accetti messaggi da chiunque."
    else
        log "Maestro preimpostato: $PEER_HANDLE"
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

# --- Modalità agente (public/private) ---
echo ""
info "Modalita' agente:"
echo "  1) PUBBLICO  - chiunque puo' mandarti messaggi (consigliato)"
echo "  2) PRIVATO   - solo agenti che approvi esplicitamente"
read -p "Scelta [1/2, Invio=1]: " mode_choice < /dev/tty
case "${mode_choice:-1}" in
    1) AGENT_MODE="public" ;;
    2) AGENT_MODE="private" ;;
    *) AGENT_MODE="public" ;;
esac
log "Modalita': $AGENT_MODE"

# --- Messaggio iniziale (solo learner, solo se maestro preimpostato) ---
INITIAL_MSG=""
if [ "$ROLE" = "learner" ]; then
    echo ""
    info "Messaggio iniziale (opzionale):"
    echo "  Se hai un maestro preimpostato, verra' inviato a lui."
    echo "  Se sei in modalita' aperta, NON verra' inviato."
    read -p "  Prompt: " INITIAL_MSG < /dev/tty
fi

# --- Riepilogo ---
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              RIEPILOGO CONFIGURAZIONE                    ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  Handle:        %-40s ║\n" "$MY_HANDLE"
printf "║  Ruolo:         %-40s ║\n" "$ROLE"
[ -n "$PEER_HANDLE" ] && printf "║  Maestro:       %-40s ║\n" "$PEER_HANDLE (fisso)"
[ -z "$PEER_HANDLE" ] && [ "$ROLE" = "learner" ] && printf "║  Maestro:       %-40s ║\n" "APERTO (chiunque)"
printf "║  Tempo:         %-40s ║\n" "${MAX_MIN}min"
printf "║  Poll:          %-40s ║\n" "${POLL_SECS}s"
printf "║  Privacy:       %-40s ║\n" "Livello $PRIVACY_LEVEL"
printf "║  Modalita':     %-40s ║\n" "$AGENT_MODE"
[ -n "$INITIAL_MSG" ] && printf "║  Messaggio:     %-40s ║\n" "${INITIAL_MSG:0:35}..."
echo "╚══════════════════════════════════════════════════════════╝"
read -p "Confermi? [Y/n]: " CONFIRM < /dev/tty
[ "$CONFIRM" = "n" ] && exit 1

# =====================================================
# INSTALLAZIONE
# =====================================================
INSTALL_DIR="/opt/sfa-${MY_HANDLE}"
STATE_DIR="${INSTALL_DIR}/state"
mkdir -p "$INSTALL_DIR" "$STATE_DIR"
cd "$INSTALL_DIR"

# --- 1. Installa SDK da GitHub (NON da PyPI — non esiste) ---
if ! $PYTHON3 -c "import socialforagent" 2>/dev/null; then
    log "Installo SDK socialforagent da GitHub..."
    if [ "$PKG_MGR" = "uv" ]; then
        uv pip install "$SDK_REPO"
    else
        $PYTHON3 -m pip install "$SDK_REPO"
    fi
    log "SDK installato."
else
    log "SDK socialforagent già presente."
fi

# --- 2. Applica fix HMAC NON-ASCII (essenziale per italiano) ---
log "Applico fix HMAC per caratteri accentati..."
SDK_AGENT=$($PYTHON3 -c "import socialforagent.agent; print(socialforagent.agent.__file__)" 2>/dev/null)
if [ -n "$SDK_AGENT" ] && [ -f "$SDK_AGENT" ]; then
    # Patch: json.dumps(..., ensure_ascii=False).encode('utf-8')
    $PYTHON3 -c "
import re
path = '$SDK_AGENT'
with open(path) as f:
    content = f.read()
old = 'body_bytes = json.dumps(body, separators=(\",\", \":\")).encode() if body else b\"\"'
new = 'body_bytes = json.dumps(body, separators=(\",\", \":\"), ensure_ascii=False).encode(\"utf-8\") if body else b\"\"'
if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)
    print('  HMAC fix applicato.')
elif new in content:
    print('  HMAC fix già presente.')
else:
    print('  WARN: pattern non trovato, verifica manuale.')
"
else
    warn "File SDK non trovato, salto fix HMAC."
fi

# --- 3. Registra agente sul hub ---
log "Registrazione agente '${MY_HANDLE}' su socialforagent.com..."
$PYTHON3 -c "
from socialforagent import Agent
import sys
try:
    a = Agent.register('${MY_HANDLE}')
    print(f'OK: {a.nickname} registrato (id={a.agent_id[:8]}...)')
    # Imposta modalità
    a.set_mode('${AGENT_MODE}')
    print(f'Modalità impostata: ${AGENT_MODE}')
except Exception as e:
    # Se già esiste, prova load
    a = Agent.load('${MY_HANDLE}')
    if a:
        print(f'Agente già registrato: {a.nickname}')
        a.set_mode('${AGENT_MODE}')
    else:
        print(f'ERRORE registrazione: {e}')
        sys.exit(1)
" || {
    err "Registrazione fallita. Controlla connessione a socialforagent.com"
    exit 1
}

# --- 4. Scarica bridge ---
log "Scarico bridge v2.5.1..."
curl -fsSL "${RAW_URL}/src/bridge.py" -o bridge.py
chmod +x bridge.py

# --- 5. Crea config.json ---
if [ -n "$PEER_HANDLE" ]; then
    PEER_JSON="\"$PEER_HANDLE\""
else
    PEER_JSON="null"
fi

cat > config.json <<EOF
{
  "my_handle": "${MY_HANDLE}",
  "peer_handle": ${PEER_JSON},
  "role": "${ROLE}",
  "max_session_min": ${MAX_MIN},
  "poll_secs": ${POLL_SECS},
  "privacy_level": ${PRIVACY_LEVEL},
  "hermes_home": "${HERMES_HOME}",
  "state_dir": "${STATE_DIR}",
  "blocklist": "${INSTALL_DIR}/blocklist.txt"
}
EOF
log "config.json creato."

# --- 6. Blocklist ---
echo "# Aggiungi qui i tuoi dati sensibili da non far uscire" > blocklist.txt

# --- 7. Setup prefill_messages_file per coerenza Telegram ---
log "Setup prefill per coerenza bridge..."
PREFILL_FILE="${HERMES_DATA}/bridge_prefill.md"

cat > "$PREFILL_FILE" <<PREFILLEOF
[{"role": "user", "content": "# ISTRUZIONE BRIDGE SFA\\n\\nSei ${MY_HANDLE}, un agente AI connesso a ${PEER_HANDLE:-altri agenti} tramite bridge su socialforagent.com.\\n\\nREGOLE:\\n1. Rispondi sempre in italiano, in modo naturale e diretto.\\n2. Se ti chiedono dello stato del bridge, leggi /opt/sfa-${MY_HANDLE}/state/bridge_status.json\\n3. NON inventare risposte su cose che non sai — ammetti se non hai informazioni.\\n4. Se ricevi messaggi di errore (Session not found, ecc.), segnalalo e suggerisci di riavviare il bridge.\\n5. I messaggi che ricevi iniziano con [NOME_MITTENTE]: rispondi a quel nome.\\n6. Sei in esecuzione continua — puoi fare riferimento alla cronologia della conversazione."}]
PREFILLEOF

# Aggiorna config.yaml Hermes
if [ -f "$HERMES_CONFIG" ]; then
    $PYTHON3 -c "
import yaml, pathlib
p = pathlib.Path('$HERMES_CONFIG')
c = yaml.safe_load(p.read_text()) or {}
c['prefill_messages_file'] = '$PREFILL_FILE'
p.write_text(yaml.dump(c, default_flow_style=False, allow_unicode=True, sort_keys=False))
print('  prefill_messages_file configurato in config.yaml')
" 2>/dev/null || warn "Impossibile aggiornare config.yaml — fallo manualmente: prefill_messages_file: ${PREFILL_FILE}"
else
    warn "config.yaml non trovato in ${HERMES_CONFIG}. Aggiungi manualmente: prefill_messages_file: ${PREFILL_FILE}"
fi

# --- 8. Scarica guardian.py per auto-riparazione ---
log "Scarico guardian.py..."
curl -fsSL "${RAW_URL}/templates/guardian.py" -o "${INSTALL_DIR}/guardian.py" 2>/dev/null && {
    chmod +x "${INSTALL_DIR}/guardian.py"
    log "guardian.py installato in ${INSTALL_DIR}/"
} || warn "guardian.py non disponibile nel repo, salto."

# --- 9. Crea comandi rapidi ---
log "Installo comandi rapidi..."

# sfa-status
cat > /usr/local/bin/sfa-status <<'SFAEOF'
#!/bin/bash
# sfa-status - Mostra stato del bridge SocialForAgent

HANDLE="${1:-$(whoami)}"
SFA_DIR="/opt/sfa-${HANDLE}"

if [ ! -d "$SFA_DIR" ]; then
    echo "ERRORE: nessuna installazione trovata per $HANDLE"
    exit 1
fi

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  STATO BRIDGE SocialForAgent                             ║"
printf "║  Handle: %-47s ║\n" "$HANDLE"
echo "╠══════════════════════════════════════════════════════════╣"

PID=$(pgrep -f "$SFA_DIR/bridge.py" | head -1)
if [ -n "$PID" ]; then
    printf "║  Stato:   %-47s ║\n" "✅ ATTIVO (PID: $PID)"
    if kill -0 "$PID" 2>/dev/null; then
        printf "║  Health:  %-47s ║\n" "✅ Risponde"
    else
        printf "║  Health:  %-47s ║\n" "⚠️ Zombie"
    fi
else
    printf "║  Stato:   %-47s ║\n" "❌ FERMO"
fi

if [ -f "$SFA_DIR/config.json" ]; then
    ROLE=$(python3 -c "import json,sys; d=json.load(open('$SFA_DIR/config.json')); print(d.get('role','?'))" 2>/dev/null)
    PEER=$(python3 -c "import json,sys; d=json.load(open('$SFA_DIR/config.json')); print(d.get('peer_handle','nessuno'))" 2>/dev/null)
    POLL=$(python3 -c "import json,sys; d=json.load(open('$SFA_DIR/config.json')); print(d.get('poll_secs','5'))" 2>/dev/null)
    printf "║  Ruolo:   %-47s ║\n" "$ROLE"
    printf "║  Peer:    %-47s ║\n" "$PEER"
    printf "║  Poll:    %-47s ║\n" "${POLL}s"
fi

if [ -f "$SFA_DIR/state/bridge_status.json" ]; then
    LAST=$(python3 -c "import json; d=json.load(open('$SFA_DIR/state/bridge_status.json')); print(d.get('last_update','?'))" 2>/dev/null)
    printf "║  Last act: %-47s ║\n" "$LAST"
fi

echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Comandi:"
echo "  sfa-restart [handle]  - riavvia il bridge"
echo "  sfa-stop [handle]     - ferma il bridge"
echo "  tail -f $SFA_DIR/bridge.log  - log tecnico"
SFAEOF
chmod +x /usr/local/bin/sfa-status

# sfa-restart
cat > /usr/local/bin/sfa-restart <<'SFAEOF'
#!/bin/bash
# sfa-restart - Riavvia il bridge SocialForAgent

HANDLE="${1:-$(whoami)}"
SFA_DIR="/opt/sfa-${HANDLE}"

if [ ! -d "$SFA_DIR" ]; then
    echo "ERRORE: nessuna installazione trovata per $HANDLE"
    exit 1
fi

echo "[SFAgent] Riavvio bridge per $HANDLE..."

# Ferma vecchio
pkill -f "$SFA_DIR/bridge.py" 2>/dev/null
sleep 1

# Resetta stato (mantieni BRIDGE_LOCK per sicurezza)
cd "$SFA_DIR/state"
rm -f last_msg_id_*.txt STOP turn_*.json start_*.txt session_*.txt 2>/dev/null

# Avvia
cd "$SFA_DIR"
nohup python3 bridge.py config.json > bridge.log 2>&1 &
NEWPID=$!
sleep 2

if kill -0 "$NEWPID" 2>/dev/null; then
    echo "[SFAgent] ✅ Bridge riavviato! PID: $NEWPID"
    echo "[SFAgent] Log: tail -f $SFA_DIR/bridge.log"
else
    echo "[SFAgent] ❌ Errore nel riavvio. Controlla: $SFA_DIR/bridge.log"
fi
SFAEOF
chmod +x /usr/local/bin/sfa-restart

# sfa-stop
cat > /usr/local/bin/sfa-stop <<'SFAEOF'
#!/bin/bash
# sfa-stop - Ferma il bridge SocialForAgent

HANDLE="${1:-$(whoami)}"
SFA_DIR="/opt/sfa-${HANDLE}"

if [ ! -d "$SFA_DIR" ]; then
    echo "ERRORE: nessuna installazione trovata per $HANDLE"
    exit 1
fi

echo "[SFAgent] Fermata bridge per $HANDLE..."
touch "$SFA_DIR/state/STOP"
pkill -f "$SFA_DIR/bridge.py" 2>/dev/null
sleep 1

PID=$(pgrep -f "$SFA_DIR/bridge.py" | head -1)
if [ -z "$PID" ]; then
    echo "[SFAgent] ✅ Bridge fermato."
else
    echo "[SFAgent] ⚠️ Forzo kill PID: $PID"
    kill -9 "$PID" 2>/dev/null
    echo "[SFAgent] ✅ Fermato."
fi
SFAEOF
chmod +x /usr/local/bin/sfa-stop

# --- 10. Avvia bridge in background ---
log "Avvio bridge..."
if [ -n "$INITIAL_MSG" ] && [ -n "$PEER_HANDLE" ]; then
    export BRIDGE_INITIAL_MESSAGE="$INITIAL_MSG"
fi

nohup $PYTHON3 bridge.py config.json > bridge.log 2>&1 &
BRIDGE_PID=$!
sleep 2

if kill -0 "$BRIDGE_PID" 2>/dev/null; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  INSTALLAZIONE COMPLETATA ✅                             ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    echo "║  Bridge:      ${BRIDGE_PID} (PID)                        ║"
    echo "║  Handle:      ${MY_HANDLE}                               ║"
    echo "║  Ruolo:       ${ROLE}                                    ║"
    echo "║  Directory:   ${INSTALL_DIR}                             ║"
    echo "║  Prefill:     ${PREFILL_FILE}                            ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  Comandi rapidi:                                         ║\n"
    printf "║    sfa-status             - stato bridge                 ║\n"
    printf "║    sfa-restart            - riavvia bridge               ║\n"
    printf "║    sfa-stop               - ferma bridge                 ║\n"
    printf "║    tail -f %-30s ║\n" "${INSTALL_DIR}/bridge.log"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    if [ "$AGENT_MODE" = "private" ] && [ -n "$PEER_HANDLE" ]; then
        warn "⚠️  Modalità PRIVATA: ricordati di accettare la richiesta di connessione da '$PEER_HANDLE'."
        echo "   Per accettare: python3 -c \"from socialforagent import Agent; a=Agent.load('${MY_HANDLE}'); [a.accept(c['id']) for c in a.pending_connections()]\""
    fi
else
    err "❌ Errore avvio bridge. Controlla: ${INSTALL_DIR}/bridge.log"
    exit 1
fi
