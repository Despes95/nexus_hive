# -*- coding: utf-8 -*-
"""
Recherche web avec DuckDuckGo et extraction de contenu.

Contient :
- fetch_web_page : récupère et nettoie le contenu d'une URL
- web_search : recherche DuckDuckGo avec option fetch contenu complet

Dépendances optionnelles :
- duckduckgo_search (ou ddgs) : recherche web
- trafilatura : extraction contenu propre (prioritaire)
- beautifulsoup4 : fallback extraction

Importé par : agents (run_search_agent)
Dépend de : io_manager (OutputManager)
"""

from __future__ import annotations

import requests
from typing import Optional, List, Dict, Any

from nexus_hive.io_manager import OutputManager

# ═══════════════════════════════════════════════════════════════════
# Dépendances optionnelles pour la recherche web
# ═══════════════════════════════════════════════════════════════════

try:
    from ddgs import DDGS

    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    try:
        from duckduckgo_search import DDGS

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


def fetch_web_page(url: str, max_length: int = 8000, io: OutputManager = None) -> str:
    """
    Récupère et nettoie le contenu texte d'une page web.

    Essaie dans l'ordre :
    1. trafilatura (extraction propre)
    2. BeautifulSoup (parsing HTML)
    3. requests brut (fallback)
    """
    try:
        if TRAFILATURA_AVAILABLE:
            try:
                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    text = trafilatura.extract(
                        downloaded,
                        include_comments=False,
                        include_tables=True,
                        deduplicate=True,
                    )
                    if text:
                        return text[:max_length]
            except Exception:
                pass

        if BS4_AVAILABLE:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                response = requests.get(url, headers=headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, "html.parser")
                for element in soup(["script", "style", "nav", "footer", "header"]):
                    element.decompose()
                text = soup.get_text(separator="\n", strip=True)
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                return "\n".join(lines)[:max_length]
            except Exception:
                pass

        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text[:max_length]

    except Exception as e:
        return f"[ERREUR FETCH] {type(e).__name__}: {str(e)}"


def web_search(
    query: str,
    max_results: int = 10,
    fetch_content: bool = False,
    io: OutputManager = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Recherche web via DuckDuckGo.

    Args:
        query: Requête de recherche
        max_results: Nombre max de résultats
        fetch_content: Si True, récupère le contenu complet des pages
        io: OutputManager pour les logs

    Returns:
        Liste de résultats [{title, url, body, full_content?}] ou None si indisponible
    """
    # Nettoyage des accents pour DuckDuckGo
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
                    "body": r.get("body", "")[:500],
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
