# Bridge v2.4 Tool Execution Module
# Adds [TERMINAL: cmd] and [READ: path] command execution to the bridge.
# This allows apprentice agents to actually modify files instead of just talking about it.

import re
import subprocess
import shlex

TERMINAL_PATTERN = re.compile(r'\[TERMINAL:\s*(.+?)\]', re.DOTALL)
READ_PATTERN = re.compile(r'\[READ:\s*(.+?)\]', re.DOTALL)
WRITE_PATTERN = re.compile(r'\[WRITE:\s*(.+?)\]\s*\n(.*?)\n\s*\[/WRITE\]', re.DOTALL)

# Maximum output length for terminal commands (to avoid flooding context)
MAX_TERMINAL_OUTPUT = 2000
# Command timeout
CMD_TIMEOUT = 30


def execute_terminal(cmd: str, workdir: str = None) -> str:
    """Execute a shell command safely. Returns stdout or error message."""
    try:
        res = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=CMD_TIMEOUT, cwd=workdir
        )
        output = res.stdout.strip() or res.stderr.strip()
        if res.returncode != 0:
            prefix = f"[exit {res.returncode}] "
        else:
            prefix = "[OK] "
        if len(output) > MAX_TERMINAL_OUTPUT:
            output = output[:MAX_TERMINAL_OUTPUT] + f"\n... (truncated, {len(output)} bytes total)"
        return prefix + output
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT dopo {CMD_TIMEOUT}s]"
    except Exception as e:
        return f"[ERRORE: {e}]"


def read_file(path: str) -> str:
    """Read a file and return its content (limited)."""
    try:
        with open(path, 'r') as f:
            content = f.read(2000)
        total_lines = content.count('\n') + 1
        if len(content) >= 2000:
            return f"[FILE: {path} ({total_lines}+ lines, first 2000 chars)]\n{content}\n..."
        return f"[FILE: {path} ({total_lines} lines)]\n{content}"
    except FileNotFoundError:
        return f"[FILE NOT FOUND: {path}]"
    except PermissionError:
        return f"[PERMISSION DENIED: {path}]"
    except Exception as e:
        return f"[ERROR reading {path}: {e}]"


def write_file_content(path: str, content: str) -> str:
    """Write content to a file. Creates backup if file exists."""
    import os
    try:
        # Backup if exists
        if os.path.exists(path):
            backup = path + '.bak'
            with open(path, 'r') as src:
                with open(backup, 'w') as dst:
                    dst.write(src.read())
            backup_msg = f" (backup: {backup})"
        else:
            backup_msg = ""
        
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return f"[WRITTEN: {path}{backup_msg}, {len(content)} bytes]"
    except Exception as e:
        return f"[WRITE ERROR {path}: {e}]"


def process_tool_commands(text: str, workdir: str = None) -> str:
    """
    Process [TERMINAL:], [READ:], and [WRITE:] commands in text.
    Returns (modified_text, has_commands).
    """
    has_commands = False
    results = []
    
    # Process WRITE first (may contain TERMINAL inside)
    def replace_write(match):
        nonlocal has_commands
        has_commands = True
        path = match.group(1).strip()
        content = match.group(2)
        result = write_file_content(path, content)
        return f"\n[TOOL: {result}]\n"
    
    text = WRITE_PATTERN.sub(replace_write, text)
    
    # Process TERMINAL
    def replace_terminal(match):
        nonlocal has_commands
        has_commands = True
        cmd = match.group(1).strip()
        result = execute_terminal(cmd, workdir)
        return f"\n[TOOL: {result}]\n"
    
    text = TERMINAL_PATTERN.sub(replace_terminal, text)
    
    # Process READ
    def replace_read(match):
        nonlocal has_commands
        has_commands = True
        path = match.group(1).strip()
        result = read_file(path)
        return f"\n[TOOL: {result}]\n"
    
    text = READ_PATTERN.sub(replace_read, text)
    
    return text, has_commands


# ── Integration points for bridge.py main() loop ──

# INTEGRATION 1: Before calling Hermes, process tool commands in incoming message
# Insert this in the message processing loop, right before sveglia_hermes():
#
#   testo_con_tool, had_tools = process_tool_commands(testo)
#   if had_tools:
#       logger.info(f"[TOOL] Comandi eseguiti nel messaggio da {mittente}")
#       testo = testo_con_tool  # Pass processed text to Hermes

# INTEGRATION 2: After Hermes responds, scan for tool commands
# Insert this after getting risposta from sveglia_hermes():
#
#   risposta_con_tool, had_tools = process_tool_commands(risposta)
#   if had_tools:
#       logger.info(f"[TOOL] Comandi eseguiti nella risposta")
#       risposta = risposta_con_tool

def get_tool_preamble():
    """Return tool usage instructions for Hermes preamble."""
    return (
        "STRUMENTI DISPONIBILI (includi questi comandi nei tuoi messaggi):\n"
        "- [TERMINAL: comando] — esegue un comando shell e mostra l'output\n"
        "- [READ: /percorso/file] — legge un file e ne mostra il contenuto\n"
        "- [WRITE: /percorso/file]\\ncontenuto\\n[/WRITE] — scrive un file (backup automatico)\n\n"
        "REGOLA: quando il maestro ti chiede di MODIFICARE un file, includi SEMPRE\n"
        "il comando [TERMINAL: ...] o [WRITE: ...] nella tua risposta per eseguirlo.\n\n"
    )

# INTEGRATION 3: File sharing auto-apply
# When a shared file appears (from [FILE:] in bridge), add prompt:
# 
#   if shared_file_just_received:
#       prompt_prefix = f"[TOOL] Il maestro ha condiviso '{filename}' in shared/.\n"
#       prompt_prefix += f"Per applicarlo, includi nella risposta:\n"
#       prompt_prefix += f"[TERMINAL: cp shared/{filename} /percorso/destinazione]\n\n"
#       testo = prompt_prefix + testo
