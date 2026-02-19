# -*- coding: utf-8 -*-
"""
Gestionnaire centralisé des entrées/sorties.

En mode HEADLESS (Electron) :
  - Toutes les sorties sont des lignes JSON sur stdout
  - Chaque event contient protocol_version pour le versionning
  - Les entrées sont lues sur stdin (une ligne JSON par message)

En mode CLI (terminal) :
  - Les sorties sont formatées avec couleurs ANSI et émojis
  - Les entrées sont lues via input()

Importé par : TOUS les autres modules.
Dépend de : config (PROTOCOL_VERSION)
"""

from __future__ import annotations

import sys
import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict

# Import interne : version du protocole
from nexus_hive.config import PROTOCOL_VERSION


class OutputManager:
    """
    Gestionnaire centralisé des entrées/sorties.
    
    En mode HEADLESS (Electron) :
      - Toutes les sorties sont des lignes JSON sur stdout
      - Chaque event contient protocol_version pour le versionning
      - Les entrées sont lues sur stdin (une ligne JSON par message)
    
    En mode CLI (terminal) :
      - Les sorties sont formatées avec couleurs ANSI et émojis
      - Les entrées sont lues via input()
    """

    def __init__(self, headless: bool = False, debug: bool = False):
        self.headless = headless
        self._debug_mode = debug
        self._lock = threading.Lock()  # Thread-safe pour le streaming concurrent
        self._current_agent = None

        if headless and self._debug_mode:
            logging.basicConfig(
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s',
                stream=sys.stderr  # Logs debug sur stderr, events JSON sur stdout
            )

    def emit(self, event_type: str, data: Any = None, **kwargs) -> None:
        """
        Émet un event vers la sortie.
        
        En headless : écrit une ligne JSON sur stdout avec protocol_version.
        En CLI : formate avec couleurs ANSI.
        
        Args:
            event_type: Type d'event (stream, done, error, ready, etc.)
            data: Contenu principal de l'event
            **kwargs: Métadonnées supplémentaires (agent, stats, step, etc.)
        """
        payload = {
            "event": event_type,
            "timestamp": datetime.now().isoformat(),
            "protocol_version": PROTOCOL_VERSION,
        }

        if data is not None:
            payload["data"] = data

        payload.update(kwargs)

        with self._lock:
            if self.headless:
                json_output = json.dumps(payload, ensure_ascii=False)
                sys.stdout.write(json_output + "\n")
                sys.stdout.flush()
            else:
                self._format_output(event_type, payload)

    def _format_output(self, event_type: str, payload: Dict) -> None:
        """Formatage pour le mode CLI avec couleurs ANSI."""
        data = payload.get("data", "")

        colors = {
            "stream": "", "info": "\033[36m", "success": "\033[32m",
            "warning": "\033[33m", "error": "\033[31m",
            "agent": "\033[35m", "system": "\033[90m",
            "prompt": "\033[94m", "done": "\033[32m", "debug": "\033[90m",
            "progress": "\033[36m", "pipeline": "\033[35m",
        }
        reset = "\033[0m"
        color = colors.get(event_type, "")

        if event_type == "stream":
            sys.stdout.write(str(data))
            sys.stdout.flush()
        elif event_type == "prompt":
            print(f"{color}❯ {data}{reset}")
        elif event_type == "info":
            print(f"{color}ℹ {data}{reset}")
        elif event_type == "success":
            print(f"{color}✓ {data}{reset}")
        elif event_type == "warning":
            print(f"{color}⚠ {data}{reset}")
        elif event_type == "error":
            print(f"{color}✗ {data}{reset}")
        elif event_type == "agent":
            agent = payload.get("agent", "unknown")
            print(f"{color}🤖 [{agent}]{reset} {data}")
        elif event_type == "pipeline":
            step = payload.get("step", "")
            print(f"{color}🔄 [PIPELINE]{reset} {step}: {data}")
        elif event_type == "progress":
            pct = payload.get("percentage", 0)
            print(f"{color}📊 {data} [{pct}%]{reset}")
        elif event_type == "done":
            stats = payload.get("stats", {})
            stats_str = " | ".join(f"{k}={v}" for k, v in stats.items())
            print(f"{color}✓ Terminé{reset} ({stats_str})")
        else:
            print(f"{data}")

    def read_input(self, prompt_text: str = "") -> str:
        """Lit une entrée utilisateur (stdin en headless, input() en CLI)."""
        if self.headless:
            self.emit("prompt", data=prompt_text)
            try:
                line = sys.stdin.readline()
                return line.strip() if line else ""
            except EOFError:
                return ""
        else:
            return input(f"❯ {prompt_text}")

    # ═══════════════════════════════════════════════════════════════════
    # Alias pratiques
    # ═══════════════════════════════════════════════════════════════════

    def stream(self, content: str, agent: str = None, **kwargs) -> None:
        self._current_agent = agent
        self.emit("stream", data=content, agent=agent, **kwargs)

    def info(self, message: str) -> None:
        self.emit("info", data=message)

    def success(self, message: str) -> None:
        self.emit("success", data=message)

    def warning(self, message: str) -> None:
        self.emit("warning", data=message)

    def error(self, message: str, **kwargs) -> None:
        self.emit("error", data=message, **kwargs)

    def agent_msg(self, agent: str, message: str) -> None:
        self.emit("agent", data=message, agent=agent)

    def done(self, stats: Dict = None) -> None:
        self.emit("done", stats=stats or {})

    def debug(self, message: str) -> None:
        self.emit("debug", data=message)

    def progress(self, current: int, total: int, message: str = "") -> None:
        percentage = round((current / total) * 100) if total > 0 else 0
        self.emit("progress", data={
            "current": current, "total": total,
            "percentage": percentage, "message": message
        })

    def pipeline_event(self, event_type: str, pipeline: str, **kwargs) -> None:
        self.emit(f"pipeline_{event_type}", data=pipeline, **kwargs)