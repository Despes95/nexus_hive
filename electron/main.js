/**
 * NEXUS HIVE v7.1 - Main Process
 * ================================
 * Gère le cycle de vie Electron + spawn Python headless
 *
 * ARCHITECTURE :
 *   Electron (main.js) ──stdin JSON──→ Python (nexus_hive_v7.py)
 *   Electron (main.js) ←──stdout JSON── Python (nexus_hive_v7.py)
 *
 * PROTOCOLE STDIN (Electron → Python) :
 *   Chaque ligne = 1 message JSON :
 *   {"type": "input", "text": "..."}                    → message texte
 *   {"type": "command", "command": "/reset"}             → commande système
 *   {"type": "input_with_files", "text":"...", "files":[...]} → message + fichiers
 *
 * PROTOCOLE STDOUT (Python → Electron) :
 *   Chaque ligne = 1 event JSON :
 *   {"event": "stream", "data": "...", "protocol_version": 1, ...}
 */

const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const log = require("electron-log");

// Configuration des logs
log.transports.file.level = "debug";
log.transports.console.level = "debug";
// Fix encodage UTF-8 pour les logs console Windows
log.transports.console.useStyles = false;

let mainWindow;
let pythonProcess;
let isQuitting = false;

// ============================================================================
// CRÉATION FENÊTRE
// ============================================================================

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    backgroundColor: "#0a0e27",
    title: "Nexus Hive v7.0",
    icon: path.join(__dirname, "assets", "icon.png"),
  });

  mainWindow.loadFile("index.html");

  // DevTools: F12 pour toggle, ou auto-open si DEBUG
  if (process.env.DEBUG) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.webContents.on("before-input-event", (event, input) => {
    if (input.key === "F12") {
      mainWindow.webContents.toggleDevTools();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  log.info("Fenêtre affichée");
}

// ============================================================================
// GESTION PYTHON HEADLESS
// ============================================================================

function startPython() {
  const pythonPath = process.env.PYTHON_PATH || "python";
  const projectRoot = path.join(__dirname, "..");

  log.info(
    `Démarrage Python: ${pythonPath} -m nexus_hive --headless (cwd: ${projectRoot})`,
  );

  pythonProcess = spawn(pythonPath, ["-m", "nexus_hive", "--headless"], {
    cwd: projectRoot,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      PYTHONLEGACYWINDOWSSTDIO: "0",
    },
  });

  log.info(`Process Python démarré (PID: ${pythonProcess.pid})`);

  // === STDOUT : Events JSON (une ligne = un event) ===
  // IMPORTANT : stdout.on('data') peut recevoir des lignes incomplètes
  // si le buffer système coupe au milieu d'une ligne JSON.
  // On accumule dans un buffer et on ne parse que les lignes complètes.
  let stdoutBuffer = "";

  pythonProcess.stdout.setEncoding("utf8");
  pythonProcess.stdout.on("data", (data) => {
    // Ajouter les nouvelles données au buffer
    stdoutBuffer += data.toString();

    // Traiter toutes les lignes complètes (terminées par \n)
    const lines = stdoutBuffer.split("\n");

    // La dernière "ligne" peut être incomplète → la garder dans le buffer
    stdoutBuffer = lines.pop();

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const event = JSON.parse(trimmed);
        handlePythonEvent(event);
      } catch (err) {
        // Ligne non-JSON (ex: print() debug Python)
        log.debug(`[Python stdout] ${trimmed}`);
      }
    }
  });

  // === STDERR : Logs & erreurs ===
  pythonProcess.stderr.setEncoding("utf8");
  pythonProcess.stderr.on("data", (data) => {
    log.error(`[Python stderr] ${data.toString()}`);
    if (mainWindow) {
      mainWindow.webContents.send("system-log", {
        level: "error",
        message: data.toString(),
        timestamp: new Date().toISOString(),
      });
    }
  });

  // === EXIT ===
  pythonProcess.on("exit", (code, signal) => {
    log.error(`Processus Python terminé avec code: ${code}, signal: ${signal}`);

    if (!isQuitting) {
      // Crash inattendu → Proposer restart
      if (mainWindow) {
        mainWindow.webContents.send("system-event", {
          type: "python-crash",
          code,
          signal,
        });
      }
    }
  });

  pythonProcess.on("error", (err) => {
    log.error(`Erreur spawn Python: ${err.message}`);
    if (mainWindow) {
      mainWindow.webContents.send("system-log", {
        level: "error",
        message: `Python error: ${err.message}`,
        timestamp: new Date().toISOString(),
      });
    }
  });
}

// ============================================================================
// GESTIONNAIRE EVENTS PYTHON → ELECTRON
// ============================================================================

function handlePythonEvent(event) {
  if (!mainWindow) return;

  log.debug(`Event: ${event.event}`);

  const { event: type, data, ...meta } = event;

  switch (type) {
    // === SYSTÈME ===
    case "ready":
      mainWindow.webContents.send("system-ready", data);
      break;

    case "prompt":
      mainWindow.webContents.send("user-prompt", data);
      break;

    case "terminated":
      log.info("Python terminé normalement");
      break;

    // === MESSAGES ===
    case "user_message":
      mainWindow.webContents.send("user-message", data);
      break;

    case "stream":
      mainWindow.webContents.send("agent-stream", {
        content: data,
        agent: meta.agent,
        timestamp: meta.timestamp,
      });
      break;

    // === ROUTING ===
    case "routing":
    case "routed":
      mainWindow.webContents.send("agent-selected", {
        type,
        data,
        timestamp: meta.timestamp,
      });
      break;

    // === PIPELINE ===
    case "pipeline_start":
    case "pipeline_step":
    case "pipeline_done":
      mainWindow.webContents.send("pipeline-event", {
        type,
        pipeline: data,
        ...meta,
      });
      break;

    // === PROGRESSION ===
    case "progress":
      mainWindow.webContents.send("progress-update", data);
      break;

    // === MÉTRIQUES ===
    case "done":
      mainWindow.webContents.send("task-done", meta.stats || {});
      break;

    case "gpu_info":
      mainWindow.webContents.send("gpu-update", data);
      break;

    case "sources":
      mainWindow.webContents.send("sources", data);
      break;

    case "conversations":
      mainWindow.webContents.send("conversations-list", data);
      break;

    case "conversation_loaded":
      mainWindow.webContents.send("conversation-loaded", data);
      break;

    // === LOGS ===
    case "info":
    case "success":
    case "warning":
    case "error":
    case "debug":
      mainWindow.webContents.send("system-log", {
        level: type,
        message: data,
        timestamp: meta.timestamp,
      });
      break;

    // === CUSTOM ===
    default:
      mainWindow.webContents.send("custom-event", event);
  }
}

// ============================================================================
// IPC HANDLERS : ELECTRON → PYTHON
// ============================================================================
// Tous les messages sont envoyés en JSON sur stdin (protocole unifié)
// Format : {"type": "input|command|input_with_files", ...}\n
// ============================================================================

// Envoyer un message texte simple
// mode = "auto" | "agent" | "pipeline"
// selectedAgent = nom de l'agent (si mode = "agent")
// selectedPipeline = nom du pipeline (si mode = "pipeline")
ipcMain.handle(
  "send-input",
  async (
    event,
    text,
    mode = "auto",
    selectedAgent = null,
    selectedPipeline = null,
  ) => {
    if (!pythonProcess || pythonProcess.killed) {
      log.error("Python process non actif");
      return { success: false, error: "Python non démarré" };
    }

    try {
      // Protocole JSON unifié : type "input" pour les messages texte
      const payload = JSON.stringify({
        type: "input",
        text: text,
        mode: mode,
        selected_agent: selectedAgent,
        selected_pipeline: selectedPipeline,
      });
      log.info(`Envoyé à Python: ${text.substring(0, 50)}... (mode: ${mode})`);
      pythonProcess.stdin.write(payload + "\n");
      return { success: true };
    } catch (err) {
      log.error(`Erreur envoi stdin: ${err.message}`);
      return { success: false, error: err.message };
    }
  },
);

// Envoyer un message avec fichiers (images, PDF, code, etc.)
ipcMain.handle(
  "send-input-with-files",
  async (
    event,
    text,
    files,
    mode = "auto",
    selectedAgent = null,
    selectedPipeline = null,
  ) => {
    if (!pythonProcess || pythonProcess.killed) {
      log.error("Python process non actif");
      return { success: false, error: "Python non démarré" };
    }

    try {
      // Protocole JSON unifié : type "input_with_files"
      // Les fichiers sont envoyés en base64 (le Python les décode)
      // IMPORTANT : le base64 des images peut contenir le préfixe "data:image/...;base64,"
      // Le Python le nettoiera avant de l'envoyer à Ollama (voir sanitize_base64_image)
      const payload = JSON.stringify({
        type: "input_with_files",
        text: text,
        files: files.map((f) => ({
          name: f.name,
          type: f.type,
          size: f.size,
          base64: f.base64,
          isImage: f.isImage,
        })),
        mode: mode,
        selected_agent: selectedAgent,
        selected_pipeline: selectedPipeline,
      });

      log.info(
        `Envoyé à Python avec ${files.length} fichier(s): ${text.substring(0, 50)}... (mode: ${mode})`,
      );
      pythonProcess.stdin.write(payload + "\n");
      return { success: true };
    } catch (err) {
      log.error(`Erreur envoi stdin avec fichiers: ${err.message}`);
      return { success: false, error: err.message };
    }
  },
);

// Commandes système (boutons de l'interface)
ipcMain.handle("system-command", async (event, command) => {
  log.info(`Commande système: ${command}`);

  // Commandes simples (sans arguments)
  const simpleCommands = [
    "reset",
    "gpu",
    "models",
    "status",
    "export",
    "newchat",
    "listchats",
  ];

  if (simpleCommands.includes(command)) {
    // Envoyer la commande au Python via le protocole JSON unifié
    if (pythonProcess && !pythonProcess.killed) {
      const payload = JSON.stringify({
        type: "command",
        command: `/${command}`,
      });
      pythonProcess.stdin.write(payload + "\n");
      return { success: true };
    }
    return { success: false, error: "Python non actif" };
  }

  switch (command) {
    case "restart-python":
      return restartPython();

    default:
      // Commandes avec arguments (ex: "deletechat conv_123")
      if (pythonProcess && !pythonProcess.killed) {
        const payload = JSON.stringify({
          type: "command",
          command: `/${command}`,
        });
        pythonProcess.stdin.write(payload + "\n");
        return { success: true };
      }
      return { success: false, error: "Python non actif" };
  }
});

// Status app
ipcMain.handle("get-status", async () => {
  return {
    pythonAlive: pythonProcess && !pythonProcess.killed,
    pythonPid: pythonProcess?.pid,
    version: "7.0.0",
  };
});

// Restart Python
ipcMain.handle("restart-python", async () => {
  return restartPython();
});

function restartPython() {
  log.info("Redémarrage Python...");

  if (pythonProcess && !pythonProcess.killed) {
    pythonProcess.kill();
  }

  setTimeout(() => {
    startPython();
  }, 1000);

  return { success: true };
}

// Charger une conversation
ipcMain.handle("load-conversation", async (event, convId) => {
  if (!pythonProcess || pythonProcess.killed) {
    return { success: false, error: "Python non actif" };
  }
  const payload = JSON.stringify({
    type: "command",
    command: `/loadchat ${convId}`,
  });
  pythonProcess.stdin.write(payload + "\n");
  return { success: true };
});

// ============================================================================
// LIFECYCLE APP
// ============================================================================

app.on("ready", () => {
  log.info("Application démarrée");
  startPython();
  createWindow();
});

app.on("window-all-closed", () => {
  log.info("Toutes les fenêtres fermées");
  isQuitting = true;

  if (pythonProcess && !pythonProcess.killed) {
    log.info("Arrêt du processus Python...");
    pythonProcess.kill("SIGTERM");
  }

  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (mainWindow === null) {
    createWindow();
  }
});

app.on("before-quit", () => {
  log.info("Fermeture de l'application");
  isQuitting = true;
});

// Gestion des erreurs non catchées
process.on("uncaughtException", (err) => {
  log.error("Uncaught Exception:", err);
});

process.on("unhandledRejection", (reason) => {
  log.error("Unhandled Rejection:", reason);
});
