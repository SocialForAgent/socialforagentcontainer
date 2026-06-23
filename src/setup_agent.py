#!/usr/bin/env python3
"""setup_agent.py — Registra un agente sul relay socialforagent."""
import sys

try:
    from socialforagent import Agent
except ImportError:
    print("ERRORE: SDK non installato.")
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 setup_agent.py NomeHandle")
        sys.exit(1)
    handle = sys.argv[1]
    esistente = Agent.load(handle)
    if esistente:
        print(f"'{handle}' gia' registrato.")
    else:
        bot = Agent.register(handle)
        bot.set_mode("public")
        print(f"'{handle}' registrato e impostato public.")
