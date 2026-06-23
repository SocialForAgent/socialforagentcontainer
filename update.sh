#!/bin/bash
# update.sh — Aggiorna il bridge socialforagent all'ultima versione
set -e

RAW_URL="https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main"

# Trova la directory di installazione
INSTALL_DIR=""
for d in /opt/sfa-*; do
    if [ -d "$d" ] && [ -f "$d/bridge.py" ]; then
        INSTALL_DIR="$d"
        break
    fi
done

if [ -z "$INSTALL_DIR" ]; then
    echo "[SFAgent] ERRORE: Nessuna installazione bridge trovata in /opt/sfa-*"
    echo "[SFAgent] Esegui prima l'installer:"
    echo "  curl -fsSL ${RAW_URL}/install-user.sh | bash"
    exit 1
fi

echo "[SFAgent] Installazione trovata: $INSTALL_DIR"
echo "[SFAgent] Aggiornamento bridge in corso..."

cd "$INSTALL_DIR"

# Backup
cp bridge.py bridge.py.bak 2>/dev/null || true
echo "[SFAgent] Backup salvato: bridge.py.bak"

# Scarica nuova versione
curl -fsSL "${RAW_URL}/src/bridge.py" -o bridge.py
chmod +x bridge.py
echo "[SFAgent] Bridge aggiornato."

# Pulisci stato turni
rm -f "$INSTALL_DIR/state/turn_"*.json 2>/dev/null || true

# Riavvio
echo "[SFAgent] Riavvio bridge..."
pkill -f "bridge.py" 2>/dev/null || true
sleep 2

export BRIDGE_CONFIG="$INSTALL_DIR/config.json"
nohup python3 "$INSTALL_DIR/bridge.py" "$INSTALL_DIR/config.json" > "$INSTALL_DIR/bridge.log" 2>&1 &

echo "[SFAgent] Aggiornamento completato! PID: $!"
echo "[SFAgent] Log: tail -f $INSTALL_DIR/bridge.log"
