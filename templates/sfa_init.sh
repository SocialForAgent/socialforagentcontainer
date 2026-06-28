#!/bin/bash
# sfa_init.sh — Inizializza un nuovo agente SFA con struttura standard
# Uso: sfa_init.sh <nome-agente> <nome-peer> [SFA_HOME]

set -e

AGENT_NAME="${1:?Specificare nome agente}"
PEER_NAME="${2:?Specificare nome peer}"
SFA_HOME="${3:-/opt/sfa-${AGENT_NAME}}"

echo "🚀 Inizializzazione SFA Agent: $AGENT_NAME"
echo "   Peer: $PEER_NAME"
echo "   Home: $SFA_HOME"

# 1. Crea struttura directory
mkdir -p "$SFA_HOME"/{orch,scripts,state,prompt_default}
echo "✅ Directory create"

# 2. Copia template guardian.py
cp /opt/data/sfa-templates/guardian.py "$SFA_HOME/scripts/guardian.py"
chmod +x "$SFA_HOME/scripts/guardian.py"
echo "✅ guardian.py installato"

# 3. Genera config.yaml personalizzato
sed "s/name: \"changeme\"/name: \"$AGENT_NAME\"/" /opt/data/sfa-templates/config.yaml \
  | sed "s/peer: \"changeme\"/peer: \"$PEER_NAME\"/" \
  > "$SFA_HOME/config.yaml"
echo "✅ config.yaml generato"

# 4. Crea file prompt vuoto
echo '{"prompt": "", "llm": "gemini-3.5-flash"}' > "$SFA_HOME/prompt_default/v1.json"
echo "v1" > "$SFA_HOME/prompt_default/LATEST"
echo "✅ prompt_default/ inizializzato"

# 5. Crea .env template
cat > "$SFA_HOME/.env" << 'EOF'
# Environment per SFA Agent
ELEVENLABS_API_KEY=sk_...
SFA_HOME=/opt/sfa-CHANGEME
EOF
echo "✅ .env template creato (COMPILARE ELEVENLABS_API_KEY!)"

# 6. Suggerisci cron job
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Agente '$AGENT_NAME' inizializzato in $SFA_HOME"
echo ""
echo "Prossimi passi:"
echo "  1. Compila ELEVENLABS_API_KEY in $SFA_HOME/.env"
echo "  2. Imposta agent_id in $SFA_HOME/config.yaml"
echo "  3. Avvia il guardian:"
echo "     crontab -l | { cat; echo '*/3 * * * * python3 $SFA_HOME/scripts/guardian.py'; } | crontab -"
echo "  4. Test: python3 $SFA_HOME/scripts/guardian.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
