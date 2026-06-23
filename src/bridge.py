#!/usr/bin/env python3
"""bridge.py — IL PONTE tra socialforagent (relay) e Hermes (cervello).

Per il codice completo del bridge, vedi il pacchetto di test fornito separatamente.
Questo file e' un placeholder per la CI.
"""

def estrai_risposta(stdout: str) -> str:
    """Estrae solo la risposta di Hermes, scarta memoria/recall."""
    righe = stdout.splitlines()
    dentro = False
    risposta = []
    for r in righe:
        if "memory" in r or "recall" in r:
            continue
        if "Hermes" in r and "─" in r:
            dentro = True
            continue
        if "Resume this session" in r:
            break
        if dentro:
            pulita = r.strip().strip("│").strip()
            if pulita and not set(pulita) <= set("─┈ "):
                risposta.append(pulita)
    return " ".join(risposta).strip()

def messaggio_sicuro(testo: str) -> tuple:
    """Privacy filter placeholder."""
    return True, ""

if __name__ == "__main__":
    print("bridge placeholder — usa il pacchetto completo per il bridge reale")
