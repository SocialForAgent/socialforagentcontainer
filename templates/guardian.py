#!/usr/bin/env python3
"""
SFA Guardian — Health check + auto-repair per agenti SocialForAgent.
Da eseguire via cron ogni 3 minuti. Silenzioso se tutto OK.

Configurazione: $SFA_HOME/config.yaml
  guardian:
    rate_limit_sec: 3600      # max 1 notifica/ora
    cooldown_safeguard: 900   # 15 min tra un ripristino prompt e l'altro
    lock_ttl: 600             # 10 min TTL lock file
    max_consecutive_fixes: 3  # dopo 3 fix dello stesso tipo → alert
    max_fixes_per_day: 10     # dopo 10 fix in 24h → rallenta
"""

import subprocess, sys, json, time, os
from datetime import datetime, timezone
from pathlib import Path

# === CONFIG ===
SFA_HOME = Path(os.environ.get("SFA_HOME", "/opt/sfa-agent"))
CONFIG_FILE = SFA_HOME / "config.yaml"
STATE_DIR = SFA_HOME / "state"
ORCH_LOG = SFA_HOME / "orch" / "orchestrator.log"
ORCH_SCRIPT = SFA_HOME / "orch" / "orchestrator.py"
PROMPT_DIR = SFA_HOME / "prompt_default"
GUARDIAN_LOG = SFA_HOME / "guardian.log"
STATE_FILE = STATE_DIR / ".watchdog_state.json"
COOLDOWN_FILE = STATE_DIR / ".safeguard_cooldown"
RATE_LIMIT_FILE = STATE_DIR / ".guardian_last_alert"
HISTORY_FILE = STATE_DIR / ".guardian_history.json"

# Default config
DEFAULTS = {
    "rate_limit_sec": 3600,
    "cooldown_safeguard": 900,
    "lock_ttl": 600,
    "max_consecutive_fixes": 3,
    "max_fixes_per_day": 10,
}

def load_config():
    cfg = DEFAULTS.copy()
    if CONFIG_FILE.exists():
        try:
            import yaml
            data = yaml.safe_load(CONFIG_FILE.read_text())
            cfg.update(data.get("guardian", {}))
        except:
            pass
    return cfg

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    GUARDIAN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(GUARDIAN_LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except:
            pass
    return {"fixes": [], "consecutive": {}, "last_alert": {}}

def save_history(h):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(h, indent=2))

def should_alert(error_type: str, cfg: dict) -> bool:
    """Check rate limits before alerting."""
    now = time.time()
    h = load_history()
    
    # Rate limit: max 1 per ora
    last = h["last_alert"].get(error_type, 0)
    if now - last < cfg["rate_limit_sec"]:
        return False
    
    # Consecutive: dopo N fix dello stesso tipo, non fixare più
    h["consecutive"][error_type] = h["consecutive"].get(error_type, 0) + 1
    if h["consecutive"][error_type] > cfg["max_consecutive_fixes"]:
        log(f"LOOP DETECTED: {error_type} fixato {h['consecutive'][error_type]} volte consecutive. STOP.")
        return True
    
    # Daily cap
    recent = [f for f in h["fixes"] if now - f["ts"] < 86400]
    if len(recent) >= cfg["max_fixes_per_day"]:
        log(f"DAILY CAP REACHED: {len(recent)} fix in 24h. Rallento.")
        return True
    
    h["last_alert"][error_type] = now
    save_history(h)
    return True

def record_fix(error_type: str, detail: str, success: bool):
    h = load_history()
    h["fixes"].append({"ts": time.time(), "type": error_type, "detail": detail, "ok": success})
    # Reset consecutive if success
    if success:
        h["consecutive"][error_type] = 0
    save_history(h)

def check_gateway(cfg: dict) -> tuple:
    """Check 1: Hermes gateway is alive."""
    try:
        r = subprocess.run(["pgrep", "-f", "hermes gateway"], capture_output=True, text=True)
        if not r.stdout.strip():
            return ("gateway_down", "Gateway Hermes non trovato")
        return (None, None)
    except Exception as e:
        return ("gateway_check_error", str(e))

def check_orchestrator(cfg: dict) -> tuple:
    """Check 2: Orchestrator log is recent."""
    if not ORCH_LOG.exists():
        return ("orch_log_missing", f"Log {ORCH_LOG} non esiste")
    age = time.time() - ORCH_LOG.stat().st_mtime
    if age > 600:
        return ("orch_stale", f"Log orchestrator fermo da {int(age)}s")
    return (None, None)

def check_safeguard(cfg: dict) -> tuple:
    """Check 3: ElevenLabs prompt matches LATEST."""
    if COOLDOWN_FILE.exists():
        try:
            last = float(COOLDOWN_FILE.read_text().strip())
            if time.time() - last < cfg["cooldown_safeguard"]:
                return (None, None)  # In cooldown
        except:
            pass
    
    if not ORCH_SCRIPT.exists():
        return (None, None)  # Skip if no orchestrator
    
    try:
        r = subprocess.run(
            ["python3", str(ORCH_SCRIPT), "--safeguard"],
            capture_output=True, text=True, timeout=30,
            cwd=str(ORCH_SCRIPT.parent)
        )
        if r.returncode != 0:
            return ("safeguard_failed", r.stderr[:200] or r.stdout[:200])
        # Success: update cooldown
        COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOLDOWN_FILE.write_text(str(time.time()))
        return (None, None)
    except subprocess.TimeoutExpired:
        return ("safeguard_timeout", "Timeout dopo 30s")
    except Exception as e:
        return ("safeguard_error", str(e))

def check_lock(cfg: dict) -> tuple:
    """Check 4: Stale lock files."""
    lock_file = SFA_HOME / "orch" / ".orchestrator.lock"
    if lock_file.exists():
        age = time.time() - lock_file.stat().st_mtime
        if age > cfg["lock_ttl"]:
            try:
                lock_file.unlink()
                return ("stale_lock_cleared", f"Lock rimosso (vecchio di {int(age)}s)")
            except:
                return ("stale_lock_error", f"Impossibile rimuovere lock")
    return (None, None)

def check_zombies(cfg: dict) -> tuple:
    """Check 5: Zombie processes."""
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        zombies = [l for l in r.stdout.splitlines() if "defunct" in l or " Z " in l.split()[7:8]]
        if len(zombies) > 5:
            # Analizza il processo padre
            ppids = set()
            for z in zombies:
                parts = z.split()
                if len(parts) > 2:
                    ppids.add(parts[2])  # PPID
            if ppids:
                return ("zombie_processes", f"Trovati {len(zombies)} zombie. PPID: {','.join(ppids)}. Investiga i processi padre.")
        elif len(zombies) > 0:
            return ("zombie_warning", f"Trovati {len(zombies)} zombie (sotto soglia)")
        return (None, None)
    except:
        return (None, None)

def fix_gateway():
    """Riavvia il gateway Hermes."""
    try:
        subprocess.run(
            ["nohup", "/opt/hermes/.venv/bin/hermes", "gateway", "run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return True
    except:
        return False

def fix_orchestrator():
    """Riavvia l'orchestrator."""
    if not ORCH_SCRIPT.exists():
        return False
    try:
        subprocess.run(
            ["python3", str(ORCH_SCRIPT)],
            cwd=str(ORCH_SCRIPT.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return True
    except:
        return False

# === MAIN ===
if __name__ == "__main__":
    cfg = load_config()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    
    problems = []
    
    # Run all checks
    checks = [
        ("gateway", check_gateway),
        ("orchestrator", check_orchestrator),
        ("safeguard", check_safeguard),
        ("lock", check_lock),
        ("zombies", check_zombies),
    ]
    
    for name, check_fn in checks:
        error_type, detail = check_fn(cfg)
        if error_type:
            problems.append((name, error_type, detail))
    
    # Se tutto OK, scrivi state file e esci silenzioso
    if not problems:
        STATE_FILE.write_text(json.dumps({
            "last_ok": datetime.now(timezone.utc).isoformat(),
            "status": "healthy"
        }))
        sys.exit(0)
    
    # Ci sono problemi: diagnostica e ripara
    alerts = []
    for name, error_type, detail in problems:
        log(f"PROBLEM [{name}]: {error_type} — {detail}")
        
        if not should_alert(error_type, cfg):
            log(f"  RATE LIMITED: {error_type}")
            continue
        
        # Tentativo di fix automatico
        fixed = False
        if error_type == "gateway_down":
            fixed = fix_gateway()
            action = "Riavvio gateway"
        elif error_type == "orch_stale" or error_type == "orch_log_missing":
            fixed = fix_orchestrator()
            action = "Riavvio orchestrator"
        elif error_type == "stale_lock_cleared":
            fixed = True  # Già fixato nel check
            action = "Lock rimosso"
        else:
            action = "Richiede indagine manuale"
        
        record_fix(error_type, detail, fixed)
        
        if fixed:
            alerts.append(f"✅ [{name}] {action}: {detail}")
        else:
            alerts.append(f"❌ [{name}] NON RISOLTO: {detail}")
        
        log(f"  ACTION: {action} — {'OK' if fixed else 'FAILED'}")
    
    # Scrivi state file anche se ci sono problemi (ma segna warning)
    STATE_FILE.write_text(json.dumps({
        "last_ok": datetime.now(timezone.utc).isoformat(),
        "status": "warning",
        "problems": [{"name": n, "type": t, "detail": d} for n, t, d in problems]
    }))
    
    # Notifica su stdout (per cron delivery)
    print("\n".join(alerts))
    sys.exit(1 if any("NON RISOLTO" in a for a in alerts) else 0)
