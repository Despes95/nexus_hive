#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  NEXUS HIVE v7.1 - Système Multi-Agents IA Local                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                            ║
║  ARCHITECTURE :                                                            ║
║  ┌─────────┐    stdin (JSON)     ┌──────────┐    HTTP     ┌──────────┐    ║
║  │ Electron │ ──────────────────→│  Python   │ ─────────→ │  Ollama  │    ║
║  │  (UI)    │ ←──────────────────│ (ce file) │ ←───────── │  (LLMs)  │    ║
║  └─────────┘   stdout (JSON)     └──────────┘  streaming  └──────────┘    ║
║                                                                            ║
║  PROTOCOLE STDIN (Electron → Python) :                                     ║
║    Chaque ligne = 1 message JSON :                                         ║
║    {"type": "input",   "text": "..."}              → message utilisateur   ║
║    {"type": "command", "command": "/reset"}         → commande système     ║
║    {"type": "input_with_files", "text":"...",        → message + fichiers  ║
║     "files": [{"name":"...", "base64":"...", ...}]}                        ║
║    Texte brut (non-JSON) → traité comme input texte (rétrocompatibilité)   ║
║                                                                            ║
║  PROTOCOLE STDOUT (Python → Electron) :                                    ║
║    Chaque ligne = 1 event JSON :                                           ║
║    {"event": "stream", "data": "...", "protocol_version": 1, ...}          ║
║    Voir EVENTS_SPEC plus bas pour la liste complète.                       ║
║                                                                            ║
║  AGENTS : chef, coder, coder2, pentester, analyst, analyst2,               ║
║           creative, vision, searcher, debugger, documenter, teacher         ║
║                                                                            ║
║  PIPELINES : code_review, code_secure, story_polish, exploit_code,         ║
║              analyze_code, learning_guide, debug_fix, full_stack,           ║
║              doc_code, refactor, phoenix, shark_tank                        ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# =============================================================================
# SPÉCIFICATION DES EVENTS (Python → Electron)
# =============================================================================
# Chaque event émis sur stdout est un objet JSON avec ces champs :
#
# CHAMPS COMMUNS (présents dans TOUS les events) :
#   - event            (str)  : Type de l'event (voir liste ci-dessous)
#   - timestamp        (str)  : ISO 8601, ex: "2025-06-15T14:30:00.123456"
#   - protocol_version (int)  : Version du protocole, actuellement 1
#
# CHAMP DATA (contenu principal, varie selon l'event) :
#   - data             (any)  : Contenu de l'event (str, dict, list, bool...)
#
# CHAMPS META (optionnels, spécifiques à certains events) :
#   - agent            (str)  : Nom de l'agent concerné
#   - stats            (dict) : Statistiques (tok_s, tokens, total_s...)
#   - step             (int)  : Numéro d'étape (pipelines)
#   - duration         (float): Durée en secondes
#   - pipeline         (str)  : Nom du pipeline
#
# LISTE DES EVENTS :
# ┌────────────────────┬───────────────────────────────────────────────────────┐
# │ Event              │ Description                                          │
# ├────────────────────┼───────────────────────────────────────────────────────┤
# │ ready              │ Système initialisé. data={version, models, pipelines}│
# │ prompt             │ Python attend un input. data=texte du prompt         │
# │ terminated         │ Arrêt propre. data=True                              │
# │ user_message       │ Echo du message utilisateur. data=texte              │
# │ stream             │ Chunk de réponse (streaming). data=texte, agent=nom  │
# │ routing            │ Routing en cours. data=nom_agent                     │
# │ routed             │ Agent sélectionné. data=nom_agent                    │
# │ pipeline_start     │ Pipeline démarré. data=nom_pipeline                  │
# │ pipeline_step      │ Étape du pipeline. data=nom, step=N, agent=nom      │
# │ pipeline_done      │ Pipeline terminé. data=nom, duration=secondes        │
# │ progress           │ Progression. data={current, total, percentage, msg}  │
# │ done               │ Génération terminée. stats={tok_s, tokens, total_s}  │
# │ gpu_info           │ Infos GPU. data={gpu_name, vram_used/total, temp_c}  │
# │ sources            │ Sources web. data=[{title, url}, ...]                │
# │ info/success/      │ Log système. data=message texte                      │
# │ warning/error/debug│                                                      │
# │ export             │ Stats exportées. data=[...stats...]                  │
# └────────────────────┴───────────────────────────────────────────────────────┘
#

from __future__ import annotations

import os
import sys
import json
import time
import hashlib
import logging
import re
import subprocess
import base64
import threading
from datetime import datetime
from pathlib import Path
from collections import OrderedDict
from typing import Optional, List, Dict, Any, Union, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum

# Version du protocole de communication Electron ↔ Python
# Incrémentez ce numéro quand le format des events change de façon incompatible
PROTOCOL_VERSION = 1

# =============================================================================
# DÉPENDANCES EXTERNES
# =============================================================================
# requests     : Appels HTTP vers Ollama (OBLIGATOIRE)
# duckduckgo   : Recherche web (optionnel, agent searcher)
# trafilatura  : Extraction contenu web propre (optionnel, meilleur que BS4)
# beautifulsoup: Fallback extraction web (optionnel)
# =============================================================================

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install: pip install requests")
    sys.exit(1)

try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    try:
        from ddgs import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        pass

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False


# =============================================================================
# 🎯 OUTPUT MANAGER - SYSTÈME DE SORTIE CENTRALISÉE
# =============================================================================

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
            "protocol_version": PROTOCOL_VERSION,  # ← Toujours inclus
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
        """Formatage pour le mode CLI avec couleurs"""
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
        """Lit une entrée utilisateur"""
        if self.headless:
            self.emit("prompt", data=prompt_text)
            try:
                line = sys.stdin.readline()
                return line.strip() if line else ""
            except EOFError:
                return ""
        else:
            return input(f"❯ {prompt_text}")

    # Alias pratiques
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
        """Émet un message de débogage"""
        self.emit("debug", data=message)

    def progress(self, current: int, total: int, message: str = "") -> None:
        percentage = round((current / total) * 100) if total > 0 else 0
        self.emit("progress", data={
            "current": current, "total": total,
            "percentage": percentage, "message": message
        })

    def pipeline_event(self, event_type: str, pipeline: str, **kwargs) -> None:
        self.emit(f"pipeline_{event_type}", data=pipeline, **kwargs)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    """Configuration centralisée"""
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    code_dir: str = "generated_code"
    memory_file: str = "memory.json"
    favorites_file: str = "favorites.json"
    stats_file: str = "session_stats.json"

    ollama_url: str = "http://127.0.0.1:11434"
    
    # URL du serveur vision (llama-server avec modèle VL)
    # Par défaut : llama-server sur le port 8081 (API OpenAI-compatible)
    # Lancer avec : llama-server -m <model.gguf> --mmproj <mmproj.gguf> --port 8081
    # Si vide ("") : utilise Ollama normalement pour la vision
    vision_url: str = "http://127.0.0.1:8081"
    
    timeout: int = 600
    max_tokens: int = 2048
    max_tokens_pipeline: int = 4096
    max_tokens_creative: int = 1024
    max_tokens_learning: int = 3072
    max_tokens_web_fetch: int = 5120

    # Mapping agent → modèle Ollama
    # IMPORTANT : ces noms doivent correspondre exactement aux modèles installés
    # Vérifiez avec : ollama list
    models: Dict[str, str] = field(default_factory=lambda: {
        "chef": "gemma3-chef:latest",          # Routeur intelligent (fine-tuné)
        "coder": "qwencoder:latest",           # Dev expert, tâches complexes
        "coder2": "qwen25-coder:latest",       # Dev full-stack, tâches simples
        "pentester": "magnum-pentest:latest",  # Sécurité offensive & audits
        "analyst": "qwen3-14b:latest",         # Analyse approfondie (14B = complexe)
        "analyst2": "qwen3-8b:latest",         # Synthèse rapide (8B = léger)
        "creative": "qwen3-14b:latest",        # Écriture créative
        "vision": "qwen3-vision:latest",       # Analyse d'images (multimodal)
        "searcher": "qwen2.5:7b-instruct",    # Recherche web (rapide, instruct)
        "searcher_synth": "qwen3-8b:latest",   # Synthèse avancée des résultats web
        "debugger": "qwen3-14b:latest",        # Diagnostic d'erreurs
        "documenter": "qwen3-14b:latest",      # Génération de documentation
        "teacher": "qwen3-14b:latest",         # Enseignement pédagogique
    })

    # Pipelines : enchaînement de 2 agents (agent1 → agent2)
    # L'agent1 produit une analyse/contenu, l'agent2 l'améliore/complète
    # Usage : /pipeline <nom> <prompt>
    pipelines: Dict[str, Tuple[str, str]] = field(default_factory=lambda: {
        "code_review": ("analyst", "coder"),       # Analyse code → améliorations
        "code_secure": ("coder", "pentester"),     # Écrit code → audit sécurité
        "story_polish": ("creative", "creative"),  # Écrit histoire → peaufine
        "exploit_code": ("analyst", "pentester"),  # Analyse système → exploits/PoC
        "analyze_code": ("analyst", "analyst2"),   # Analyse profonde → synthèse
        "learning_guide": ("coder", "teacher"),    # Contenu technique → parcours pédago
        "debug_fix": ("debugger", "coder"),        # Diagnostic erreur → code corrigé
        "full_stack": ("coder", "coder2"),         # Backend → frontend
        "doc_code": ("coder", "documenter"),       # Code → documentation complète
        "refactor": ("analyst", "coder"),          # Analyse archi → code refactorisé
        "phoenix": ("analyst", "coder"),           # Refactoring intelligent (modernise)
        "shark_tank": ("creative", "analyst"),     # Pitch idée → débat & verdict
    })

    agent_prompts: Dict[str, str] = field(default_factory=lambda: {
        "chef": "Tu es un routeur intelligent. Analyse la demande et routing vers l'agent approprié.",
        "coder": "Tu es un expert développeur. Écris du code propre, sécurisé, bien documenté et fonctionnel. Réponds en français.",
        "coder2": "Tu es un développeur full-stack. Crée des applications complètes avec frontend et backend. Réponds en français.",
        "pentester": "Tu es un expert en sécurité informatique. Analyse le code pour détecter les vulnérabilités. Réponds en français.",
        "analyst": "Tu es un analyste technique. Analyse, compare et explique en profondeur avec raisonnement structuré. Réponds en français.",
        "analyst2": "Tu es un assistant analytique. Résume et synthétise les informations clairement. Réponds en français.",
        "creative": "Tu es un auteur créatif. Écris des histoires engageantes, originales et captivantes. Réponds en français.",
        "vision": "Tu es un expert en vision par ordinateur. Analyse les images en détail et décrit ce que tu vois. Réponds en français.",
        "searcher": "Tu es un expert en recherche. Trouve et synthétise les informations pertinentes avec sources. Réponds en français.",
        "debugger": "Tu es un expert en debugging. Analyse les erreurs, tracebacks et propose des solutions. Réponds en français.",
        "documenter": "Tu es un expert en documentation. Crée une documentation complète et claire. Réponds en français.",
        "teacher": "Tu es un enseignant expert. Explique de manière pédagogique avec exemples. Réponds en français.",
    })

    loop_detect_window: int = 500
    loop_detect_threshold: int = 2
    route_cache_max: int = 100
    memory_summary_threshold: int = 20
    no_memory: bool = False
    conversation_mode: bool = False
    stat_only: bool = False
    headless: bool = False
    debug: bool = False

    def __post_init__(self):
        os.makedirs(self.code_dir, exist_ok=True)

    @classmethod
    def from_args(cls, args: List[str]) -> Config:
        config = cls()
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--no-memory":
                config.no_memory = True
            elif arg == "--conversation-mode":
                config.conversation_mode = True
            elif arg == "--stat-only":
                config.stat_only = True
            elif arg == "--headless":
                config.headless = True
            elif arg == "--debug":
                config.debug = True
            elif arg == "--ollama" and i + 1 < len(args):
                config.ollama_url = args[i + 1]
                i += 1
            elif arg == "--vision-url" and i + 1 < len(args):
                # URL du serveur vision séparé (llama-server)
                # Passer "" pour désactiver et utiliser Ollama
                config.vision_url = args[i + 1]
                i += 1
            i += 1
        return config


# =============================================================================
# EXCEPTIONS
# =============================================================================

class AgentError(Exception): pass
class OllamaConnectionError(AgentError): pass
class OllamaTimeoutError(AgentError): pass
class ModelNotFoundError(AgentError): pass
class RoutingError(AgentError): pass
class LoopDetectedError(AgentError): pass


# =============================================================================
# CACHE LRU
# =============================================================================

class LRUCache:
    def __init__(self, max_size: int = 100):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def set(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = value

    def clear(self) -> None:
        self._cache.clear()

    def __contains__(self, key: str) -> bool:
        return key in self._cache


# =============================================================================
# DÉTECTEUR DE BOUCLE
# =============================================================================

class LoopDetector:
    def __init__(self, config: Config):
        self.config = config
        self.window = config.loop_detect_window
        self.threshold = config.loop_detect_threshold
        self._last_outputs: List[str] = []

    def detect(self, text: str) -> bool:
        if len(text) < self.window * 3:
            return False

        if self._detect_exact_repetition(text):
            return True
        if self._detect_similar_to_history(text):
            return True

        self._update_history(text)
        return False

    def _detect_exact_repetition(self, text: str) -> bool:
        tail = text[-self.window:]
        before = text[:-self.window]
        # Augmenter le seuil à 3 répétitions (au lieu de 2)
        return before.count(tail) >= 3

    def _detect_similar_to_history(self, text: str) -> bool:
        if not self._last_outputs:
            return False

        normalized = self._normalize_text(text)
        for old_output in self._last_outputs[-3:]:
            old_norm = self._normalize_text(old_output)
            similarity = self._calculate_similarity(normalized, old_norm)
            # Seuil augmenté à 95% pour éviter faux positifs sur contenu structuré
            if similarity > 0.95:
                return True
        return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    @staticmethod
    def _calculate_similarity(text1: str, text2: str) -> float:
        if not text1 or not text2:
            return 0.0
        set1, set2 = set(text1), set(text2)
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2) if set1 | set2 else 0.0

    def _update_history(self, text: str) -> None:
        self._last_outputs.append(text)
        if len(self._last_outputs) > 5:
            self._last_outputs = self._last_outputs[-5:]

    def reset(self) -> None:
        self._last_outputs.clear()


# =============================================================================
# MÉMOIRE
# =============================================================================

@dataclass
class MemoryEntry:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    agent: Optional[str] = None
    model: Optional[str] = None
    elapsed: Optional[float] = None
    pipeline: Optional[str] = None
    step: Optional[int] = None
    intermediate: bool = False


class MemoryStore:
    def __init__(self, config: Config, io: OutputManager):
        self.config = config
        self.io = io
        self.memory: Dict[str, Any] = {"history": [], "summary": ""}
        self._load()

    def _load(self) -> None:
        if self.config.no_memory:
            return
        path = os.path.join(self.config.base_dir, self.config.memory_file)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.memory = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                self.io.debug(f"Erreur chargement mémoire: {e}")
                self.memory = {"history": [], "summary": ""}

    def save(self) -> None:
        if self.config.no_memory:
            return
        path = os.path.join(self.config.base_dir, self.config.memory_file)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
        except IOError as e:
            self.io.error(f"Erreur sauvegarde mémoire: {e}")

    def add_user_message(self, content: str) -> None:
        self.memory["history"].append({
            "role": "user", "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_assistant_message(self, content: str, agent: str, model: str,
                             elapsed: float = 0, pipeline: str = None,
                             step: int = None, intermediate: bool = False) -> None:
        entry = {
            "role": "assistant", "agent": agent, "model": model,
            "content": content, "elapsed": elapsed,
            "timestamp": datetime.now().isoformat(),
            "intermediate": intermediate
        }
        if pipeline:
            entry["pipeline"] = pipeline
        if step:
            entry["step"] = step
        self.memory["history"].append(entry)

    def set_summary(self, summary: str) -> None:
        self.memory["summary"] = summary
        self.memory["history"] = self.memory["history"][-10:]

    def get_context_messages(self, max_history: int = 10) -> List[Dict[str, str]]:
        msgs = []
        if self.memory.get("summary"):
            msgs.append({"role": "system",
                        "content": "Résumé précédent: " + self.memory["summary"]})
        for m in self.memory["history"][-max_history:]:
            if m.get("intermediate", False):
                continue
            role = m["role"] if m["role"] in ["user", "assistant"] else "assistant"
            msgs.append({"role": role, "content": m["content"]})
        return msgs[-8:]

    def search(self, keyword: str) -> List[Tuple[int, Dict[str, Any]]]:
        keyword_norm = LoopDetector._normalize_text(keyword)
        matches = []
        for i, m in enumerate(self.memory["history"]):
            content_norm = LoopDetector._normalize_text(m.get("content", ""))
            if keyword_norm in content_norm:
                matches.append((i, m))
        return matches

    def clear(self) -> None:
        self.memory = {"history": [], "summary": ""}
        self.save()


# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================

def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def strip_think_tags(text: str) -> str:
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


def extract_code_blocks(text: str) -> List[str]:
    return re.findall(r"```(?:python|bash|)?\s*\n?(.*?)\s*```",
                     text, re.DOTALL | re.IGNORECASE)


def save_code_to_file(code_blocks: List[str], agent_name: str,
                      user_prompt: str = "", config: Config = None,
                      io: OutputManager = None) -> List[str]:
    if not code_blocks or not config:
        return []

    saved = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for i, code in enumerate(code_blocks):
        name = agent_name + "_" + ts + ("_" + str(i) if i > 0 else "") + ".py"
        path = os.path.join(config.code_dir, name)

        header = f"# Agent: {agent_name}\n"
        header += f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        if user_prompt:
            header += f"# Prompt: {user_prompt[:100]}\n"
        header += "# " + "=" * 50 + "\n\n"

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(header + code.strip() + "\n")
            saved.append(path)
            if io:
                io.debug(f"Code sauvegardé: {path}")
        except IOError as e:
            if io:
                io.error(f"Erreur sauvegarde code: {e}")

    return saved


def detect_conversation_chain(text: str) -> bool:
    patterns = [r'\nuser\s*\n', r'\nassistant\s*\n', r'\nUser:\s*', r'\nAssistant:\s*']
    return any(re.search(p, text, re.MULTILINE | re.IGNORECASE) for p in patterns)


def get_gpu_info(config: Config) -> Optional[Dict[str, Any]]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 5:
                return {
                    "gpu_name": parts[0].strip(),
                    "vram_used_mb": int(parts[1].strip()),
                    "vram_total_mb": int(parts[2].strip()),
                    "gpu_util": int(parts[3].strip()),
                    "temp_c": int(parts[4].strip())
                }
    except Exception:
        pass
    return None


def encode_image_to_base64(path: str) -> Optional[str]:
    """Lit un fichier image et retourne son contenu en base64 brut (sans préfixe data:)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except IOError:
        return None


def sanitize_base64_image(b64_string: str) -> str:
    """
    Nettoie une chaîne base64 pour Ollama.
    
    Ollama attend du base64 BRUT sans préfixe.
    Le navigateur (via FileReader.readAsDataURL) envoie :
      "data:image/png;base64,iVBORw0KGgo..."
    
    Cette fonction :
    1. Retire le préfixe "data:image/...;base64," si présent
    2. Retire les espaces/retours à la ligne parasites
    3. Retourne du base64 brut prêt pour Ollama
    """
    # Retirer le préfixe data URI si présent
    # Regex : "data:<mimetype>;base64," → on prend tout après la virgule
    if b64_string.startswith("data:"):
        # Trouver la virgule qui sépare le header du contenu
        comma_idx = b64_string.find(",")
        if comma_idx != -1:
            b64_string = b64_string[comma_idx + 1:]
    
    # Nettoyer les espaces/retours à la ligne (parfois ajoutés par le transport)
    b64_string = b64_string.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    
    return b64_string


# =============================================================================
# RECHERCHE WEB AVEC CROSS-CHECK
# =============================================================================

def fetch_web_page(url: str, max_length: int = 8000, io: OutputManager = None) -> str:
    try:
        if TRAFILATURA_AVAILABLE:
            try:
                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    text = trafilatura.extract(downloaded, include_comments=False,
                                            include_tables=True, deduplicate=True)
                    if text:
                        return text[:max_length]
            except Exception:
                pass

        if BS4_AVAILABLE:
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                response = requests.get(url, headers=headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, 'html.parser')
                for element in soup(["script", "style", "nav", "footer", "header"]):
                    element.decompose()
                text = soup.get_text(separator='\n', strip=True)
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                return '\n'.join(lines)[:max_length]
            except Exception:
                pass

        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text[:max_length]

    except Exception as e:
        return f"[ERREUR FETCH] {type(e).__name__}: {str(e)}"


def web_search(query: str, max_results: int = 10, fetch_content: bool = False,
              io: OutputManager = None) -> Optional[List[Dict[str, Any]]]:
    clean_query = query.replace("è", "e").replace("é", "e").replace("ê", "e")
    clean_query = clean_query.replace("à", "a").replace("â", "a")
    clean_query = clean_query.replace("ù", "u").replace("û", "u")
    clean_query = clean_query.replace("ô", "o").replace("î", "i")

    results = []

    if not DDGS_AVAILABLE:
        if io:
            io.error("duckduckgo_search non installé. pip install duckduckgo-search")
        return None

    try:
        with DDGS() as ddgs:
            for r in ddgs.text(clean_query, max_results=max_results):
                result = {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "body": r.get("body", "")[:500]
                }
                if fetch_content:
                    if io:
                        io.info(f"Fetching: {result['url'][:40]}...")
                    result["full_content"] = fetch_web_page(result["url"], io=io)
                results.append(result)
        return results
    except Exception as e:
        if io:
            io.error(f"Erreur recherche web: {e}")
        return []


# =============================================================================
# ROUTING INTELLIGENT
# =============================================================================

class Router:
    def __init__(self, config: Config, cache: LRUCache, io: OutputManager):
        self.config = config
        self.cache = cache
        self.io = io

    def get_route(self, user_query: str, memory: MemoryStore) -> str:
        cache_key = self._get_cache_key(user_query)
        if cache_key in self.cache:
            cached = self.cache.get(cache_key)
            self.io.debug(f"Routing cache: {cached}")
            return cached

        route = self._keyword_routing(user_query)
        if route:
            self.cache.set(cache_key, route)
            return route

        route = self._ai_routing(user_query)
        self.cache.set(cache_key, route)
        return route

    def _get_cache_key(self, query: str) -> str:
        norm = normalize_text(query)[:200]
        return hashlib.md5(norm.encode()).hexdigest()[:10]

    def _keyword_routing(self, query: str) -> Optional[str]:
        query_norm = normalize_text(query)

        keywords = {
            "sec": ["securite", "hachage", "auth", "bcrypt", "jwt", "chiffrement"],
            "cod": ["script", "code", "ecris", "programme", "endpoint", "api", "fonction"],
            "exp": ["exploit", "faille", "vulnerabilite", "injection"],
            "src": ["recherche", "actualite", "news", "recemment", "2025", "2026"],
            "cre": ["histoire", "raconte", "conte", "fiction", "poeme", "creatif"],
            "img": ["image", "photo", "screenshot", ".png", ".jpg"],
            "learn": ["apprendre", "apprentissage", "conseil", "roadmap", "debutant"],
            "debug": ["erreur", "bug", "debug", "traceback", "exception"],
            "doc": ["documente", "documentation", "docstring", "readme"],
            "refactor": ["refactor", "refactoriser", "moderniser", "ameliorer"],
            "debats": ["debats", "quelle est la meilleure", "comparons"],
        }

        has = {k: any(w in query_norm for w in v) for k, v in keywords.items()}

        if has["sec"] and has["cod"]:
            return "code_secure"
        if has["exp"] and has["cod"]:
            return "exploit_code"
        if has["cre"] and not has["cod"]:
            return "creative"
        if has["learn"] and not has["cod"]:
            return "learning_guide"
        if has["debug"] and has["cod"]:
            return "debug_fix"
        if has["doc"] and has["cod"]:
            return "doc_code"
        if has["refactor"]:
            return "phoenix"
        if has["debats"]:
            return "shark_tank"

        return None

    def _ai_routing(self, query: str) -> str:
        return "analyst"


# =============================================================================
# APPEL OLLAMA
# =============================================================================

def call_ollama_stream(
    model_name: str, messages: List[Dict[str, str]], config: Config, io: OutputManager,
    temperature: float = 0.7, images: Optional[List[str]] = None,
    max_tokens: Optional[int] = None, is_creative: bool = False,
    loop_detector: Optional[LoopDetector] = None
) -> Tuple[str, float, Dict[str, Any]]:
    """
    Appelle un modèle Ollama en streaming via /api/chat.
    
    Args:
        model_name: Nom exact du modèle Ollama (ex: "qwen3-14b:latest")
        messages: Liste de messages [{"role": "user/system/assistant", "content": "..."}]
        config: Configuration globale
        io: OutputManager pour les logs et le streaming
        temperature: Créativité du modèle (0.0 = déterministe, 1.0 = créatif)
        images: Liste de chaînes base64 d'images (pour les modèles vision)
        max_tokens: Limite de tokens en sortie
        is_creative: Si True, active la détection de chaînes conversationnelles
        loop_detector: Détecteur de boucles (coupe la génération si répétition)
    
    Returns:
        Tuple (réponse_texte, durée_secondes, stats_dict)
    
    Raises:
        OllamaTimeoutError: Timeout dépassé
        OllamaConnectionError: Ollama non accessible
        AgentError: Erreur inattendue
    """

    if max_tokens is None:
        max_tokens = config.max_tokens

    # Séquences d'arrêt pour éviter que le modèle "joue" les deux rôles
    stop_sequences = ["user\n", "User:", "user:", "Human:", "Assistant:",
                     "\nuser\n", "\nassistant\n", "\nUser\n", "\nAssistant\n"]

    # Construction du payload pour l'API Ollama /api/chat
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "keep_alive": 0,  # Libère le modèle de la VRAM après chaque appel
        "options": {
            "num_predict": max_tokens,
            "stop": stop_sequences
        }
    }

    # ══════════════════════════════════════════════════════════════════════
    # GESTION DES IMAGES (modèles vision)
    # ══════════════════════════════════════════════════════════════════════
    # Ollama attend les images en base64 BRUT dans le dernier message
    # Format : messages[-1]["images"] = ["iVBORw0KGgo...", ...]
    # IMPORTANT : PAS de préfixe "data:image/...;base64,"
    # C'est la cause #1 des erreurs 500 avec les modèles vision
    if images:
        # Nettoyer chaque image : retirer le préfixe data URI si présent
        clean_images = []
        for img_b64 in images:
            clean = sanitize_base64_image(img_b64)
            # Vérification : le base64 doit avoir une taille raisonnable
            if len(clean) < 100:
                io.warning(f"Image base64 suspecte (trop courte: {len(clean)} chars)")
                continue
            clean_images.append(clean)
        
        if clean_images:
            payload["messages"][-1]["images"] = clean_images
            io.debug(f"Images attachées: {len(clean_images)} (tailles: {[len(i)//1024 for i in clean_images]}KB)")
        else:
            io.warning("Aucune image valide après nettoyage")

    full_response = ""
    start = time.time()
    stats: Dict[str, Any] = {}
    http_response = None
    loop_detected = False
    conversation_detected = False

    try:
        io.debug(f"Appel Ollama: {model_name} (max {max_tokens}t)")

        http_response = requests.post(
            f"{config.ollama_url}/api/chat", json=payload,
            timeout=config.timeout, stream=True
        )
        http_response.raise_for_status()

        in_think = False

        for line in http_response.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue

            if chunk.get("done", False):
                ec = chunk.get("eval_count", 0)
                ed = chunk.get("eval_duration", 0)
                if ed > 0:
                    stats["tok_s"] = round(ec / (ed / 1e9), 1)
                    stats["tokens"] = ec
                stats["prompt_tokens"] = chunk.get("prompt_eval_count", "?")
                if "total_duration" in chunk:
                    stats["total_s"] = round(chunk["total_duration"] / 1e9, 1)
                break

            content = chunk.get("message", {}).get("content", "")
            if not content:
                continue

            full_response += content

            if "</thinking>" in full_response or "</think>" in full_response:
                in_think = False
                full_response = strip_think_tags(full_response)
                continue
            if "<thinking>" in full_response or "<think>" in full_response:
                if not in_think:
                    in_think = True

            if loop_detector and not loop_detected:
                if loop_detector.detect(full_response):
                    loop_detected = True
                    io.warning("Boucle détectée - arrêt automatique")
                    if http_response:
                        http_response.close()
                    break

            if is_creative and not conversation_detected:
                if detect_conversation_chain(full_response):
                    conversation_detected = True
                    io.warning("Chaîne conversationnelle détectée - arrêt")
                    if http_response:
                        http_response.close()
                    break

            if not in_think:
                io.stream(content, agent=model_name)

        elapsed = round(time.time() - start, 1)
        result = strip_think_tags(full_response)

        if loop_detected or conversation_detected:
            window = config.loop_detect_window
            if len(result) > window * 2:
                for pattern in [r'\nuser\s*\n', r'\nassistant\s*\n']:
                    match = re.search(pattern, result, re.IGNORECASE)
                    if match:
                        result = result[:match.start()]
                        break
                else:
                    tail = result[-window:]
                    first_occurrence = result.find(tail)
                    if first_occurrence >= 0 and first_occurrence < len(result) - window:
                        result = result[:first_occurrence + window]
            result = result.rstrip() + "\n[REPONSE COUPÉE - répétition détectée]"

        io.debug(f"Réponse: {len(result)} chars, {elapsed}s")
        return result, elapsed, stats

    except requests.exceptions.Timeout:
        io.error(f"Timeout après {config.timeout}s")
        raise OllamaTimeoutError(f"Timeout après {config.timeout}s")
    except requests.exceptions.ConnectionError:
        io.error("Ollama non accessible")
        raise OllamaConnectionError("Ollama non accessible")
    except Exception as e:
        io.error(f"Erreur inattendue: {type(e).__name__}: {str(e)}")
        raise AgentError(f"Erreur inattendue: {type(e).__name__}: {str(e)}")
    finally:
        if http_response:
            try:
                http_response.close()
            except Exception:
                pass


def unload_model(model_name: str, config: Config, io: OutputManager) -> None:
    """Demande à Ollama de libérer un modèle de la VRAM."""
    if not model_name:
        return
    for _ in range(2):
        try:
            requests.post(
                f"{config.ollama_url}/api/generate",
                json={"model": model_name, "keep_alive": 0, "prompt": ""},
                timeout=8
            )
            time.sleep(0.4)
            break
        except Exception:
            pass


# =============================================================================
# APPEL SERVEUR VISION (llama-server / API OpenAI-compatible)
# =============================================================================
# Quand Ollama ne supporte pas bien un modèle vision (erreur 500),
# on utilise llama-server qui charge le modèle GGUF directement.
#
# Prérequis :
#   llama-server -m <model.gguf> --mmproj <mmproj.gguf> --port 8081
#
# L'API est compatible OpenAI : POST /v1/chat/completions
# Les images sont envoyées en base64 dans le contenu du message :
#   {"role": "user", "content": [
#       {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
#       {"type": "text", "text": "Décris cette image"}
#   ]}
# =============================================================================

def call_vision_server(
    messages: List[Dict], config: Config, io: OutputManager,
    images: List[str], temperature: float = 0.7,
    max_tokens: int = 2048
) -> Tuple[str, float, Dict[str, Any]]:
    """
    Appelle le serveur vision (llama-server) via l'API OpenAI-compatible.
    
    Contrairement à Ollama, llama-server utilise le format OpenAI pour les images :
    les images sont dans le "content" du message sous forme d'objets image_url.
    
    Args:
        messages: Messages de contexte [{"role": "...", "content": "..."}]
        config: Configuration (vision_url doit être défini)
        io: OutputManager pour streaming
        images: Liste de chaînes base64 brutes (sans préfixe data:)
        temperature: Créativité
        max_tokens: Limite de tokens
    
    Returns:
        Tuple (réponse_texte, durée_secondes, stats_dict)
    """
    
    # ═══════════════════════════════════════════════════════════════
    # Construction du message avec images au format OpenAI
    # ═══════════════════════════════════════════════════════════════
    # Le dernier message utilisateur doit contenir les images
    # Format : content = [{"type": "image_url", ...}, {"type": "text", ...}]
    
    # Copier les messages pour ne pas modifier l'original
    api_messages = []
    for msg in messages:
        api_messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Transformer le dernier message pour inclure les images
    if api_messages and images:
        last_msg = api_messages[-1]
        text_content = last_msg.get("content", "")
        
        # Construire le contenu multimodal (images + texte)
        multimodal_content = []
        
        for img_b64 in images:
            # llama-server attend le préfixe data: (contrairement à Ollama)
            # On détecte le type d'image ou on met png par défaut
            if not img_b64.startswith("data:"):
                img_url = f"data:image/png;base64,{img_b64}"
            else:
                img_url = img_b64
            
            multimodal_content.append({
                "type": "image_url",
                "image_url": {"url": img_url}
            })
        
        # Ajouter le texte après les images
        multimodal_content.append({
            "type": "text",
            "text": text_content
        })
        
        last_msg["content"] = multimodal_content
    
    # ═══════════════════════════════════════════════════════════════
    # Payload API OpenAI-compatible
    # ═══════════════════════════════════════════════════════════════
    payload = {
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,  # Streaming SSE
    }
    
    full_response = ""
    start = time.time()
    stats: Dict[str, Any] = {}
    http_response = None
    
    try:
        endpoint = f"{config.vision_url}/v1/chat/completions"
        io.debug(f"Appel Vision Server: {endpoint} (max {max_tokens}t)")
        
        http_response = requests.post(
            endpoint,
            json=payload,
            timeout=config.timeout,
            stream=True,
            headers={"Content-Type": "application/json"}
        )
        http_response.raise_for_status()
        
        # ═══════════════════════════════════════════════════════════
        # Parsing du streaming SSE (Server-Sent Events)
        # Format : "data: {...}\n\n" pour chaque chunk
        #          "data: [DONE]\n\n" pour la fin
        # ═══════════════════════════════════════════════════════════
        in_think = False
        
        for line in http_response.iter_lines():
            if not line:
                continue
            
            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            
            # Les lignes SSE commencent par "data: "
            if not line_str.startswith("data: "):
                continue
            
            data_str = line_str[6:]  # Retirer "data: "
            
            # Signal de fin
            if data_str.strip() == "[DONE]":
                break
            
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            
            # Extraire le contenu delta
            choices = chunk.get("choices", [])
            if not choices:
                continue
            
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            
            if not content:
                continue
            
            full_response += content
            
            # Gestion des tags <think> (certains modèles réfléchissent)
            if "</think>" in full_response or "</thinking>" in full_response:
                in_think = False
                full_response = strip_think_tags(full_response)
                continue
            if "<think>" in full_response or "<thinking>" in full_response:
                if not in_think:
                    in_think = True
            
            # Streamer vers l'UI (sauf pendant la réflexion)
            if not in_think:
                io.stream(content, agent="vision")
            
            # Récupérer les stats d'usage si présentes
            usage = chunk.get("usage")
            if usage:
                stats["tokens"] = usage.get("completion_tokens", 0)
                stats["prompt_tokens"] = usage.get("prompt_tokens", 0)
        
        elapsed = round(time.time() - start, 1)
        result = strip_think_tags(full_response)
        
        # Calculer tok/s
        if stats.get("tokens") and elapsed > 0:
            stats["tok_s"] = round(stats["tokens"] / elapsed, 1)
        elif elapsed > 0 and len(result) > 0:
            # Estimation approximative si pas de stats d'usage
            approx_tokens = len(result.split())
            stats["tok_s"] = round(approx_tokens / elapsed, 1)
            stats["tokens"] = approx_tokens
        
        stats["total_s"] = elapsed
        
        io.debug(f"Vision réponse: {len(result)} chars, {elapsed}s")
        return result, elapsed, stats
    
    except requests.exceptions.ConnectionError:
        io.error(f"Serveur vision non accessible sur {config.vision_url}")
        io.error("Vérifiez que llama-server est lancé avec le modèle vision")
        raise AgentError(f"Serveur vision non accessible: {config.vision_url}")
    except requests.exceptions.Timeout:
        io.error(f"Timeout vision après {config.timeout}s")
        raise OllamaTimeoutError(f"Timeout vision")
    except Exception as e:
        io.error(f"Erreur vision: {type(e).__name__}: {str(e)}")
        raise AgentError(f"Erreur vision: {type(e).__name__}: {str(e)}")
    finally:
        if http_response:
            try:
                http_response.close()
            except Exception:
                pass


# =============================================================================
# AGENTS
# =============================================================================

def run_search_agent(user_input: str, memory: MemoryStore, config: Config, io: OutputManager,
                    use_advanced_synth: bool = False, fetch_pages: bool = False) -> Tuple[str, float, Dict, str]:

    io.info("Recherche web...")
    if fetch_pages:
        io.info("[Mode FETCH ACTIVE - récupération contenu complet]")

    wc = len(user_input.split())
    mr = 5 if fetch_pages else (10 if wc < 5 else (5 if wc > 20 else 8))
    results = web_search(user_input, max_results=mr, fetch_content=fetch_pages, io=io)

    if results is None:
        io.error("Module de recherche non disponible")
        return "Module manquant.", 0.0, {}, "searcher"
    if not results:
        return "Aucun résultat.", 0.0, {}, "searcher"

    # Construction du contexte
    search_ctx = "Résultats web:\n\n"
    for i, r in enumerate(results):
        search_ctx += f"{i+1}. {r['title']}\n   URL: {r['url']}\n   Résumé: {r['body']}\n"
        if fetch_pages and "full_content" in r:
            content = r['full_content'][:2000]
            search_ctx += f"   Contenu: {content}\n"
        search_ctx += "\n"

    context_messages = memory.get_context_messages()

    synth_model = config.models["searcher_synth"] if use_advanced_synth else config.models["searcher"]
    synth_prompt = config.agent_prompts.get("searcher", "Synthétise ces résultats web en français. Cite tes sources.")

    messages = [{"role": "system", "content": synth_prompt}]
    messages.extend(context_messages)

    prompt_suffix = "Synthétise en français avec détails et citations." if not fetch_pages else \
                   "Analyse en profondeur ces articles. Synthétise les points clés avec citations précises."

    messages.append({"role": "user", "content": search_ctx + "\nQuestion: " + user_input + "\n" + prompt_suffix})

    # Émettre les sources
    io.emit("sources", data=[{"title": r["title"], "url": r["url"]} for r in results])

    max_tokens = config.max_tokens_web_fetch if fetch_pages else config.max_tokens
    response, elapsed, stats = call_ollama_stream(
        synth_model, messages, config, io, max_tokens=max_tokens
    )
    unload_model(synth_model, config, io)

    io.success(f"Recherche terminée: {elapsed}s")
    return response, elapsed, stats, "searcher"


def run_single_agent(agent: str, user_input: str, memory: MemoryStore, config: Config, io: OutputManager,
                    images: Optional[List[str]] = None, auto_select: bool = True,
                    max_tokens: Optional[int] = None, original_prompt: str = "",
                    loop_detector: LoopDetector = None) -> Tuple[str, float, Dict, str]:

    # Auto-select complexité
    cx = "?"
    if auto_select and agent in ["coder", "coder2", "analyst", "analyst2"]:
        tn = normalize_text(user_input)
        score = 0
        strong_kw = ["compare", "architecture", "securise", "bcrypt", "machine learning", "distribue"]
        normal_kw = ["optimise", "refactor", "threading", "concurrent", "database", "api rest"]
        for kw in strong_kw:
            if kw in tn: score += 2
        for kw in normal_kw:
            if kw in tn: score += 1
        if len(tn.split()) > 30: score += 1
        if len(tn.split()) > 60: score += 1

        if agent in ["coder", "coder2"]:
            cx = "complex" if score >= 2 else "simple"
            agent = config.models.get("coder" if cx == "complex" else "coder2", agent)
        elif agent in ["analyst", "analyst2"]:
            cx = "complex" if score >= 2 else "simple"
            agent = config.models.get("analyst" if cx == "complex" else "analyst2", agent)

    if max_tokens is None:
        max_tokens = config.max_tokens

    is_creative = (agent == config.models.get("creative"))
    if is_creative:
        max_tokens = min(max_tokens, config.max_tokens_creative)

    model = config.models.get(agent, agent)
    context_messages = memory.get_context_messages()
    messages = [{"role": "system", "content": config.agent_prompts.get(agent, "Assistant. Français.")}]
    messages.extend(context_messages)
    messages.append({"role": "user", "content": user_input})

    tag = f" [{cx}]" if cx != "?" else ""
    io.agent_msg(agent.upper(), f"Répond{tag}...")

    # ═══════════════════════════════════════════════════════════════
    # CHOIX DU BACKEND : llama-server (vision) ou Ollama (autres)
    # ═══════════════════════════════════════════════════════════════
    if agent == "vision" and images and config.vision_url:
        # Utiliser le serveur vision dédié (llama-server, API OpenAI)
        # Avantage : supporte les GGUF quantifiés, plus stable qu'Ollama
        io.debug(f"Vision via llama-server: {config.vision_url}")
        response, elapsed, stats = call_vision_server(
            messages, config, io,
            images=images, temperature=0.7, max_tokens=max_tokens
        )
        # Pas de unload_model pour llama-server (il gère sa propre mémoire)
    else:
        # Utiliser Ollama pour tous les autres agents (ou vision si pas de vision_url)
        response, elapsed, stats = call_ollama_stream(
            model, messages, config, io, images=images, max_tokens=max_tokens,
            is_creative=is_creative, loop_detector=loop_detector
        )
        unload_model(model, config, io)

    # Sauvegarder le code
    code_blocks = extract_code_blocks(response)
    saved = save_code_to_file(code_blocks, agent, original_prompt or user_input[:100], config, io)
    if saved:
        io.debug(f"Code sauvegardé: {len(saved)} fichier(s)")

    io.done(stats)
    return response, elapsed, stats, agent


def run_pipeline(pipeline_name: str, user_input: str, memory: MemoryStore,
                config: Config, io: OutputManager, loop_detector: LoopDetector) -> Tuple[str, float, Dict, str]:

    io.pipeline_event("start", pipeline_name)

    a1, a2 = config.pipelines.get(pipeline_name, ("analyst", "coder"))

    io.info(f"=== PIPELINE: {pipeline_name.upper()} ===")
    io.info(f"Étape 1: {a1.upper()} -> Étape 2: {a2.upper()}")

    # Étape 1
    io.pipeline_event("step", pipeline_name, step=1, agent=a1)
    tokens_step1 = config.max_tokens_pipeline
    if pipeline_name in ["learning_guide", "debug_fix", "phoenix", "shark_tank"]:
        tokens_step1 = config.max_tokens_learning

    r1, e1, s1, aa1 = run_single_agent(
        a1, user_input, memory, config, io,
        max_tokens=tokens_step1, original_prompt=user_input,
        loop_detector=None  # Désactivé pour pipelines
    )

    memory.add_assistant_message(r1, aa1, config.models.get(aa1, a1), e1,
                                pipeline=pipeline_name, step=1, intermediate=True)

    # Étape 2
    io.pipeline_event("step", pipeline_name, step=2, agent=a2)

    # Prompts spécifiques par pipeline
    if pipeline_name == "debug_fix":
        a2_input = (
            f"MISSION: Corriger le code buggé basé sur l'analyse d'erreur.\n\n"
            f"ANALYSE DE L'ERREUR (par {aa1.upper()}):\n```\n{r1}\n```\n\n"
            f"CODE/CONTEXTE ORIGINAL:\n{user_input}\n\n"
            f"TÂCHE: Propose le code corrigé, explique la correction, ajoute des tests si pertinent."
        )
    elif pipeline_name == "full_stack":
        a2_input = (
            f"MISSION: Créer le frontend pour accompagner ce backend.\n\n"
            f"BACKEND (par {aa1.upper()}):\n```\n{r1}\n```\n\n"
            f"Demande originale: {user_input}\n\n"
            f"TÂCHE: Crée un frontend HTML/CSS/JS ou React qui consomme cette API."
        )
    elif pipeline_name == "learning_guide":
        a2_input = (
            f"MISSION: Créer un parcours d'apprentissage complet.\n\n"
            f"CONTENU TECHNIQUE (par {aa1.upper()}):\n```\n{r1}\n```\n\n"
            f"SUJET: {user_input}\n\n"
            f"TÂCHE: Structure ce contenu en phases progressives, ajoute ressources, projets pratiques."
        )
    elif pipeline_name == "phoenix":
        a2_input = (
            f"MISSION: Refactoriser et moderniser le code.\n\n"
            f"ANALYSE (par {aa1.upper()}):\n```\n{r1}\n```\n\n"
            f"CODE ORIGINAL: {user_input}\n\n"
            f"TÂCHE: Propose une version modernisée, meilleure architecture, code propre."
        )
    elif pipeline_name == "shark_tank":
        a2_input = (
            f"MISSION: Débattre et évaluer cette idée.\n\n"
            f"IDÉE PRÉSENTÉE (par {aa1.upper()}):\n```\n{r1}\n```\n\n"
            f"SUJET: {user_input}\n\n"
            f"TÂCHE: Évalue les avantages, inconvénients, risques. Donne un verdict final."
        )
    else:
        a2_input = (
            f"Travail de {aa1.upper()}:\n"
            f"--- DEMANDE ---\n{user_input}\n"
            f"--- RÉPONSE ---\n{r1}\n"
            f"--- TÂCHE ---\nAméliore, corrige ou complète. Version finale uniquement."
        )

    tokens_step2 = config.max_tokens_pipeline
    if pipeline_name in ["learning_guide", "debug_fix", "phoenix", "shark_tank"]:
        tokens_step2 = config.max_tokens_learning

    r2, e2, s2, aa2 = run_single_agent(
        a2, a2_input, memory, config, io,
        max_tokens=tokens_step2, original_prompt=user_input,
        loop_detector=None  # Désactivé pour pipelines
    )

    memory.add_assistant_message(r2, aa2, config.models.get(aa2, a2), e2,
                                pipeline=pipeline_name, step=2, intermediate=False)

    total = round(e1 + e2, 1)
    io.success(f"=== FIN PIPELINE {pipeline_name.upper()} ({total}s) ===")
    io.pipeline_event("done", pipeline_name, duration=total)

    return r2, total, s2, aa2


# =============================================================================
# MAIN
# =============================================================================

def main():
    config = Config.from_args(sys.argv[1:])
    io = OutputManager(headless=config.headless, debug=config.debug)

    if not config.headless:
        io.emit("info", data="=" * 60)
        io.emit("info", data="NEXUS HIVE v7.0 - Multi-Agents System")
        io.emit("info", data="=" * 60)
        io.emit("info", data=f"Dossier: {config.base_dir}")
        io.emit("info", data=f"Ollama: {config.ollama_url}")
        io.emit("info", data=f"Mode: {'HEADLESS' if config.headless else 'CLI'}")
        io.emit("info", data="")

    # Initialisation des composants
    cache = LRUCache(max_size=config.route_cache_max)
    memory = MemoryStore(config, io)
    router = Router(config, cache, io)
    loop_detector = LoopDetector(config)

    # Émettre l'état ready
    io.emit("ready", data={
        "version": "7.0",
        "headless": config.headless,
        "models": list(config.models.keys()),
        "pipelines": list(config.pipelines.keys())
    })

    io.success("Système prêt!")

    current_agent = None
    session_stats = []

    while True:
        try:
            raw_input = io.read_input("")

            if not raw_input:
                continue

            # ═══════════════════════════════════════════════════════════════
            # PARSING DE L'INPUT (protocole stdin unifié)
            # ═══════════════════════════════════════════════════════════════
            # Le stdin accepte 3 formats :
            #
            # FORMAT 1 - JSON structuré (recommandé, depuis Electron) :
            #   {"type": "input", "text": "ma question"}
            #   {"type": "command", "command": "/reset"}
            #   {"type": "input_with_files", "text": "...", "files": [...]}
            #
            # FORMAT 2 - Ancien format fichiers (rétrocompatibilité) :
            #   {"__type": "input_with_files", "text": "...", "files": [...]}
            #
            # FORMAT 3 - Texte brut (mode CLI, rétrocompatibilité) :
            #   "ma question" ou "/reset"
            # ═══════════════════════════════════════════════════════════════
            
            user_input = raw_input
            attached_files = None
            images_b64 = None

            try:
                parsed = json.loads(raw_input)
                if isinstance(parsed, dict):
                    msg_type = parsed.get("type") or parsed.get("__type", "")
                    
                    if msg_type == "command":
                        # Format JSON pour les commandes système
                        user_input = parsed.get("command", "").strip()
                    
                    elif msg_type == "input":
                        # Format JSON pour les messages texte simples
                        user_input = parsed.get("text", "").strip()
                    
                    elif msg_type == "input_with_files":
                        # Format JSON pour les messages avec fichiers
                        user_input = parsed.get("text", "").strip()
                        attached_files = parsed.get("files", [])
                        
                        # ───────────────────────────────────────────────
                        # Extraction des images et fichiers texte
                        # ───────────────────────────────────────────────
                        images_b64 = []
                        file_descriptions = []
                        
                        for f in attached_files:
                            if f.get("isImage") and f.get("base64"):
                                # IMAGE : nettoyer le base64 (retirer préfixe data:)
                                # C'est CRITIQUE : Ollama crash (500) si le base64
                                # contient le préfixe "data:image/png;base64,"
                                clean_b64 = sanitize_base64_image(f["base64"])
                                images_b64.append(clean_b64)
                                file_descriptions.append(f"[Image: {f['name']}]")
                                io.debug(f"Image nettoyée: {f['name']} ({len(clean_b64)//1024}KB b64)")
                            else:
                                # FICHIER TEXTE : décoder et injecter dans le prompt
                                try:
                                    raw_b64 = f.get("base64", "")
                                    # Même nettoyage du préfixe data: pour les fichiers
                                    if raw_b64.startswith("data:"):
                                        comma_idx = raw_b64.find(",")
                                        if comma_idx != -1:
                                            raw_b64 = raw_b64[comma_idx + 1:]
                                    content = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
                                    # Limiter à 3000 chars pour ne pas exploser le contexte
                                    file_descriptions.append(
                                        f"[Fichier: {f['name']}]\n```\n{content[:3000]}\n```"
                                    )
                                except Exception as e:
                                    file_descriptions.append(
                                        f"[Fichier binaire: {f['name']} ({f.get('size', 0)} bytes)]"
                                    )
                                    io.debug(f"Fichier non-décodable: {f['name']} - {e}")
                        
                        # Prompt par défaut si l'utilisateur n'a rien écrit
                        if not user_input:
                            if images_b64:
                                user_input = "Décris en détail ce que tu vois dans cette image."
                            else:
                                user_input = "Analyse le contenu de ce fichier."
                        
                        # Ajouter les descriptions de fichiers au prompt utilisateur
                        if file_descriptions:
                            user_input = user_input + "\n\n" + "\n".join(file_descriptions)
                        
                        # Si aucune image valide, remettre à None
                        if not images_b64:
                            images_b64 = None
                        
                        io.debug(
                            f"Input avec fichiers: {len(attached_files)} fichier(s), "
                            f"{len(images_b64 or [])} image(s)"
                        )
                    
                    else:
                        # JSON non reconnu → traiter comme texte brut
                        user_input = raw_input
                        
            except (json.JSONDecodeError, ValueError):
                # Pas du JSON → texte brut (mode CLI ou texte simple)
                user_input = raw_input

            if not user_input:
                continue

            # ═══════════════════════════════════════════════════════════════
            # COMMANDES SYSTÈME (commencent par / ou mots-clés spéciaux)
            # ═══════════════════════════════════════════════════════════════

            if user_input.lower() in ["exit", "quit", "bye"]:
                io.emit("info", data="Au revoir!")
                break

            cmd = user_input.lower().strip()

            if cmd == "/reset":
                # Réinitialise tout : mémoire, cache routing, agent actif, détecteur boucles
                memory.clear()
                cache.clear()
                current_agent = None
                loop_detector.reset()
                io.success("Reset complet!")
                continue

            if cmd == "/gpu":
                # Affiche les infos GPU (nvidia-smi) et émet l'event gpu_info
                gpu = get_gpu_info(config)
                if gpu:
                    io.info(f"GPU: {gpu['gpu_name']} | VRAM: {gpu['vram_used_mb']}/{gpu['vram_total_mb']}MB | {gpu['gpu_util']}% | {gpu['temp_c']}°C")
                    io.emit("gpu_info", data=gpu)
                else:
                    io.warning("GPU non disponible")
                continue

            if cmd == "/models":
                # Liste tous les modèles configurés
                io.info("Modèles disponibles:")
                for name, model in config.models.items():
                    io.info(f"  {name}: {model}")
                continue

            if cmd == "/status":
                # Affiche l'état du système
                io.info("Statut du système:")
                io.info(f"  Mémoire: {len(memory.memory['history'])} entrées")
                io.info(f"  Cache routing: {len(cache)} éléments")
                io.info(f"  Mode conversation: {'ON' if config.conversation_mode else 'OFF'}")
                continue

            if cmd == "/export":
                # Exporte les stats de session en JSON
                if session_stats:
                    stats_path = os.path.join(config.base_dir, config.stats_file)
                    with open(stats_path, "w", encoding="utf-8") as f:
                        json.dump(session_stats, f, ensure_ascii=False, indent=2)
                    io.success(f"Stats exportées: {config.stats_file} ({len(session_stats)} entrées)")
                    io.emit("export", data=session_stats)
                else:
                    io.warning("Aucune stat à exporter")
                continue

            if cmd.startswith("/agent "):
                # Force un agent spécifique pour tous les messages suivants
                agent_name = user_input[7:].strip().lower()
                if agent_name in config.models or agent_name in config.pipelines:
                    current_agent = agent_name
                    io.success(f"Agent actif: {agent_name}")
                else:
                    io.error(f"Agent inconnu: {agent_name}")
                continue

            if cmd.startswith("/pipeline "):
                # Lance un pipeline manuellement : /pipeline <nom> <prompt optionnel>
                parts = user_input[10:].strip().split(maxsplit=1)
                pipeline_name = parts[0].lower()
                prompt = parts[1] if len(parts) > 1 else ""
                
                if pipeline_name in config.pipelines:
                    memory.add_user_message(user_input)
                    actual_prompt = prompt if prompt else user_input
                    resp, elapsed, stats, agent = run_pipeline(
                        pipeline_name, actual_prompt, memory, config, io, loop_detector
                    )
                    session_stats.append({"pipeline": pipeline_name, "elapsed": elapsed, "agent": agent})
                else:
                    io.error(f"Pipeline inconnu: {pipeline_name}")
                continue

            # ═══════════════════════════════════════════════════════════════
            # TRAITEMENT DU MESSAGE UTILISATEUR
            # ═══════════════════════════════════════════════════════════════
            
            # Sauvegarder en mémoire
            memory.add_user_message(user_input)

            # ───────────────────────────────────────────────
            # CAS 1 : Images attachées → forcer agent VISION
            # ───────────────────────────────────────────────
            if images_b64:
                route = "vision"
                io.emit("routing", data="vision")
                io.emit("routed", data="vision")
                io.info("→ VISION (image détectée)")

                resp, elapsed, stats, agent = run_single_agent(
                    "vision", user_input, memory, config, io,
                    images=images_b64, original_prompt=user_input,
                    loop_detector=loop_detector
                )
                memory.add_assistant_message(resp, "vision", config.models["vision"], elapsed)
                session_stats.append({"agent": "vision", "elapsed": elapsed, "stats": stats})

            else:
                # ───────────────────────────────────────────────
                # CAS 2 : Texte seul → routing intelligent
                # ───────────────────────────────────────────────
                
                if config.conversation_mode and current_agent:
                    # Mode conversation : garder le même agent
                    route = current_agent
                    io.info(f"[Mode Conversation] Agent: {route}")
                else:
                    # Routing automatique (mots-clés → cache → fallback analyst)
                    io.info("Routing...")
                    route = router.get_route(user_input, memory)
                    if config.conversation_mode:
                        current_agent = route
                    io.info(f"→ {route}")

                # Exécution selon le type de route
                start_time = time.time()

                if route == "searcher":
                    # Agent recherche web (DuckDuckGo + synthèse)
                    io.info("→ SEARCHER")
                    resp, elapsed, stats, agent = run_search_agent(user_input, memory, config, io)
                    memory.add_assistant_message(resp, "searcher", config.models["searcher"], elapsed)
                    session_stats.append({"agent": "searcher", "elapsed": elapsed, "stats": stats})

                elif route in config.pipelines:
                    # Pipeline multi-agents (2 étapes)
                    io.info(f"→ PIPELINE {route.upper()}")
                    resp, elapsed, stats, agent = run_pipeline(route, user_input, memory, config, io, loop_detector)
                    session_stats.append({"pipeline": route, "elapsed": elapsed, "agent": agent})

                else:
                    # Agent simple
                    io.info(f"→ {route.upper()}")
                    resp, elapsed, stats, agent = run_single_agent(
                        route, user_input, memory, config, io,
                        images=None, original_prompt=user_input,
                        loop_detector=loop_detector
                    )
                    memory.add_assistant_message(resp, agent, config.models.get(agent, route), elapsed)
                    session_stats.append({"agent": agent, "elapsed": elapsed, "stats": stats})

            # Sauvegarder la mémoire après chaque échange
            memory.save()

            io.done(stats)

        except KeyboardInterrupt:
            io.warning("Interruption détectée")
            break
        except Exception as e:
            io.error(f"Erreur: {str(e)}")
            if config.debug:
                import traceback
                io.error(traceback.format_exc())

    io.emit("terminated", data=True)


if __name__ == "__main__":
    main()
