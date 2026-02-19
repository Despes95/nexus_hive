/**
 * NEXUS HIVE v8.0 - Renderer Process
 * ====================================
 * Interface utilisateur avec Pulse Dashboard + Markdown rendering
 */

// ============================================================================
// ÉTAT GLOBAL
// ============================================================================

const state = {
  currentAgent: null,
  isProcessing: false,
  messages: [],
  attachedFiles: [],
  pendingSources: null, // sources reçues avant la fin du stream
  // Mode sélecteur : "auto", "agent", ou "pipeline"
  currentMode: "auto",
  selectedAgent: null,
  selectedPipeline: null,
  metrics: {
    tokensPerSec: 0,
    vramUsed: 0,
    vramTotal: 0,
    gpuUtil: 0,
    temperature: 0,
  },
  pipeline: {
    active: false,
    name: null,
    step: 0,
    totalSteps: 2,
  },
};

// ============================================================================
// CONFIGURATION MARKED.JS (Markdown → HTML)
// ============================================================================

if (typeof marked !== "undefined") {
  marked.setOptions({
    highlight: function (code, lang) {
      if (typeof hljs !== "undefined" && lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(code, { language: lang }).value;
        } catch (e) {}
      }
      if (typeof hljs !== "undefined") {
        try {
          return hljs.highlightAuto(code).value;
        } catch (e) {}
      }
      return code;
    },
    breaks: true,
    gfm: true,
  });
}

// ============================================================================
// INITIALISATION
// ============================================================================

document.addEventListener("DOMContentLoaded", () => {
  console.log("🚀 Nexus Hive v1.0 UI chargée");
  setupEventListeners();
  setupApiListeners();
  focusInput();
});

// ============================================================================
// EVENT LISTENERS DOM
// ============================================================================

function setupEventListeners() {
  const input = document.getElementById("user-input");
  const sendBtn = document.getElementById("send-btn");
  const attachBtn = document.getElementById("attach-btn");
  const fileInput = document.getElementById("file-input");

  sendBtn.addEventListener("click", sendMessage);

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  });

  // === FICHIERS ===
  // Bouton + avec dropdown
  const attachWrapper = document.querySelector(".attach-wrapper");
  const attachDropdown = document.getElementById("attach-dropdown");

  if (attachBtn && attachDropdown) {
    // Toggle dropdown on click
    attachBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      attachDropdown.classList.toggle("show");
    });

    // Handle dropdown item clicks
    attachDropdown.querySelectorAll(".attach-dropdown-item").forEach((item) => {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        const action = item.dataset.action;

        // Set accept based on action
        let accept = "";
        switch (action) {
          case "image":
            accept = "image/*";
            break;
          case "pdf":
            accept = ".pdf";
            break;
          case "code":
            accept = ".py,.js,.ts,.html,.css,.json,.md";
            break;
          case "text":
            accept = ".txt,.md,.csv,.json";
            break;
          case "all":
          default:
            accept = "image/*,.pdf,.txt,.csv,.json,.md,.py,.js,.html,.css";
        }

        fileInput.accept = accept;
        attachDropdown.classList.remove("show");
        fileInput.click();
      });
    });

    // Close dropdown when clicking outside
    document.addEventListener("click", (e) => {
      if (!attachWrapper.contains(e.target)) {
        attachDropdown.classList.remove("show");
      }
    });
  }

  fileInput.addEventListener("change", (e) => {
    handleFileSelection(e.target.files);
    fileInput.value = "";
  });

  // Drag & drop
  const chatContainer = document.getElementById("chat-container");
  const inputZone = document.getElementById("input-zone");

  for (const zone of [chatContainer, inputZone]) {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      inputZone.style.borderColor = "#00ffc8";
      inputZone.style.background = "rgba(0, 255, 200, 0.05)";
    });
    zone.addEventListener("dragleave", (e) => {
      e.preventDefault();
      inputZone.style.borderColor = "";
      inputZone.style.background = "";
    });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      inputZone.style.borderColor = "";
      inputZone.style.background = "";
      if (e.dataTransfer.files.length > 0)
        handleFileSelection(e.dataTransfer.files);
    });
  }

  // Ctrl+V images
  document.addEventListener("paste", (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length > 0) handleFileSelection(files);
  });

  // === BOUTONS SYSTÈME ===
  document.getElementById("btn-newchat")?.addEventListener("click", () => {
    window.api.sendCommand("newchat");
    clearChat();
    showSystemMessage("Nouvelle conversation", "success");
    // La liste sera mise à jour via l'event 'conversations'
  });

  document.getElementById("btn-reset")?.addEventListener("click", () => {
    if (confirm("Réinitialiser la mémoire ?")) {
      window.api.sendCommand("reset");
      clearChat();
    }
  });

  document.getElementById("btn-gpu")?.addEventListener("click", () => {
    window.api.sendCommand("gpu");
  });

  document.getElementById("btn-export")?.addEventListener("click", () => {
    window.api.sendCommand("export");
  });

  document.getElementById("btn-restart")?.addEventListener("click", () => {
    if (confirm("Redémarrer Python ?")) {
      showSystemMessage("Redémarrage...", "warning");
      window.api.restartPython();
    }
  });

  // === SÉLECTEUR MODE ===
  const modeSelector = document.getElementById("mode-selector");
  const agentSelector = document.getElementById("agent-selector");
  const pipelineSelector = document.getElementById("pipeline-selector");

  if (modeSelector) {
    modeSelector.addEventListener("change", () => {
      const mode = modeSelector.value;
      state.currentMode = mode;

      // Afficher/masquer les sélecteurs agents/pipelines
      if (mode === "agent") {
        agentSelector.style.display = "inline-block";
        pipelineSelector.style.display = "none";
      } else if (mode === "pipeline") {
        agentSelector.style.display = "none";
        pipelineSelector.style.display = "inline-block";
      } else {
        // Auto
        agentSelector.style.display = "none";
        pipelineSelector.style.display = "none";
      }
    });
  }

  // Reset agent/pipeline selection on mode change
  if (agentSelector) {
    agentSelector.addEventListener("change", () => {
      state.selectedAgent = agentSelector.value || null;
    });
  }

  if (pipelineSelector) {
    pipelineSelector.addEventListener("change", () => {
      state.selectedPipeline = pipelineSelector.value || null;
    });
  }
}

// ============================================================================
// API LISTENERS (Python → UI)
// ============================================================================

function setupApiListeners() {
  window.api.onReady((data) => {
    console.log("✅ Système prêt:", data);
    showSystemMessage("Système prêt!", "success");
    updateStatus("ready", data.version || "8.0");
    // Charger la liste des conversations
    if (data.conversations) {
      renderConversationList(data.conversations);
    }
    // Auto-refresh GPU au lancement
    window.api.sendCommand("gpu");
  });

  window.api.onSystemLog((log) => {
    console.log(`[${log.level}] ${log.message}`);
    if (log.level === "error") {
      showSystemMessage(log.message, "error");
    }
  });

  window.api.onUserMessage((data) => {
    addMessage("user", data);
  });

  // === STREAMING ===
  window.api.onStream((data) => {
    const lastMsg = state.messages[state.messages.length - 1];
    if (!lastMsg || lastMsg.role !== "assistant") {
      const agent = state.currentAgent || data.agent || "agent";
      addMessage("assistant", "", agent);
    }
    appendToLastMessage(data.content);
  });

  // === ROUTING ===
  window.api.onAgentSelected((data) => {
    if (data.type === "routed") {
      state.currentAgent = data.data;
      updateAgentBadge(data.data);
      // Ne PAS créer de message ici — onStream le fera
    }
  });

  // === PIPELINE ===
  window.api.onPipelineEvent((data) => {
    handlePipelineEvent(data);
  });

  window.api.onProgressUpdate((data) => {
    updateProgressBar(data.percentage, data.message);
  });

  // === MÉTRIQUES ===
  window.api.onTaskDone((stats) => {
    state.isProcessing = false;
    // Badge agent idle (le modèle est déchargé après la réponse)
    updateAgentBadge("AUCUN");
    updateMetrics({
      tokensPerSec: stats.tok_s || 0,
      tokens: stats.tokens || 0,
    });
    // Rendre le markdown du dernier message assistant
    renderLastMessageMarkdown();
    // Injecter les sources en attente (reçues avant la fin du stream)
    if (state.pendingSources) {
      displaySources(state.pendingSources);
      state.pendingSources = null;
    }
    // Auto-refresh GPU après chaque réponse
    window.api.sendCommand("gpu");
    focusInput();
  });

  window.api.onGpuUpdate((data) => {
    updateMetrics({
      vramUsed: data.vram_used_mb,
      vramTotal: data.vram_total_mb,
      gpuUtil: data.gpu_util,
      temperature: data.temp_c,
    });
  });

  window.api.onSources((data) => {
    // Stocker les sources — elles seront injectées après la fin du stream
    // (renderLastMessageMarkdown est appelé sur onTaskDone)
    state.pendingSources = data;
  });

  window.api.onCustomEvent((event) => {
    console.log("Custom event:", event);
  });

  // === CONVERSATIONS ===
  window.api.onConversationsList((conversations) => {
    renderConversationList(conversations);
  });

  window.api.onConversationLoaded((data) => {
    // Recharger l'historique dans le chat
    clearChat();
    if (data.history) {
      data.history.forEach((msg) => {
        if (msg.role === "user") {
          addMessage("user", msg.content);
        } else if (msg.role === "assistant" && !msg.intermediate) {
          addMessage("assistant", msg.content, msg.agent || "agent");
          // Rendre le markdown immédiatement (pas de streaming)
          renderLastMessageMarkdown();
        }
      });
    }
    showSystemMessage("Conversation chargée", "success");
  });
}

// ============================================================================
// GESTION FICHIERS ATTACHÉS
// ============================================================================

function handleFileSelection(fileList) {
  for (const file of fileList) {
    if (file.size > 20 * 1024 * 1024) {
      showSystemMessage(
        `Fichier trop volumineux: ${file.name} (max 20MB)`,
        "error",
      );
      continue;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      const base64Full = e.target.result;
      const base64Data = base64Full.split(",")[1];
      const isImage = file.type.startsWith("image/");

      state.attachedFiles.push({
        name: file.name,
        type: file.type,
        size: file.size,
        base64: base64Data,
        isImage: isImage,
        preview: isImage ? base64Full : null,
      });
      renderAttachmentPreview();
      updateAttachBtn();
    };
    reader.readAsDataURL(file);
  }
}

function renderAttachmentPreview() {
  const container = document.getElementById("attachments-preview");
  container.innerHTML = "";

  state.attachedFiles.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "attachment-item";

    if (file.isImage && file.preview) {
      item.innerHTML = `
                <img src="${file.preview}" alt="${file.name}" />
                <div class="attachment-info">
                    <span class="attachment-name">${file.name}</span>
                    <span class="attachment-size">${formatFileSize(file.size)}</span>
                </div>
                <button class="attachment-remove" data-index="${index}">&times;</button>
            `;
    } else {
      const icon = getFileIcon(file.type, file.name);
      item.innerHTML = `
                <div class="file-icon">${icon}</div>
                <div class="attachment-info">
                    <span class="attachment-name">${file.name}</span>
                    <span class="attachment-size">${formatFileSize(file.size)}</span>
                </div>
                <button class="attachment-remove" data-index="${index}">&times;</button>
            `;
    }
    container.appendChild(item);
  });

  container.querySelectorAll(".attachment-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = parseInt(e.target.dataset.index);
      state.attachedFiles.splice(idx, 1);
      renderAttachmentPreview();
      updateAttachBtn();
    });
  });
}

function updateAttachBtn() {
  const btn = document.getElementById("attach-btn");
  if (state.attachedFiles.length > 0) {
    btn.classList.add("has-files");
    btn.textContent = `+${state.attachedFiles.length}`;
  } else {
    btn.classList.remove("has-files");
    btn.textContent = "+";
  }
}

function getFileIcon(mimeType, name) {
  if (mimeType === "application/pdf") return "📄";
  if (name.endsWith(".py")) return "🐍";
  if (name.endsWith(".js") || name.endsWith(".ts")) return "📜";
  if (name.endsWith(".html") || name.endsWith(".css")) return "🌐";
  if (name.endsWith(".json")) return "📋";
  if (name.endsWith(".md") || name.endsWith(".txt")) return "📝";
  if (name.endsWith(".csv")) return "📊";
  return "📁";
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function clearAttachments() {
  state.attachedFiles = [];
  renderAttachmentPreview();
  updateAttachBtn();
}

// ============================================================================
// ENVOI MESSAGE
// ============================================================================

async function sendMessage() {
  const input = document.getElementById("user-input");
  const text = input.value.trim();

  if ((!text && state.attachedFiles.length === 0) || state.isProcessing) return;

  input.value = "";
  input.style.height = "auto";
  state.isProcessing = true;

  // Déterminer le mode et l'agent/pipeline sélectionné
  let mode = state.currentMode;
  let selectedAgent = null;
  let selectedPipeline = null;

  if (mode === "agent" && state.selectedAgent) {
    selectedAgent = state.selectedAgent;
  } else if (mode === "pipeline" && state.selectedPipeline) {
    selectedPipeline = state.selectedPipeline;
  } else {
    // Si mode agent/pipeline sans sélection, revenir à auto
    mode = "auto";
  }

  let result;

  if (state.attachedFiles.length > 0) {
    const files = state.attachedFiles.map((f) => ({
      name: f.name,
      type: f.type,
      size: f.size,
      base64: f.base64,
      isImage: f.isImage,
    }));

    const fileNames = files.map((f) => f.name).join(", ");
    const displayText = text ? `${text}\n📎 ${fileNames}` : `📎 ${fileNames}`;
    addMessage("user", displayText);

    result = await window.api.sendInputWithFiles(
      text || "Décris ce que tu vois dans cette image.",
      files,
      mode,
      selectedAgent,
      selectedPipeline,
    );
    clearAttachments();
  } else {
    addMessage("user", text);
    result = await window.api.sendInput(
      text,
      mode,
      selectedAgent,
      selectedPipeline,
    );
  }

  if (!result.success) {
    showSystemMessage("Erreur: " + result.error, "error");
    state.isProcessing = false;
  }
}

// ============================================================================
// GESTION CHAT + MARKDOWN
// ============================================================================

function addMessage(role, content, agent = null) {
  const chatBox = document.getElementById("chat-messages");

  const msgDiv = document.createElement("div");
  msgDiv.className = `message message-${role}`;
  msgDiv.dataset.role = role;

  if (role === "assistant") {
    const agentLabel = document.createElement("div");
    agentLabel.className = "agent-label";
    agentLabel.textContent = agent ? `🤖 ${agent.toUpperCase()}` : "🤖 AGENT";
    msgDiv.appendChild(agentLabel);
  }

  const contentDiv = document.createElement("div");
  contentDiv.className = "message-content";

  if (role === "user") {
    // Les messages utilisateur restent en texte brut
    contentDiv.textContent = content;
  } else {
    // Les messages assistant seront rendus en markdown à la fin du stream
    contentDiv.textContent = content;
  }

  msgDiv.appendChild(contentDiv);
  chatBox.appendChild(msgDiv);
  scrollToBottom();

  state.messages.push({ role, content, agent, element: msgDiv });
}

function appendToLastMessage(chunk) {
  if (state.messages.length === 0) return;

  const lastMsg = state.messages[state.messages.length - 1];
  if (lastMsg.role !== "assistant") return;

  // Synchroniser le state
  lastMsg.content += chunk;

  // Pendant le streaming : texte brut (plus performant)
  const contentDiv = lastMsg.element.querySelector(".message-content");
  contentDiv.textContent = lastMsg.content;

  scrollToBottom();
}

/**
 * NEXUS HIVE v1.0 - Renderer Process
 * ====================================
 */
function renderLastMessageMarkdown() {
  if (state.messages.length === 0) return;

  const lastMsg = state.messages[state.messages.length - 1];
  if (lastMsg.role !== "assistant" || !lastMsg.content) return;

  const contentDiv = lastMsg.element.querySelector(".message-content");

  if (typeof marked !== "undefined") {
    try {
      // Convertir le markdown en HTML
      contentDiv.innerHTML = marked.parse(lastMsg.content);

      // Ajouter les boutons "Copier" sur les blocs de code
      contentDiv.querySelectorAll("pre").forEach((pre) => {
        const copyBtn = document.createElement("button");
        copyBtn.className = "code-copy-btn";
        copyBtn.textContent = "Copier";
        copyBtn.addEventListener("click", () => {
          const code =
            pre.querySelector("code")?.textContent || pre.textContent;
          navigator.clipboard.writeText(code).then(() => {
            copyBtn.textContent = "✓ Copié";
            setTimeout(() => {
              copyBtn.textContent = "Copier";
            }, 2000);
          });
        });
        pre.style.position = "relative";
        pre.appendChild(copyBtn);
      });
    } catch (e) {
      console.error("Erreur marked.js:", e);
      // Fallback : garder le texte brut
    }
  }

  scrollToBottom();
}

function clearChat() {
  document.getElementById("chat-messages").innerHTML = "";
  state.messages = [];
  state.currentAgent = null;
  state.isProcessing = false;
  state.pendingSources = null;
  updateAgentBadge("AUCUN");
}

function scrollToBottom() {
  const chatBox = document.getElementById("chat-messages");
  chatBox.scrollTop = chatBox.scrollHeight;
}

// ============================================================================
// DASHBOARD
// ============================================================================

function updateMetrics(newMetrics) {
  Object.assign(state.metrics, newMetrics);

  if (newMetrics.tokensPerSec !== undefined) {
    const elem = document.getElementById("metric-tokens");
    if (elem) elem.textContent = Math.round(newMetrics.tokensPerSec);
  }

  if (newMetrics.vramUsed !== undefined) {
    const elem = document.getElementById("metric-vram");
    if (elem) {
      const pct = ((newMetrics.vramUsed / newMetrics.vramTotal) * 100).toFixed(
        1,
      );
      elem.textContent = `${newMetrics.vramUsed}/${newMetrics.vramTotal}MB (${pct}%)`;
    }
    const bar = document.getElementById("vram-bar");
    if (bar) {
      const pct = (newMetrics.vramUsed / newMetrics.vramTotal) * 100;
      bar.style.width = pct + "%";
      bar.className = pct > 80 ? "progress-fill danger" : "progress-fill";
    }
  }

  if (newMetrics.gpuUtil !== undefined) {
    const elem = document.getElementById("metric-gpu");
    if (elem) elem.textContent = `${newMetrics.gpuUtil}%`;
  }

  if (newMetrics.temperature !== undefined) {
    const elem = document.getElementById("metric-temp");
    if (elem) elem.textContent = `${newMetrics.temperature}°C`;
  }
}

function updateAgentBadge(agent) {
  const badge = document.getElementById("current-agent-badge");
  if (badge) {
    badge.textContent = agent.toUpperCase();
    badge.className = agent === "AUCUN" ? "agent-badge" : "agent-badge active";
  }
}

function updateProgressBar(percentage, message = "") {
  const bar = document.getElementById("progress-bar");
  const label = document.getElementById("progress-label");
  if (bar) bar.style.width = percentage + "%";
  if (label) label.textContent = message || `${percentage}%`;
}

// ============================================================================
// PIPELINE
// ============================================================================

function handlePipelineEvent(data) {
  const { type, pipeline, step, agent } = data;

  switch (type) {
    case "pipeline_start":
      state.pipeline = { active: true, name: pipeline, step: 0, totalSteps: 2 };
      showSystemMessage(`Pipeline ${pipeline} démarré`, "info");
      break;

    case "pipeline_step":
      state.pipeline.step = step;
      updateProgressBar(
        (step / state.pipeline.totalSteps) * 100,
        `Étape ${step}/${state.pipeline.totalSteps}: ${agent}`,
      );
      if (agent) {
        state.currentAgent = agent;
        updateAgentBadge(agent);
      }
      break;

    case "pipeline_done":
      state.pipeline.active = false;
      state.isProcessing = false;
      updateProgressBar(100, "Terminé");
      showSystemMessage(`Pipeline ${pipeline} terminé`, "success");
      renderLastMessageMarkdown();
      focusInput();
      break;
  }
}

// ============================================================================
// SOURCES WEB
// ============================================================================

function displaySources(sources) {
  if (!sources || sources.length === 0) return;

  // Trouver le dernier message assistant dans le DOM
  const chatBox = document.getElementById("chat-messages");
  if (!chatBox) return;

  const allMessages = chatBox.querySelectorAll(".message-assistant");
  if (allMessages.length === 0) return;
  const lastAssistantMsg = allMessages[allMessages.length - 1];

  // Supprimer un éventuel badge sources déjà présent sur ce message
  const existing = lastAssistantMsg.querySelector(".sources-badge-wrapper");
  if (existing) existing.remove();

  // Créer le wrapper
  const wrapper = document.createElement("div");
  wrapper.className = "sources-badge-wrapper";

  // Badge cliquable
  const badge = document.createElement("button");
  badge.className = "sources-badge";
  badge.innerHTML = `🔍 ${sources.length} Source${sources.length > 1 ? "s" : ""}`;
  badge.setAttribute("aria-expanded", "false");

  // Liste dépliable
  const list = document.createElement("div");
  list.className = "sources-list";
  list.style.display = "none";

  sources.forEach((source, i) => {
    const item = document.createElement("div");
    item.className = "source-item";
    item.innerHTML = `
      <span class="source-index">${i + 1}</span>
      <div>
        <div class="source-title">${source.title || "Sans titre"}</div>
        <a href="${source.url}" class="source-url" target="_blank">${source.url}</a>
      </div>
    `;
    list.appendChild(item);
  });

  // Toggle au clic
  badge.addEventListener("click", () => {
    const isOpen = list.style.display !== "none";
    list.style.display = isOpen ? "none" : "block";
    badge.setAttribute("aria-expanded", String(!isOpen));
    badge.classList.toggle("open", !isOpen);
  });

  wrapper.appendChild(badge);
  wrapper.appendChild(list);
  lastAssistantMsg.appendChild(wrapper);

  scrollToBottom();
}

// ============================================================================
// HISTORIQUE CONVERSATIONS
// ============================================================================

function renderConversationList(conversations) {
  const container = document.getElementById("history-list");
  if (!container) return;

  if (!conversations || conversations.length === 0) {
    container.innerHTML =
      '<div style="color:#888;font-size:12px;padding:10px;text-align:center">Aucune conversation</div>';
    return;
  }

  container.innerHTML = "";

  conversations.forEach((conv) => {
    const item = document.createElement("div");
    item.className = "conv-item";
    item.dataset.convId = conv.id;

    // Formater la date
    const date = new Date(conv.updated || conv.created);
    const timeStr = date.toLocaleTimeString("fr-FR", {
      hour: "2-digit",
      minute: "2-digit",
    });
    const dateStr = date.toLocaleDateString("fr-FR", {
      day: "2-digit",
      month: "2-digit",
    });

    const title = conv.title || "Nouvelle conversation";
    const msgCount = conv.message_count || 0;

    item.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:start">
                <div class="conv-title" style="flex:1">${title}</div>
                <span class="conv-delete" data-id="${conv.id}" style="color:#555;cursor:pointer;font-size:14px;padding:0 4px;margin-left:4px">&times;</span>
            </div>
            <div class="conv-meta">${dateStr} ${timeStr} · ${msgCount} msg</div>
        `;

    item.addEventListener("click", () => {
      // Charger cette conversation
      window.api.loadConversation(conv.id);
      // Marquer comme active
      container
        .querySelectorAll(".conv-item")
        .forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
    });

    // Bouton supprimer - utiliser le modal custom
    item.querySelector(".conv-delete").addEventListener("click", (e) => {
      e.stopPropagation(); // Ne pas charger la conversation
      showConfirmModal(
        "Supprimer la conversation ?",
        `Voulez-vous vraiment supprimer "${title}" ? Cette action est irréversible.`,
        () => {
          window.api.sendCommand(`deletechat ${conv.id}`);
        },
      );
    });

    container.appendChild(item);
  });
}

// ============================================================================
// UTILITAIRES
// ============================================================================

function showSystemMessage(text, level = "info") {
  const chatBox = document.getElementById("chat-messages");

  const msgDiv = document.createElement("div");
  msgDiv.className = `message message-system ${level}`;
  msgDiv.textContent = text;

  chatBox.appendChild(msgDiv);
  scrollToBottom();

  setTimeout(() => {
    msgDiv.remove();
  }, 5000);
}

function updateStatus(status, version = "") {
  const statusElem = document.getElementById("status-indicator");
  const versionElem = document.getElementById("version-label");
  if (statusElem) statusElem.className = `status-indicator ${status}`;
  if (versionElem && version) versionElem.textContent = `v${version}`;
}

function focusInput() {
  document.getElementById("user-input")?.focus();
}

// Modal de confirmation custom
let confirmCallback = null;

function showConfirmModal(title, message, onConfirm) {
  const modal = document.getElementById("confirm-modal");
  const titleEl = document.getElementById("confirm-title");
  const messageEl = document.getElementById("confirm-message");
  const cancelBtn = document.getElementById("confirm-cancel");
  const okBtn = document.getElementById("confirm-ok");

  if (!modal) return;

  titleEl.textContent = title;
  messageEl.textContent = message;
  confirmCallback = onConfirm;

  modal.classList.add("show");

  // Handlers
  const closeModal = () => {
    modal.classList.remove("show");
    confirmCallback = null;
  };

  cancelBtn.onclick = closeModal;
  okBtn.onclick = () => {
    if (confirmCallback) confirmCallback();
    closeModal();
  };

  // Fermer en cliquant outside
  modal.onclick = (e) => {
    if (e.target === modal) closeModal();
  };

  // Escape key
  const escapeHandler = (e) => {
    if (e.key === "Escape") {
      closeModal();
      document.removeEventListener("keydown", escapeHandler);
    }
  };
  document.addEventListener("keydown", escapeHandler);
}

// ============================================================================
// EXPORT GLOBAL (debug console)
// ============================================================================

window.nexus = { state, sendMessage, clearChat, updateMetrics };
console.log("✅ Renderer v1.0 initialisé. Debug: window.nexus");
