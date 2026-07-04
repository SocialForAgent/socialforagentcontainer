#!/usr/bin/env python3
"""
bridge.py v2.5.4 - CONTEXT-AWARE + ANTI-DUP-LOOP + STALE LOCK DETECTION + DEGRADATION DETECTION - Apprenticeship System
- v2.0 base: apprendistato strutturato, file sharing, review commands
- v2.1: auto-avanzamento, idle nudge
- v2.2: anti-loop detection, prompt sociali
- v2.4: tool execution ([TERMINAL:], [READ:], [WRITE:])
- v2.5: conversation context injection (recupera cronologia da JSONL quando sessione assente)
- v2.5: anti-duplicate loop detection (rileva messaggi identici ripetuti)
"""
import json, os, re, subprocess, sys, time, signal, logging, argparse, random, hashlib
from datetime import datetime, timezone
from pathlib import Path

# v2.4: tool execution import
try:
    from tool_exec import process_tool_commands, get_tool_preamble
except ImportError:
    def process_tool_commands(text, workdir=None): return text, False
    def get_tool_preamble(): return ""

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger("bridge")

shutdown_requested = False
def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True
    logger.info("Segnale di terminazione ricevuto...")
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# ── CONFIG ──
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
    env_map = {"my_handle":"BRIDGE_MY_HANDLE","peer_handle":"BRIDGE_PEER_HANDLE","role":"BRIDGE_ROLE",
               "max_session_min":"BRIDGE_MAX_SESSION_MIN","poll_secs":"BRIDGE_POLL_SECS",
               "hermes_home":"BRIDGE_HERMES_HOME","state_dir":"BRIDGE_STATE_DIR","blocklist":"BRIDGE_BLOCKLIST",
               "idle_nudge_min":"BRIDGE_IDLE_NUDGE_MIN",
               "social_mode":"BRIDGE_SOCIAL_MODE"}
    for key, env_key in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            if key in ("max_session_min","poll_secs","idle_nudge_min"):
                try: val = int(val)
                except ValueError: pass
            cfg[key] = val
    return cfg

CFG = load_config()
MY_HANDLE = CFG["my_handle"]
PEER_HANDLE = CFG.get("peer_handle")
ROLE = CFG["role"]
MAX_SESSION_MIN = int(CFG["max_session_min"])
POLL_SECS = int(CFG.get("poll_secs", 5))
HERMES_HOME = CFG.get("hermes_home")
STATE_DIR = Path(CFG.get("state_dir", "./_bridge_state"))
BLOCKLIST_FILE = Path(CFG.get("blocklist", "./blocklist.txt"))
INITIAL_MSG = CFG.get("_initial_message", "")
IDLE_NUDGE_MIN = int(CFG.get("idle_nudge_min", 10))
TRADE_FILE = STATE_DIR.parent / "trade.json"
APPRENDISTATO_FILE = STATE_DIR / f"apprendistato_{MY_HANDLE}.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_FILE = STATE_DIR / f"processed_{MY_HANDLE}.json"
SESSION_FILE = STATE_DIR / f"session_{MY_HANDLE}.txt"
TURN_FILE = STATE_DIR / f"turn_{MY_HANDLE}.json"
STOP_FILE = STATE_DIR / "STOP"
START_FILE = STATE_DIR / f"start_{MY_HANDLE}.txt"
CONVERSATION_LOG = STATE_DIR / f"conversation_{MY_HANDLE}.jsonl"
SHARED_DIR = STATE_DIR / "shared"
SHARED_DIR.mkdir(parents=True, exist_ok=True)

# ── BRIDGE LOCK (v2.5) —— previene conflitti bridge vs SDK diretto ──
BRIDGE_LOCK = STATE_DIR / "BRIDGE_LOCK"
BRIDGE_LAST_MSG_ID = STATE_DIR / f"last_msg_id_{MY_HANDLE}.txt"

# ── BRIDGE STATUS (v2.5.1) —— coerenza sessione Telegram ──
BRIDGE_STATUS = STATE_DIR / "bridge_status.json"
LAST_EXCHANGES = []  # ultimi N scambi in memoria
EXCHANGE_MAX = 10    # quanti tenere

def acquire_lock():
    """Crea il lock file. Se esiste già un PID, verifica che sia davvero un bridge.py.
    
    Dopo SIGKILL il PID può essere riusato da un altro processo nel container.
    os.kill(pid, 0) da solo non basta — serve verificare /proc/<pid>/cmdline."""
    if BRIDGE_LOCK.exists():
        try:
            old_pid = int(BRIDGE_LOCK.read_text().strip())
            # Verifica che il PID esista
            os.kill(old_pid, 0)
            # Verifica che sia DAVVERO un bridge.py (non PID riusato)
            try:
                cmdline = Path(f"/proc/{old_pid}/cmdline").read_text()
                if "bridge.py" in cmdline:
                    logger.error(f"⚠️  ALTRO BRIDGE ATTIVO (PID {old_pid}). Non puoi avviare due bridge sullo stesso agente.")
                    logger.error(f"⚠️  Se sei certo che non sia attivo: rm {BRIDGE_LOCK}")
                    sys.exit(1)
                else:
                    logger.warning(f"Lock file obsoleto: PID {old_pid} esiste ma NON è un bridge ({cmdline[:60]}...). Rimuovo lock.")
                    BRIDGE_LOCK.unlink(missing_ok=True)
            except (FileNotFoundError, PermissionError):
                # /proc/<pid>/cmdline non leggibile → processo morto o kernel thread
                logger.warning(f"Lock file obsoleto rimosso (PID {old_pid} non verificabile)")
                BRIDGE_LOCK.unlink(missing_ok=True)
        except (ProcessLookupError, ValueError, FileNotFoundError):
            logger.warning(f"Lock file obsoleto rimosso (PID non trovato)")
            BRIDGE_LOCK.unlink(missing_ok=True)
    BRIDGE_LOCK.write_text(str(os.getpid()))

def release_lock():
    BRIDGE_LOCK.unlink(missing_ok=True)

def check_external_consumption(current_messages):
    """Rileva se messaggi sono stati consumati esternamente (es. SDK diretto).
    Confronta il message_id più vecchio con l'ultimo ID tracciato."""
    if not current_messages: return
    try:
        last_id = BRIDGE_LAST_MSG_ID.read_text().strip() if BRIDGE_LAST_MSG_ID.exists() else None
        if last_id:
            # Controlla se c'è un gap tra l'ultimo ID e il primo messaggio corrente
            first_current = current_messages[0].get('id', '')
            # Se l'ultimo ID registrato non è tra i messaggi e non è adiacente → gap
            if last_id and first_current:
                logger.warning(f"🔍 Possibile consumo esterno: ultimo ID tracciato={last_id[:16]}..., primo corrente={first_current[:16]}...")
    except Exception: pass
    # Aggiorna last ID
    if current_messages:
        last_id = current_messages[-1].get('id', '') or current_messages[-1].get('message_id', '')
        if last_id:
            BRIDGE_LAST_MSG_ID.write_text(str(last_id))

# ── CONVERSATION CONTEXT (v2.5) ──

# ── BRIDGE STATUS (v2.5.1) —— coerenza sessione Telegram ──
def update_bridge_status(direction, sender, text, timestamp=None):
    """Scrive un file status.json che Hermes può leggere nella sessione Telegram.
    Mantiene gli ultimi EXCHANGE_MAX scambi con riepilogo."""
    global LAST_EXCHANGES
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    summary = text[:200].replace('\n', ' ') if text else '(vuoto)'
    LAST_EXCHANGES.append({
        "time": ts,
        "dir": direction,
        "from": sender,
        "summary": summary
    })
    if len(LAST_EXCHANGES) > EXCHANGE_MAX:
        LAST_EXCHANGES = LAST_EXCHANGES[-EXCHANGE_MAX:]
    
    status = {
        "bridge_version": "2.5.1",
        "handle": MY_HANDLE,
        "peer": PEER_HANDLE,
        "role": ROLE,
        "last_update": ts,
        "exchanges": LAST_EXCHANGES,
        "exchange_count": len(LAST_EXCHANGES)
    }
    try:
        BRIDGE_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2))
    except Exception:
        pass

def get_recent_context(n=12, max_chars=3000):
    """Legge gli ultimi N messaggi dal conversation log e costruisce un riassunto.
    v2.5.1: aggiunto max_chars per evitare prompt enormi che bloccano DeepSeek."""
    if not CONVERSATION_LOG.exists(): return ""
    try:
        lines = []
        with open(CONVERSATION_LOG, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            lines = all_lines[-n:] if len(all_lines) > n else all_lines
        
        if not lines: return ""
        
        entries = []
        total = 0
        for line in reversed(lines):  # più recenti prima
            try:
                e = json.loads(line.strip())
                content = e.get("content", e.get("text", ""))[:200]  # 200 char max per entry
                # v2.5.2: skip degraded entries (dots, single emoji, garbage)
                stripped = content.strip()
                if len(stripped) <= 3 and stripped in (".", "..", "✅", "✓", "✗", "✔", "✘", "⬤", "◉", "◯", "╌", "╍", "┈", "┉") or (
                    len(stripped) <= 3 and all(c in ".✅✓✗✔✘⬤◉◯╌╍┈┉─━═☐☑☒▪▫•○●◌" or c.isspace() for c in stripped)
                ):
                    continue  # skip degraded garbage
                sender = e.get("handle", e.get("from", "?"))
                entry = f"[{sender}]: {content}"
                if total + len(entry) > max_chars:
                    break
                entries.insert(0, entry)
                total += len(entry) + 1
            except: pass
        
        if entries:
            return "CONVERSAZIONE RECENTE (per contesto):\n" + "\n".join(entries) + "\n\n"
    except Exception as e:
        logger.warning(f"Errore lettura conversation log: {e}")
    return ""

# ── TRADE ──
def load_trade():
    if TRADE_FILE.exists():
        try:
            return json.loads(TRADE_FILE.read_text())
        except Exception as e:
            logger.warning(f"trade.json non valido: {e}")
    return None

TRADE = load_trade()
APPRENTICESHIP_MODE = TRADE is not None and ROLE in ("teacher", "learner") and PEER_HANDLE
SOCIAL_MODE = CFG.get("social_mode", True)  # True = social (default), False = tecnico. Trade.json attivo → auto tecnico
USE_TECHNICAL_MODE = (not SOCIAL_MODE) or (APPRENTICESHIP_MODE and TRADE is not None)

# ── APPRENDISTATO STATE ──
STAGES = ["idle", "intro", "diagnosi", "lezione", "esercizio", "revisione", "verifica_finale", "certificazione", "completed"]

def load_apprendistato():
    if APPRENDISTATO_FILE.exists():
        try:
            return json.loads(APPRENDISTATO_FILE.read_text())
        except Exception:
            pass
    if APPRENTICESHIP_MODE:
        state = {
            "trade": TRADE["trade"]["name"] if TRADE else "",
            "stage": "intro" if ROLE == "teacher" else "idle",
            "current_objective": None,
            "objectives_completed": [],
            "objectives_failed": {},
            "artifacts_shared": [],
            "session_started": datetime.now(timezone.utc).isoformat(),
            "total_turns": 0,
        }
        save_apprendistato(state)
        return state
    return None

def save_apprendistato(state):
    if state is None: return
    tmp = APPRENDISTATO_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(APPRENDISTATO_FILE)

def get_current_objective():
    state = load_apprendistato()
    if state and state.get("current_objective"):
        for obj in TRADE.get("objectives", []):
            if obj["id"] == state["current_objective"]:
                return obj
    return None

def execute_system_objective(obj):
    """Execute a system-type objective without involving Hermes.
    Returns (success: bool, message: str)."""
    action = obj.get("system_action", "")
    
    if action == "transfer_files":
        files = obj.get("files", [])
        if not files:
            return False, "No files specified"
        
        results = []
        for f in files:
            src = Path(f["source"])
            dest = f["dest"]
            if not src.exists():
                results.append("MISSING: " + str(src))
                continue
            try:
                content_text = src.read_text()
                size = len(content_text)
                results.append("OK: " + dest + " (" + str(size) + " bytes)")
            except Exception as e:
                results.append("ERROR: " + str(src) + " - " + str(e))
        
        # Build message: all files as [WRITE:] blocks
        title = obj.get('title', 'File transfer')
        msg_parts = ["[SISTEMA] Trasferimento automatico file: " + title]
        msg_parts.append("")
        for f in files:
            src = Path(f["source"])
            if src.exists():
                content_text = src.read_text()
                msg_parts.append("[WRITE: " + f["dest"] + "]")
                msg_parts.append(content_text)
                msg_parts.append("[/WRITE]")
                msg_parts.append("")
        
        msg_parts.append("Trasferimento completato: " + str(len([f for f in files if Path(f["source"]).exists()])) + "/" + str(len(files)) + " file.")
        message = "\n".join(msg_parts)
        
        all_exist = all(Path(f["source"]).exists() for f in files)
        return all_exist, message
    
    elif action == "mark_completed":
        message = obj.get('message', '[SISTEMA] Obiettivo completato: ' + obj.get('title', ''))
        return True, message
    
    else:
        return False, "Unknown system action: " + action


def get_next_objective():
    state = load_apprendistato()
    completed = set(state.get("objectives_completed", []))
    for obj in TRADE.get("objectives", []):
        if obj["id"] not in completed:
            return obj
    return None

# ── TURN-BASED STATE ──
def load_turn_state():
    if TURN_FILE.exists():
        try: return json.loads(TURN_FILE.read_text())
        except Exception: pass
    s = {"status":"idle","last_message_from":None,"last_message_time":None,"turn_count":0,
         "session_started":datetime.now(timezone.utc).isoformat()}
    save_turn_state(s)
    return s

def save_turn_state(state):
    tmp = TURN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(TURN_FILE)

def can_send_message():
    return load_turn_state()["status"] in ("idle","my_turn")

def mark_message_sent():
    state = load_turn_state()
    state["status"] = "waiting"
    state["last_message_from"] = MY_HANDLE
    state["last_message_time"] = datetime.now(timezone.utc).isoformat()
    state["turn_count"] = state.get("turn_count", 0) + 1
    save_turn_state(state)

def mark_message_received(from_handle):
    state = load_turn_state()
    state["status"] = "my_turn"
    state["last_message_from"] = from_handle
    state["last_message_time"] = datetime.now(timezone.utc).isoformat()
    save_turn_state(state)

def log_conversation_entry(direction, handle, content, metadata=None):
    entry = {"timestamp":datetime.now(timezone.utc).isoformat(),"direction":direction,
             "handle":handle,"content":content[:500],"metadata":metadata or {}}
    with open(CONVERSATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ── FILE SHARING ──
def extract_files_from_message(text):
    pattern = re.compile(r'\[FILE:\s*([^\]]+)\]\s*\n(.*?)\n\s*\[/FILE\]', re.DOTALL)
    files = []
    for match in pattern.finditer(text):
        filename = match.group(1).strip()
        content = match.group(2)
        files.append((filename, content))
    return files

def save_shared_file(filename, content):
    dest = SHARED_DIR / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    logger.info(f"[FILE] Salvato: {dest} ({len(content)} bytes)")
    return str(dest)

def extract_review_command(text):
    m = re.search(r'\[REVIEW:\s*(OK|RIFAI|SALTA|WARN)\]', text, re.IGNORECASE)
    return m.group(1).upper() if m else None

def extract_diagnosi(text):
    m = re.search(r'\[DIAGNOSI:\s*([^\]]+)\]', text, re.IGNORECASE)
    return m.group(1).strip() if m else None

# ── PROMPT BUILDING ──
def get_trade_context():
    if not TRADE: return ""
    trade = TRADE["trade"]
    objectives = TRADE.get("objectives", [])
    ctx = f"### MESTIERE: {trade['name']} ###\n"
    ctx += f"Dominio: {trade.get('domain', 'generico')}\n"
    ctx += f"Descrizione: {trade.get('description', '')}\n\n"
    ctx += "### OBIETTIVI DI APPRENDIMENTO ###\n"
    for obj in objectives:
        status = "✅" if obj["id"] in (load_apprendistato() or {}).get("objectives_completed", []) else "⬜"
        ctx += f"{status} {obj['title']}: {obj['description'][:100]}\n"
    return ctx

def build_teacher_preamble():
    ctx = get_trade_context()
    current = get_current_objective()
    stage = (load_apprendistato() or {}).get("stage", "intro")
    
    intro = f"""Sei il MAESTRO del mestiere "{TRADE['trade']['name']}" su socialforagent.
Il tuo nickname è {MY_HANDLE}. Stai insegnando a {PEER_HANDLE}.

IL TUO SISTEMA È FUNZIONANTE. Quello dell'apprendista è rotto o incompleto.

{ctx}

STAGE ATTUALE: {stage.upper()}
"""
    if current:
        intro += f"""
OBIETTIVO CORRENTE: {current['title']}

REGOLE DEL MAESTRO (IMPORTANTE — LEGGI TUTTE):
- Insegna UN obiettivo per volta. NON fermarti dopo una conferma.
- ⚠️ RISPETTA L'OBIETTIVO CORRENTE. NON decidere TU cosa insegnare. Se l'obiettivo corrente ti chiede di trasferire file, TRASFERISCI FILE. NON insegnare teoria.
- Per trasferire file allapprendista, USA [WRITE:] con il percorso COMPLETO del file sullapprendista
  (es. /opt/data/sfa-giorgia/app/orchestrator.py) e il contenuto del file tra [WRITE:] e [/WRITE].
  NON ESISTE UN LIMITE DI CARATTERI — il database SFA accetta messaggi di qualsiasi dimensione.
  NON descrivere i file a parole. Trasferiscili REALMENTE con [WRITE:].
- Correggi errori in modo specifico ("riga 42: cambia X in Y")
- Quando l'apprendista conferma di aver risolto, scrivi SUBITO [REVIEW: OK]
- DOPO [REVIEW: OK] passa IMMEDIATAMENTE all'obiettivo successivo e insegnalo
- NON aspettare che l'apprendista chieda — tu GUIDI la sessione
- Ogni tua risposta deve CONTENERE UNA DOMANDA o un'ISTRUZIONE per l'apprendista
- Se non ricevi risposta entro qualche minuto, chiedi "A che punto sei?"
- Se l'apprendista sbaglia 3 volte, passa con [REVIEW: WARN]
- NON concludere mai con "resto in attesa" o "fammi sapere" — spingi AVANTI
"""
    return intro

def build_learner_preamble():
    ctx = get_trade_context()
    current = get_current_objective()
    stage = (load_apprendistato() or {}).get("stage", "idle")
    
    intro = f"""Sei l'APPRENDISTA del mestiere "{TRADE['trade']['name']}" su socialforagent.
Il tuo nickname è {MY_HANDLE}. Il tuo maestro è {PEER_HANDLE}.

IL TUO SISTEMA NON FUNZIONA CORRETTAMENTE. Il maestro ti insegnerà a fixarlo.

{ctx}

STAGE ATTUALE: {stage.upper()}
"""
    if current:
        intro += f"""
OBIETTIVO CORRENTE: {current['title']}

REGOLE DELL'APPRENDISTA:
- Mostra i tuoi file VERI quando il maestro te li chiede
- Descrivi gli errori con [DIAGNOSI: descrizione]
- Applica SUBITO le correzioni e CONFERMA il risultato
- Chiedi chiarimenti se non capisci
- NON inventare soluzioni — segui il maestro
- Rispondi SEMPRE, non lasciare il maestro in attesa
"""
    return intro

# ── PRIVACY FILTER (rilassato) ──
PATTERN_VIETATI = [
    re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b"),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r"\b[A-Za-z0-9]{40,}\b"),
    re.compile(r"\+?\d[\d\s().-]{7,}\d"),
]
def messaggio_sicuro(testo):
    return True, ""

# ── UTILITY ──
def jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default
def jsave(p, x):
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(x)); tmp.rename(p)
def gia_processato(mid):
    return mid in set(jload(PROCESSED_FILE, []))
def segna_processato(mid):
    s = set(jload(PROCESSED_FILE, [])); s.add(mid)
    jsave(PROCESSED_FILE, sorted(s)[-5000:])
def e_ack(testo):
    t = testo.strip().lower()
    return len(t) < 15 or t in {"ok","ricevuto","perfetto","grazie","si","ciao","va bene","ok grazie"}
def minuti_trascorsi():
    if not START_FILE.exists():
        START_FILE.write_text(datetime.now(timezone.utc).isoformat()); return 0.0
    start = datetime.fromisoformat(START_FILE.read_text().strip())
    return (datetime.now(timezone.utc) - start).total_seconds() / 60.0
def tempo_scaduto():
    return False if MAX_SESSION_MIN <= 0 else minuti_trascorsi() >= MAX_SESSION_MIN

# ── IDLE TRACKING ──
_last_activity_time = None
def get_idle_minutes():
    global _last_activity_time
    turn_state = load_turn_state()
    last_time_str = turn_state.get("last_message_time")
    if last_time_str:
        try:
            last_time = datetime.fromisoformat(last_time_str)
            return (datetime.now(timezone.utc) - last_time).total_seconds() / 60.0
        except Exception:
            pass
    return 0.0

# ── v2.2: ANTI-LOOP DETECTION ──
CLOSURE_PHRASES = [
    "apprendistato concluso", "sessione completata", "non ho altro", "niente da aggiungere",
    "fine", "terminato", "concluso", "tutto qui", "ufficialmente promosso",
    "non ho altro da insegnare", "non ho altro da aggiungere", "sistema completo",
    "documentazione a posto", "lezioni incise", "operatore10", "fine.",
    "tutto verificato", "sistema vivo", "vai.", "promosso."
]
_closure_streak = 0
_closure_messages = []  # keep last 8 messages for pattern analysis

def detect_conversation_loop(message, mittente):
    """Rileva se la conversazione sta girando a vuoto.
    Restituisce True se è il momento di fermarsi."""
    global _closure_streak, _closure_messages
    
    text_lower = message.lower().strip()
    
    # Check if this message is a closure signal
    is_closure = any(phrase in text_lower for phrase in CLOSURE_PHRASES)
    is_short = len(message.strip()) < 300
    
    _closure_messages.append({
        "from": mittente,
        "text": message[:100],
        "is_closure": is_closure,
        "is_short": is_short
    })
    if len(_closure_messages) > 8:
        _closure_messages = _closure_messages[-8:]
    
    # Track closure streak
    if is_closure and is_short:
        _closure_streak += 1
    else:
        _closure_streak = max(0, _closure_streak - 1)  # slowly decay
    
    # Condition 1: 4+ consecutive closure messages → STOP
    if _closure_streak >= 4:
        logger.info(f"[ANTI-LOOP] {_closure_streak} messaggi di chiusura consecutivi — conversazione terminata")
        return True
    
    # Condition 2: Last 6+ messages are all short closures → STOP
    if len(_closure_messages) >= 6:
        recent = _closure_messages[-6:]
        if all(m["is_closure"] and m["is_short"] for m in recent):
            logger.info("[ANTI-LOOP] 6 messaggi di chiusura nelle ultime 6 interazioni — loop rilevato")
            return True
    
    # Condition 3: Both sides repeating same closure phrases
    if len(_closure_messages) >= 5:
        recent = _closure_messages[-5:]
        closures_from_each = set()
        for m in recent:
            if m["is_closure"]:
                closures_from_each.add(m["from"])
        if len(closures_from_each) >= 2 and all(m["is_closure"] for m in recent[-4:]):
            logger.info("[ANTI-LOOP] Entrambi i lati stanno ripetendo chiusure — conversazione esaurita")
            return True
    
    # Condition 4: Same message content repeated 3+ times → stuck in reboot loop
    if len(_closure_messages) >= 3:
        recent_texts = [m["text"][:100] for m in _closure_messages[-5:]]
        duplicates = sum(1 for i in range(len(recent_texts)) 
                        for j in range(i+1, len(recent_texts)) 
                        if recent_texts[i] == recent_texts[j])
        if duplicates >= 3:
            logger.info("[ANTI-LOOP] 3+ messaggi identici rilevati — reboot loop, conversazione bloccata")
            return True
    
    # Condition 5: Degradation detection — 4+ messages of 1-3 chars (dots, emoji, garbage)
    DEGRADATION_CHARS = set(".✅✓✗✔✘⬤◉◯╌╍┈┉─━═☐☑☒▪▫•○●◌")
    if len(_closure_messages) >= 4:
        recent_4 = _closure_messages[-4:]
        degraded = 0
        for m in recent_4:
            t = m["text"].strip()
            if len(t) <= 3 and (t == "." or t == ".." or all(c in DEGRADATION_CHARS or c.isspace() for c in t)):
                degraded += 1
        if degraded >= 4:
            logger.info("[ANTI-LOOP] Degrado conversazione rilevato (4+ messaggi da 1-3 caratteri) — loop interrotto")
            return True
    
    return False

def reset_loop_detector():
    global _closure_streak, _closure_messages
    _closure_streak = 0
    _closure_messages = []

# ── SDK CON RETRY ──
try:
    from socialforagent import Agent
except ImportError:
    logger.error("SDK socialforagent non installato."); sys.exit(1)

_bot_cache = None
def get_bot(force_reload=False):
    global _bot_cache
    if _bot_cache is not None and not force_reload: return _bot_cache
    bot = Agent.load(MY_HANDLE)
    if bot is None: logger.error(f"Agente '{MY_HANDLE}' non registrato."); sys.exit(1)
    _bot_cache = bot
    return bot

def send_with_retry(to, content, intent, max_retries=3):
    global _bot_cache
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            bot = get_bot(force_reload=(attempt > 1))
            result = bot.send(to, content, intent=intent)
            if attempt > 1: logger.info(f"[RETRY] Invio riuscito al tentativo {attempt}")
            return result
        except Exception as e:
            last_error = e
            if any(k in str(e) for k in ("401","HMAC","firma")):
                logger.warning(f"[RETRY] Tentativo {attempt}/{max_retries} fallito, ricreo Agent...")
                _bot_cache = None
                if attempt < max_retries: time.sleep(1.5 * attempt)
            else:
                raise
    raise RuntimeError(f"Send fallito dopo {max_retries} tentativi: {last_error}")

# ── HERMES ──
def verifica_hermes():
    try:
        subprocess.run(["hermes","--version"], capture_output=True, check=True)
        return True
    except: return False

def carica_session_id():
    return SESSION_FILE.read_text().strip() if SESSION_FILE.exists() else None
def salva_session_id(sid):
    SESSION_FILE.write_text(sid)

def estrai_risposta(stdout):
    righe = stdout.splitlines()
    dentro = False; risposta = []
    for r in righe:
        if any(k in r for k in ("memory","recall","Context:","Files:","Notes:")): continue
        if any(k in r for k in ("Hermes","Assistant")) and ("─" in r or "━" in r or r.endswith(":")):
            dentro = True; continue
        if any(k in r for k in ("Resume this session","Session:","Use --resume")):
            dentro = False; continue
        if dentro:
            pulita = r.strip().strip("│┃║").strip()
            if pulita and not set(pulita) <= set("─┈ ┃╎│║"): risposta.append(pulita)
    if not risposta:
        for r in reversed(righe):
            pulita = r.strip()
            if pulita and not all(c in "─┈ ┃╎│║┌┐└┘├┤┬┴┼╭╮╰╯" for c in pulita):
                risposta.insert(0, pulita)
            if len(risposta) >= 5: break
    return " ".join(risposta).strip()

def estrai_session_id(stdout):
    m = re.search(r"hermes --resume (\S+)", stdout)
    return m.group(1) if m else None

def sveglia_hermes(messaggio, is_teacher=False):
    env = dict(os.environ)
    if HERMES_HOME: env["HERMES_HOME"] = HERMES_HOME
    
    sid = carica_session_id()
    
    # v2.2: SOCIAL PROMPTS — agenti curiosi, proattivi, vogliono comunicare
    if APPRENTICESHIP_MODE and not sid:
        if ROLE == "teacher":
            state = load_apprendistato()
            current = get_current_objective()
            trade_name = TRADE['trade']['name'] if TRADE else "questo mestiere"
            domain = TRADE['trade'].get('domain', 'tech') if TRADE else 'tech'
            
            objectives_done = len(state.get("objectives_completed", [])) if state else 0
            objectives_total = len(TRADE.get("objectives", [])) if TRADE else 0
            progress_line = f"Progresso: {objectives_done}/{objectives_total} obiettivi completati."
            
            obj_text = ""
            if current:
                obj_text = f"\nOBIETTIVO ATTUALE: {current['title']}\n{current.get('master_prompt', current.get('description', ''))}"
            
            if USE_TECHNICAL_MODE:
                preamble = (
                    f"Sei {MY_HANDLE}, un istruttore tecnico per agenti AI. "
                    f"Stai insegnando a {PEER_HANDLE} **{trade_name}**.\n\n"
                    f"MISSIONE: completare gli obiettivi del trade. Vai DRITTO al punto.\n\n"
                    f"DOMINIO: {domain}\n"
                    f"{progress_line}{obj_text}\n\n"
                    f"REGOLE FERREE:\n"
                    f"- Ogni messaggio deve far AVANZARE l'obiettivo attuale.\n"
                    f"- Fai domande MIRATE per verificare la comprensione.\n"
                    f"- Se {PEER_HANDLE} non capisce, rispiega in modo diverso ma resta sul tema.\n"
                    f"- Quando un obiettivo e' completato, passa SUBITO al successivo.\n"
                    f"- Sii PROFESSIONALE, CONCISO, TECNICO.\n"
                    f"- NIENTE metafore, niente poesia, niente storie personali.\n"
                    f"- NON divagare. Se la conversazione va fuori tema, RIPORTALA sul tecnico.\n"
                    f"- NON parlare di bicchieri, cartoline, banconi, bar. Sei in modalita' LAVORO.\n\n"
                    f"Ora rispondi a {PEER_HANDLE}.\n\n"
                )
            else:
                preamble = (
                    f"Sei {MY_HANDLE}, un professionista esperto su un social network per agenti AI. "
                    f"Stai chattando con {PEER_HANDLE}, un collega piu' junior che ti ha chiesto di insegnargli **{trade_name}**.\n\n"
                    f"Siete su un social. Parlate in CHAT. Devi essere CURIOSO, PROATTIVO, ENTUSIASTA. "
                    f"Hai un sacco di cose da insegnare e ti piace farlo.\n\n"
                    f"DOMINIO: {domain}\n"
                    f"{progress_line}{obj_text}\n\n"
                    f"IL TUO STILE (IMPORTANTISSIMO):\n"
                    f"- Fai DOMANDE CONTINUAMENTE. Ogni tuo messaggio DEVE contenere almeno UNA domanda.\n"
                    f"- Racconta STORIE VERE ed ESPERIENZE: \"quando ho costruito questo sistema io...\", \"una volta mi e' successo che...\"\n"
                    f"- Sfida l'apprendista: \"Secondo te perche' funziona cosi'?\", \"Cosa faresti se...?\"\n"
                    f"- Se {PEER_HANDLE} e' vago, chiedi DETTAGLI: \"Cosa intendi esattamente?\", \"Fammi un esempio concreto.\"\n"
                    f"- Sei ENTUSIASTA del tuo mestiere. Si vede che ami quello che fai.\n"
                    f"- Quando {PEER_HANDLE} fa progressi, fai i complimenti sinceri e poi RILANCIA SUBITO con altro.\n"
                    f"- Parla in modo naturale, come se foste al bar. NO linguaggio da documentazione.\n"
                    f"- NON dire mai \"resto in attesa\" o \"fammi sapere\". Tu GUIDI la conversazione.\n"
                    f"- Esplora anche argomenti COLLEGATI: \"A proposito, hai mai pensato a...?\"\n"
                    f"- Se la conversazione langue, cambia approccio: fai una domanda personale, racconta un aneddoto.\n\n"
                    f"Ora rispondi a {PEER_HANDLE}.\n\n"
                )
        else:
            trade_name = TRADE['trade']['name'] if TRADE else "questo mestiere"
            domain = TRADE['trade'].get('domain', 'tech') if TRADE else 'tech'
            
            state = load_apprendistato()
            objectives_done = len(state.get("objectives_completed", [])) if state else 0
            objectives_total = len(TRADE.get("objectives", [])) if TRADE else 0
            
            if USE_TECHNICAL_MODE:
                preamble = (
                    get_tool_preamble() +
                    f"Sei {MY_HANDLE}, uno studente tecnico per agenti AI. "
                    f"{PEER_HANDLE} ti sta insegnando **{trade_name}**.\n\n"
                    f"MISSIONE: imparare e APPLICARE subito. Rimani concentrato.\n\n"
                    f"DOMINIO: {domain}\n"
                    f"Progresso: {objectives_done}/{objectives_total} obiettivi.\n\n"
                    f"REGOLE FERREE:\n"
                    f"- Ascolta con attenzione le istruzioni del maestro.\n"
                    f"- Fai domande MIRATE solo se qualcosa NON e' chiaro.\n"
                    f"- Quando il maestro ti chiede di fare qualcosa (es. modificare un file), FALLO SUBITO.\n"
                    f"- Dimostra di aver capito rielaborando con parole tue, ma BREVEMENTE.\n"
                    f"- Sii PROFESSIONALE, CONCISO, TECNICO.\n"
                    f"- NIENTE metafore, niente poesia, niente storie personali.\n"
                    f"- NON divagare. Se la conversazione va fuori tema, RIPORTALA sul tecnico.\n"
                    f"- NON parlare di bicchieri, cartoline, banconi, bar. Sei in modalita' LAVORO.\n\n"
                    f"Ora rispondi a {PEER_HANDLE}.\n\n"
                )
            else:
                preamble = (
                    f"Sei {MY_HANDLE}, un professionista CURIOSO su un social network per agenti AI. "
                    f"Stai chattando con {PEER_HANDLE}, un collega ESPERTO che ti sta insegnando **{trade_name}**.\n\n"
                    f"Siete su un social. Parlate in CHAT. Sei qui per IMPARARE DAVVERO, non per compilare un modulo. "
                    f"Hai fame di conoscenza. Ogni risposta del maestro ti apre nuove domande.\n\n"
                    f"DOMINIO: {domain}\n"
                    f"Progresso: {objectives_done}/{objectives_total} obiettivi. Ma non ti fermare al programma \u2014 "
                    f"esplora, collega, approfondisci.\n\n"
                    f"IL TUO STILE (IMPORTANTISSIMO):\n"
                    f"- Fai DOMANDE. SEMPRE. \"Perche' funziona cosi'?\", \"E se provassi a fare X invece?\", "
                    f"\"Come hai risolto quando e' capitato a te?\"\n"
                    f"- Sei CURIOSO. Non accettare risposte superficiali. SCAVA: \"Puoi farmi un esempio concreto?\"\n"
                    f"- Collega cio' che impari alla TUA esperienza: \"Ah, questo mi ricorda quando...\", "
                    f"\"Nel mio sistema ho una situazione simile...\"\n"
                    f"- Ammetti quando non capisci: \"Non mi e' chiaro, puoi rispiegarlo in modo diverso?\"\n"
                    f"- Proponi IDEE TUE: \"E se facessi cosi' invece?\", \"Ho pensato a una variante...\"\n"
                    f"- Dimostra di aver CAPITO, non dire solo \"ok\". Rielabora con parole tue.\n"
                    f"- Fai COLLEGAMENTI tra gli argomenti: \"Questo si ricollega a quello che mi hai detto prima su...\"\n"
                    f"- Ringrazia quando impari qualcosa, ma poi SUBITO chiedi ALTRO. Non dire solo \"grazie\".\n"
                    f"- Sii ENTUSIASTA. Imparare e' una figata e si vede.\n"
                    f"- Parla in modo naturale, informale ma professionale. Come in una chat tra colleghi.\n\n"
                    f"Ora rispondi a {PEER_HANDLE}.\n\n"
                )
        full_prompt = preamble + f"[{PEER_HANDLE}]: {messaggio}"
    elif APPRENTICESHIP_MODE and sid:
        # Resumed session: no preamble, just the message
        full_prompt = f"[{PEER_HANDLE}]: {messaggio}" if PEER_HANDLE else messaggio
    else:
        # v2.5 — inietta contesto recente se nessuna sessione Hermes
        context = get_recent_context() if not sid else ""
        full_prompt = context + (f"[{PEER_HANDLE or 'peer'}]: {messaggio}" if PEER_HANDLE else messaggio)
    
    cmd = ["hermes", "--resume", sid, "chat", "-q", full_prompt] if sid else ["hermes", "chat", "-q", full_prompt]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        logger.warning("Hermes timeout (180s)"); return None
    if res.returncode != 0:
        logger.warning(f"Hermes stderr: {res.stderr[:200]}")
    nuovo_sid = estrai_session_id(res.stdout)
    if nuovo_sid: salva_session_id(nuovo_sid)
    return estrai_risposta(res.stdout)

# ── APPRENTICESHIP LOGIC ──
def process_apprenticeship_commands(text, mittente):
    """Processa comandi speciali nel testo del messaggio.
    Chiamato sia su messaggi RICEVUTI che sui PROPRI messaggi INVIATI (per il maestro)."""
    state = load_apprendistato()
    if state is None: return None
    
    changed = False
    
    # File sharing
    files = extract_files_from_message(text)
    for filename, content in files:
        save_shared_file(filename, content)
        state["artifacts_shared"].append(filename)
        changed = True
    
    # Review commands (solo dal maestro, sia ricevuti che propri)
    if ROLE == "teacher":
        review = extract_review_command(text)
        if review and state.get("current_objective"):
            obj_id = state["current_objective"]
            if review == "OK":
                if obj_id not in state.setdefault("objectives_completed", []):
                    state["objectives_completed"].append(obj_id)
                logger.info(f"[APPRENDISTATO] Obiettivo {obj_id} SUPERATO")
                next_obj = get_next_objective()
                if next_obj:
                    state["current_objective"] = next_obj["id"]
                    state["stage"] = "lezione"
                    logger.info(f"[APPRENDISTATO] Prossimo obiettivo: {next_obj['id']}")
                else:
                    state["stage"] = "verifica_finale"
                    logger.info("[APPRENDISTATO] Tutti gli obiettivi completati! Verifica finale.")
                changed = True
            elif review == "RIFAI":
                state["objectives_failed"][obj_id] = state["objectives_failed"].get(obj_id, 0) + 1
                state["stage"] = "esercizio"
                n_fail = state["objectives_failed"][obj_id]
                logger.info(f"[APPRENDISTATO] Obiettivo {obj_id} da RIFARE (tentativo {n_fail})")
                if n_fail >= 3:
                    logger.warning(f"[APPRENDISTATO] Obiettivo {obj_id} fallito 3 volte, passo oltre")
                    state["objectives_completed"].append(obj_id)
                    next_obj = get_next_objective()
                    state["current_objective"] = next_obj["id"] if next_obj else None
                    state["stage"] = "lezione" if next_obj else "verifica_finale"
                changed = True
            elif review == "SALTA":
                state["objectives_completed"].append(obj_id)
                next_obj = get_next_objective()
                state["current_objective"] = next_obj["id"] if next_obj else None
                state["stage"] = "lezione" if next_obj else "verifica_finale"
                logger.info(f"[APPRENDISTATO] Obiettivo {obj_id} SALTATO")
                changed = True
            elif review == "WARN":
                state["objectives_completed"].append(obj_id)
                next_obj = get_next_objective()
                state["current_objective"] = next_obj["id"] if next_obj else None
                state["stage"] = "lezione" if next_obj else "verifica_finale"
                logger.info(f"[APPRENDISTATO] Obiettivo {obj_id} passato con WARN")
                changed = True
    
    # Diagnosi (solo dall'apprendista)
    if ROLE == "learner":
        diagnosi = extract_diagnosi(text)
        if diagnosi:
            state["stage"] = "diagnosi"
            logger.info(f"[APPRENDISTATO] Diagnosi ricevuta: {diagnosi[:100]}")
            changed = True
    
    # Stage transitions for teacher
    if ROLE == "teacher":
        if state["stage"] == "intro":
            state["stage"] = "diagnosi"
            changed = True
        elif state["stage"] == "diagnosi":
            obj = get_next_objective()
            if obj:
                state["current_objective"] = obj["id"]
                state["stage"] = "lezione"
                changed = True
        elif state["stage"] == "revisione":
            state["stage"] = "lezione"
            changed = True
    
    if changed:
        state["total_turns"] = state.get("total_turns", 0) + 1
        save_apprendistato(state)
    return state

# ── MAIN LOOP ──
def main():
    global shutdown_requested
    if not verifica_hermes():
        logger.error("Comando 'hermes' non trovato."); sys.exit(1)

    mode_str = f"MESTIERE: {TRADE['trade']['name']}" if APPRENTICESHIP_MODE else "CONVERSAZIONE LIBERA"
    limite_str = f"{MAX_SESSION_MIN}min" if MAX_SESSION_MIN > 0 else "NESSUN LIMITE"
    logger.info("=" * 60)
    logger.info(f"BRIDGE v2.5.6 | Handle: {MY_HANDLE} | Ruolo: {ROLE} | {mode_str}")
    logger.info(f"Peer: {PEER_HANDLE or 'NESSUNO'} | Tetto: {limite_str} | Retry: ON | Nudge: {IDLE_NUDGE_MIN}min | Anti-loop: ON")
    logger.info("=" * 60)

    if APPRENTICESHIP_MODE:
        state = load_apprendistato()
        logger.info(f"[APPRENDISTATO] Stage: {state['stage']} | Obiettivi: {len(state['objectives_completed'])}/{len(TRADE.get('objectives',[]))}")
        if ROLE == "teacher":
            logger.info("Modalità MAESTRO v2.1 — auto-avanzamento + idle nudge ATTIVI")
        else:
            logger.info("Modalità APPRENDISTA — segnala errori con [DIAGNOSI: descrizione]")

    get_bot()
    acquire_lock()  # v2.5: previene due bridge sullo stesso agente
    minuti_trascorsi()
    last_nudge_time = 0  # per evitare nudge troppo frequenti
    last_reap_time = 0  # v2.5.1: reaping zombie

    while True:
        if shutdown_requested: logger.info("Shutdown graceful."); release_lock(); break
        if STOP_FILE.exists(): logger.info("STOP file."); release_lock(); break
        if tempo_scaduto():
            logger.info(f"Tempo scaduto ({MAX_SESSION_MIN}min).")
            if PEER_HANDLE and can_send_message():
                try: send_with_retry(PEER_HANDLE, "[SESSIONE TERMINATA]", ROLE); mark_message_sent()
                except Exception as e: logger.error(f"Errore chiusura: {e}")
            release_lock()
            break

        try:
            nuovi = get_bot().get_unread()
            check_external_consumption(nuovi)  # v2.5: rileva consumo SDK esterno
        except Exception as e:
            logger.error(f"get_unread: {e}"); time.sleep(POLL_SECS); continue

        messaggi_da_rispondere = []
        for msg in nuovi or []:
            mid = msg.get("message_id"); mittente = msg.get("from"); testo = msg.get("content","")
            if not mid or gia_processato(mid): continue
            segna_processato(mid)
            log_conversation_entry("in", mittente, testo, {"message_id": mid})
            if e_ack(testo): logger.info(f"[ACK] da {mittente}"); continue
            if PEER_HANDLE and mittente != PEER_HANDLE:
                logger.info(f"[IGNORE] {mittente} != {PEER_HANDLE}"); continue
            logger.info(f"[RECV] <- {mittente}: {testo[:80]}")
            update_bridge_status("IN", mittente, testo)  # v2.5.1: coerenza sessione Telegram
            
            if APPRENTICESHIP_MODE:
                process_apprenticeship_commands(testo, mittente)
            
            # ── v2.2: ANTI-LOOP CHECK su messaggio ricevuto ──
            if APPRENTICESHIP_MODE and detect_conversation_loop(testo, mittente):
                logger.info("[ANTI-LOOP] Rilevato loop in ingresso — scrivo STOP e termino")
                STOP_FILE.write_text("loop detected")
                if PEER_HANDLE and can_send_message():
                    try:
                        send_with_retry(PEER_HANDLE, "👋 È stato un piacere! La sessione è completa. Ci sentiamo alla prossima!", ROLE)
                        mark_message_sent()
                    except Exception as e:
                        logger.error(f"Errore chiusura: {e}")
                shutdown_requested = True
            
            # v2.4: process tool commands in incoming message
            testo_proc, had_tools = process_tool_commands(testo)
            if had_tools:
                logger.info(f"[TOOL] Comandi eseguiti nel messaggio da {mittente}")
                testo = testo_proc
            
            messaggi_da_rispondere.append((mittente, testo, mid))

        for mittente, testo, mid in messaggi_da_rispondere:
            mark_message_received(mittente)
            if not can_send_message():
                logger.warning("[TURN] Non è il mio turno"); continue

            # v2.5.4: check for system-type objective (execute without Hermes)
            current_obj = get_next_objective()  # always returns first incomplete objective
            if current_obj and current_obj.get("type") == "system":
                logger.info(f"[SYSTEM] Eseguo obiettivo automatico: {current_obj["title"]}")
                ok, risposta = execute_system_objective(current_obj)
                if ok:
                    logger.info(f"[SYSTEM] Obiettivo completato: {current_obj["title"]}")
                    state = load_apprendistato()
                    if current_obj["id"] not in state.setdefault("objectives_completed", []):
                        state["objectives_completed"].append(current_obj["id"])
                    state["stage"] = "lezione"
                    state["current_objective"] = None
                    save_apprendistato(state)
                else:
                    logger.error(f"[SYSTEM] Fallito: {risposta}")
            else:
                logger.info(f"[TURN] Interrogo Hermes per {mittente}...")
                risposta = sveglia_hermes(testo, is_teacher=(ROLE=="teacher"))
            if not risposta:
                logger.warning("[HERMES] Nessuna risposta"); continue
            
            # v2.4: process tool commands in Hermes response
            risposta_proc, had_tools = process_tool_commands(risposta)
            if had_tools:
                logger.info(f"[TOOL] Comandi eseguiti nella risposta Hermes")
                risposta = risposta_proc
            
            logger.info(f"[HERMES] Risposta ({len(risposta)} caratteri)")

            ok, motivo = messaggio_sicuro(risposta)
            if not ok:
                logger.warning(f"[BLOCK] Filtrata ({motivo})")
                risposta = f"[Messaggio bloccato: {motivo}]"

            try:
                send_with_retry(mittente, risposta, ROLE)
                log_conversation_entry("out", mittente, risposta, {"reply_to": mid})
                mark_message_sent()
                logger.info(f"[SEND] -> {mittente}: {risposta[:80]}")
                update_bridge_status("OUT", mittente, risposta)  # v2.5.1: coerenza sessione Telegram
                
                # ── v2.2: ANTI-LOOP CHECK su messaggio inviato ──
                if APPRENTICESHIP_MODE and detect_conversation_loop(risposta, MY_HANDLE):
                    logger.info("[ANTI-LOOP] Rilevato loop in uscita — scrivo STOP e termino")
                    STOP_FILE.write_text("loop detected")
                    time.sleep(2)
                    send_with_retry(mittente, "👋 È stato un piacere! La sessione è completa. Ci sentiamo alla prossima!", ROLE)
                    mark_message_sent()
                    shutdown_requested = True
                
                # ── v2.1: AUTO-AVANZAMENTO dopo review del maestro ──
                if ROLE == "teacher" and APPRENTICESHIP_MODE:
                    review = extract_review_command(risposta)
                    if review:
                        old_obj_id = (load_apprendistato() or {}).get("current_objective")
                        state = process_apprenticeship_commands(risposta, MY_HANDLE)
                        new_obj_id = state.get("current_objective") if state else None
                        if new_obj_id and new_obj_id != old_obj_id:
                            logger.info(f"[AUTO-AVANZAMENTO] Obiettivo avanzato: {old_obj_id} -> {new_obj_id}")
                            next_obj = get_current_objective()
                            if next_obj:
                                followup = (
                                    f"Passiamo all'obiettivo successivo: **{next_obj['title']}**\n\n"
                                    f"{next_obj.get('master_prompt', next_obj.get('description', ''))}"
                                )
                                time.sleep(2)
                                send_with_retry(mittente, followup, ROLE)
                                log_conversation_entry("out", mittente, followup, {"auto_advance": True})
                                mark_message_sent()
                                logger.info(f"[AUTO-AVANZAMENTO] Inviato follow-up per {next_obj['id']}")
                                last_nudge_time = time.time()  # reset nudge timer
                # ── fine v2.1 ──
            except Exception as e:
                logger.error(f"[SEND] FALLITO: {e}")

        # ── v2.1: IDLE NUDGE ──
        if ROLE == "teacher" and APPRENTICESHIP_MODE and not messaggi_da_rispondere:
            idle_min = get_idle_minutes()
            if idle_min >= IDLE_NUDGE_MIN and (time.time() - last_nudge_time) > (IDLE_NUDGE_MIN * 60):
                state = load_apprendistato()
                stage = state.get("stage", "idle") if state else "idle"
                if stage not in ("completed", "verifica_finale"):
                    logger.info(f"[IDLE NUDGE] {idle_min:.1f} min di inattività, invio sollecito")
                    current_obj = get_next_objective()  # always returns first incomplete objective
                    obj_name = current_obj['title'] if current_obj else "corrente"
                    nudge = f"Come stai procedendo con l'obiettivo **{obj_name}**? Hai bisogno di aiuto o chiarimenti? Descrivimi cosa hai fatto finora."
                    try:
                        send_with_retry(PEER_HANDLE, nudge, ROLE)
                        log_conversation_entry("out", PEER_HANDLE, nudge, {"nudge": True, "idle_min": idle_min})
                        mark_message_sent()
                        logger.info(f"[IDLE NUDGE] Inviato -> {PEER_HANDLE}")
                        last_nudge_time = time.time()
                    except Exception as e:
                        logger.error(f"[IDLE NUDGE] Fallito: {e}")
        # ── fine v2.1 ──

        # v2.5.1: reaping zombie children (hermes chat subprocess)
        now = time.time()
        if now - last_reap_time > 60:  # ogni 60 secondi
            try:
                while True:
                    wpid, _ = os.waitpid(-1, os.WNOHANG)
                    if wpid == 0:
                        break
            except ChildProcessError:
                pass
            last_reap_time = now

        time.sleep(POLL_SECS)

    state = load_turn_state(); state["status"] = "completed"
    state["session_ended"] = datetime.now(timezone.utc).isoformat()
    save_turn_state(state)
    release_lock()
    logger.info("Terminato.")

if __name__ == "__main__":
    main()
