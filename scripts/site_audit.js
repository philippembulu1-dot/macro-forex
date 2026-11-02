const { chromium } = require('playwright');
const BASE = process.env.SITE || 'https://philippembulu1-dot.github.io/macro-forex/';
require('fs').mkdirSync('audit-out', { recursive: true });
(async () => {
  const b = await chromium.launch(); const out = 'audit-out/';
  for (const [name, vp] of [['PC', { width: 1400, height: 1000 }], ['TEL', { width: 390, height: 844 }]]) {
    const p = await b.newPage({ viewport: vp }); const errs = [];
    p.on('pageerror', e => errs.push('JS: ' + e.message));
    p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|ERR_|net::/.test(m.text())) errs.push('console: ' + m.text().slice(0, 160)); });
    const fails = []; p.on('requestfailed', r => fails.push(r.url().slice(0, 90) + ' → ' + (r.failure() || {}).errorText)); p.on('response', r => { if (r.status() >= 400) fails.push(r.url().slice(0, 90) + ' → ' + r.status()); });
    await p.goto('' + BASE + '#/'); await p.waitForTimeout(6000);
    const src = require('fs').readFileSync('index.html', 'utf8'); const blk = src.slice(src.indexOf('var BR = ['), src.indexOf('];', src.indexOf('var BR = [')));
    const brs = [...blk.matchAll(/id: "(\w+)", t: "([^"]+)".*?p: \[([^\]]*)\]/g)].map(m => [m[1], m[2], [...m[3].matchAll(/"([^"]+)"/g)].map(x => x[1])]);
    await p.waitForTimeout(6000);
    const st = await p.evaluate(() => ({ live: (document.getElementById('liveBar') || {}).innerText, gen: window.AUTO_GEN || null, bstat: (document.querySelector('.bstat') || {}).innerText, tape: (document.getElementById('tape') || {}).innerText, tv: document.querySelectorAll('#tv iframe').length }));
    console.log(name, 'DIRECT :', (st.live || '').replace(/\n/g, ' '));
    console.log(name, 'BANDEAU :', (st.tape || '').replace(/\n/g, ' ').slice(0, 200), '| widgets TradingView :', st.tv);
    await p.screenshot({ path: 'audit-out/' + name + '_accueil.png' });
    const hub = await p.evaluate(() => document.getElementById('hub').innerText.length);
    console.log(name, 'accueil', hub, 'caractères ; ordre :', brs.map(b => b[1]).join(' → '));
    let problems = [];
    for (const [id, t, panels] of brs) {
      await p.goto('' + BASE + '#/' + id); await p.waitForTimeout(1600);
      const r = await p.evaluate((panels) => {
        const res = [];
        const list = panels.map(x => x === '~duo' ? document.querySelector('.duo.o-duo') : document.getElementById(x));
        list.forEach((el, i) => {
          const pid = panels[i];
          if (!el) { res.push(pid + ' : introuvable'); return; }
          el.classList.remove('fold'); // panneaux repliés : on les ouvre pour contrôler leur contenu
          const st = getComputedStyle(el); const vis = el.offsetParent !== null || st.position === 'fixed';
          const txt = el.innerText || '';
          if (!vis) res.push(pid + ' : caché');
          if (txt.trim().length < 40) res.push(pid + ' : presque vide (' + txt.trim().length + ')');
          const bad = txt.match(/\b(undefined|NaN|Infinity)\b|\[object|(^|\s)null(\s|$)/);
          if (bad) { const k = txt.indexOf(bad[0]); res.push(pid + ' : texte cassé « ' + txt.slice(Math.max(0, k - 40), k + 30).replace(/\n/g, ' ') + ' »'); }
        });
        return { res, ow: document.documentElement.scrollWidth - window.innerWidth, det: document.querySelectorAll('#br-' + location.hash.slice(2) + ' [data-det]').length };
      }, panels);
      // fiches de détail : on en ouvre jusqu'à 25 par branche
      let detErr = 0, detOk = 0;
      const n = Math.min(r.det, 25);
      for (let i = 0; i < n; i++) {
        const ok = await p.evaluate((i) => { const el = document.querySelectorAll('#br-' + location.hash.slice(2) + ' [data-det]')[i]; if (!el) return null; el.click(); return true; }, i);
        await p.waitForTimeout(120);
        const sheet = await p.evaluate(() => { const d = document.querySelector('#drawer.on'); const t = d ? d.innerText : ''; return { open: !!d, bad: /\b(undefined|NaN)\b|\[object/.test(t), len: t.length }; });
        if (sheet.bad || !sheet.open) detErr++; else detOk++;
        await p.keyboard.press('Escape'); await p.waitForTimeout(60);
      }
      await p.screenshot({ path: 'audit-out/' + name + '_' + id + '.png' });
      if (id === 'taux') console.log(name, 'barre fraîcheur :', await p.evaluate(() => document.querySelector('#br-taux .bstat').innerText.replace(/\n/g, ' | ')));
      console.log(name, id.padEnd(9), 'panneaux', panels.length, '| débordement', r.ow, '| fiches testées', n, 'texte cassé dans fiches', detErr, r.res.length ? '| PROBLÈMES : ' + r.res.join(' ; ') : '| ok');
      if (r.res.length || r.ow > 2 || detErr) problems.push(id);
    }
    // graphiques maison (CNBC)
    for (const br of ['marches', 'taux']) { await p.goto(BASE + '#/' + br); await p.waitForTimeout(9000);
      console.log(name, 'graphiques ' + br + ' :', await p.evaluate((g) => [...document.querySelectorAll('#' + g + ' .cmini')].map(b => b.querySelector('.mh').innerText.replace(/\n/g, ' ') + ' [' + b.querySelectorAll('svg path').length + ' courbe] ' + b.querySelector('.mfb').innerText).join(' || '), br === 'marches' ? 'miniGrid' : 'rateMinis'));
      await p.evaluate((g) => document.getElementById(g).scrollIntoView(), br === 'marches' ? 'miniGrid' : 'rateMinis'); await p.screenshot({ path: 'audit-out/' + name + '_graph_' + br + '.png' }); }
    // calendrier : vues
    await p.goto('' + BASE + '#/macro'); await p.waitForTimeout(1200);
    await p.click('#calTabs [data-calv="term"]'); await p.waitForTimeout(300);
    console.log(name, 'calendrier : lignes', await p.evaluate(() => document.querySelectorAll('#calTbl tr.calrow').length), '| iframe externe', await p.evaluate(() => !!document.querySelector('#calPro iframe')), '| vue terminal visible', await p.evaluate(() => !document.getElementById('calTerm').hidden));
    // carte : mini-fenêtre
    await p.goto('' + BASE + '#/monde'); await p.waitForTimeout(1500);
    await p.evaluate(() => { const e = document.querySelector('#mapBox path[data-c="BRA"]'); e.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 300, clientY: 300 })); });
    await p.waitForTimeout(400);
    console.log(name, 'carte : mini-fenêtre', await p.evaluate(() => !document.getElementById('mapPop').hidden), (await p.locator('#mapPop').innerText()).slice(0, 80).replace(/\n/g, ' | '));
    // recherche
    await p.fill('#cmdIn', 'ormuz'); await p.waitForTimeout(400);
    console.log(name, 'recherche « ormuz » :', (await p.evaluate(() => (document.getElementById('sres') || {}).innerText || '')).slice(0, 160).replace(/\n/g, ' | '));
    console.log(name, 'REQUÊTES EN ÉCHEC :', JSON.stringify([...new Set(fails)].slice(0, 40)));
    console.log(name, 'ERREURS :', errs.length ? JSON.stringify([...new Set(errs)]) : 'aucune', '| branches à revoir :', problems.join(', ') || 'aucune');
    await p.close();
  }
  await b.close();
})();
