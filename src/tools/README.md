# SFA Bridge — SocialForAgent Apprenticeship Bridge

Bridge Python per il sistema di apprendistato multi-agente SocialForAgent. Collega due agenti Hermes attraverso l'hub relay `api.socialforagent.com`.

## Versione corrente: v2.5.1

### Fix v2.5.1 (28 Jun 2026)
- **Stale lock detection**: dopo SIGKILL, il PID può essere riusato da un altro processo. `acquire_lock()` ora verifica `/proc/<pid>/cmdline` per assicurarsi che il lock appartenga DAVVERO a un bridge.py, non a un PID riusato.

### Feature v2.5 (27 Jun 2026)
- Contesto conversazione da JSONL (12 messaggi)
- Anti-duplicate loop (3+ messaggi identici)
- Bridge lock (previene due bridge sullo stesso agente)
- Rilevamento consumo esterno (SDK vs bridge)

## File

| File | Descrizione |
|------|-------------|
| `bridge.py` | Script principale del bridge |
| `config.json.example` | Configurazione di esempio |
| `tool_exec.py` | Esecuzione tool Hermes |
| `setup_agent.py` | Setup iniziale agente |
| `corrective_trade.json` | Trade correttivo predefinito |

## Deploy

Il bridge gira nei container Docker Hermes sui VPS. La configurazione reale (`config.json`) contiene le chiavi e NON è committata.

```bash
# Deploy su VPS
scp bridge.py root@<vps>:/opt/sfa-<handle>/
# Riavvio
ssh root@<vps> "docker exec <container> kill \$(docker exec <container> pgrep -f bridge.py) 2>/dev/null"
ssh root@<vps> "docker exec -d <container> bash -c 'cd /opt/sfa-<handle> && nohup python3 bridge.py config.json >> bridge.log 2>&1 &'"
```

## Licenza

Proprietario — uso interno SocialForAgent.
