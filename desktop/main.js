// Application Windows « Macro Forex » : une fenêtre dédiée qui affiche le terminal en ligne.
// Le site reste la source unique : toute mise à jour du terminal arrive sans réinstaller l'application.
const { app, BrowserWindow, shell, Menu, ipcMain, net } = require("electron");
const path = require("path");
const fs = require("fs");

const SITE = "https://philippembulu1-dot.github.io/macro-forex/";
/* ---- Traduction (anglais -> français) : le service gratuit de Google est appelé ici, depuis l'application, pour les pages externes ---- */
const CACHE_TR = new Map();
async function traduire(t) {
  if (CACHE_TR.has(t)) return CACHE_TR.get(t);
  const u = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=fr&dt=t&q=" + encodeURIComponent(t);
  let fr = null;
  try {
    const r = await net.fetch(u); if (r.ok) { const j = await r.json(); fr = (j[0] || []).map((x) => x[0]).join(""); }
  } catch (e) { /* secours ci-dessous */ }
  if (!fr) {
    try {
      const r = await net.fetch("https://api.mymemory.translated.net/get?langpair=en%7Cfr&q=" + encodeURIComponent(t.slice(0, 480)));
      if (r.ok) { const j = await r.json(); const o = j && j.responseData && j.responseData.translatedText; if (o && !/MYMEMORY WARNING|QUERY LENGTH/i.test(o)) fr = o; }
    } catch (e) { /* hors ligne */ }
  }
  if (fr) { if (CACHE_TR.size > 6000) CACHE_TR.clear(); CACHE_TR.set(t, fr); }
  return fr;
}
ipcMain.handle("mf-traduire", async (e, arr) => {
  if (!Array.isArray(arr) || arr.length > 60) return [];
  const list = arr.map((x) => (typeof x === "string" ? x.slice(0, 1800) : ""));
  const out = new Array(list.length).fill(null); let i = 0;
  const worker = async () => { while (i < list.length) { const k = i++; if (list[k]) out[k] = await traduire(list[k]); } };
  await Promise.all([worker(), worker(), worker(), worker()]);
  return out;
});

const PREF = { contextIsolation: true, nodeIntegration: false, nodeIntegrationInSubFrames: true, sandbox: true, spellcheck: false, preload: path.join(__dirname, "preload.js") };
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
/* Les pages d'autres sites s'ouvrent dans une fenêtre de l'application, traduites en français.
   Ctrl+Maj+O : ouvrir la page dans le navigateur ; Alt+← : retour ; Ctrl+W : fermer. */
function pageExterne(url) {
  const w = new BrowserWindow({ width: 1200, height: 860, minWidth: 380, minHeight: 500, backgroundColor: "#ffffff", title: "Macro Forex : page externe", icon: path.join(__dirname, "icon.png"), autoHideMenuBar: true, webPreferences: PREF });
  w.setMenuBarVisibility(false);
  w.loadURL(url);
  w.webContents.setWindowOpenHandler(({ url: u }) => { if (/^https?:/i.test(u)) w.loadURL(u); return { action: "deny" }; });
  w.webContents.on("page-title-updated", (e, t) => { e.preventDefault(); w.setTitle("Macro Forex : " + t); });
  w.webContents.on("before-input-event", (e, i) => {
    if (i.type !== "keyDown") return;
    if (i.control && i.shift && i.key.toLowerCase() === "o") { shell.openExternal(w.webContents.getURL()); e.preventDefault(); }
    if (i.control && i.key.toLowerCase() === "w") { w.close(); e.preventDefault(); }
    if (i.alt && i.key === "ArrowLeft" && w.webContents.navigationHistory.canGoBack()) { w.webContents.navigationHistory.goBack(); e.preventDefault(); }
    if (i.key === "F5") { w.webContents.reload(); e.preventDefault(); }
  });
}
function creer() {
  const s = lireEtat();
  win = new BrowserWindow({
    width: s.width || 1440, height: s.height || 900, x: s.x, y: s.y, minWidth: 380, minHeight: 600,
    backgroundColor: "#060607", title: "Macro Forex", icon: path.join(__dirname, "icon.png"), autoHideMenuBar: true, show: false,
    webPreferences: PREF
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
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:/i.test(url)) { if (interne(url)) win.loadURL(url); else pageExterne(url); } return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!interne(url) && !url.startsWith("file:")) { e.preventDefault(); if (/^https?:/i.test(url)) pageExterne(url); } });
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
