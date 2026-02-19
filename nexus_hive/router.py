# -*- coding: utf-8 -*-
"""
Routing intelligent des requêtes vers les agents.

Trois niveaux de routing :
1. Direct : images → vision, /search → searcher (pas besoin d'IA)
2. Mots-clés : détection rapide par keywords (code, sécu, créatif, etc.)
3. IA : appel qwen2.5:7b-instruct pour les cas ambigus (~1-2s)

Le routing est cachéable via LRUCache pour éviter les appels répétés.

Importé par : main
Dépend de : config, io_manager, memory (LRUCache, MemoryStore), utils, ollama_client
"""

from __future__ import annotations

import hashlib
from typing import Optional

from nexus_hive.config import Config
from nexus_hive.io_manager import OutputManager
from nexus_hive.memory import LRUCache, MemoryStore
from nexus_hive.utils import normalize_text
from nexus_hive.ollama_client import call_ollama_stream, unload_model


# ═══════════════════════════════════════════════════════════════════
# Prompt système du routeur qwen2.5
# Il reçoit la liste complète des agents et pipelines,
# comprend la question, et retourne UN seul nom.
# ═══════════════════════════════════════════════════════════════════
ROUTER_SYSTEM_PROMPT = """Tu es un routeur intelligent. Ta SEULE tâche est de choisir le meilleur agent ou pipeline pour répondre à la demande de l'utilisateur.

AGENTS DISPONIBLES :
- senior : Développeur expert, code complexe, architecture, algorithmes
- junior : Développeur full-stack, scripts simples, code rapide
- stratege : Analyste technique, analyse approfondie, comparaisons, raisonnement complexe
- eclaireur : Synthèse rapide, résumés, questions générales simples, maths
- hacker : Sécurité informatique, audit, vulnérabilités, pentest
- ecrivain : Écriture créative, histoires, fiction, poèmes
- veilleur : Recherche web, actualités, informations récentes
- medecin : Diagnostic de bugs, erreurs, tracebacks
- prof : Pédagogie, explication, apprentissage, vulgarisation
- vision : Analyse d'images et screenshots
- nolimit : Questions sans filtre, contenu non censuré
- savant : Expert scientifique, physique, chimie, biologie, maths avancées
- historien : Histoire, géopolitique, contexte historique

PIPELINES DISPONIBLES :
- code_review : Analyse de code puis améliorations
- code_secure : Écriture de code puis audit sécurité
- story_polish : Écriture créative puis peaufinage
- exploit_code : Analyse système puis exploits/PoC
- analyze_code : Analyse profonde puis synthèse
- learning_guide : Contenu technique puis parcours pédagogique
- debug_fix : Diagnostic erreur puis code corrigé
- full_stack : Backend puis frontend
- doc_code : Code puis documentation complète
- refactor : Analyse architecture puis refactoring
- phoenix : Analyse puis modernisation complète
- shark_tank : Pitch idée puis débat et verdict
- research_deep : Recherche web puis analyse approfondie
- audit_full : Audit sécurité complet puis diagnostic
- optimize_legacy : Analyse legacy puis modernisation
- clone_ui : Screenshot interface puis reproduction en code

RÈGLES :
- Réponds UNIQUEMENT avec le nom de l'agent ou du pipeline, RIEN d'autre
- Pas d'explication, pas de phrase, juste le nom
- Pour les questions simples ou générales → eclaireur
- Pour le code simple (scripts, hello world, petits programmes) → junior
- Pour le code complexe (architecture, multi-fichiers, design patterns) → senior
- Pour les questions de sécurité → hacker
- Pour l'écriture créative → ecrivain
- Pour les questions sur l'actualité ou des faits récents → veilleur
- Pour les erreurs et bugs → medecin
- Pour les explications pédagogiques → prof
- Pour les questions scientifiques → savant
- Pour l'histoire et la géopolitique → historien
- Pour les questions sans filtre → nolimit
- En cas de doute entre agent simple et pipeline → choisis l'agent simple"""


class Router:
    """
    Routeur intelligent qui dirige les requêtes vers le bon agent ou pipeline.

    Ordre de priorité :
    1. Cache LRU (requête déjà vue)
    2. Mots-clés évidents (détection rapide, sans appel IA)
    3. qwen2.5:7b-instruct (routing intelligent, ~1-2s)
    """

    # Modèle utilisé pour le routing IA
    ROUTER_MODEL = "qwen2.5:7b-instruct"

    def __init__(self, config: Config, cache: LRUCache, io: OutputManager):
        self.config = config
        self.cache = cache
        self.io = io
        # Liste des noms valides pour valider la réponse du routeur
        self._valid_routes = set(config.models.keys()) | set(config.pipelines.keys())

    def get_route(self, user_query: str, memory: MemoryStore) -> str:
        """Détermine l'agent ou pipeline approprié pour la requête."""
        cache_key = self._get_cache_key(user_query)
        if cache_key in self.cache:
            cached = self.cache.get(cache_key)
            self.io.debug(f"Routing cache: {cached}")
            return cached

        # Essayer les mots-clés d'abord (instantané, pas d'appel réseau)
        route = self._keyword_routing(user_query)
        if route:
            self.io.debug(f"Routing keywords: {route}")
            self.cache.set(cache_key, route)
            return route

        # Sinon, appel qwen2.5 pour décider (~1-2s)
        route = self._ai_routing(user_query)
        self.cache.set(cache_key, route)
        return route

    def _get_cache_key(self, query: str) -> str:
        """Génère une clé de cache normalisée pour la requête."""
        norm = normalize_text(query)[:200]
        return hashlib.md5(norm.encode()).hexdigest()[:10]

    def _keyword_routing(self, query: str) -> Optional[str]:
        """Désactivé — tout passe par qwen2.5."""
        return None

    def _ai_routing(self, query: str) -> str:
        """
        Routing par qwen2.5:7b-instruct.

        Rapide (~1-2s), léger, comprend bien les instructions.
        Retourne le nom d'un agent ou pipeline valide.
        Fallback sur analyst2 si le modèle répond n'importe quoi.
        """
        self.io.debug("Routing IA (qwen2.5)...")

        messages = [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        try:
            # IO muet : le routeur ne doit RIEN émettre sur stdout
            from nexus_hive.io_manager import OutputManager

            silent_io = OutputManager(headless=self.io.headless, debug=False)
            silent_io.emit = lambda *args, **kwargs: None  # Muet total
            silent_io.stream = lambda *args, **kwargs: None
            silent_io.info = lambda *args, **kwargs: None
            silent_io.debug = lambda *args, **kwargs: None
            silent_io.warning = lambda *args, **kwargs: None
            silent_io.error = lambda *args, **kwargs: None
            silent_io.agent_msg = lambda *args, **kwargs: None
            silent_io.done = lambda *args, **kwargs: None

            response, elapsed, stats = call_ollama_stream(
                self.ROUTER_MODEL,
                messages,
                self.config,
                silent_io,
                temperature=0.1,
                max_tokens=20,
            )

            # Nettoyer la réponse : le modèle doit retourner juste un nom
            route = response.strip().lower().replace('"', "").replace("'", "")
            # Prendre le premier mot si le modèle a bavardé
            route = route.split("\n")[0].split()[0] if route else ""

            # Décharger le routeur de la VRAM immédiatement
            unload_model(self.ROUTER_MODEL, self.config, self.io)

            if route in self._valid_routes:
                self.io.debug(f"Routing IA: {route} ({elapsed}s)")
                return route
            else:
                self.io.warning(f"Routing IA invalide: '{route}' → fallback eclaireur")
                return "eclaireur"

        except Exception as e:
            self.io.warning(f"Routing IA erreur: {e} → fallback eclaireur")
            return "eclaireur"
