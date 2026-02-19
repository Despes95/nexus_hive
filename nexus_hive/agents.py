# -*- coding: utf-8 -*-
"""
Exécution des agents et pipelines.

Contient :
- run_search_agent : agent recherche web (DuckDuckGo + synthèse LLM)
- run_single_agent : exécution d'un agent unique (avec auto-sélection complexité)
- run_pipeline : exécution d'un pipeline multi-agents (2 étapes)

Importé par : main
Dépend de : config, io_manager, memory, utils, ollama_client, vision, web_search
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Tuple

from nexus_hive.config import Config
from nexus_hive.io_manager import OutputManager
from nexus_hive.memory import MemoryStore, LoopDetector
from nexus_hive.utils import normalize_text, extract_code_blocks, save_code_to_file
from nexus_hive.ollama_client import call_ollama_stream, unload_model
from nexus_hive.vision import VisionServerManager
from nexus_hive.web_search import web_search


def run_search_agent(
    user_input: str,
    memory: MemoryStore,
    config: Config,
    io: OutputManager,
    use_advanced_synth: bool = False,
    fetch_pages: bool = False,
) -> Tuple[str, float, Dict, str]:
    """
    Agent recherche web : cherche sur DuckDuckGo et synthétise les résultats.

    Args:
        user_input: Requête de recherche
        memory: Mémoire de conversation
        config: Configuration
        io: OutputManager
        use_advanced_synth: Si True, utilise searcher_synth (modèle plus puissant)
        fetch_pages: Si True, récupère le contenu complet des pages

    Returns:
        Tuple (réponse, durée, stats, "searcher")
    """
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

    # Construction du contexte de recherche
    search_ctx = "Résultats web:\n\n"
    for i, r in enumerate(results):
        search_ctx += (
            f"{i+1}. {r['title']}\n   URL: {r['url']}\n   Résumé: {r['body']}\n"
        )
        if fetch_pages and "full_content" in r:
            content = r["full_content"][:2000]
            search_ctx += f"   Contenu: {content}\n"
        search_ctx += "\n"

    context_messages = memory.get_context_messages()

    synth_model = (
        config.models["veilleur_synth"]
        if use_advanced_synth
        else config.models["veilleur"]
    )
    synth_prompt = config.agent_prompts.get(
        "veilleur", "Synthétise ces résultats web en français. Cite tes sources."
    )

    messages = [{"role": "system", "content": synth_prompt}]
    messages.extend(context_messages)

    prompt_suffix = (
        "Synthétise en français avec détails et citations."
        if not fetch_pages
        else "Analyse en profondeur ces articles. Synthétise les points clés avec citations précises."
    )

    messages.append(
        {
            "role": "user",
            "content": search_ctx + "\nQuestion: " + user_input + "\n" + prompt_suffix,
        }
    )

    # Émettre les sources vers l'UI
    io.emit("sources", data=[{"title": r["title"], "url": r["url"]} for r in results])

    max_tokens = config.max_tokens_web_fetch if fetch_pages else config.max_tokens
    response, elapsed, stats = call_ollama_stream(
        synth_model, messages, config, io, max_tokens=max_tokens
    )
    unload_model(synth_model, config, io)

    io.success(f"Recherche terminée: {elapsed}s")
    return response, elapsed, stats, "searcher"


def run_single_agent(
    agent: str,
    user_input: str,
    memory: MemoryStore,
    config: Config,
    io: OutputManager,
    images: Optional[List[str]] = None,
    auto_select: bool = True,
    max_tokens: Optional[int] = None,
    original_prompt: str = "",
    loop_detector: LoopDetector = None,
) -> Tuple[str, float, Dict, str]:
    """
    Exécute un agent unique.

    Gère automatiquement :
    - La sélection de complexité (coder/coder2, analyst/analyst2)
    - Le choix du backend (llama-server pour vision, Ollama pour les autres)
    - La sauvegarde du code généré

    Args:
        agent: Nom de l'agent (clé dans config.models)
        user_input: Message de l'utilisateur
        memory: Mémoire de conversation
        config: Configuration
        io: OutputManager
        images: Images en base64 (pour l'agent vision)
        auto_select: Si True, auto-sélectionne la complexité
        max_tokens: Limite de tokens (override)
        original_prompt: Prompt original (pour les headers de code sauvegardé)
        loop_detector: Détecteur de boucles

    Returns:
        Tuple (réponse, durée, stats, agent_final)
    """
    original_agent = agent  # Garder le nom original avant l'auto-select

    # Auto-select complexité
    cx = "?"
    if auto_select and agent in ["senior", "junior", "stratege", "eclaireur"]:
        tn = normalize_text(user_input)
        score = 0
        strong_kw = [
            "compare",
            "architecture",
            "securise",
            "bcrypt",
            "machine learning",
            "distribue",
        ]
        normal_kw = [
            "optimise",
            "refactor",
            "threading",
            "concurrent",
            "database",
            "api rest",
        ]
        for kw in strong_kw:
            if kw in tn:
                score += 2
        for kw in normal_kw:
            if kw in tn:
                score += 1
        if len(tn.split()) > 30:
            score += 1
        if len(tn.split()) > 60:
            score += 1

        if agent in ["senior", "junior"]:
            cx = "complex" if score >= 2 else "simple"
            agent = "senior" if cx == "complex" else "junior"
        elif agent in ["stratege", "eclaireur"]:
            cx = "complex" if score >= 2 else "simple"
            agent = "stratege" if cx == "complex" else "eclaireur"

    if max_tokens is None:
        max_tokens = config.agent_max_tokens.get(original_agent, config.max_tokens)

    is_creative = agent == "ecrivain"
    if is_creative:
        max_tokens = min(max_tokens, config.max_tokens_creative)
        # Désactiver le loop_detector pour l'agent écrivain
        loop_detector = None
    elif agent in ["nolimit", "savant", "historien"]:
        # Ces agents n'ont pas de loop_detector non plus
        loop_detector = None

    model = config.models.get(agent, agent)
    display_agent = original_agent  # Nom à afficher dans l'UI
    context_messages = memory.get_context_messages()
    messages = [
        {
            "role": "system",
            "content": config.agent_prompts.get(agent, "Assistant. Français."),
        }
    ]
    messages.extend(context_messages)
    messages.append({"role": "user", "content": user_input})

    tag = f" [{cx}]" if cx != "?" else ""
    io.agent_msg(original_agent.upper(), f"Répond{tag}...")

    # ═══════════════════════════════════════════════════════════════
    # CHOIX DU BACKEND : llama-server (vision) ou Ollama (autres)
    # ═══════════════════════════════════════════════════════════════
    if agent == "vision" and images and config.vision_url:
        # AUTO-GESTION DU SERVEUR VISION
        io.info("Préparation du serveur vision (auto)...")

        with VisionServerManager(config, io) as vision_mgr:
            response, elapsed, stats = vision_mgr.call_vision(
                messages, images=images, temperature=0.7, max_tokens=max_tokens
            )
        io.info("Serveur vision arrêté, VRAM libérée")
    else:
        # Ollama pour tous les autres agents
        response, elapsed, stats = call_ollama_stream(
            model,
            messages,
            config,
            io,
            images=images,
            max_tokens=max_tokens,
            is_creative=is_creative,
            loop_detector=loop_detector,
            agent_name=display_agent,
        )
        unload_model(model, config, io)

    # Sauvegarder le code généré
    code_blocks = extract_code_blocks(response)
    saved = save_code_to_file(
        code_blocks, agent, original_prompt or user_input[:100], config, io
    )
    if saved:
        io.debug(f"Code sauvegardé: {len(saved)} fichier(s)")

    io.done(stats)
    return response, elapsed, stats, agent


def run_pipeline(
    pipeline_name: str,
    user_input: str,
    memory: MemoryStore,
    config: Config,
    io: OutputManager,
    loop_detector: LoopDetector,
) -> Tuple[str, float, Dict, str]:
    """
    Exécute un pipeline multi-agents (2 étapes).

    Étape 1 : agent1 analyse/produit du contenu
    Étape 2 : agent2 améliore/complète/corrige

    Args:
        pipeline_name: Nom du pipeline (clé dans config.pipelines)
        user_input: Message de l'utilisateur
        memory: Mémoire de conversation
        config: Configuration
        io: OutputManager
        loop_detector: Détecteur de boucles

    Returns:
        Tuple (réponse_finale, durée_totale, stats, agent_final)
    """
    io.pipeline_event("start", pipeline_name)

    a1, a2 = config.pipelines.get(pipeline_name, ("analyst", "coder"))

    io.info(f"=== PIPELINE: {pipeline_name.upper()} ===")
    io.info(f"Étape 1: {a1.upper()} -> Étape 2: {a2.upper()}")

    # ═══════════════════════════════════════════════════════════════
    # ÉTAPE 1
    # ═══════════════════════════════════════════════════════════════
    io.pipeline_event("step", pipeline_name, step=1, agent=a1)
    tokens_step1 = config.max_tokens_pipeline
    if pipeline_name in ["learning_guide", "debug_fix", "phoenix", "shark_tank"]:
        tokens_step1 = config.max_tokens_learning

    r1, e1, s1, aa1 = run_single_agent(
        a1,
        user_input,
        memory,
        config,
        io,
        max_tokens=tokens_step1,
        original_prompt=user_input,
        loop_detector=None,  # Désactivé pour pipelines
    )

    memory.add_assistant_message(
        r1,
        aa1,
        config.models.get(aa1, a1),
        e1,
        pipeline=pipeline_name,
        step=1,
        intermediate=True,
    )

    # ═══════════════════════════════════════════════════════════════
    # ÉTAPE 2 - Prompts spécifiques par pipeline
    # ═══════════════════════════════════════════════════════════════
    io.pipeline_event("step", pipeline_name, step=2, agent=a2)

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
        a2,
        a2_input,
        memory,
        config,
        io,
        max_tokens=tokens_step2,
        original_prompt=user_input,
        loop_detector=None,  # Désactivé pour pipelines
    )

    memory.add_assistant_message(
        r2,
        aa2,
        config.models.get(aa2, a2),
        e2,
        pipeline=pipeline_name,
        step=2,
        intermediate=False,
    )

    total = round(e1 + e2, 1)
    io.success(f"=== FIN PIPELINE {pipeline_name.upper()} ({total}s) ===")
    io.pipeline_event("done", pipeline_name, duration=total)

    return r2, total, s2, aa2
