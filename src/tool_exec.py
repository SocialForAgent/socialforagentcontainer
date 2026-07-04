#!/usr/bin/env python3
"""
tool_exec.py — Bridge tool execution module.
Enables [TERMINAL:], [READ:], [WRITE:] commands in bridge messages.
Agents can actually modify files, not just talk about them.
"""

import re
import subprocess
import pathlib
import shlex
import tempfile
import shutil

TOOL_TIMEOUT = 60  # max seconds per tool command


def get_tool_preamble() -> str:
    """Returns the tool usage preamble injected into Hermes system prompt."""
    return (
        "STRUMENTI DISPONIBILI (puoi usarli nelle tue risposte):\n"
        "- [TERMINAL: comando]  → esegue un comando shell e mostra l'output\n"
        "- [READ: percorso]     → legge un file e ne mostra il contenuto\n"
        "- [WRITE: percorso]    → scrive/crea un file col contenuto specificato\n"
        "  contenuto\n"
        "  [/WRITE]\n\n"
        "USA GLI STRUMENTI PER MODIFICARE REALMENTE I FILE, non solo descrivere cosa fare.\n"
        "Quando modifichi un file, includi SEMPRE [TERMINAL:] o [WRITE:] nel messaggio.\n\n"
    )


def process_tool_commands(text: str, workdir: str | None = None) -> tuple[str, bool]:
    """
    Extract and execute tool commands from text.
    Returns (modified_text, had_commands).
    
    Commands:
      [TERMINAL: cmd]           -> executes cmd, replaces with [TOOL: output]
      [READ: path[:line]]       -> reads file, replaces with [TOOL: content]
      [WRITE: path]             -> writes file content until [/WRITE]
        content
        [/WRITE]
    """
    had_commands = False
    modified = text
    
    # ── WRITE blocks (must process first — multiline) ──
    def _process_write(match):
        nonlocal had_commands
        path = match.group(1).strip()
        content = match.group(2)
        result = tool_write_file(path, content)
        had_commands = True
        return f"\n[TOOL: WRITE {path}]\n{result}\n[/TOOL]\n"
    
    modified = re.sub(
        r'\[WRITE:\s*([^\]]+)\]\s*\n(.*?)\[/WRITE\]',
        _process_write,
        modified,
        flags=re.DOTALL
    )
    
    # ── TERMINAL blocks ──
    def _process_terminal(match):
        nonlocal had_commands
        cmd = match.group(1).strip()
        result = tool_execute_terminal(cmd, workdir)
        had_commands = True
        return f"\n[TOOL: TERMINAL {cmd[:80]}]\n{result}\n[/TOOL]\n"
    
    modified = re.sub(
        r'\[TERMINAL:\s*([^\]]+)\]',
        _process_terminal,
        modified
    )
    
    # ── READ blocks ──
    def _process_read(match):
        nonlocal had_commands
        ref = match.group(1).strip()
        result = tool_read_file(ref)
        had_commands = True
        return f"\n[TOOL: READ {ref}]\n{result}\n[/TOOL]\n"
    
    modified = re.sub(
        r'\[READ:\s*([^\]]+)\]',
        _process_read,
        modified
    )
    
    return modified, had_commands


def tool_execute_terminal(cmd: str, workdir: str | None = None) -> str:
    """Execute a shell command and return the output."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
            cwd=workdir,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr] {err}")
        if result.returncode != 0:
            parts.append(f"[exit: {result.returncode}]")
        return "\n".join(parts) if parts else f"[exit: {result.returncode}]"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT dopo {TOOL_TIMEOUT}s]"
    except Exception as e:
        return f"[ERROR: {e}]"


def tool_read_file(ref: str) -> str:
    """
    Read a file. Supports:
      [READ: /path/to/file]        → whole file (max 200 lines)
      [READ: /path/to/file:42]     → line 42 with context
      [READ: /path/to/file:10-30]  → lines 10-30
    """
    parts = ref.rsplit(":", 1)
    path_str = parts[0].strip()
    line_spec = parts[1].strip() if len(parts) == 2 else None
    
    path = pathlib.Path(path_str)
    if not path.exists():
        return f"[ERROR: file non trovato: {path}]"
    if not path.is_file():
        return f"[ERROR: non è un file: {path}]"
    
    try:
        content = path.read_text()
        lines = content.split("\n")
        
        if line_spec:
            if "-" in line_spec:
                start, end = line_spec.split("-", 1)
                start = int(start) - 1
                end = int(end)
            else:
                line_num = int(line_spec) - 1
                start = max(0, line_num - 3)
                end = min(len(lines), line_num + 4)
            
            start = max(0, start)
            end = min(len(lines), end)
            
            result = []
            for i in range(start, end):
                marker = "→" if (line_spec and "-" not in line_spec and i == int(line_spec) - 1) else " "
                result.append(f"{i+1:4d} {marker} {lines[i]}")
            return "\n".join(result)
        else:
            # Whole file, max 200 lines
            if len(lines) > 200:
                return "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(lines[:200])) + f"\n... ({len(lines) - 200} altre linee)"
            return "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(lines))
    except Exception as e:
        return f"[ERROR: {e}]"


def tool_write_file(path_str: str, content: str) -> str:
    """Write content to a file. Creates .bak backup if file exists."""
    path = pathlib.Path(path_str)
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Backup existing file
        if path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            backup_msg = f" (backup: {bak.name})"
        else:
            backup_msg = " (nuovo file)"
        
        path.write_text(content)
        size = path.stat().st_size
        lines = content.count("\n") + 1
        return f"OK: {path} scritto ({lines} righe, {size} byte){backup_msg}"
    except Exception as e:
        return f"[ERROR: {e}]"
