# -*- coding: utf-8 -*-
"""
Fonctions utilitaires partagées.

Contient :
- normalize_text : normalisation de texte pour comparaisons
- strip_think_tags : suppression des blocs <think>...</think>
- extract_code_blocks : extraction des blocs de code markdown
- save_code_to_file : sauvegarde automatique du code généré
- detect_conversation_chain : détection de chaînes user/assistant parasites
- get_gpu_info : récupération des infos GPU via nvidia-smi
- encode_image_to_base64 : lecture d'une image en base64
- sanitize_base64_image : nettoyage du base64 pour Ollama

Importé par : ollama_client, agents, memory, router, main
Dépend de : config (Config), io_manager (OutputManager)
"""

from __future__ import annotations

import os
import re
import base64
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Any

from nexus_hive.config import Config
from nexus_hive.io_manager import OutputManager


def normalize_text(text: str) -> str:
    """Normalise un texte pour les comparaisons (minuscules, pas de ponctuation)."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def strip_think_tags(text: str) -> str:
    """Supprime les blocs <think>...</think> et <thinking>...</thinking> du texte."""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


def extract_code_blocks(text: str) -> List[str]:
    """Extrait tous les blocs de code markdown (```...```) du texte."""
    return re.findall(r"```(?:python|bash|)?\s*\n?(.*?)\s*```",
                     text, re.DOTALL | re.IGNORECASE)


def save_code_to_file(code_blocks: List[str], agent_name: str,
                      user_prompt: str = "", config: Config = None,
                      io: OutputManager = None) -> List[str]:
    """
    Sauvegarde les blocs de code extraits dans des fichiers .py.
    
    Args:
        code_blocks: Liste de blocs de code à sauvegarder
        agent_name: Nom de l'agent qui a produit le code
        user_prompt: Prompt utilisateur (pour le header du fichier)
        config: Configuration (pour le répertoire de sortie)
        io: OutputManager (pour les logs)
    
    Returns:
        Liste des chemins de fichiers sauvegardés
    """
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
    """Détecte si le modèle génère une fausse conversation user/assistant."""
    patterns = [r'\nuser\s*\n', r'\nassistant\s*\n', r'\nUser:\s*', r'\nAssistant:\s*']
    return any(re.search(p, text, re.MULTILINE | re.IGNORECASE) for p in patterns)


def get_gpu_info(config: Config) -> Optional[Dict[str, Any]]:
    """Récupère les infos GPU via nvidia-smi (VRAM, utilisation, température)."""
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
    if b64_string.startswith("data:"):
        comma_idx = b64_string.find(",")
        if comma_idx != -1:
            b64_string = b64_string[comma_idx + 1:]

    # Nettoyer les espaces/retours à la ligne
    b64_string = b64_string.strip().replace("\n", "").replace("\r", "").replace(" ", "")

    return b64_string