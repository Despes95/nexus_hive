# Multi-Agent System v6.4

Systeme multi-agents 100% local avec Ollama. Zero framework, zero cloud, zero API key.

Orchestre plusieurs modeles LLM specialises via un routeur intelligent, avec streaming temps reel, pipelines multi-agents, recherche web, analyse d'images, et monitoring GPU.

Teste sur Windows 11 + RTX 3060 12 Go VRAM.

## Installation

```bash
<<<<<<< HEAD
# Prerequis  Python 3.11+, Ollama installe, GPU NVIDIA
mkdir CIAmulti-agent
cd CIAmulti-agent
=======
# Prerequis : Python 3.11+, Ollama installe, GPU NVIDIA
mkdir C:\IA\multi-agent
cd C:\IA\multi-agent
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a

pip install requests duckduckgo-search
```

Telecharger `multi_agent.py` dans ce dossier.

## Modeles requis

```bash
<<<<<<< HEAD
ollama pull qwen2.57b-instruct
ollama pull qwencoderlatest
ollama pull qwen2.5-coderlatest
ollama pull qwen314b
ollama pull qwen38b
ollama pull gemma3latest
ollama pull magnum-pentestlatest    # ou equivalent securite
ollama pull qwen3-visionlatest      # optionnel, pour analyse images
=======
ollama pull qwen2.5:7b-instruct
ollama pull qwencoder:latest
ollama pull qwen2.5-coder:latest
ollama pull qwen3:14b
ollama pull qwen3:8b
ollama pull gemma3:latest
ollama pull magnum-pentest:latest    # ou equivalent securite
ollama pull qwen3-vision:latest      # optionnel, pour analyse images
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a
```

Adaptez les noms dans le dictionnaire `MODELS` du script si vos tags sont differents.

## Lancement

```bash
ollama serve                           # Terminal 1
<<<<<<< HEAD
cd CIAmulti-agent
=======
cd C:\IA\multi-agent
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a
python multi_agent.py                  # Terminal 2
```

### Options

<<<<<<< HEAD
## Flag Effet

`--no-memory` Pas de persistence entre sessions
`--stat-only` Affiche uniquement les statistiques
=======
| Flag | Effet |
|------|-------|
| `--no-memory` | Pas de persistence entre sessions |
| `--stat-only` | Affiche uniquement les statistiques |
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a

## Architecture

```
User Input
<<<<<<< HEAD

    v
[Chef  Routeur] --cache-- Agent connu

    v                            v
Analyse demande            Retour cache

    v
Agent simple OU Pipeline (2 agents)

    v
[Streaming temps reel] -- Terminal

    v
[Auto-save code] -- generated_code

    v
[Unload VRAM] -- Pret pour la prochaine demande
=======
    |
    v
[Chef / Routeur] --cache--> Agent connu ?
    |                            |
    v                            v
Analyse demande            Retour cache
    |
    v
Agent simple OU Pipeline (2 agents)
    |
    v
[Streaming temps reel] --> Terminal
    |
    v
[Auto-save code] --> generated_code/
    |
    v
[Unload VRAM] --> Pret pour la prochaine demande
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a
```

## Agents

<<<<<<< HEAD
## Agent Modele Specialite

chef gemma3 Routeur intelligent
coder qwencoder 14B Code Python complexe
coder2 qwen25-coder 7B Code Python simple (rapide)
pentester magnum-pentest Securite, audit, pentesting
analyst qwen3-14b Raisonnement, analyse complexe
analyst2 qwen3-8b Analyse simple (rapide)
creative gemma3 Ecriture creative
vision qwen3-vision Analyse d'images
searcher qwen2.5-7b Recherche web + synthese

## Pipelines

## Pipeline Agent 1 - Agent 2 Usage

code_secure coder - pentester Code securise
code_review pentester - coder Audit + correction
exploit_code pentester - coder2 Failles + exploit
story_polish creative - analyst Ecriture amelioree
analyze_code coder - analyst Code + verification

## Commandes

## Commande Description

`exit` Quitte (exporte stats auto)
`reset` Efface memoire + cache
`status` GPU + modeles charges
`gpu` Infos GPU
`history` 10 derniers echanges + resume
`models` Liste des modeles
`pipelines` Liste des pipelines
`clear_vram` Decharge tous les modeles
`export` Exporte stats session (JSON)
`search question` Recherche web DuckDuckGo
`Ctrl+C` Interrompt une generation
=======
| Agent | Modele | Specialite |
|-------|--------|-----------|
| chef | gemma3 | Routeur intelligent |
| coder | qwencoder 14B | Code Python complexe |
| coder2 | qwen25-coder 7B | Code Python simple (rapide) |
| pentester | magnum-pentest | Securite, audit, pentesting |
| analyst | qwen3-14b | Raisonnement, analyse complexe |
| analyst2 | qwen3-8b | Analyse simple (rapide) |
| creative | gemma3 | Ecriture creative |
| vision | qwen3-vision | Analyse d'images |
| searcher | qwen2.5-7b | Recherche web + synthese |

## Pipelines

| Pipeline | Agent 1 -> Agent 2 | Usage |
|----------|-------------------|-------|
| code_secure | coder -> pentester | Code securise |
| code_review | pentester -> coder | Audit + correction |
| exploit_code | pentester -> coder2 | Failles + exploit |
| story_polish | creative -> analyst | Ecriture amelioree |
| analyze_code | coder -> analyst | Code + verification |

## Commandes

| Commande | Description |
|----------|-------------|
| `exit` | Quitte (exporte stats auto) |
| `/reset` | Efface memoire + cache |
| `/status` | GPU + modeles charges |
| `/gpu` | Infos GPU |
| `/history` | 10 derniers echanges + resume |
| `/models` | Liste des modeles |
| `/pipelines` | Liste des pipelines |
| `/clear_vram` | Decharge tous les modeles |
| `/export` | Exporte stats session (JSON) |
| `/search <question>` | Recherche web DuckDuckGo |
| `Ctrl+C` | Interrompt une generation |
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a

## Features

### Streaming temps reel
<<<<<<< HEAD

Les reponses s'affichent mot par mot. Ctrl+C interrompt proprement.

### Auto-selection de modele

Le script estime la complexite et choisit automatiquement le modele optimal (legerpuissant).

### Sauvegarde automatique du code

Chaque bloc de code genere est sauvegarde dans `generated_code` avec un header (agent, date, prompt).

### Resume automatique de memoire

Quand l'historique depasse 20 echanges, un resume est genere automatiquement et les anciens messages sont supprimes. Le contexte reste coherent.

### Recherche web

Agent searcher via DuckDuckGo (gratuit, sans API key). Recherche + synthese avec citations.

### Gestion VRAM

Un seul modele en VRAM a la fois. Chargementdechargement automatique. Compatible RTX 3060 12 Go.

### Anti-hallucination

Stop sequences + instructions explicites empechent les modeles de generer des conversations fictives.

### Cache routing

=======
Les reponses s'affichent mot par mot. Ctrl+C interrompt proprement.

### Auto-selection de modele
Le script estime la complexite et choisit automatiquement le modele optimal (leger/puissant).

### Sauvegarde automatique du code
Chaque bloc de code genere est sauvegarde dans `generated_code/` avec un header (agent, date, prompt).

### Resume automatique de memoire
Quand l'historique depasse 20 echanges, un resume est genere automatiquement et les anciens messages sont supprimes. Le contexte reste coherent.

### Recherche web
Agent searcher via DuckDuckGo (gratuit, sans API key). Recherche + synthese avec citations.

### Gestion VRAM
Un seul modele en VRAM a la fois. Chargement/dechargement automatique. Compatible RTX 3060 12 Go.

### Anti-hallucination
Stop sequences + instructions explicites empechent les modeles de generer des conversations fictives.

### Cache routing
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a
Les routes recentes sont mises en cache pour eviter de rappeler le chef sur des demandes similaires.

## Fichiers

<<<<<<< HEAD
## Fichier Description

`multi_agent.py` Script principal
`agent_memory.json` Historique + resume (auto)
`stats_export.json` Stats de session
`logs` Logs journaliers
`generated_code` Code Python sauvegarde
=======
| Fichier | Description |
|---------|-------------|
| `multi_agent.py` | Script principal |
| `agent_memory.json` | Historique + resume (auto) |
| `stats_export.json` | Stats de session |
| `logs/` | Logs journaliers |
| `generated_code/` | Code Python sauvegarde |
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a

## Exemples

```
<<<<<<< HEAD
Toi Ecris un script securise pour un login avec bcrypt
- PIPELINE CODE_SECURE (coder - pentester)

Toi Compare Python vs Rust pour du backend
- ANALYST (qwen3-14b) [complex]

Toi Raconte une histoire absurde
- CREATIVE (gemma3)

Toi Quelles sont les news IA en 2026
- SEARCHER (recherche web)

Toi Decris cette image Cscreenshotstest.png
- VISION (qwen3-vision)
=======
Toi: Ecris un script securise pour un login avec bcrypt
-> PIPELINE CODE_SECURE (coder -> pentester)

Toi: Compare Python vs Rust pour du backend
-> ANALYST (qwen3-14b) [complex]

Toi: Raconte une histoire absurde
-> CREATIVE (gemma3)

Toi: Quelles sont les news IA en 2026 ?
-> SEARCHER (recherche web)

Toi: Decris cette image C:\screenshots\test.png
-> VISION (qwen3-vision)
>>>>>>> 81144224b6011be1a0c7dd6628bdee18bde5898a
```

## Roadmap

- [ ] Support API cloud (Claude, OpenAI, MiniMax)
- [ ] TUI avec textual (interface terminal riche)
- [ ] Bot Telegram
- [ ] Memoire vectorielle (ChromaDB)
- [ ] Dashboard web (Gradio)

## Disclaimer

L'agent pentester est configure pour des analyses a but pedagogique et ethique uniquement.

## Licence

MIT
