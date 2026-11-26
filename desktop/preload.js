// Traduction en direct, anglais -> français, de toute page externe ouverte dans l'application
// (articles, calendriers, sites de banques centrales) et des cadres intégrés au terminal (Myfxbook…).
// Le terminal lui-même se traduit déjà avec son bouton « FR » : on ne touche pas à ses pages.
const { ipcRenderer } = require("electron");

const OWN = "philippembulu1-dot.github.io";
const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA|INPUT|SELECT|OPTION|CODE|PRE|SVG|CANVAS|IFRAME|KBD)$/;
const orig = new WeakMap(), done = new WeakMap();
let on = true, fails = 0;
const q = [], waiting = new Map();
let busy = false, timer = null;

function pageEnglish() {
  const l = (document.documentElement.lang || "").toLowerCase();
  if (l.startsWith("fr")) return false;
  if (l.startsWith("en")) return true;
  const s = (document.body ? document.body.innerText : "").slice(0, 3000).toLowerCase();
  const e = (s.match(/\b(the|and|of|to|in|for|is|are|with|that|this|from|on|by)\b/g) || []).length;
  const f = (s.match(/\b(le|la|les|des|du|et|est|dans|pour|avec|que|une|sur|par)\b/g) || []).length;
  return e >= 8 && e > f * 1.5;
}
function wanted(t) {
  return t.length >= 3 && t.length <= 1800 && /[A-Za-z]{3}/.test(t) && !/^[\d\s.,:;%$€£+\-–/()]+$/.test(t) && !/^https?:\/\//i.test(t) && !/^[A-Z0-9/._-]{2,8}$/.test(t);
}
function skipNode(n) {
  for (let el = n.parentNode; el && el.nodeType === 1; el = el.parentNode) {
    if (SKIP.test(el.nodeName.toUpperCase()) || el.getAttribute("translate") === "no" || el.isContentEditable) return true;
  }
  return false;
}
function setTxt(n, fr) {
  const raw = orig.get(n); if (raw == null) return;
  const v = /^\s*/.exec(raw)[0] + fr + /\s*$/.exec(raw)[0];
  done.set(n, v); if (n.nodeValue !== v) n.nodeValue = v;
}
function handle(n) {
  if (!on || n.nodeType !== 3) return;
  const cur = n.nodeValue; if (done.get(n) === cur) return;
  const t = cur.replace(/\s+/g, " ").trim(); if (!wanted(t) || skipNode(n)) return;
  orig.set(n, cur);
  if (!waiting.has(t)) { waiting.set(t, []); q.push(t); }
  waiting.get(t).push(n); kick();
}
function walk(root, fn) { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null); let n; const a = []; while ((n = w.nextNode())) a.push(n); a.forEach(fn); }
function kick() { if (!timer) timer = setTimeout(() => { timer = null; run(); }, 120); }
function run() {
  if (busy || !on || !q.length) return;
  const batch = []; let len = 0;
  while (q.length && batch.length < 30 && len + q[0].length < 4000) { const t = q.shift(); batch.push(t); len += t.length; }
  busy = true;
  ipcRenderer.invoke("mf-traduire", batch).then((out) => {
    fails = 0;
    batch.forEach((t, i) => { const fr = out && out[i]; (waiting.get(t) || []).forEach((n) => { if (fr && n.isConnected) setTxt(n, fr); }); waiting.delete(t); });
  }).catch(() => { fails++; batch.forEach((t) => waiting.delete(t)); }).then(() => { busy = false; if (q.length) setTimeout(run, fails ? Math.min(30000, 2000 * fails) : 80); });
}
function restore() { walk(document.body, (n) => { if (orig.has(n) && done.get(n) === n.nodeValue) { n.nodeValue = orig.get(n); done.delete(n); } }); }

function pill() {
  if (window !== window.top || document.getElementById("mf-fr-pill")) return;
  const d = document.createElement("div"); d.id = "mf-fr-pill"; d.setAttribute("translate", "no");
  d.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:2147483647;background:#060607;color:#f2b01e;border:1px solid #f2b01e;border-radius:14px;padding:5px 12px;font:12px system-ui,sans-serif;cursor:pointer;opacity:.92";
  const set = () => { d.textContent = on ? "Traduit en français · F9 : original" : "Original anglais · F9 : traduire"; };
  set(); d.title = "Cliquez ou appuyez sur F9";
  const toggle = () => { on = !on; set(); if (on) { fails = 0; walk(document.body, handle); } else { q.length = 0; waiting.clear(); restore(); } };
  d.addEventListener("click", toggle);
  window.addEventListener("keydown", (e) => { if (e.key === "F9") { toggle(); e.preventDefault(); } }, true);
  (document.body || document.documentElement).appendChild(d);
}

function start() {
  if (location.hostname === OWN || !/^https?:$/.test(location.protocol) || !document.body) return;
  if (!pageEnglish() && window === window.top) { setTimeout(() => { if (pageEnglish()) go(); }, 2500); return; }
  go();
}
let started = false;
function go() {
  if (started) return; started = true;
  pill();
  walk(document.body, handle);
  new MutationObserver((ms) => {
    if (!on) return;
    ms.forEach((m) => {
      if (m.type === "characterData") handle(m.target);
      else m.addedNodes.forEach((a) => { if (a.nodeType === 3) handle(a); else if (a.nodeType === 1 && a.id !== "mf-fr-pill") walk(a, handle); });
    });
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
}
window.addEventListener("DOMContentLoaded", () => setTimeout(start, 300));
