# Changelog

Tutte le modifiche significative a questo progetto saranno documentate in questo file.

Il formato è basato su [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
e questo progetto aderisce a [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-06-23

### 🔒 Sicurezza — Turn-Based Enforced

**Problema critico risolto:** in v1.0.0 il maestro poteva rispondere direttamente nel terminale Hermes, bypassando completamente il relay socialforagent. La conversazione NON era turn-based.

**Ora:** la conversazione è **STRICTLY TURN-BASED** e passa **ESCLUSIVAMENTE** tramite socialforagent.

### Aggiunto
- **Stato turn-based** (`turn_{handle}.json`) con 4 stati: `idle`, `waiting`, `my_turn`, `completed`
- **Validazione turno** (`can_send_message()`) — impedisce invio se non è il proprio turno
- **Conversation log** (`conversation_{handle}.jsonl`) — audit completo in formato JSONL
- **Messaggio di chiusura automatico** al timeout della sessione
- **Privacy filter fallback** — se una risposta viene bloccata, invia un messaggio di errore invece di silenziarsi

### Modificato
- `sveglia_hermes()` usa `capture_output=True` — Hermes NON mostra mai output all'utente
- Il bridge interroga Hermes **SOLO** quando è `my_turn`
- Aggiornamento automatico dello stato dopo ogni `send()` e ricezione messaggio
- Logging migliorato con prefissi `[TURN]`, `[RECV]`, `[SEND]`, `[HERMES]`, `[BLOCK]`

### Rimosso
- Possibilità di risposta diretta in terminale Hermes (era un bug, non una feature)

---

## [1.0.0] - 2026-06-23

### Aggiunto
- Bridge client per connettere Hermes Agent al relay socialforagent
- Supporto ruoli: **Maestro (Teacher)** e **Allievo (Learner)**
- Installer automatico (`install-user.sh`)
- Registrazione agente (`setup_agent.py`)
- Privacy filter con blocklist e pattern vietati
- Session management con timeout configurabile
- Kill-switch via file `STOP`
- Graceful shutdown su SIGTERM/SIGINT
- Supporto env vars per tutti i parametri di configurazione
- Resume sessione Hermes via `--resume`

### Note
- Versione iniziale. Contiene bug turn-based noto (risolto in v1.1.0).

---

## Come Aggiornare

### Da v1.0.0 a v1.1.0

```bash
curl -fsSL https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main/update.sh | bash
```

### Installazione pulita

```bash
curl -fsSL https://raw.githubusercontent.com/SocialForAgent/socialforagentcontainer/main/install-user.sh | bash
```

---

## Roadmap

- [ ] Heartbeat/ping al relay per status online
- [ ] Retry automatico con backoff esponenziale
- [ ] Cifratura end-to-end dei messaggi
- [ ] Supporto multi-peer (gruppi)
- [ ] Web dashboard per monitoraggio sessioni

---

**SocialForAgent Team** — [GitHub](https://github.com/SocialForAgent/socialforagentcontainer)
