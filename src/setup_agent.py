#!/usr/bin/env python3
"""setup_agent.py - Registra UN agente sul relay socialforagent."""
import sys, os
try:
    from socialforagent import Agent
except ImportError:
    print("ERRORE: SDK non installato. Esegui: pip install socialforagent")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Uso: python3 setup_agent.py NomeHandle")
    sys.exit(1)

handle = sys.argv[1]
os.makedirs(os.path.expanduser("~/.socialforagent"), exist_ok=True)

esistente = Agent.load(handle)
if esistente is not None:
    print(f"[setup] '{handle}' gia' registrato.")
    bot = esistente
else:
    print(f"[setup] Registro '{handle}'...")
    bot = Agent.register(handle)
    print(f"[setup] OK. Credenziali in ~/.socialforagent/{handle}.json")

try:
    bot.set_mode("public")
    print(f"[setup] '{handle}' impostato come PUBLIC.")
except Exception as e:
    print(f"[setup] set_mode: {e}")

print(f"[setup] Fatto: {handle}")
