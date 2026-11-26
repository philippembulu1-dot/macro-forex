// Application Windows « Macro Forex » : une fenêtre dédiée qui affiche le terminal en ligne.
// Le site reste la source unique : toute mise à jour du terminal arrive sans réinstaller l'application.
const { app, BrowserWindow, shell, Menu } = require("electron");
const path = require("path");
const fs = require("fs");

const SITE = "https://philippembulu1-dot.github.io/macro-forex/";
const STATE = path.join(app.getPath("userData"), "fenetre.json");

function lireEtat() { try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch (e) { return {}; } }
function ecrireEtat(w) {
  try {
    const b = w.getBounds();
    fs.writeFileSync(STATE, JSON.stringify({ x: b.x, y: b.y, width: b.width, height: b.height, max: w.isMaximized() }));
  } catch (e) { /* sans importance */ }
}

if (!app.requestSingleInstanceLock()) { app.quit(); }

let win = null;
function creer() {
  const s = lireEtat();
  win = new BrowserWindow({
    width: s.width || 1440, height: s.height || 900, x: s.x, y: s.y, minWidth: 380, minHeight: 600,
    backgroundColor: "#060607", title: "Macro Forex", icon: path.join(__dirname, "icon.png"), autoHideMenuBar: true, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false }
  });
  Menu.setApplicationMenu(null);
  if (s.max) win.maximize();
  win.once("ready-to-show", () => win.show());
  win.loadURL(SITE);
  win.webContents.on("did-fail-load", (e, code, desc, url, principal) => {
    if (principal && code !== -3) win.loadFile(path.join(__dirname, "offline.html"));
  });
  // Les liens vers d'autres sites s'ouvrent dans le navigateur, pas dans l'application.
  const interne = (u) => { try { return new URL(u).origin === new URL(SITE).origin; } catch (e) { return false; } };
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:/i.test(url) && !interne(url)) shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!interne(url) && !url.startsWith("file:")) { e.preventDefault(); if (/^https?:/i.test(url)) shell.openExternal(url); } });
  win.on("close", () => ecrireEtat(win));
  win.on("closed", () => { win = null; });
  // F5 / Ctrl+R : recharger ; F11 : plein écran
  win.webContents.on("before-input-event", (e, i) => {
    if (i.type !== "keyDown") return;
    if (i.key === "F5" || (i.control && i.key.toLowerCase() === "r")) { win.webContents.loadURL(SITE); e.preventDefault(); }
    if (i.key === "F11") { win.setFullScreen(!win.isFullScreen()); e.preventDefault(); }
  });
}

app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.whenReady().then(creer);
app.on("window-all-closed", () => app.quit());
