#!/usr/bin/env python3
"""
SFA Bridge Guardian — keeps the bridge alive autonomously.
No user intervention needed.

Checks every 30 seconds if bridge.py is running.
If not, restarts it. Logs to guardian.log.
"""

import os
import sys
import time
import subprocess
import pathlib

GUARDIAN_VERSION = "1.0.0"
CHECK_INTERVAL = 30  # seconds
MAX_RESTARTS_PER_HOUR = 10  # safety: don't restart more than 10 times/hour

# Detect the bridge directory (guardian lives in the same dir as bridge.py)
BRIDGE_DIR = pathlib.Path(__file__).parent.resolve()
LOG_FILE = BRIDGE_DIR / "guardian.log"
CONFIG_FILE = BRIDGE_DIR / "config.json"
BRIDGE_SCRIPT = BRIDGE_DIR / "bridge.py"
RESTART_COUNT_FILE = BRIDGE_DIR / ".guardian_restart_count"


def log(msg: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[{timestamp}] {msg}"
    print(line, file=sys.stderr)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def find_hermes_python() -> str | None:
    """Find a working python3 with the bridge dependencies."""
    candidates = [
        "/opt/hermes/.venv/bin/python3",
        sys.executable,
    ]
    for py in candidates:
        if pathlib.Path(py).exists():
            return py
    return "python3"


def is_bridge_running() -> bool:
    """Check if a bridge.py process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "bridge.py"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_restart_count() -> int:
    """Read restart counter, reset if older than 1 hour."""
    try:
        text = RESTART_COUNT_FILE.read_text().strip()
        parts = text.split(":")
        if len(parts) == 2:
            hour_mark = int(parts[0])
            count = int(parts[1])
            current_hour = int(time.time() / 3600)
            if current_hour == hour_mark:
                return count
    except (ValueError, OSError, FileNotFoundError):
        pass
    return 0


def increment_restart_count() -> int:
    """Increment and persist restart counter."""
    current_hour = int(time.time() / 3600)
    count = get_restart_count()
    if int(time.time() / 3600) != current_hour:
        count = 0
    count += 1
    RESTART_COUNT_FILE.write_text(f"{current_hour}:{count}")
    return count


def restart_bridge() -> bool:
    """Restart the bridge process. Returns True on success."""
    python = find_hermes_python()
    log(f"Restarting bridge with {python}...")

    try:
        subprocess.Popen(
            [python, str(BRIDGE_SCRIPT), str(CONFIG_FILE)],
            cwd=str(BRIDGE_DIR),
            stdout=open(BRIDGE_DIR / "bridge.log", "a"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/data")},
        )
        time.sleep(3)
        if is_bridge_running():
            log("Bridge restarted successfully")
            return True
        else:
            log("ERROR: Bridge did not start after restart attempt")
            return False
    except Exception as e:
        log(f"ERROR: Failed to restart bridge: {e}")
        return False


def main() -> None:
    log(f"SFA Guardian v{GUARDIAN_VERSION} starting for {BRIDGE_DIR.name}")
    log(f"Watchdog: checking every {CHECK_INTERVAL}s, max {MAX_RESTARTS_PER_HOUR} restarts/hour")

    last_log_time = 0

    while True:
        try:
            if not is_bridge_running():
                restart_count = get_restart_count()
                if restart_count >= MAX_RESTARTS_PER_HOUR:
                    log(f"WARNING: {restart_count} restarts in this hour, limit reached. Waiting...")
                    time.sleep(CHECK_INTERVAL)
                    continue

                log(f"Bridge not running (attempt {restart_count + 1}/{MAX_RESTARTS_PER_HOUR})")
                success = restart_bridge()
                if success:
                    increment_restart_count()

            # Heartbeat log every 10 minutes
            now = time.time()
            if now - last_log_time > 600:
                log("Heartbeat: bridge is running, guardian OK")
                last_log_time = now

        except Exception as e:
            log(f"Guardian error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
