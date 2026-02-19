# -*- coding: utf-8 -*-
"""
Gestion de la mémoire et détection de boucles.

Contient :
- LRUCache : cache LRU pour le routing
- LoopDetector : détection de répétitions dans les sorties des modèles
- MemoryEntry : dataclass d'une entrée mémoire
- MemoryStore : stockage et recherche dans l'historique des conversations
- ConversationManager : gestion multi-conversations avec persistance JSON

Importé par : agents, router, main
Dépend de : config (Config), io_manager (OutputManager), utils (normalize_text)
"""

from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from collections import OrderedDict
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field

from nexus_hive.config import Config
from nexus_hive.io_manager import OutputManager


# =============================================================================
# CACHE LRU (utilisé par le Router pour le cache de routing)
# =============================================================================


class LRUCache:
    """Cache LRU simple avec taille maximale."""

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

    def __len__(self) -> int:
        return len(self._cache)


# =============================================================================
# DÉTECTEUR DE BOUCLE
# =============================================================================


class LoopDetector:
    """
    Détecte les boucles de répétition dans la sortie streaming des modèles.

    Deux méthodes de détection :
    1. Répétition exacte : la queue du texte apparaît 4+ fois
    2. Similarité historique : texte trop similaire (>95%) aux sorties précédentes

    IMPORTANT : Ne PAS appeler detect() pendant les blocs <think> de Qwen3,
    car les pensées longues et répétitives déclenchent des faux positifs.
    """

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
        tail = text[-self.window :]
        before = text[: -self.window]
        return before.count(tail) >= 4

    def _detect_similar_to_history(self, text: str) -> bool:
        if not self._last_outputs:
            return False

        normalized = self._normalize_text(text)
        for old_output in self._last_outputs[-3:]:
            old_norm = self._normalize_text(old_output)
            similarity = self._calculate_similarity(normalized, old_norm)
            if similarity > 0.95:
                return True
        return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text)
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
    """Une entrée dans l'historique de conversation."""

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
    """
    Stockage de l'historique de conversation avec persistance JSON.

    Gère :
    - L'historique des messages (user + assistant)
    - Un résumé compressé des anciens messages
    - La recherche par mot-clé dans l'historique
    - La construction du contexte pour les appels LLM
    """

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
        self.memory["history"].append(
            {
                "role": "user",
                "content": content,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def add_assistant_message(
        self,
        content: str,
        agent: str,
        model: str,
        elapsed: float = 0,
        pipeline: str = None,
        step: int = None,
        intermediate: bool = False,
    ) -> None:
        entry = {
            "role": "assistant",
            "agent": agent,
            "model": model,
            "content": content,
            "elapsed": elapsed,
            "timestamp": datetime.now().isoformat(),
            "intermediate": intermediate,
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
        """Construit la liste de messages de contexte pour l'appel LLM."""
        msgs = []
        if self.memory.get("summary"):
            msgs.append(
                {
                    "role": "system",
                    "content": "Résumé précédent: " + self.memory["summary"],
                }
            )
        for m in self.memory["history"][-max_history:]:
            if m.get("intermediate", False):
                continue
            role = m["role"] if m["role"] in ["user", "assistant"] else "assistant"
            msgs.append({"role": role, "content": m["content"]})
        return msgs[-8:]

    def search(self, keyword: str) -> List[Tuple[int, Dict[str, Any]]]:
        """Recherche par mot-clé dans l'historique."""
        from nexus_hive.utils import normalize_text

        keyword_norm = normalize_text(keyword)
        matches = []
        for i, m in enumerate(self.memory["history"]):
            content_norm = normalize_text(m.get("content", ""))
            if keyword_norm in content_norm:
                matches.append((i, m))
        return matches

    def clear(self) -> None:
        self.memory = {"history": [], "summary": ""}
        self.save()


# =============================================================================
# GESTIONNAIRE DE CONVERSATIONS
# =============================================================================


class ConversationManager:
    """
    Gère les multi-conversations avec persistance JSON.

    Structure :
        conversations/
        ├── index.json              ← liste de toutes les conversations
        ├── conv_<id>.json          ← historique complet d'une conversation
        └── ...

    Chaque conversation dans index.json :
        {
            "id": "conv_20260216_2035_a1b2",
            "title": "Comment faire hello world...",
            "created": "2026-02-16T20:35:00",
            "updated": "2026-02-16T20:45:00",
            "message_count": 4,
            "agents_used": ["eclaireur", "junior"]
        }

    Migration SQLite future : remplacer _load_index/_save_index et
    _load_conversation/_save_conversation par des requêtes SQL.
    L'interface publique reste identique.
    """

    def __init__(self, config: Config, io: OutputManager):
        self.config = config
        self.io = io
        self.conversations_dir = os.path.join(config.base_dir, "conversations")
        self._index: List[Dict[str, Any]] = []
        self._current_id: Optional[str] = None
        self._ensure_dir()
        self._load_index()

    def _ensure_dir(self) -> None:
        """Crée le dossier conversations/ s'il n'existe pas."""
        os.makedirs(self.conversations_dir, exist_ok=True)

    def _index_path(self) -> str:
        return os.path.join(self.conversations_dir, "index.json")

    def _conv_path(self, conv_id: str) -> str:
        return os.path.join(self.conversations_dir, f"{conv_id}.json")

    # ─── Index ───────────────────────────────────────────────────────

    def _load_index(self) -> None:
        path = self._index_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._index = []
        else:
            self._index = []

    def _save_index(self) -> None:
        try:
            with open(self._index_path(), "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except IOError as e:
            self.io.error(f"Erreur sauvegarde index conversations: {e}")

    # ─── Opérations publiques ────────────────────────────────────────

    def new_conversation(self) -> str:
        """
        Crée une nouvelle conversation et retourne son ID.
        L'ancienne conversation est automatiquement sauvegardée.
        """
        now = datetime.now()
        short_id = uuid.uuid4().hex[:4]
        conv_id = f"conv_{now.strftime('%Y%m%d_%H%M')}_{short_id}"

        entry = {
            "id": conv_id,
            "title": "Nouvelle conversation",
            "created": now.isoformat(),
            "updated": now.isoformat(),
            "message_count": 0,
            "agents_used": [],
        }

        self._index.insert(0, entry)  # Plus récent en premier
        self._save_index()

        # Créer le fichier conversation vide
        self._save_conversation(conv_id, {"history": [], "summary": ""})

        self._current_id = conv_id
        self.io.debug(f"Nouvelle conversation: {conv_id}")
        return conv_id

    def get_current_id(self) -> Optional[str]:
        """Retourne l'ID de la conversation active."""
        return self._current_id

    def list_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retourne la liste des conversations (les plus récentes en premier).
        Utilisé par l'UI pour afficher la sidebar.
        """
        return self._index[:limit]

    def update_conversation_meta(
        self,
        conv_id: str,
        title: Optional[str] = None,
        message_count: Optional[int] = None,
        agent: Optional[str] = None,
    ) -> None:
        """Met à jour les métadonnées d'une conversation dans l'index."""
        for entry in self._index:
            if entry["id"] == conv_id:
                entry["updated"] = datetime.now().isoformat()
                if title and entry["title"] == "Nouvelle conversation":
                    # Titre auto = premier message tronqué à 50 chars
                    entry["title"] = title[:50] + ("..." if len(title) > 50 else "")
                if message_count is not None:
                    entry["message_count"] = message_count
                if agent and agent not in entry["agents_used"]:
                    entry["agents_used"].append(agent)
                self._save_index()
                return

    def load_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """
        Charge une conversation complète depuis son fichier JSON.
        Retourne None si la conversation n'existe pas.
        """
        path = self._conv_path(conv_id)
        if not os.path.exists(path):
            self.io.warning(f"Conversation introuvable: {conv_id}")
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._current_id = conv_id
            self.io.debug(f"Conversation chargée: {conv_id}")
            return data
        except (json.JSONDecodeError, IOError) as e:
            self.io.error(f"Erreur chargement conversation {conv_id}: {e}")
            return None

    def save_memory_to_conversation(self, memory: MemoryStore) -> None:
        """
        Sauvegarde le MemoryStore actuel dans la conversation courante.
        Appelé après chaque échange ou quand on change de conversation.
        """
        if not self._current_id:
            return

        self._save_conversation(self._current_id, memory.memory)

        # Mettre à jour le count dans l'index
        msg_count = len(memory.memory.get("history", []))
        self.update_conversation_meta(self._current_id, message_count=msg_count)

    def switch_conversation(self, conv_id: str, memory: MemoryStore) -> bool:
        """
        Sauvegarde la conversation courante, puis charge celle demandée
        dans le MemoryStore. Retourne True si succès.
        """
        # Sauvegarder l'actuelle
        self.save_memory_to_conversation(memory)

        # Charger la nouvelle
        data = self.load_conversation(conv_id)
        if data is None:
            return False

        memory.memory = data
        self._current_id = conv_id
        return True

    def delete_conversation(self, conv_id: str) -> bool:
        """Supprime une conversation (index + fichier)."""
        # Retirer de l'index
        self._index = [c for c in self._index if c["id"] != conv_id]
        self._save_index()

        # Supprimer le fichier
        path = self._conv_path(conv_id)
        if os.path.exists(path):
            try:
                os.remove(path)
            except IOError:
                pass

        # Si c'était la conversation active, reset
        if self._current_id == conv_id:
            self._current_id = None

        return True

    # ─── Privé ───────────────────────────────────────────────────────

    def _save_conversation(self, conv_id: str, data: Dict[str, Any]) -> None:
        try:
            with open(self._conv_path(conv_id), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            self.io.error(f"Erreur sauvegarde conversation {conv_id}: {e}")
