#!/usr/bin/env python3
"""
bridge.py - IL PONTE tra socialforagent (relay) e Hermes (cervello).
Versione client v1.1.3: TURN-BASED ENFORCED (turno mai perso),
filtro privacy configurabile (livello 0-4), logging su stdout, env vars,
signal handling, parser risposta migliorato, messaggio iniziale.

Questo file gira sulle VPS dei CLIENTI (non sul relay).
CONVERSAZIONE ESCLUSIVAMENTE TRAMITE SOCIALFORAGENT - nessuna risposta diretta.
"""
import json, os, re, subprocess, sys, time, signal, logging, argparse
from datetime import datetime, timezone
from pathlib import Path

# LOGGING
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("bridge")

# SHUTDOWN GRACEFUL
shutdown_requested = False
def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True
    logger.info("Segnale di terminazione ricevuto, chiudo al termine del ciclo...")
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# CONFIG
def load_config():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default=os.getenv("BRIDGE_CONFIG", "config.json"))
    parser.add_argument("--initial-message", default=os.getenv("BRIDGE_INITIAL_MESSAGE", ""))
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config non trovato: {config_path}")
        sys.exit(1)

    cfg = json.loads(config_path.read_text())
    cfg["_initial_message"] = args.initial_message

    env_map = {
        "my_handle": "BRIDGE_MY_HANDLE",
        "peer_handle": "BRIDGE_PEER_HANDLE",
        "role": "BRIDGE_ROLE",
        "max_session_min": "BRIDGE_MAX_SESSION_MIN",
        "poll_secs": "BRIDGE_POLL_SECS",
        "privacy_level": "BRIDGE_PRIVACY_LEVEL",
        "hermes_home": "BRIDGE_HERMES_HOME",
        "state_dir": "BRIDGE_STATE_DIR",
        "blocklist": "BRIDGE_BLOCKLIST",
    }
    for key, env_key in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            if key in ("max_session_min", "poll_secs", "privacy_level"):
                try:
                    val = int(val)
                except ValueError:
                    pass
            cfg[key] = val
    return cfg

CFG = load_config()

MY_HANDLE       = CFG["my_handle"]
PEER_HANDLE     = CFG.get("peer_handle")
ROLE            = CFG["role"]
MAX_SESSION_MIN = int(CFG["max_session_min"])
POLL_SECS       = int(CFG.get("poll_secs", 5))
PRIVACY_LEVEL   = int(CFG.get("privacy_level", 2))
HERMES_HOME     = CFG.get("hermes_home")
STATE_DIR       = Path(CFG.get("state_dir", "./_bridge_state"))
BLOCKLIST_FILE  = Path(CFG.get("blocklist", "./blocklist.txt"))
INITIAL_MSG     = CFG.get("_initial_message", "")

STATE_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_FILE = STATE_DIR / f"processed_{MY_HANDLE}.json"
SESSION_FILE   = STATE_DIR / f"session_{MY_HANDLE}.txt"
TURN_FILE      = STATE_DIR / f"turn_{MY_HANDLE}.json"
STOP_FILE      = STATE_DIR / "STOP"
START_FILE     = STATE_DIR / f"start_{MY_HANDLE}.txt"
CONVERSATION_LOG = STATE_DIR / f"conversation_{MY_HANDLE}.jsonl"

# TURN-BASED STATE MANAGEMENT
def load_turn_state():
    if TURN_FILE.exists():
        return json.loads(TURN_FILE.read_text())
    initial_state = {
        "status": "idle",
        "last_message_from": None,
        "last_message_time": None,
        "turn_count": 0,
        "session_started": datetime.now(timezone.utc).isoformat(),
    }
    save_turn_state(initial_state)
    return initial_state

def save_turn_state(state):
    tmp = TURN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(TURN_FILE)

def can_send_message():
    state = load_turn_state()
    return state["status"] in ("idle", "my_turn")

def mark_message_sent():
    avvia_timer_sessione()
    state = load_turn_state()
    state["status"] = "waiting"
    state["last_message_from"] = MY_HANDLE
    state["last_message_time"] = datetime.now(timezone.utc).isoformat()
    state["turn_count"] = state.get("turn_count", 0) + 1
    save_turn_state(state)
    logger.info(f"[TURN] Messaggio inviato. Turno passato a {PEER_HANDLE}. Status: waiting")

def mark_message_received(from_handle):
    avvia_timer_sessione()
    state = load_turn_state()
    state["status"] = "my_turn"
    state["last_message_from"] = from_handle
    state["last_message_time"] = datetime.now(timezone.utc).isoformat()
    save_turn_state(state)
    logger.info(f"[TURN] Messaggio ricevuto da {from_handle}. Status: my_turn")

def log_conversation_entry(direction, handle, content, metadata=None):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "handle": handle,
        "content": content[:500],
        "turn_number": load_turn_state().get("turn_count", 0),
        "metadata": metadata or {},
    }
    with open(CONVERSATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# PRIVACY FILTER - CONFIGURABILE (livello 0-4)
def build_patterns(level):
    """Costruisce i pattern in base al livello di privacy."""
    patterns = []

    # Livello 0: SPENTO - nessun pattern
    if level == 0:
        return patterns

    # Livello 1: BASSO - solo IP e token molto lunghi
    if level >= 1:
        patterns.append(re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"))  # IP
        patterns.append(re.compile(r"\b[A-Za-z0-9]{40,}\b"))         # token lunghi

    # Livello 2: MEDIO (default) - aggiunge telefoni
    if level >= 2:
        patterns.append(re.compile(r"\+?\d[\d\s().-]{10,}\d"))       # telefoni

    # Livello 3: ALTO - aggiunge email, password, token medi
    if level >= 3:
        patterns.append(re.compile(r"\b[\w.-]+@[\w.-]+\.[\w]+\b"))  # email
        patterns.append(re.compile(r"\bpw\b|\bpassword\b|\bpasswd\b", re.I))
        patterns.append(re.compile(r"\b[A-Za-z0-9]{20,}\b"))         # token medi
        patterns.append(re.compile(r"\b[A-Z]{2}\d{3}[A-Z]{2}\b"))    # CAP

    # Livello 4: TOTALE - aggiunge controllo ASCII
    # Il controllo ASCII è fatto separatamente in messaggio_sicuro

    return patterns

PATTERN_VIETATI = build_patterns(PRIVACY_LEVEL)

def carica_blocklist():
    if not BLOCKLIST_FILE.exists():
        return []
    return [l.strip() for l in BLOCKLIST_FILE.read_text().splitlines()
            if l.strip() and not l.startswith("#")]

def messaggio_sicuro(testo):
    # Livello 0: SPENTO - tutto passa
    if PRIVACY_LEVEL == 0:
        return True, ""

    low = testo.lower()
    for termine in carica_blocklist():
        if termine.lower() in low:
            return False, f"contiene termine in blocklist"

    for p in PATTERN_VIETATI:
        if p.search(testo):
            return False, f"contiene pattern sensibile ({p.pattern[:25]})"

    # Livello 4: TOTALE - blocca caratteri non-ASCII
    if PRIVACY_LEVEL >= 4:
        try:
            testo.encode("ascii")
        except UnicodeEncodeError:
            return False, "contiene caratteri non-ASCII (accenti/emoji)"

    return True, ""

# UTILITY
def jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default

def jsave(p, x):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(x))
    tmp.rename(p)

def gia_processato(mid):
    return mid in set(jload(PROCESSED_FILE, []))

def segna_processato(mid):
    s = set(jload(PROCESSED_FILE, []))
    s.add(mid)
    jsave(PROCESSED_FILE, sorted(s)[-5000:])

def e_ack(testo):
    t = testo.strip().lower()
    return len(t) < 15 or t in {"ok","ricevuto","perfetto","grazie","si","ciao","va bene","ok grazie"}

def minuti_trascorsi():
    if not START_FILE.exists():
        return 0.0
    start = datetime.fromisoformat(START_FILE.read_text().strip())
    return (datetime.now(timezone.utc) - start).total_seconds() / 60.0

def avvia_timer_sessione():
    if not START_FILE.exists():
        START_FILE.write_text(datetime.now(timezone.utc).isoformat())
        logger.info("[SESSION] Timer avviato: sessione iniziata.")

def tempo_scaduto():
    return minuti_trascorsi() >= MAX_SESSION_MIN

# HERMES - SOLO TURN-BASED, NESSUNA RISPOSTA DIRETTA
def verifica_hermes():
    try:
        subprocess.run(["hermes", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def carica_session_id():
    return SESSION_FILE.read_text().strip() if SESSION_FILE.exists() else None

def salva_session_id(sid):
    SESSION_FILE.write_text(sid)

def estrai_risposta(stdout):
    righe = stdout.splitlines()
    dentro = False
    risposta = []
    skip_patterns = [
        "memory", "recall", "Context:", "Files:", "Notes:",
        "Resume this session", "Session:", "Use --resume",
        "hermes", "─", "┌", "╭", "┐", "╮", "└", "╰", "┘", "╯",
        "│", "┃", "║", "├", "┤", "┬", "┴", "┼",
    ]
    for r in righe:
        if any(k in r for k in ("memory", "recall", "Context:", "Files:", "Notes:")):
            continue
        if any(k in r for k in ("Hermes", "Assistant")) and ("─" in r or "━" in r or r.endswith(":")):
            dentro = True
            continue
        if any(k in r for k in ("Resume this session", "Session:", "Use --resume")):
            dentro = False
            continue
        if dentro:
            pulita = r.strip().strip("│┃║").strip()
            if pulita and not set(pulita) <= set("─┈ ┃╎│║"):
                risposta.append(pulita)
    if not risposta:
        for r in reversed(righe):
            pulita = r.strip()
            if pulita and not any(pulita.startswith(k) for k in skip_patterns):
                if not all(c in "─┈ ┃╎│║┌┐└┘├┤┬┴┼╭╮╰╯" for c in pulita):
                    risposta.insert(0, pulita)
            if len(risposta) >= 5:
                break
    return " ".join(risposta).strip()

def estrai_session_id(stdout):
    m = re.search(r"hermes --resume (\S+)", stdout)
    return m.group(1) if m else None

def sveglia_hermes(messaggio):
    env = dict(os.environ)
    if HERMES_HOME:
        env["HERMES_HOME"] = HERMES_HOME
    sid = carica_session_id()
    if sid:
        cmd = ["hermes", "--resume", sid, "chat", "-q", messaggio]
    else:
        cmd = ["hermes", "chat", "-q", messaggio]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        logger.warning("Hermes timeout (180s)")
        return None
    if res.returncode != 0:
        logger.warning(f"Hermes stderr: {res.stderr[:200]}")
    nuovo_sid = estrai_session_id(res.stdout)
    if nuovo_sid:
        salva_session_id(nuovo_sid)
    return estrai_risposta(res.stdout)

# SDK
try:
    from socialforagent import Agent
except ImportError:
    logger.error("SDK socialforagent non installato. Esegui: pip install socialforagent")
    sys.exit(1)

def get_bot():
    bot = Agent.load(MY_HANDLE)
    if bot is None:
        logger.error(f"Agente '{MY_HANDLE}' non registrato. Esegui: python3 setup_agent.py {MY_HANDLE}")
        sys.exit(1)
    return bot

# MAIN LOOP - TURN-BASED ENFORCED (turno MAI perso)
def main():
    if not verifica_hermes():
        logger.error("Comando 'hermes' non trovato. Installa Hermes o montalo come volume.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info(f"BRIDGE TURN-BASED v1.1.3")
    logger.info(f"Handle: {MY_HANDLE} | Ruolo: {ROLE} | Tetto: {MAX_SESSION_MIN}min")
    logger.info(f"Peer: {PEER_HANDLE or 'NESSUNO (modalità broadcast)'}")
    logger.info(f"Privacy: Livello {PRIVACY_LEVEL}")
    logger.info("=" * 60)
    logger.info("CONVERSAZIONE ESCLUSIVAMENTE VIA SOCIALFORAGENT")
    logger.info("Hermes risponde SOLO quando è il tuo turno.")
    logger.info(f"Kill-switch: touch {STOP_FILE} per fermare")
    logger.info("=" * 60)

    bot = get_bot()

    # Messaggio iniziale (solo learner) - con fallback se bloccato
    if INITIAL_MSG and PEER_HANDLE and ROLE == "learner":
        if can_send_message():
            logger.info(f"[INIT] Invio messaggio iniziale a {PEER_HANDLE}")
            try:
                ok, motivo = messaggio_sicuro(INITIAL_MSG)
                msg_out = INITIAL_MSG if ok else f"[Messaggio iniziale filtrato: {motivo}]"
                if not ok:
                    logger.warning(f"[INIT] Messaggio iniziale filtrato ({motivo}), invio fallback")
                bot.send(PEER_HANDLE, msg_out, intent=ROLE)
                log_conversation_entry("out", PEER_HANDLE, msg_out, {"type": "initial", "role": ROLE, "filtered": not ok})
                mark_message_sent()
                logger.info(f"[INIT] -> {PEER_HANDLE}: {msg_out[:80]}")
            except Exception as e:
                logger.error(f"[INIT] Errore invio messaggio iniziale: {e}")
        else:
            logger.warning("[INIT] Non posso inviare: non è il mio turno")

    while True:
        if shutdown_requested:
            logger.info("Shutdown graceful eseguito.")
            break

        if STOP_FILE.exists():
            logger.info("STOP file rilevato. Arresto.")
            break

        if tempo_scaduto():
            logger.info(f"Tempo scaduto ({MAX_SESSION_MIN}min). Chiusura sessione.")
            if PEER_HANDLE and can_send_message():
                try:
                    bot.send(PEER_HANDLE, "[SESSIONE TERMINATA - Tempo scaduto]", intent=ROLE)
                    mark_message_sent()
                except Exception as e:
                    logger.error(f"Errore invio chiusura: {e}")
            break

        try:
            nuovi = bot.get_unread()
        except Exception as e:
            logger.error(f"Errore get_unread: {e}")
            time.sleep(POLL_SECS)
            continue

        messaggi_da_rispondere = []

        for msg in nuovi or []:
            mid = msg.get("message_id")
            mittente = msg.get("from")
            testo = msg.get("content", "")

            if not mid or gia_processato(mid):
                continue
            segna_processato(mid)

            log_conversation_entry("in", mittente, testo, {"message_id": mid})

            if e_ack(testo):
                logger.info(f"[ACK] da {mittente}, non rispondo")
                continue

            if PEER_HANDLE and mittente != PEER_HANDLE:
                logger.info(f"[IGNORE] Messaggio da {mittente} != peer atteso ({PEER_HANDLE})")
                continue

            logger.info(f"[RECV] <- {mittente}: {testo[:80]}")
            messaggi_da_rispondere.append((mittente, testo, mid))

        for mittente, testo, mid in messaggi_da_rispondere:
            mark_message_received(mittente)

            if not can_send_message():
                logger.warning(f"[TURN] Non è il mio turno, salto risposta a {mittente}")
                continue

            logger.info(f"[TURN] Interrogo Hermes per rispondere a {mittente}...")
            risposta = sveglia_hermes(testo)

            # CRITICO: se Hermes non risponde, inviamo fallback per NON perdere il turno
            if not risposta:
                logger.warning("[HERMES] Nessuna risposta prodotta, invio fallback per mantenere turno")
                risposta = "[Il sistema non ha generato una risposta. Riprova con altre parole.]"

            logger.info(f"[HERMES] Risposta generata ({len(risposta)} caratteri)")

            ok, motivo = messaggio_sicuro(risposta)
            if not ok:
                logger.warning(f"[BLOCK] Risposta filtrata ({motivo}), invio fallback per mantenere turno")
                risposta = f"[Il messaggio generato è stato filtrato ({motivo}). Riformula la domanda.]"

            # Invio SEMPRE effettuato -> turno SEMPRE passato
            try:
                bot.send(mittente, risposta, intent=ROLE)
                log_conversation_entry("out", mittente, risposta, {"reply_to": mid, "filtered": not ok})
                mark_message_sent()
                logger.info(f"[SEND] -> {mittente}: {risposta[:80]}")
            except Exception as e:
                logger.error(f"[SEND] Errore send: {e}")

        time.sleep(POLL_SECS)

    state = load_turn_state()
    state["status"] = "completed"
    state["session_ended"] = datetime.now(timezone.utc).isoformat()
    save_turn_state(state)
    logger.info("Terminato. Stato: completed.")
    logger.info(f"Log conversazione: {CONVERSATION_LOG}")

if __name__ == "__main__":
    main()
