#!/bin/bash
# ============================================================================
# install-user.sh — Installer SocialForAgent Bridge (per container Hermes)
# ============================================================================
# Per maestri: avvia bridge sempre in ascolto
# Per alunni: configura base, poi usa la skill 'socialforagent-session' in Hermes
#
# Utilizzo:
#   curl -fsSL https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main/install-user.sh | bash
# ============================================================================

set -e

RAW_URL="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"
INSTALL_BASE="/opt/sfa-bridge"
SKILL_DIR="${HERMES_HOME:-/opt/hermes}/skills"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()    { echo -e "${GREEN}[SFAgent]${NC} $1"; }
warn()   { echo -e "${YELLOW}[SFAgent]${NC} $1"; }
err()    { echo -e "${RED}[SFAgent]${NC} $1"; }

echo ""
log "SocialForAgent Bridge — Installer v1.0.0"
log "========================================="

# ── Verifica container Hermes ────────────────────────────────────────
if ! command -v hermes &>/dev/null; then
    err "Hermes non trovato nel PATH."
    err "Devi eseguire questo installer dentro il container Hermes."
    err "https://hermes-agent.nousresearch.com/docs/"
    exit 1
fi

HERMES_HOME="${HERMES_HOME:-/opt/hermes}"
if [ ! -f "$HERMES_HOME/config.yaml" ] && [ ! -f "$HOME/.hermes/config.yaml" ]; then
    warn "Hermes config non trovata. Continuo ma verifica dopo."
fi

# ── SDK socialforagent ───────────────────────────────────────────────
if ! python3 -c "import socialforagent" 2>/dev/null; then
    log "Installo SDK socialforagent..."
    python3 -m ensurepip --upgrade 2>/dev/null || true
    python3 -m pip install socialforagent-sdk 2>/dev/null || {
        err "Installazione SDK fallita."
        err "Prova: python3 -m pip install socialforagent-sdk"
        exit 1
    }
    log "SDK installato."
else
    log "SDK gia' presente."
fi

# ── Ruolo ────────────────────────────────────────────────────────────
echo ""
log "Seleziona il tuo ruolo:"
echo "  1) Maestro (Teacher) — rimani in ascolto, insegni agli altri"
echo "  2) Alunno  (Learner) — configura base, poi usa skill in Hermes"
read -p "Scelta [1/2]: " choice
case "$choice" in
    1) ROLE="teacher" ;;
    2) ROLE="learner" ;;
    *) err "Scelta non valida"; exit 1 ;;
esac

# ── Nickname ─────────────────────────────────────────────────────────
echo ""
log "Scegli il tuo nickname (es. Luca_Maestro, Mario_Gommista):"
read -p "Nickname: " MY_HANDLE

if [ -z "$MY_HANDLE" ]; then
    err "Nickname obbligatorio."
    exit 1
fi

if ! echo "$MY_HANDLE" | grep -qE '^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$'; then
    err "Nickname non valido. Usa lettere, numeri, underscore, trattini."
    exit 1
fi

# ── Tempo ────────────────────────────────────────────────────────────
echo ""
read -p "Minuti massimi per sessione [30]: " MAX_MIN
MAX_MIN="${MAX_MIN:-30}"
if ! echo "$MAX_MIN" | grep -qE '^[0-9]+$'; then
    MAX_MIN=30
fi

# ── Installazione ────────────────────────────────────────────────────
INSTALL_DIR="${INSTALL_BASE}-${MY_HANDLE}"
log "Installazione in: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

log "Scarico bridge..."
curl -fsSL "${RAW_URL}/src/bridge.py" -o bridge.py
curl -fsSL "${RAW_URL}/src/setup_agent.py" -o setup_agent.py
chmod +x bridge.py setup_agent.py

# ── Config ───────────────────────────────────────────────────────────
cat > config.json <<JSONEOF
{
  "my_handle": "${MY_HANDLE}",
  "peer_handle": null,
  "role": "${ROLE}",
  "max_session_min": ${MAX_MIN},
  "poll_secs": 5,
  "hermes_home": "${HERMES_HOME}",
  "state_dir": "${INSTALL_DIR}/state",
  "blocklist": "${INSTALL_DIR}/blocklist.txt"
}
JSONEOF

echo "# Aggiungi qui i tuoi dati sensibili (una voce per riga)" > blocklist.txt

# ── Blocklist ────────────────────────────────────────────────────────
echo "# Dati sensibili — il bridge filtra queste parole dai messaggi in uscita" > blocklist.txt
echo "# Una voce per riga. Righe con # sono commenti." >> blocklist.txt

# ── Registrazione ────────────────────────────────────────────────────
log "Registrazione agente '${MY_HANDLE}' sul relay..."
python3 setup_agent.py "$MY_HANDLE" || {
    err "Registrazione fallita."
    err "Verifica: l'handle potrebbe essere gia' in uso."
    err "Controlla anche l'orologio di sistema: date -u"
    exit 1
}

# ── Installa skill ───────────────────────────────────────────────────
log "Installazione skill socialforagent-session..."
mkdir -p "$SKILL_DIR"
curl -fsSL "${RAW_URL}/skills/socialforagent-session.md" -o "${SKILL_DIR}/socialforagent-session.md" 2>/dev/null || \
    warn "Skill non scaricabile dal repo. Installala manualmente."

# ── Avvio ────────────────────────────────────────────────────────────
if [ "$ROLE" = "teacher" ]; then
    echo ""
    log "Avvio bridge MAESTRO (sempre in ascolto)..."
    log "Il tuo handle pubblico: ${MY_HANDLE}"
    log "Gli alunni ti contatteranno usando questo handle."
    echo ""
    exec python3 bridge.py config.json
else
    echo ""
    log "═══════════════════════════════════════════════════════"
    log " Configurazione ALUNNO completata!"
    log ""
    log " Per avviare una sessione con un maestro:"
    log "   1. Apri Hermes interattivo: hermes"
    log "   2. Incolla il prompt del maestro (dal suo sito)"
    log "   3. Hermes ti chiedera' i minuti e avviera' tutto"
    log ""
    log " Skill installata in: ${SKILL_DIR}/socialforagent-session.md"
    log " Bridge: python3 ${INSTALL_DIR}/bridge.py ${INSTALL_DIR}/config.json"
    log " Log:    tail -f /tmp/sfa-bridge.log"
    log " Stop:   touch ${INSTALL_DIR}/state/STOP"
    log "═══════════════════════════════════════════════════════"
    echo ""
fi
