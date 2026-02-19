/**
 * NEXUS HIVE v7.1 - Preload Script
 * ==================================
 * Bridge sécurisé entre le main process et le renderer.
 *
 * SÉCURITÉ : contextBridge expose une API limitée au renderer.
 * Le renderer n'a JAMAIS accès à Node.js directement (contextIsolation: true).
 * Chaque fonction est un appel IPC vers main.js qui gère la communication Python.
 *
 * PROTOCOLE :
 *   sendInput(text)           → {"type": "input", "text": "..."}
 *   sendInputWithFiles(...)   → {"type": "input_with_files", ...}
 *   sendCommand(cmd)          → {"type": "command", "command": "/..."}
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  // ═══════════════════════════════════════════════════════════════════
  // ENTRÉE UTILISATEUR (renderer → main → Python stdin)
  // ═══════════════════════════════════════════════════════════════════

  // Envoie un message texte simple au Python
  // mode = "auto" | "agent" | "pipeline"
  // selectedAgent = nom de l'agent (si mode = "agent")
  // selectedPipeline = nom du pipeline (si mode = "pipeline")
  sendInput: (
    text,
    mode = "auto",
    selectedAgent = null,
    selectedPipeline = null,
  ) =>
    ipcRenderer.invoke(
      "send-input",
      text,
      mode,
      selectedAgent,
      selectedPipeline,
    ),

  // Envoie un message avec fichiers joints (images, PDF, code...)
  // files = [{name, type, size, base64, isImage}, ...]
  sendInputWithFiles: (
    text,
    files,
    mode = "auto",
    selectedAgent = null,
    selectedPipeline = null,
  ) =>
    ipcRenderer.invoke(
      "send-input-with-files",
      text,
      files,
      mode,
      selectedAgent,
      selectedPipeline,
    ),

  // ═══════════════════════════════════════════════════════════════════
  // COMMANDES SYSTÈME (boutons de l'interface)
  // ═══════════════════════════════════════════════════════════════════
  sendCommand: (command) => ipcRenderer.invoke("system-command", command),

  // ═══════════════════════════════════════════════════════════════════
  // STATUT & CONTRÔLE
  // ═══════════════════════════════════════════════════════════════════
  getStatus: () => ipcRenderer.invoke("get-status"),
  restartPython: () => ipcRenderer.invoke("restart-python"),

  // ═══════════════════════════════════════════════════════════════════
  // ÉVÉNEMENTS (Python stdout → main → renderer)
  // Chaque callback reçoit un objet {event, data, protocol_version, ...}
  // ═══════════════════════════════════════════════════════════════════

  // Système prêt : data = {version, models, pipelines}
  onReady: (callback) => {
    ipcRenderer.on("system-ready", (event, data) => callback(data));
  },

  // Chunk de réponse (streaming) : data = {content, agent, timestamp}
  onStream: (callback) => {
    ipcRenderer.on("agent-stream", (event, data) => callback(data));
  },

  // Log système : data = {level, message, timestamp}
  onSystemLog: (callback) => {
    ipcRenderer.on("system-log", (event, data) => callback(data));
  },

  // Python attend un input : data = texte du prompt
  onPrompt: (callback) => {
    ipcRenderer.on("user-prompt", (event, data) => callback(data));
  },

  // Génération terminée : data = {tok_s, tokens, total_s, ...}
  onTaskDone: (callback) => {
    ipcRenderer.on("task-done", (event, data) => callback(data));
  },

  // Agent sélectionné par le routeur : data = {type: "routed", data: "agent_name"}
  onAgentSelected: (callback) => {
    ipcRenderer.on("agent-selected", (event, data) => callback(data));
  },

  // Echo du message utilisateur : data = texte
  onUserMessage: (callback) => {
    ipcRenderer.on("user-message", (event, data) => callback(data));
  },

  // Events système (crash Python, etc.)
  onSystemEvent: (callback) => {
    ipcRenderer.on("system-event", (event, data) => callback(data));
  },

  // Events non mappés (fallback)
  onCustomEvent: (callback) => {
    ipcRenderer.on("custom-event", (event, data) => callback(data));
  },

  // ═══════════════════════════════════════════════════════════════════
  // PULSE DASHBOARD (métriques temps réel)
  // ═══════════════════════════════════════════════════════════════════

  // Mise à jour métriques globales
  onMetricsUpdate: (callback) => {
    ipcRenderer.on("metrics-update", (event, data) => callback(data));
  },

  // Progression d'une tâche : data = {current, total, percentage, message}
  onProgressUpdate: (callback) => {
    ipcRenderer.on("progress-update", (event, data) => callback(data));
  },

  // Événement pipeline : data = {type, pipeline, step, agent, duration}
  onPipelineEvent: (callback) => {
    ipcRenderer.on("pipeline-event", (event, data) => callback(data));
  },

  // Infos GPU : data = {gpu_name, vram_used_mb, vram_total_mb, gpu_util, temp_c}
  onGpuUpdate: (callback) => {
    ipcRenderer.on("gpu-update", (event, data) => callback(data));
  },

  // Sources web : data = [{title, url}, ...]
  onSources: (callback) => {
    ipcRenderer.on("sources", (event, data) => callback(data));
  },

  // ═══════════════════════════════════════════════════════════════════
  // CONVERSATIONS
  // ═══════════════════════════════════════════════════════════════════

  // Charger une conversation par ID
  loadConversation: (convId) => ipcRenderer.invoke("load-conversation", convId),

  // Liste des conversations mise à jour
  onConversationsList: (callback) => {
    ipcRenderer.on("conversations-list", (event, data) => callback(data));
  },

  // Conversation chargée (avec historique complet)
  onConversationLoaded: (callback) => {
    ipcRenderer.on("conversation-loaded", (event, data) => callback(data));
  },
});

console.log("Preload script chargé - Nexus Hive v1.0 (protocol v1)");
