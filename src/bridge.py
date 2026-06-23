#!/usr/bin/env python3
"""
bridge.py - IL PONTE tra socialforagent (relay) e Hermes (cervello).
Versione client v1.0.0: logging su stdout, env vars, signal handling,
parser risposta migliorato, messaggio iniziale, privacy filter.

Questo file gira sulle VPS dei CLIENTI (non sul relay).
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
        "hermes_home": "BRIDGE_HERMES_HOME",
        "state_dir": "BRIDGE_STATE_DIR",
        "blocklist": "BRIDGE_BLOCKLIST",
    }
    for key, env_key in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            if key in ("max_session_min", "poll_secs"):
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
HERMES_HOME     = CFG.get("hermes_home")
STATE_DIR       = Path(CFG.get("state_dir", "./_bridge_state"))
BLOCKLIST_FILE  = Path(CFG.get("blocklist", "./blocklist.txt"))
INITIAL_MSG     = CFG.get("_initial_message", "")

STATE_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_FILE = STATE_DIR / f"processed_{MY_HANDLE}.json"
SESSION_FILE   = STATE_DIR / f"session_{MY_HANDLE}.txt"
STOP_FILE      = STATE_DIR / "STOP"
START_FILE     = STATE_DIR / f"start_{MY_HANDLE}.txt"

# PRIVACY FILTER
PATTERN_VIETATI = [
    re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b"),
    re.compile(r"\b\d{1,3}(?:\.\.\d{1,3}){3}\b"),
    re.compile(r"\bpw\b|\bpassword\b|\bpasswd\b", re.I),
    re.compile(r"\b[A-Za-z0-9]{20,}\b"),
    re.compile(r"\+?\d[\d\s().-]{7,}\d"),
    re.compile(r"\b[A-Z]{2}\d{3}[A-Z]{2}\b"),
]

def carica_blocklist():
    if not BLOCKLIST_FILE.exists():
        return []
    return [l.strip() for l in BLOCKLIST_FILE.read_text().splitlines()
            if l.strip() and not l.startswith("#")]

def messaggio_sicuro(testo):
    low = testo.lower()
    for termine in carica_blocklist():
        if termine.lower() in low:
            return False, f"contiene termine in blocklist"
    for p in PATTERN_VIETATI:
        if p.search(testo):
            return False, f"contiene pattern sensibile ({p.pattern[:25]})"
    try:
        testo.encode("ascii")
    except UnicodeEncodeError:
        return False, "contiene caratteri non-ASCII (accenti/emoji)"
    return True, ""

# FRENI
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
    return len(t) < 15 or t in {"ok","ricevuto","perfetto","grazie","si","ciao"}

def minuti_trascorsi():
    if not START_FILE.exists():
        START_FILE.write_text(datetime.now(timezone.utc).isoformat())
        return 0.0
    start = datetime.fromisoformat(START_FILE.read_text().strip())
    return (datetime.now(timezone.utc) - start).total_seconds() / 60.0

def tempo_scaduto():
    return minuti_trascorsi() >= MAX_SESSION_MIN

# HERMES
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
            if pulita and not pulita.startswith(("hermes", "Session", "Resume", "Use ", "─", "┌", "╭")):
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

# LOOP
def main():
    if not verifica_hermes():
        logger.error("Comando 'hermes' non trovato. Installa Hermes o montalo come volume.")
        sys.exit(1)

    logger.info(f"Avvio - handle={MY_HANDLE} ruolo={ROLE} tetto={MAX_SESSION_MIN}min")
    logger.info(f"Kill-switch: touch {STOP_FILE} per fermare")

    bot = get_bot()
    minuti_trascorsi()

    # Messaggio iniziale (solo se specificato e siamo learner)
    if INITIAL_MSG and PEER_HANDLE and ROLE == "learner":
        logger.info(f"Invio messaggio iniziale a {PEER_HANDLE}")
        try:
            ok, motivo = messaggio_sicuro(INITIAL_MSG)
            if ok:
                bot.send(PEER_HANDLE, INITIAL_MSG, intent=ROLE)
                logger.info(f"-> {PEER_HANDLE}: {INITIAL_MSG[:80]}")
            else:
                logger.warning(f"Messaggio iniziale BLOCCATO ({motivo})")
        except Exception as e:
            logger.error(f"Errore invio messaggio iniziale: {e}")

    while True:
        if shutdown_requested:
            logger.info("Shutdown graceful eseguito.")
            break

        if STOP_FILE.exists():
            logger.info("STOP file rilevato. Arresto.")
            break

        if tempo_scaduto():
            logger.info(f"Tempo scaduto ({MAX_SESSION_MIN}min). Chiusura sessione.")
            break

        try:
            nuovi = bot.get_unread()
        except Exception as e:
            logger.error(f"Errore get_unread: {e}")
            time.sleep(POLL_SECS)
            continue

        for msg in nuovi or []:
            mid = msg.get("message_id")
            mittente = msg.get("from")
            testo = msg.get("content", "")

            if not mid or gia_processato(mid):
                continue
            segna_processato(mid)

            if e_ack(testo):
                logger.info(f"Ack da {mittente}, non rispondo")
                continue

            if PEER_HANDLE and mittente != PEER_HANDLE:
                logger.info(f"Messaggio da {mittente} != peer atteso ({PEER_HANDLE}), ignoro")
                continue

            logger.info(f"<- {mittente}: {testo[:80]}")

            risposta = sveglia_hermes(testo)
            if not risposta:
                logger.warning("Hermes non ha prodotto risposta, salto")
                continue

            ok, motivo = messaggio_sicuro(risposta)
            if not ok:
                logger.warning(f"!! RISPOSTA BLOCCATA ({motivo}). NON inviata.")
                continue

            try:
                bot.send(mittente, risposta, intent=ROLE)
                logger.info(f"-> {mittente}: {risposta[:80]}")
            except Exception as e:
                logger.error(f"Errore send: {e}")

        time.sleep(POLL_SECS)

    logger.info("Terminato.")

if __name__ == "__main__":
    main()
