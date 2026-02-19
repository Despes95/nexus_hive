# -*- coding: utf-8 -*-
"""
Gestionnaire du serveur vision (llama-server auto-géré).

Cycle de vie complet :
  1. Vérifie que les fichiers existent (exe, model, mmproj)
  2. Décharge les modèles Ollama de la VRAM (pour libérer de la place)
  3. Lance llama-server en arrière-plan
  4. Attend que le serveur soit prêt (health check)
  5. Fait l'appel vision via l'API OpenAI-compatible
  6. Tue le serveur et libère la VRAM

Tout est transparent pour l'utilisateur : il glisse une image,
le système fait le reste automatiquement.

Importé par : agents (run_single_agent)
Dépend de : config, io_manager, utils (strip_think_tags)
"""

from __future__ import annotations

import os
import time
import json
import subprocess
import requests
from typing import List, Dict, Any, Tuple

from nexus_hive.config import Config, AgentError, OllamaTimeoutError
from nexus_hive.io_manager import OutputManager
from nexus_hive.utils import strip_think_tags


class VisionServerManager:
    """
    Gère le lancement automatique de llama-server pour les tâches vision.
    
    Cycle de vie :
        start() → wait_ready() → [appels API] → stop()
    
    Usage :
        with VisionServerManager(config, io) as mgr:
            response = mgr.call_vision(messages, images)
    """

    def __init__(self, config: Config, io: OutputManager):
        self.config = config
        self.io = io
        self.process = None  # subprocess.Popen du llama-server
        self._ready = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False  # Ne pas avaler les exceptions

    def _check_files(self) -> bool:
        """Vérifie que tous les fichiers nécessaires existent."""
        checks = [
            (self.config.llama_server_exe, "llama-server.exe"),
            (self.config.vision_model, "modèle vision GGUF"),
            (self.config.vision_mmproj, "projecteur multimodal (mmproj)"),
        ]
        for path, label in checks:
            if not os.path.exists(path):
                self.io.error(f"Fichier manquant ({label}): {path}")
                return False
        return True

    def _unload_ollama_models(self) -> None:
        """
        Demande à Ollama de libérer TOUS les modèles de la VRAM.
        
        C'est nécessaire car la RTX 3060 (12GB) ne peut pas avoir
        un modèle Ollama ET llama-server chargés en même temps.
        """
        self.io.debug("Déchargement des modèles Ollama pour libérer la VRAM...")
        try:
            resp = requests.get(
                f"{self.config.ollama_url}/api/ps",
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                for model_info in models:
                    model_name = model_info.get("name", "")
                    if model_name:
                        self.io.debug(f"Déchargement: {model_name}")
                        try:
                            requests.post(
                                f"{self.config.ollama_url}/api/generate",
                                json={"model": model_name, "keep_alive": 0, "prompt": ""},
                                timeout=8
                            )
                        except Exception:
                            pass
                if models:
                    time.sleep(1.5)
                    self.io.debug(f"{len(models)} modèle(s) déchargé(s)")
        except Exception as e:
            self.io.debug(f"Pas de modèles Ollama à décharger: {e}")

    def start(self) -> bool:
        """Lance llama-server en arrière-plan et attend qu'il soit prêt."""
        if not self._check_files():
            return False

        # Étape 1 : Libérer la VRAM
        self._unload_ollama_models()

        # Étape 2 : Lancer llama-server
        cmd = [
            self.config.llama_server_exe,
            "-m", self.config.vision_model,
            "--mmproj", self.config.vision_mmproj,
            "--port", str(self.config.vision_port),
            "--n-gpu-layers", "-1",
            "--ctx-size", str(self.config.vision_ctx_size),
            "--no-mmap",
            "--flash-attn", "on",
            "-ctk", "q8_0",
            "-ctv", "q8_0",
        ]

        self.io.info("Lancement du serveur vision...")
        self.io.debug(f"Commande: {' '.join(cmd)}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self.io.debug(f"llama-server lancé (PID: {self.process.pid})")
        except FileNotFoundError:
            self.io.error(f"llama-server introuvable: {self.config.llama_server_exe}")
            return False
        except Exception as e:
            self.io.error(f"Erreur lancement llama-server: {e}")
            return False

        # Étape 3 : Attendre que le serveur soit prêt
        return self._wait_ready()

    def _wait_ready(self, timeout: int = 60, interval: float = 1.0) -> bool:
        """Attend que llama-server réponde au health check."""
        start = time.time()
        health_url = f"{self.config.vision_url}/health"
        attempt = 0

        while time.time() - start < timeout:
            attempt += 1

            # Vérifier que le process n'est pas mort
            if self.process and self.process.poll() is not None:
                stderr_output = ""
                try:
                    stderr_output = self.process.stderr.read().decode("utf-8", errors="replace")[-500:]
                except Exception:
                    pass
                self.io.error(f"llama-server a crashé (code: {self.process.returncode})")
                if stderr_output:
                    self.io.error(f"Dernières lignes: {stderr_output}")
                return False

            try:
                resp = requests.get(health_url, timeout=2)
                if resp.status_code == 200:
                    elapsed = round(time.time() - start, 1)
                    self.io.success(f"Serveur vision prêt ({elapsed}s, {attempt} tentatives)")
                    self._ready = True
                    return True
            except requests.exceptions.ConnectionError:
                pass
            except Exception:
                pass

            if attempt % 5 == 0:
                self.io.info(f"Chargement du modèle vision... ({int(time.time() - start)}s)")

            time.sleep(interval)

        self.io.error(f"Timeout: llama-server pas prêt après {timeout}s")
        self.stop()
        return False

    def stop(self) -> None:
        """Tue le process llama-server et libère la VRAM."""
        if self.process:
            self.io.debug("Arrêt du serveur vision...")
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
                self.io.debug(f"Serveur vision arrêté (PID: {self.process.pid})")
            except Exception as e:
                self.io.debug(f"Erreur arrêt llama-server: {e}")
                try:
                    self.process.kill()
                except Exception:
                    pass
            finally:
                self.process = None
                self._ready = False

            time.sleep(0.5)

    def call_vision(
        self, messages: List[Dict], images: List[str],
        temperature: float = 0.7, max_tokens: int = 2048
    ) -> Tuple[str, float, Dict[str, Any]]:
        """
        Fait un appel vision au llama-server déjà lancé.
        
        Args:
            messages: Messages de contexte [{"role": "...", "content": "..."}]
            images: Liste de chaînes base64 brutes (sans préfixe data:)
            temperature: Créativité du modèle
            max_tokens: Limite de tokens en sortie
        
        Returns:
            Tuple (réponse_texte, durée_secondes, stats_dict)
        """
        if not self._ready:
            raise AgentError("Serveur vision non prêt")

        # Construction du message avec images au format OpenAI
        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        # Transformer le dernier message pour inclure les images
        if api_messages and images:
            last_msg = api_messages[-1]
            text_content = last_msg.get("content", "")

            multimodal_content = []

            for img_b64 in images:
                # llama-server attend le préfixe data: (contrairement à Ollama)
                if not img_b64.startswith("data:"):
                    img_url = f"data:image/png;base64,{img_b64}"
                else:
                    img_url = img_b64

                multimodal_content.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })

            multimodal_content.append({
                "type": "text",
                "text": text_content
            })

            last_msg["content"] = multimodal_content

        # Appel API avec streaming SSE
        payload = {
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        full_response = ""
        start = time.time()
        stats: Dict[str, Any] = {}
        http_response = None

        try:
            endpoint = f"{self.config.vision_url}/v1/chat/completions"
            self.io.debug(f"Appel Vision: {endpoint}")

            http_response = requests.post(
                endpoint, json=payload,
                timeout=self.config.timeout, stream=True,
                headers={"Content-Type": "application/json"}
            )
            http_response.raise_for_status()

            in_think = False

            for line in http_response.iter_lines():
                if not line:
                    continue

                line_str = line.decode("utf-8") if isinstance(line, bytes) else line

                if not line_str.startswith("data: "):
                    continue

                data_str = line_str[6:]

                if data_str.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
                content = delta.get("content", "")

                if not content:
                    continue

                full_response += content

                # Gestion des tags <think> (Qwen3)
                if "</think>" in full_response or "</thinking>" in full_response:
                    in_think = False
                    full_response = strip_think_tags(full_response)
                    continue
                if "<think>" in full_response or "<thinking>" in full_response:
                    if not in_think:
                        in_think = True

                if not in_think:
                    self.io.stream(content, agent="vision")

                # Stats d'usage
                usage = chunk.get("usage")
                if usage:
                    stats["tokens"] = usage.get("completion_tokens", 0)
                    stats["prompt_tokens"] = usage.get("prompt_tokens", 0)

            elapsed = round(time.time() - start, 1)
            result = strip_think_tags(full_response)

            if stats.get("tokens") and elapsed > 0:
                stats["tok_s"] = round(stats["tokens"] / elapsed, 1)
            elif elapsed > 0 and len(result) > 0:
                approx_tokens = len(result.split())
                stats["tok_s"] = round(approx_tokens / elapsed, 1)
                stats["tokens"] = approx_tokens

            stats["total_s"] = elapsed

            self.io.debug(f"Vision réponse: {len(result)} chars, {elapsed}s")
            return result, elapsed, stats

        except requests.exceptions.ConnectionError:
            self.io.error(f"Serveur vision non accessible: {self.config.vision_url}")
            raise AgentError(f"Serveur vision non accessible")
        except requests.exceptions.Timeout:
            self.io.error(f"Timeout vision après {self.config.timeout}s")
            raise OllamaTimeoutError("Timeout vision")
        except Exception as e:
            self.io.error(f"Erreur vision: {type(e).__name__}: {str(e)}")
            raise AgentError(f"Erreur vision: {str(e)}")
        finally:
            if http_response:
                try:
                    http_response.close()
                except Exception:
                    pass