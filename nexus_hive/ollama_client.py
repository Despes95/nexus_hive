# -*- coding: utf-8 -*-
"""
Client Ollama pour les appels LLM en streaming.

Contient :
- call_ollama_stream : appel streaming /api/chat avec gestion boucles et <think>
- unload_model : libération VRAM d'un modèle

Importé par : agents, vision, router (futur)
Dépend de : config, io_manager, utils, memory (LoopDetector)
"""

from __future__ import annotations

import re
import json
import time
import requests
from typing import Optional, List, Dict, Any, Tuple

from nexus_hive.config import (
    Config,
    OllamaTimeoutError,
    OllamaConnectionError,
    AgentError,
)
from nexus_hive.io_manager import OutputManager
from nexus_hive.memory import LoopDetector
from nexus_hive.utils import (
    strip_think_tags,
    detect_conversation_chain,
    sanitize_base64_image,
)


def call_ollama_stream(
    model_name: str,
    messages: List[Dict[str, str]],
    config: Config,
    io: OutputManager,
    temperature: float = 0.7,
    images: Optional[List[str]] = None,
    max_tokens: Optional[int] = None,
    is_creative: bool = False,
    loop_detector: Optional[LoopDetector] = None,
    agent_name: Optional[str] = None,
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

        # ══════════════════════════════════════════════════════════════════
    # DÉSACTIVER LE MODE THINKING DE QWEN3
    # Qwen3 entre en mode <think> par défaut, ce qui cause des boucles
    # sur les questions simples. On ajoute /no_think au prompt système.
    # ══════════════════════════════════════════════════════════════════
    if messages and messages[0].get("role") == "system":
        if "/no_think" not in messages[0]["content"]:
            messages[0]["content"] = "/no_think\n" + messages[0]["content"]

    # Séquences d'arrêt pour éviter que le modèle "joue" les deux rôles
    stop_sequences = [
        "user\n",
        "User:",
        "user:",
        "Human:",
        "Assistant:",
        "\nuser\n",
        "\nassistant\n",
        "\nUser\n",
        "\nAssistant\n",
    ]

    # Construction du payload pour l'API Ollama /api/chat
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "keep_alive": 0,  # Libère le modèle de la VRAM après chaque appel
        "options": {"num_predict": max_tokens, "stop": stop_sequences},
    }

    # ══════════════════════════════════════════════════════════════════════
    # GESTION DES IMAGES (modèles vision)
    # ══════════════════════════════════════════════════════════════════════
    # Ollama attend les images en base64 BRUT dans le dernier message
    # Format : messages[-1]["images"] = ["iVBORw0KGgo...", ...]
    # IMPORTANT : PAS de préfixe "data:image/...;base64,"
    if images:
        clean_images = []
        for img_b64 in images:
            clean = sanitize_base64_image(img_b64)
            if len(clean) < 100:
                io.warning(f"Image base64 suspecte (trop courte: {len(clean)} chars)")
                continue
            clean_images.append(clean)

        if clean_images:
            payload["messages"][-1]["images"] = clean_images
            io.debug(
                f"Images attachées: {len(clean_images)} (tailles: {[len(i)//1024 for i in clean_images]}KB)"
            )
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
            f"{config.ollama_url}/api/chat",
            json=payload,
            timeout=config.timeout,
            stream=True,
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

            # Gestion des blocs <think>/<thinking> de Qwen3
            if "</thinking>" in full_response or "</think>" in full_response:
                in_think = False
                full_response = strip_think_tags(full_response)
                continue
            if "<thinking>" in full_response or "<think>" in full_response:
                if not in_think:
                    in_think = True

            # Détection de boucle (SKIP pendant <think>)
            if loop_detector and not loop_detected:
                if not in_think and loop_detector.detect(full_response):
                    loop_detected = True
                    io.warning("Boucle détectée - arrêt automatique")
                    if http_response:
                        http_response.close()
                    break

            # Détection de chaîne conversationnelle parasite
            if is_creative and not conversation_detected:
                if detect_conversation_chain(full_response):
                    conversation_detected = True
                    io.warning("Chaîne conversationnelle détectée - arrêt")
                    if http_response:
                        http_response.close()
                    break

            if not in_think:
                io.stream(content, agent=agent_name or model_name)

        elapsed = round(time.time() - start, 1)
        result = strip_think_tags(full_response)

        # Nettoyage si boucle ou chaîne détectée
        if loop_detected or conversation_detected:
            window = config.loop_detect_window
            if len(result) > window * 2:
                for pattern in [r"\nuser\s*\n", r"\nassistant\s*\n"]:
                    match = re.search(pattern, result, re.IGNORECASE)
                    if match:
                        result = result[: match.start()]
                        break
                else:
                    tail = result[-window:]
                    first_occurrence = result.find(tail)
                    if (
                        first_occurrence >= 0
                        and first_occurrence < len(result) - window
                    ):
                        result = result[: first_occurrence + window]
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
                timeout=8,
            )
            time.sleep(0.4)
            break
        except Exception:
            pass
