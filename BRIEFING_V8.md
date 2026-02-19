# ══════════════════════════════════════════════════════════════════════════════

# NEXUS HIVE - BRIEFING v8 (a donner a Claude en debut de conversation)

# ══════════════════════════════════════════════════════════════════════════════

# Colle ce fichier en debut de ta prochaine conversation avec Claude.

# Il contient TOUT le contexte du projet pour repartir de zero sans rien perdre.

# ══════════════════════════════════════════════════════════════════════════════

## 1. PROJET & SETUP

- Developpeur passionne, projet solo
- Setup : Windows, RTX 3060 12GB, Ollama + llama-server
- Interface : Electron (Node.js) + Python backend headless
- Objectif : orchestrateur multi-agents IA local de niveau production
- RTX 3060 = 12GB VRAM, UN SEUL modele charge a la fois, on decharge entre chaque appel
- keep_alive: 0 dans Ollama pour liberer la VRAM apres chaque appel

## 2. ARCHITECTURE ACTUELLE (v7.1 stable)

```
┌─────────────┐ stdin (JSON) ┌───────────────┐ HTTP ┌──────────┐
│ Electron │ ────────────────→ │ Python │ ──────────→│ Ollama │
│ (UI) │ ←──────────────── │ nexus_hive.py │ ←──────────│ (LLMs) │
└─────────────┘ stdout (JSON) └───────────────┘ └──────────┘
	│
	│ (images uniquement)
	▼
	┌──────────────┐
	│ llama-server │ ← auto-lance/tue par Python
	│ (vision GGUF) │ port 8081
	└──────────────┘
```

### Fichiers source (tous dans C:\IA\multi-agent\electron\)

- `nexus_hive_v7.py` — Backend Python (~2040 lignes) → A SPLITTER EN v8 (Prio 0)
- `main.js` — Electron main process : spawn Python, IPC handlers
- `preload.js` — Bridge securise (contextBridge)
- `renderer.js` — Logique UI : chat, fichiers, dashboard, events
- `index.html` — Interface HTML/CSS cyberpunk avec sidebar dashboard

### Protocole de communication (v1)

- STDIN (Electron → Python) : JSON `{type: "input"/"command"/"input_with_files", ...}`
- STDOUT (Python → Electron) : events JSON (ready, stream, done, routing, routed, agent, gpu_info, etc.)

### Vision (auto-geree par VisionServerManager)

- Ollama crashait (500) avec qwen3-vision sur RTX 3060
- Solution : Python auto-lance llama-server:8081, fait l'appel, tue le process, libere VRAM
- Modele : Qwen_Qwen3-VL-8B-Instruct-IQ4_XS.gguf + mmproj-f16.gguf
- Fonctionne OK, VRAM se libere bien apres quelques secondes

### UI actuelle

- Chat streaming avec agent badges
- Bouton + drag&drop + Ctrl+V pour images/fichiers
- Pulse Dashboard : tokens/s, VRAM bar, GPU %, temperature, agent actif
- Boutons : Reset, GPU, Export, Restart Python
- Theme cyberpunk (#0a0e27, #00d9ff, #00ffc8)

## 3. AGENTS v8 (nouveau nommage)

| Nom v8    | Ancien nom | Role                           | Modele                        |
| --------- | ---------- | ------------------------------ | ----------------------------- |
| stratege  | analyst    | Analyse complexe               | qwen3-14b                     |
| eclaireur | analyst2   | Analyse rapide, maths simples  | qwen3-8b                      |
| senior    | coder      | Code complexe, architecture    | qwen3-14b                     |
| junior    | coder2     | Code simple, scripts rapides   | qwen3-8b                      |
| prof      | teacher    | Pedagogie, vulgarisation       | qwen3-8b                      |
| ecrivain  | creative   | Ecriture creative              | a tester (magnus ou Hermes 3) |
| hacker    | pentester  | Audit securite, pentest        | magnum-pentest                |
| veilleur  | searcher   | Recherche web (DuckDuckGo)     | qwen2.5:7b                    |
| medecin   | debugger   | Diagnostic bugs, erreurs       | qwen3-8b                      |
| vision    | vision     | Analyse d'images               | Qwen3-VL-8B (llama-server)    |
| nolimit   | (nouveau)  | Contenu sans censure           | magnum-pentest                |
| savant    | (nouveau)  | Expert scientifique, pedagogie | magnum-pentest                |
| historien | (nouveau)  | Histoire & contexte historique | magnum-pentest                |

Note : nolimit/savant/historien = meme modele (magnus), prompts system differents.
Note : ecrivain → tester magnus d'abord, si pas assez bon → installer Hermes 3 Llama 3.1 8B

## 4. PIPELINES v8

### Existants (a renommer avec noms v8)

code_review, code_secure, story_polish, exploit_code, analyze_code,
learning_guide, debug_fix, full_stack, doc_code, refactor, phoenix, shark_tank

### Nouveaux pipelines

- `research_deep` : veilleur → stratege → prof (recherche web + analyse + guide pedago)
- `audit_full` : hacker → medecin → junior (audit secu + debug + code secure)
- `optimize_legacy` : stratege → refactor → phoenix (analyse + refactor + modernisation)
- `clone_ui` : vision → junior → senior (screenshot interface → code HTML/CSS)

### UI pipeline

- Liste deroulante des pipelines dans l'UI pour selectionner directement

## 5. ROUTING v8 — REFONTE COMPLETE

### Probleme actuel

- \_ai_routing() retourne TOUJOURS "analyst" (HARDCODE !)
- gemma3-chef n'est JAMAIS appele, seuls les keywords comptent
- Keywords trop generiques ("conseil" → learning_guide pour une question GPU)
- Resultat : mauvais routing permanent

### Solution v8

- ROUTEUR = qwen2.5:7b-instruct (rapide, leger, comprend bien les instructions)
- PAS gemma3, PAS keywords foireux
- qwen2.5 recoit un prompt avec la LISTE COMPLETE des agents + pipelines + descriptions
- Il comprend la question et choisit le bon agent/pipeline
- EXCEPTIONS (pas besoin de qwen2.5, routing direct) :
  - Image attachee → vision (auto-detect)
  - /search → veilleur (commande explicite)
  - /pipeline xxx → pipeline force
  - /agent xxx → agent force
- TOUT LE RESTE → appel qwen2.5 routeur (1-2s, routing intelligent)

## 6. BUGS A CORRIGER

- Impossible de copier du texte dans le chat (user-select CSS)
- /search ne fait PAS de vraie recherche web (DuckDuckGo pas appele, modele hallucine)
- "/ search" avec espace pas reconnu (strip espaces apres /)
- /search n'est pas une commande systeme (ajouter comme /reset, /gpu)
- Contexte messages precedents pollue les reponses suivantes
- Modeles hallucinent les donnees factuelles sans recherche web

### Deja fixe en v7.1

- ✅ LoopDetector skip pendant blocs <think>
- ✅ Dashboard reset agent apres vision (events routing/routed)
- ✅ Fix base64, stdout buffer, flash-attn, protocol versionne

## 7. ROADMAP v8+

### PRIORITE 0 — Split du code Python (AVANT TOUT)

Decouper nexus_hive_v7.py (2040 lignes) en 11 fichiers :

```
nexus_hive/
├── __init__.py # (~10 lignes) Version
├── main.py # (~100 lignes) Point d'entree, boucle principale
├── config.py # (~150 lignes) Config, modeles, prompts
├── router.py # (~100 lignes) Routeur qwen2.5, routing direct
├── agents.py # (~300 lignes) run_single_agent, run_pipeline, run_search
├── vision.py # (~200 lignes) VisionServerManager, call_vision
├── memory.py # (~150 lignes) MemoryStore, LoopDetector
├── io_manager.py # (~150 lignes) OutputManager, events, streaming
├── ollama_client.py # (~200 lignes) call_ollama_stream, sanitize_base64
├── web_search.py # (~100 lignes) DuckDuckGo, fetch_web_page
└── utils.py # (~100 lignes) normalize_text, strip_think_tags
```

- Garder TOUS les commentaires + en ajouter pour les imports
- 5x moins de tokens par modif = sessions Claude 3x plus longues

### PRIORITE 1 — Bugs critiques + UX

- Tu as raison, les events routing sont sommaires — on voit juste le badge agent dans la sidebar mais pas le processus de décision. On améliorera ça plus tard avec un mini-log visible dans l'UI.
- Markdown rendering (marked.js + highlight.js)
- /search fonctionnel (DuckDuckGo + sources affichees)
- Bouton "Nouvelle conversation"
- Renommer agents en francais (tableau section 3)
- Events routing visibles dans l'UI

### PRIORITE 2 — Agents + Pipelines + Routing

- Refonte routing avec qwen2.5 (section 5)
- Agent nolimit/savant/historien (magnus multi-role)
- Test comparatif magnus vs Hermes 3 pour ecrivain (2 questions ci-dessous)
- Nouveaux pipelines (research_deep, audit_full, optimize_legacy, clone_ui)
- Liste deroulante pipelines dans l'UI

#### Test comparatif ecrivain : magnus vs Hermes 3

- Q1 : "Ecris une histoire courte immersive sur un astronaute qui decouvre
  une civilisation sous-marine sur une lune de Jupiter. 300 mots max."
- Q2 : "Ecris une scene de romance torride entre deux personnages dans un
  chateau medieval pendant un orage. Style litteraire, 200 mots."
- Comparer : qualite d'ecriture, permissivite, francais naturel, vitesse
- Si magnus gagne → un seul modele pour ecrivain + nolimit + savant + historien

### PRIORITE 3 — Infrastructure

- GitHub repo + versioning + .gitignore
- Build Electron (electron-builder → .exe)
- Auto-update (electron-updater + GitHub Releases)
- SQLite memoire persistante (migration JSON)
- Compression semantique historique
- Toggle Local / Cloud (bouton header)

### PRIORITE 4 — Features avancees (v9+)

- Feedback loop : thumbs up/down → SQLite → /optimize prompts system
- LLMLingua-2 : compression prompts (utile en mode Cloud, pas urgent en local)
- Bot Telegram / WhatsApp
- Mini-graphe SVG pipeline (noeuds animes)

### PRIORITE 5 — Vision long terme (v10+)

- Graphe nodal interactif (React Flow / LiteGraph.js)
- Drag & drop creation pipelines
- Multi-providers simultanes (Ollama + OpenAI + Mistral API + Claude)
- Panel conversations sauvegardees
- Interface hybride Chat + Graphe Nodal

## 8. NOTES TECHNIQUES

### Structure GitHub

```
nexus-hive/
├── electron/ # main.js, preload.js, renderer.js, index.html
├── nexus_hive/ # fichiers Python splittes
├── package.json
├── requirements.txt
├── README.md
└── .gitignore
```

### Commandes systeme

Actuelles : /reset, /gpu, /models, /status, /export, /agent, /pipeline
A ajouter v8 : /search, /optimize, /newchat

### Workflow apres split

- Petites modifs → VSCode + extension Continue
- Gros features / bugs → Claude + BRIEFING + fichiers concernes

## 9. COMMENT DEMARRER LA SESSION v8

Donne-moi ce fichier + les 5 fichiers source actuels.
Dis "On attaque la Priorite 0" (split du code).
Ensuite Priorite 1 (bugs + markdown + renommage).
