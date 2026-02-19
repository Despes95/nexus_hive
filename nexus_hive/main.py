#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Point d'entrée de Nexus Hive.

Boucle principale qui :
1. Lit les messages sur stdin (JSON ou texte brut)
2. Parse les commandes système (/reset, /gpu, /models, etc.)
3. Route les requêtes vers le bon agent/pipeline
4. Gère les fichiers et images attachés

Usage :
    python -m nexus_hive --headless          # Mode Electron (stdin/stdout JSON)
    python -m nexus_hive                     # Mode CLI (terminal)
    python -m nexus_hive --headless --debug  # Mode Electron + logs debug sur stderr

Importé par : __main__.py (ou lancé directement)
Dépend de : TOUS les autres modules
"""

from __future__ import annotations

import os
import sys
import json
import time
import base64

from nexus_hive.config import Config
from nexus_hive.io_manager import OutputManager
from nexus_hive.memory import LRUCache, LoopDetector, MemoryStore, ConversationManager
from nexus_hive.router import Router
from nexus_hive.agents import run_single_agent, run_pipeline, run_search_agent
from nexus_hive.utils import get_gpu_info, sanitize_base64_image


def main():
    config = Config.from_args(sys.argv[1:])
    io = OutputManager(headless=config.headless, debug=config.debug)

    if not config.headless:
        io.emit("info", data="=" * 60)
        io.emit("info", data="NEXUS HIVE v8.0 - Multi-Agents System")
        io.emit("info", data="=" * 60)
        io.emit("info", data=f"Dossier: {config.base_dir}")
        io.emit("info", data=f"Ollama: {config.ollama_url}")
        io.emit("info", data=f"Mode: {'HEADLESS' if config.headless else 'CLI'}")
        io.emit("info", data="")

    # Initialisation des composants
    cache = LRUCache(max_size=config.route_cache_max)
    memory = MemoryStore(config, io)
    conv_manager = ConversationManager(config, io)
    router = Router(config, cache, io)
    loop_detector = LoopDetector(config)

    # Créer ou reprendre la dernière conversation
    convs = conv_manager.list_conversations(limit=1)
    if convs:
        conv_manager.switch_conversation(convs[0]["id"], memory)
    else:
        conv_manager.new_conversation()

    # Émettre l'état ready
    io.emit(
        "ready",
        data={
            "version": "1.0",
            "headless": config.headless,
            "models": list(config.models.keys()),
            "pipelines": list(config.pipelines.keys()),
            "conversations": conv_manager.list_conversations(),
        },
    )

    io.success("Système prêt!")

    current_agent = None
    session_stats = []

    while True:
        try:
            raw_input_str = io.read_input("")

            if not raw_input_str:
                continue

            # ═══════════════════════════════════════════════════════════════
            # PARSING DE L'INPUT (protocole stdin unifié)
            # ═══════════════════════════════════════════════════════════════
            # FORMAT 1 - JSON structuré (Electron) :
            #   {"type": "input", "text": "ma question"}
            #   {"type": "command", "command": "/reset"}
            #   {"type": "input_with_files", "text": "...", "files": [...]}
            #
            # FORMAT 2 - Texte brut (mode CLI) :
            #   "ma question" ou "/reset"
            # ═══════════════════════════════════════════════════════════════

            user_input = raw_input_str
            attached_files = None
            images_b64 = None
            # Mode sélecteur par défaut
            input_mode = "auto"
            selected_agent = None
            selected_pipeline = None

            try:
                parsed = json.loads(raw_input_str)
                if isinstance(parsed, dict):
                    msg_type = parsed.get("type") or parsed.get("__type", "")

                    if msg_type == "command":
                        user_input = parsed.get("command", "").strip()

                    elif msg_type == "input":
                        user_input = parsed.get("text", "").strip()
                        # Mode sélecteur (auto/agent/pipeline)
                        input_mode = parsed.get("mode", "auto")
                        selected_agent = parsed.get("selected_agent")
                        selected_pipeline = parsed.get("selected_pipeline")

                    elif msg_type == "input_with_files":
                        user_input = parsed.get("text", "").strip()
                        attached_files = parsed.get("files", [])
                        # Mode sélecteur (auto/agent/pipeline)
                        input_mode = parsed.get("mode", "auto")
                        selected_agent = parsed.get("selected_agent")
                        selected_pipeline = parsed.get("selected_pipeline")

                        # Extraction des images et fichiers texte
                        images_b64 = []
                        file_descriptions = []

                        for f in attached_files:
                            if f.get("isImage") and f.get("base64"):
                                # IMAGE : nettoyer le base64
                                clean_b64 = sanitize_base64_image(f["base64"])
                                images_b64.append(clean_b64)
                                file_descriptions.append(f"[Image: {f['name']}]")
                                io.debug(
                                    f"Image nettoyée: {f['name']} ({len(clean_b64)//1024}KB b64)"
                                )
                            else:
                                # FICHIER TEXTE : décoder et injecter dans le prompt
                                try:
                                    raw_b64 = f.get("base64", "")
                                    if raw_b64.startswith("data:"):
                                        comma_idx = raw_b64.find(",")
                                        if comma_idx != -1:
                                            raw_b64 = raw_b64[comma_idx + 1 :]
                                    content = base64.b64decode(raw_b64).decode(
                                        "utf-8", errors="replace"
                                    )
                                    file_descriptions.append(
                                        f"[Fichier: {f['name']}]\n```\n{content[:3000]}\n```"
                                    )
                                except Exception as e:
                                    file_descriptions.append(
                                        f"[Fichier binaire: {f['name']} ({f.get('size', 0)} bytes)]"
                                    )
                                    io.debug(
                                        f"Fichier non-décodable: {f['name']} - {e}"
                                    )

                        # Prompt par défaut si l'utilisateur n'a rien écrit
                        if not user_input:
                            if images_b64:
                                user_input = (
                                    "Décris en détail ce que tu vois dans cette image."
                                )
                            else:
                                user_input = "Analyse le contenu de ce fichier."

                        # Ajouter les descriptions de fichiers au prompt
                        if file_descriptions:
                            user_input = (
                                user_input + "\n\n" + "\n".join(file_descriptions)
                            )

                        if not images_b64:
                            images_b64 = None

                        io.debug(
                            f"Input avec fichiers: {len(attached_files)} fichier(s), "
                            f"{len(images_b64 or [])} image(s)"
                        )

                    else:
                        user_input = raw_input_str

            except (json.JSONDecodeError, ValueError):
                user_input = raw_input_str

            if not user_input:
                continue

            # ═══════════════════════════════════════════════════════════════
            # COMMANDES SYSTÈME
            # ═══════════════════════════════════════════════════════════════

            if user_input.lower() in ["exit", "quit", "bye"]:
                io.emit("info", data="Au revoir!")
                break

            cmd = user_input.lower().strip()

            if cmd == "/reset":
                memory.clear()
                cache.clear()
                current_agent = None
                loop_detector.reset()
                io.success("Reset complet!")
                continue

            if cmd == "/newchat":
                # Sauvegarder la conversation actuelle
                conv_manager.save_memory_to_conversation(memory)
                # Créer une nouvelle conversation
                conv_id = conv_manager.new_conversation()
                memory.clear()
                loop_detector.reset()
                # Envoyer la liste des conversations à l'UI
                io.emit("conversations", conv_manager.list_conversations())
                io.success("Nouvelle conversation!")
                continue

            if cmd.startswith("/loadchat"):
                parts = cmd.split(maxsplit=1)
                if len(parts) < 2:
                    io.warning("Usage: /loadchat <conversation_id>")
                    continue
                conv_id = parts[1].strip()
                if conv_manager.switch_conversation(conv_id, memory):
                    loop_detector.reset()
                    io.emit(
                        "conversation_loaded",
                        {"id": conv_id, "history": memory.memory["history"]},
                    )
                    io.success(f"Conversation chargée: {conv_id}")
                else:
                    io.error(f"Conversation introuvable: {conv_id}")
                continue

            if cmd == "/gpu":
                gpu = get_gpu_info(config)
                if gpu:
                    io.info(
                        f"GPU: {gpu['gpu_name']} | VRAM: {gpu['vram_used_mb']}/{gpu['vram_total_mb']}MB | {gpu['gpu_util']}% | {gpu['temp_c']}°C"
                    )
                    io.emit("gpu_info", data=gpu)
                else:
                    io.warning("GPU non disponible")
                continue

            if cmd == "/models":
                io.info("Modèles disponibles:")
                for name, model in config.models.items():
                    io.info(f"  {name}: {model}")
                continue

            if cmd == "/status":
                io.info("Statut du système:")
                io.info(f"  Mémoire: {len(memory.memory['history'])} entrées")
                io.info(f"  Cache routing: {len(cache)} éléments")
                io.info(
                    f"  Mode conversation: {'ON' if config.conversation_mode else 'OFF'}"
                )
                continue

            if cmd == "/export":
                if session_stats:
                    stats_path = os.path.join(config.base_dir, config.stats_file)
                    with open(stats_path, "w", encoding="utf-8") as f:
                        json.dump(session_stats, f, ensure_ascii=False, indent=2)
                    io.success(
                        f"Stats exportées: {config.stats_file} ({len(session_stats)} entrées)"
                    )
                    io.emit("export", data=session_stats)
                else:
                    io.warning("Aucune stat à exporter")
                continue

            if cmd == "/listchats":
                io.emit("conversations", conv_manager.list_conversations())
                continue

            if cmd.startswith("/deletechat"):
                parts = cmd.split(maxsplit=1)
                if len(parts) >= 2:
                    conv_manager.delete_conversation(parts[1].strip())
                    io.emit("conversations", conv_manager.list_conversations())
                    io.success("Conversation supprimée")
                continue

            if cmd.startswith("/agent "):
                agent_name = user_input[7:].strip().lower()
                if agent_name in config.models or agent_name in config.pipelines:
                    current_agent = agent_name
                    io.success(f"Agent actif: {agent_name}")
                else:
                    io.error(f"Agent inconnu: {agent_name}")
                continue

            if cmd.startswith("/pipeline "):
                parts = user_input[10:].strip().split(maxsplit=1)
                pipeline_name = parts[0].lower()
                prompt = parts[1] if len(parts) > 1 else ""

                if pipeline_name in config.pipelines:
                    memory.add_user_message(user_input)
                    actual_prompt = prompt if prompt else user_input
                    resp, elapsed, stats, agent = run_pipeline(
                        pipeline_name, actual_prompt, memory, config, io, loop_detector
                    )
                    session_stats.append(
                        {"pipeline": pipeline_name, "elapsed": elapsed, "agent": agent}
                    )
                else:
                    io.error(f"Pipeline inconnu: {pipeline_name}")
                continue

            # ═══════════════════════════════════════════════════════════════
            # TRAITEMENT DU MESSAGE UTILISATEUR
            # ═══════════════════════════════════════════════════════════════

            memory.add_user_message(user_input)

            # CAS 1 : Images → agent VISION
            if images_b64:
                route = "vision"
                io.emit("routing", data="vision")
                io.emit("routed", data="vision")
                io.info("→ VISION (image détectée)")

                resp, elapsed, stats, agent = run_single_agent(
                    "vision",
                    user_input,
                    memory,
                    config,
                    io,
                    images=images_b64,
                    original_prompt=user_input,
                    loop_detector=loop_detector,
                )
                memory.add_assistant_message(
                    resp, "vision", config.models["vision"], elapsed
                )
                session_stats.append(
                    {"agent": "vision", "elapsed": elapsed, "stats": stats}
                )

            else:
                # CAS 2 : Texte → routing intelligent ou sélecteur manuel

                # Déterminer la route selon le mode
                route = None

                # Mode Agent : utiliser l'agent sélectionné
                if input_mode == "agent" and selected_agent:
                    route = selected_agent
                    io.info(f"[Mode Agent] → {route.upper()}")

                # Mode Pipeline : utiliser le pipeline sélectionné
                elif input_mode == "pipeline" and selected_pipeline:
                    route = selected_pipeline
                    io.info(f"[Mode Pipeline] → {route.upper()}")

                # Mode Auto : routing intelligent
                else:
                    if config.conversation_mode and current_agent:
                        route = current_agent
                        io.info(f"[Mode Conversation] Agent: {route}")
                    else:
                        io.info("Routing...")
                        route = router.get_route(user_input, memory)
                        if config.conversation_mode:
                            current_agent = route
                        io.info(f"→ {route}")

                io.emit("routing", data=route)
                io.emit("routed", data=route)

                conv_manager.update_conversation_meta(
                    conv_manager.get_current_id(), agent=route
                )

                start_time = time.time()

                if route == "veilleur":
                    io.info("→ VEILLEUR")
                    resp, elapsed, stats, agent = run_search_agent(
                        user_input, memory, config, io
                    )
                    memory.add_assistant_message(
                        resp, "veilleur", config.models["veilleur"], elapsed
                    )
                    session_stats.append(
                        {"agent": "veilleur", "elapsed": elapsed, "stats": stats}
                    )

                elif route in config.pipelines:
                    io.info(f"→ PIPELINE {route.upper()}")
                    resp, elapsed, stats, agent = run_pipeline(
                        route, user_input, memory, config, io, loop_detector
                    )
                    session_stats.append(
                        {"pipeline": route, "elapsed": elapsed, "agent": agent}
                    )

                else:
                    io.info(f"→ {route.upper()}")
                    resp, elapsed, stats, agent = run_single_agent(
                        route,
                        user_input,
                        memory,
                        config,
                        io,
                        images=None,
                        original_prompt=user_input,
                        loop_detector=loop_detector,
                    )
                    memory.add_assistant_message(
                        resp, agent, config.models.get(agent, route), elapsed
                    )
                    session_stats.append(
                        {"agent": agent, "elapsed": elapsed, "stats": stats}
                    )

            memory.save()
            # Sauvegarder dans la conversation active
            conv_manager.save_memory_to_conversation(memory)
            # Titre auto : premier message user
            if len(memory.memory["history"]) <= 2:
                conv_manager.update_conversation_meta(
                    conv_manager.get_current_id(), title=user_input
                )
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
