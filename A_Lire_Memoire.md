# NEXUS HIVE — Récapitulatif Session 17/02/2026 (v3)

## ✅ CE QUI A ÉTÉ FAIT

### Priorité 0 — Split backend

- [x] Split `nexus_hive_v7.py` (2040 lignes) en 12 modules Python
- [x] Structure : `C:\IA\nexus_hive\nexus_hive\` (package Python)
- [x] Fichiers : `__init__.py`, `__main__.py`, `config.py`, `io_manager.py`, `memory.py`, `utils.py`, `ollama_client.py`, `vision.py`, `web_search.py`, `router.py`, `agents.py`, `main.py`
- [x] `main.js` modifié : `python -m nexus_hive --headless` avec `cwd: projectRoot`
- [x] `config.py` : `base_dir` pointe vers `C:\IA\nexus_hive\` (pas le sous-dossier)
- [x] Import validé : `python -c "import nexus_hive; print('OK')"`

### Priorité 1 — Bugs critiques + UX

- [x] **Routing qwen2.5:7b-instruct** — Remplace le routing hardcodé. Rapide (~5-6s), fiable
- [x] **Routeur silencieux** — Le routeur n'affiche plus ses tokens dans le chat (silent_io avec lambdas muettes dans `router.py` → `_ai_routing()`)
- [x] **Renommage agents français** — stratege, eclaireur, senior, junior, prof, ecrivain, hacker, veilleur, medecin, vision, nolimit, savant, historien
- [x] **Copier-coller CSS** — `user-select: text` sur `.message` et `.message-content` dans `index.html`
- [x] **Markdown rendering** — marked.js + highlight.js chargés via CDN dans `index.html`, rendu dans `renderLastMessageMarkdown()` de `renderer.js` appelé sur event `done`
- [x] **Bouton copier code** — Créé dynamiquement dans `renderLastMessageMarkdown()`, class `.code-copy-btn`, apparaît au hover sur les blocs `<pre>`
- [x] **Bouton "Nouvelle conversation"** — Bouton `#btn-newchat` dans header, appelle `/newchat` via `sendCommand`, clear le chat côté renderer
- [x] **Éclaireur sur qwen2.5** — `config.py` : `"eclaireur": "qwen2.5:7b-instruct"` au lieu de qwen3-8b
- [x] **Tokens par agent** — Dict `agent_max_tokens` dans `config.py`, utilisé dans `agents.py` → `run_single_agent()` via `config.agent_max_tokens.get(original_agent, config.max_tokens)`
- [x] **Recherche web fonctionnelle** — `run_search_agent()` dans `agents.py` appelle `web_search()` de `web_search.py` (DuckDuckGo), émet event `sources` avec `[{title, url, body}, ...]`
- [x] **Sources en bas de réponse** — Badge cliquable "🔍 X Sources" sous la réponse du veilleur
  - `displaySources()` dans `renderer.js` injecte un `.sources-badge-wrapper` sous le dernier `.message-assistant`
  - Badge pill `🔍 N Source(s)` avec flèche ▾/▴, toggle au clic, liste dépliable `.sources-list`
  - CSS ajouté dans `index.html` : `.sources-badge-wrapper`, `.sources-badge`, `.sources-badge.open`, `.sources-list`
  - `#sources-container` sidebar conservé mais reste `display:none` (non utilisé)
  - **Fichiers modifiés** : `renderer.js`, `index.html`

- [x] **Sélecteur Auto/Agent/Pipeline** — Menu déroulant à côté de l'input
  - 3 modes : Auto (qwen2.5 choisit), Agent (liste manuelle), Pipeline (liste manuelle)
  - En mode Agent/Pipeline : envoie le choix au Python qui skip le routing
  - **Fichiers modifiés** : `renderer.js`, `index.html`, `preload.js`, `main.js`, `main.py`

### Historique des conversations

- [x] **ConversationManager** dans `memory.py` — Classe avec : `new_conversation()`, `list_conversations()`, `load_conversation()`, `save_memory_to_conversation()`, `switch_conversation()`, `delete_conversation()`, `update_conversation_meta()`
- [x] Dossier `conversations/` avec `index.json` (liste) + fichiers `conv_<id>.json` (historique complet)
- [x] Commandes Python : `/newchat`, `/loadchat <id>`, `/listchats` dans `main.py`
- [x] API Electron : `loadConversation()` dans `preload.js`, handlers `conversations-list` et `conversation-loaded` dans `main.js`, `load-conversation` IPC handler
- [x] Titre auto = premier message user tronqué à 50 chars, via `update_conversation_meta(title=user_input)`
- [x] **Sidebar historique** — Section `#history-list` dans `index.html`, fonction `renderConversationList()` dans `renderer.js`, listener `onConversationsList` + `onConversationLoaded`
- [x] Chargement conversation passée : `onConversationLoaded` recrée les messages dans le chat avec `addMessage()` + `renderLastMessageMarkdown()` pour chaque message assistant

---

## 🐛 BUGS CONNUS

### Backend Python

- [ ] **agents_used toujours vide** dans `conversations/index.json`
  - **Cause** : `update_conversation_meta()` n'est jamais appelé avec le paramètre `agent`
  - **Fix** : Dans `main.py`, après le routing (après la ligne qui émet `routed`), ajouter : `conv_manager.update_conversation_meta(conv_manager.get_current_id(), agent=route)`
  - **Fichier** : `main.py`

- [ ] **Mémoire polluée** — l'ancien `memory.json` contient des boucles de `.` des anciennes sessions qwen3-8b
  - **Fix** : `del C:\IA\nexus_hive\memory.json`
  - **Fichier** : aucun code à modifier, juste supprimer le fichier

- [ ] **Double event `done`** — Le client reçoit 2 events `done` à chaque réponse
  - **Cause** : `agents.py` → `run_single_agent()` appelle `io.done(stats)` ET `main.py` appelle aussi `io.done(stats)` après le retour de la fonction
  - **Fix** : Supprimer le `io.done()` dans `agents.py` et ne garder que celui de `main.py`
  - **Fichiers** : `agents.py` + `main.py`

- [ ] **Warning DuckDuckGo** : `RuntimeWarning: This package duckduckgo_search has been renamed to ddgs`
  - **Cause** : `web_search.py` importe `from duckduckgo_search import DDGS` en premier
  - **Fix** : Inverser : essayer `from ddgs import DDGS` d'abord, puis fallback sur `duckduckgo_search`
  - **Fichier** : `web_search.py`

- [ ] **LoopDetector faux positifs** — Se déclenche sur contenu structuré (listes numérotées longues)
  - **État** : Seuil augmenté de 3 à 4 dans `_detect_exact_repetition()` de `memory.py`
  - **Fichier** : `memory.py`

- [ ] **Timeout global** — Actuellement `timeout: int = 600` (10 min) dans `config.py`
  - **Fix** : Passer à `timeout: int = 240` (4 min)
  - **Fichier** : `config.py`

### Frontend Electron

- [ ] **Badge agent actif reste allumé** — Après la réponse, le badge sidebar "🤖 AGENT ACTIF: ECLAIREUR" garde l'animation `pulse` alors que le modèle est déchargé
  - **Fix** : Dans `renderer.js` → `onTaskDone` handler, ajouter `updateAgentBadge('AUCUN')` ou créer un état "idle" avec style différent
  - **Fichier** : `renderer.js`

- [ ] **Sources dans la sidebar débordent** — Les URLs longues cassent le layout de la sidebar
  - **Décision** : On déplace les sources de la sidebar vers un badge cliquable EN BAS de la réponse du veilleur (style Grok/Perplexity)
  - **Comment ça marche actuellement** : L'event `sources` (tableau `[{title, url, body}, ...]`) arrive via `window.api.onSources()` dans `renderer.js`. La fonction `displaySources()` écrit dans `#sources-container` dans la sidebar
  - **Fix voulu** : Au lieu d'écrire dans la sidebar, injecter un badge "🔍 X Sources" sous le dernier message assistant. Au clic, déplier la liste des sources
  - **Fichiers** : `renderer.js` (modifier `displaySources()`) + `index.html` (CSS pour le badge + liste dépliable, supprimer ou masquer `#sources-container` de la sidebar)

- [ ] **Encodage logs Windows** — `d├®marr├®e` au lieu de `démarrée`
  - **Cosmétique**. Fix partiel : `chcp 65001` avant `npm start`, ou `log.transports.file.encoding = 'utf-8'` dans `main.js`
  - **Fichier** : `main.js`

- [ ] **Preload affiche v7.1** — Le `console.log` dans `preload.js` dit encore "Nexus Hive v7.1"
  - **Fix** : Changer en "Nexus Hive v1.0 (protocol v1)"
  - **Fichier** : `preload.js`

- [ ] **Température/GPU à 0** — Le dashboard Pulse affiche 0 partout sauf quand on clique le bouton GPU
  - **Comment ça marche** : Le bouton GPU envoie la commande `/gpu` au Python, qui appelle `get_gpu_info()` de `utils.py` et émet l'event `gpu_info`. Le renderer le reçoit via `onGpuUpdate()`
  - **Fix voulu** : Auto-appeler `/gpu` après chaque réponse (dans `onTaskDone`) ou toutes les 30s via `setInterval`
  - **Fichiers** : `renderer.js` (ajouter l'appel auto dans `onTaskDone` ou un `setInterval` au démarrage)

- [ ] **Croix suppression conversations** — Bouton ✕ sur chaque item de la sidebar
  - **Code fourni** mais pas encore intégré. Nécessite :
    1. `renderer.js` : dans `renderConversationList()`, ajouter un `<span class="conv-delete">✕</span>` avec `e.stopPropagation()` + `window.api.sendCommand('deletechat conv_id')`
    2. `main.py` : ajouter commande `/deletechat <id>` qui appelle `conv_manager.delete_conversation(id)` puis émet `conversations` avec la liste mise à jour
    3. `main.js` : le `default` du switch `system-command` doit passer les commandes avec arguments au Python
  - **Fichiers** : `renderer.js`, `main.py`, `main.js`

---

## 📋 PROCHAINES ÉTAPES

### Priorité 1 — Finir l'UI

- [x] **Sources en bas de réponse** — Badge cliquable "🔍 X Sources" sous la réponse du veilleur
  - `displaySources()` dans `renderer.js` injecte un `.sources-badge-wrapper` sous le dernier `.message-assistant`
  - Badge pill `🔍 N Source(s)` avec flèche ▾/▴, toggle au clic, liste dépliable `.sources-list`
  - CSS ajouté dans `index.html` : `.sources-badge-wrapper`, `.sources-badge`, `.sources-badge.open`, `.sources-list`
  - `#sources-container` sidebar conservé mais reste `display:none` (non utilisé)
  - **Fichiers modifiés** : `renderer.js`, `index.html`

- [x] **Sélecteur Auto/Agent/Pipeline** — Menu déroulant à côté de l'input
  - 3 modes : Auto (qwen2.5 choisit), Agent (liste manuelle), Pipeline (liste manuelle)
  - En mode Agent/Pipeline : envoie le choix au Python qui skip le routing
  - **Fichiers modifiés** : `renderer.js`, `index.html`, `preload.js`, `main.js`, `main.py`

- [x] **Bouton "+" amélioré** — Remplacer le trombone `📎` par un `+` avec sous-menu
  - Sous-menu avec choix : Image, PDF, Code, Texte, Tous fichiers
  - Click outside pour fermer le dropdown
  - **Fichiers nécessaires** : `renderer.js`, `main.py`, `main.js`

- [x] **Correction bugs frontend rapides** — Badge agent idle, GPU auto-refresh
  - **Fichiers nécessaires** : `renderer.js`

### Priorité 2 — Agents + Pipelines

- [ ] **Test agents nolimit/savant/historien** — Configurés dans config.py avec magnus, jamais testés en production
- [ ] **Nouveaux pipelines à tester** (configurés dans `config.py` mais jamais exécutés) :
  - `research_deep` : veilleur → stratege (recherche web → analyse approfondie)
  - `audit_full` : hacker → medecin (audit sécu → diagnostic)
  - `optimize_legacy` : stratege → senior (analyse archi → refactoring)
  - `clone_ui` : vision → junior (screenshot interface → code HTML/CSS)

### Priorité 3 — Infrastructure

- [x] **Harmoniser version → v1.0** — Mettre à jour : `package.json`, `__init__.py`, `preload.js`, `index.html`, `main.js`
- [ ] **GitHub repo** + `.gitignore` : `node_modules/`, `conversations/`, `memory.json`, `__pycache__/`, `generated_code/`
- [ ] **Build Electron** — electron-builder → .exe installable
- [ ] **SQLite** — Migration du JSON vers SQLite pour les conversations (remplacer `_load_index`/`_save_index` dans `ConversationManager` par des requêtes SQL, l'interface publique ne change pas)

### Priorité 4+ — Features avancées

- [ ] Feedback loop (thumbs up/down → optimisation prompts)
- [ ] Qwen3-TTS-12Hz-1.7B-CustomVoice (interface vocale)
- [ ] Bot Telegram / WhatsApp
- [ ] Multi-providers (Ollama + OpenAI + Mistral + Claude + Kimi)
- [ ] Graphe nodal interactif (React Flow / LiteGraph.js)

---

## 🔍 RECHERCHE DE BUGS — Checklist de test

À exécuter après chaque session de modifs pour détecter les régressions :

### Tests rapides (~2 min)

- [ ] `1+1` → Routing vers eclaireur, réponse rapide, pas de boucle
- [ ] `fait moi un script python hello world` → Routing vers junior, code formaté en markdown avec bouton Copier
- [ ] `raconte moi une histoire de pirates` → Routing vers ecrivain (qwen3-14b)
- [ ] Cliquer "Nouveau" → Chat vide, nouvelle conversation apparaît dans la sidebar
- [ ] Cliquer sur une ancienne conversation → Se recharge avec markdown rendu

### Tests moyens (~5 min)

- [ ] `quelles sont les dernières news IA 2026` → Routing vers veilleur, DuckDuckGo appelé, sources affichées
- [ ] Envoyer une image → Routing vers vision, llama-server démarre puis se tue après
- [ ] Pipeline `/pipeline code_review [code]` → 2 étapes (stratege puis senior), résultat final affiché
- [ ] `/gpu` → Infos GPU affichées dans le dashboard Pulse (VRAM, utilisation, température)

### Tests longs (~10 min)

- [ ] Question complexe au stratege (qwen3-14b) → Vérifie que le bloc `<think>` ne bloque pas le stream (le texte doit apparaître après la réflexion)
- [ ] Conversation de 5+ messages → Vérifie que le contexte n'est pas pollué (la réponse doit concerner la dernière question)
- [ ] Recherche web + question de suivi dans la même conversation → Vérifie que le veilleur est rappelé si nécessaire

---

## 🗂️ STRUCTURE ACTUELLE DES FICHIERS

```
C:\IA\nexus_hive\
├── conversations/           ← Historique conversations (JSON → SQLite en Prio 3)
│   ├── index.json           ← [{id, title, created, updated, message_count, agents_used}]
│   └── conv_*.json          ← {history: [{role, content, agent, model, elapsed, timestamp}], summary: ""}
├── nexus_hive/              ← Package Python (backend)
│   ├── __init__.py          (v8.0.0 → harmoniser en v1.0)
│   ├── __main__.py          (entry point : from nexus_hive.main import main)
│   ├── config.py            (Config dataclass : models, pipelines, agent_prompts, agent_max_tokens, chemins)
│   ├── io_manager.py        (OutputManager : emit JSON sur stdout en headless, ANSI en CLI)
│   ├── memory.py            (LRUCache, LoopDetector, MemoryStore, ConversationManager)
│   ├── utils.py             (normalize_text, strip_think_tags, extract_code_blocks, get_gpu_info, encode_image_to_base64)
│   ├── ollama_client.py     (call_ollama_stream : POST /api/chat, gestion <think>, LoopDetector, keep_alive:0)
│   ├── vision.py            (VisionServerManager : start/stop llama-server, call_vision SSE)
│   ├── web_search.py        (web_search DuckDuckGo, fetch_web_page trafilatura/BS4)
│   ├── router.py            (Router : ROUTER_SYSTEM_PROMPT, _keyword_routing désactivé, _ai_routing qwen2.5 avec silent_io)
│   ├── agents.py            (run_single_agent, run_pipeline, run_search_agent, auto-select complexité)
│   └── main.py              (boucle stdin JSON, commandes /reset /newchat /loadchat /listchats /gpu /status /export, routing, ConversationManager)
├── electron/                ← Frontend Electron
│   ├── index.html           (layout grid, cyberpunk CSS, markdown CSS, sidebar avec historique + dashboard)
│   ├── renderer.js          (state global, marked.js config, setupEventListeners, setupApiListeners, streaming, renderLastMessageMarkdown, renderConversationList)
│   ├── main.js              (createWindow, startPython spawn, handlePythonEvent switch, IPC handlers send-input/send-input-with-files/system-command/load-conversation)
│   ├── preload.js           (contextBridge API : sendInput, sendInputWithFiles, sendCommand, loadConversation, on*)
│   ├── package.json         (electron + electron-log)
│   └── node_modules/
├── generated_code/          ← Code généré automatiquement par les agents (avec headers)
└── memory.json              ← LEGACY → À SUPPRIMER
```

## 🔧 MODÈLES OLLAMA UTILISÉS

| Agent          | Modèle                | Tokens max | Usage                       |
| -------------- | --------------------- | ---------- | --------------------------- |
| chef (routeur) | qwen2.5:7b-instruct   | 20         | Routing uniquement          |
| eclaireur      | qwen2.5:7b-instruct   | 2048       | Questions simples, maths    |
| junior         | qwen25-coder:latest   | 2048       | Code simple, scripts        |
| senior         | qwencoder:latest      | 4096       | Code complexe, architecture |
| stratege       | qwen3-14b:latest      | 4096       | Analyse approfondie         |
| prof           | qwen3-8b:latest       | 3072       | Pédagogie                   |
| ecrivain       | qwen3-14b:latest      | 4096       | Créatif                     |
| hacker         | magnum-pentest:latest | 4096       | Sécurité                    |
| veilleur       | qwen2.5:7b-instruct   | 2048       | Recherche web               |
| veilleur_synth | qwen3-8b:latest       | 2048       | Synthèse résultats web      |
| medecin        | qwen3-8b:latest       | 2048       | Debug                       |
| nolimit        | magnum-pentest:latest | 6144       | Sans censure                |
| savant         | magnum-pentest:latest | 6144       | Science                     |
| historien      | magnum-pentest:latest | 6144       | Histoire                    |
| vision         | qwen3-vision:latest   | 2048       | Images (llama-server)       |

## 🔄 PIPELINES

| Pipeline        | Étape 1  | Étape 2   | Description                          | Testé ? |
| --------------- | -------- | --------- | ------------------------------------ | ------- |
| code_review     | stratege | senior    | Analyse code → améliorations         | ❌      |
| code_secure     | senior   | hacker    | Écrit code → audit sécurité          | ❌      |
| story_polish    | ecrivain | ecrivain  | Écrit histoire → peaufine            | ❌      |
| exploit_code    | stratege | hacker    | Analyse système → exploits/PoC       | ❌      |
| analyze_code    | stratege | eclaireur | Analyse profonde → synthèse          | ❌      |
| learning_guide  | senior   | prof      | Contenu technique → parcours pédago  | ❌      |
| debug_fix       | medecin  | senior    | Diagnostic erreur → code corrigé     | ❌      |
| full_stack      | senior   | junior    | Backend → frontend                   | ❌      |
| doc_code        | senior   | stratege  | Code → documentation complète        | ❌      |
| refactor        | stratege | senior    | Analyse archi → code refactorisé     | ❌      |
| phoenix         | stratege | senior    | Refactoring intelligent              | ❌      |
| shark_tank      | ecrivain | stratege  | Pitch idée → débat & verdict         | ❌      |
| research_deep   | veilleur | stratege  | Recherche web → analyse approfondie  | ❌      |
| audit_full      | hacker   | medecin   | Audit sécu → diagnostic              | ❌      |
| optimize_legacy | stratege | senior    | Analyse legacy → modernisation       | ❌      |
| clone_ui        | vision   | junior    | Screenshot interface → code HTML/CSS | ❌      |

## 💡 IDÉES À EXPLORER (Obsidian)

- **Qwen3-TTS** : Interface vocale locale, custom voice, 1.7B (léger sur RTX 3060)
- **Claude Code** : Refactors lourds, tests unitaires, multi-fichiers
- **Claude + Obsidian** : Plugin Copilot ou Smart Connections avec API key Anthropic
- **Gemini CLI** : Provider additionnel en v3+
- **Miro AI** : Inspiration pour le graphe nodal des pipelines
- **Obsidian + qwen2.5 local** : Plugin Local GPT ou Smart Connections via Ollama
- **Opencode** : Alternative à Claude Code, à explorer

## 📝 NOTES TECHNIQUES

- **Lancer le projet** : `cd C:\IA\nexus_hive\electron && npm start`
- **Python standalone** : `cd C:\IA\nexus_hive && python -c "import nexus_hive; print('OK')"`
- **Routeur** : qwen2.5 met ~5-6s pour choisir un agent, résultat caché en LRU cache (hash MD5 des 200 premiers chars)
- **Vision** : llama-server auto-lance (port 8081), décharge Ollama avant, tue le process après
- **Streaming** : texte brut pendant le stream via `contentDiv.textContent`, markdown rendu au `done` via `marked.parse()`
- **VRAM** : `keep_alive: 0` sur chaque appel Ollama = décharge immédiate après réponse
- **Protocole stdin** : `{"type": "input|command|input_with_files", ...}\n` — une ligne JSON par message
- **Protocole stdout** : `{"event": "stream|done|routing|routed|sources|...", "data": ..., "protocol_version": 1}\n`
