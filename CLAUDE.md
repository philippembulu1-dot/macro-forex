# BRIEF POUR CLAUDE CODE : terminal macro/forex personnel

> À coller dans Claude Code (ou à enregistrer sous le nom `CLAUDE.md` à la racine du dépôt). Langue du site et des échanges : **français**.

## 1. Qui, quoi, pourquoi
- Utilisateur : trader forex/or débutant, basé à Kinshasa (UTC+1). Budget : **zéro** (tout doit être gratuit : hébergement, données, outils).
- Objectif : un **terminal macro personnel de type institutionnel** (style sombre ambre, façon « Trade Sider »), lisible sur téléphone, **qui s'actualise tout seul** sans qu'il re-téléverse des fichiers.
- Focus : XAU/USD (or), indices (NAS100, S&P), forex majeurs (EUR, GBP, JPY, CHF, AUD, CAD, NZD vs USD).
- Hébergement : **GitHub Pages** (dépôt public, branche `main`, racine). Un robot **GitHub Actions** (gratuit) récupère les données côté serveur et écrit `data/macro.json`, que le site lit.

## 2. Règles non négociables
1. **Pas de journal de trading** (il en a déjà un). Les « Notes rapides » en localStorage sont autorisées.
2. **Aucun chiffre inventé.** Ce qui n'est pas automatisable gratuitement reste en saisie manuelle avec un badge « à vérifier » et une date. Les valeurs de démonstration doivent être étiquetées « Démo ».
3. Toute donnée affichée porte une **date** et un **état de fraîcheur** (à jour / ancien / échec). Chaque source du robot est **isolée** : une panne n'empêche pas les autres.
4. Tout gratuit, sans clé API obligatoire, sans carte bancaire.
5. Mobile d'abord, accessible, sans dépendance lourde. Un seul `index.html` autonome si possible (CSS + JS inline) ; données dans `data/macro.json`.
6. Avertissement légal permanent en pied de page (déjà présent : à conserver).
7. Il a un **forfait gratuit** : avancer **par étapes**, livrer à chaque étape quelque chose qui marche, et lui dire quoi taper pour continuer.
8. Ne jamais prétendre qu'une source marche sans l'avoir exécutée. **Vérifier chaque source avec un vrai appel réseau** et corriger (le premier jet a été écrit sans accès Internet : voir §6).

## 3. État au 31 oct. 2026, 9 h 30 : TERMINÉ, 79 sources sur 79, aucune erreur
**Site** : https://philippembulu1-dot.github.io/macro-forex/ — dépôt `philippembulu1-dot/macro-forex`, branche `main`.
Fichiers : `index.html` (site complet, CSS + JS inline, ~4 500 lignes), `scripts/update_data.py` (robot, Python standard), `.github/workflows/update-data.yml` (cron `17,47 * * * *` ≈ 1 passage/h réel, lancement manuel, relance à chaque envoi du robot ; actions checkout v5 / setup-python v6), `data/` : `macro.json`, `seasonal.json`, `macro-history.json`, `surprises.json`, `reactions.json`, `gold-etf.json`, `world-map.json`.
**Secrets GitHub** (remplis par l'utilisateur, ne jamais les demander ni les afficher) : `FRED_API_KEY`, `MYFXBOOK_EMAIL`, `MYFXBOOK_PASSWORD`. Le robot n'écrit que « présent / vide » dans `diag`.

**12 branches** (hub en tuiles, transitions, recherche globale, fiche détaillée au clic sur tout chiffre `data-det`, étiquette de nature de donnée EN DIRECT / DIFFÉRÉ / QUOTIDIEN / HEBDO / MENSUEL / CALCUL / OFFICIEL sur chaque panneau, effets de mouvement) :
- **Ce matin** : résumé de la veille, publications du jour (heure de Kinshasa), biais par actif, **lecture macro et scénarios or/dollar** (facteurs, confirmation par le prix, contradictions, données manquantes, conditions de confirmation/invalidation), **régime macro**, **moteurs du marché** (8 moteurs classés en écarts-types, 4 majeurs).
- **Or** : PAXG en direct + contrat à terme COMEX différé (secours robot si Binance injoignable), **flux de l'or** (COT détaillé gestionnaires/producteurs/swaps, ETF GLD/IAU via encours Nasdaq mémorisé, demande WGC lue automatiquement), volatilité GVZ, sensibilités, 6 piliers (contribution + confiance, « non validé statistiquement »), saisonnalité 20 ans.
- **Devises** : force multi-horizons 1 h / 4 h / 1 j / 1 sem. (Yahoo horaire) avec taux, inflation et fonds à levier ; paires BCE, heatmap, corrélations, volatilité.
- **Taux & pricing** : pricing Fed (Kalshi + méthode CME recalculée), courbe des taux 2/5/10/30 ans, pentes 2s10s et 5s30s, décomposition 10 ans (réel / inflation attendue), Taylor (r* et u* du dot plot), écarts, taux/VIX/dollar (vrai DXY ICE), indicateurs des desks.
- **Banques centrales** : taux, décisions et inflation automatiques (BRI + sources nationales + presse du jour), **probabilités méthode CME FedWatch** recalculées sur les contrats ZQ, Kalshi/Polymarket, réunions (Fed lue sur son site), comité (orientation calculée), communiqués.
- **Macro & calendrier** : cycle (4 phases + horloge), dot plot Fed, projections BCE (tableau lu automatiquement), calendrier filtrable avec chiffres officiels « off. » et analyse de chaque publication, surprises, publications de la semaine, indicateurs des 8 économies (OCDE KEI + mémoire `ffmem` des derniers chiffres du calendrier), guide de lecture.
- **Analyses** : notes ING/investingLive, consensus des banques relevé automatiquement dans la presse, vidéos. **Monde & maritime** : carte, détroits (FMI PortWatch), réserves. **Marchés** : graphiques TradingView, moniteur multi-actifs (corrélations 20/120 j, divergences). **Positionnement** : COT, sentiment Myfxbook. **Outils** : lots, pivots, convertisseur, checklist, notes. **Données** : fraîcheur de chaque source. Plus la **mémoire des réactions** (event study 1→240 min, continuation / retournement).

## 4. Pistes possibles (non demandées pour l'instant)
- Se remplissent seuls dans les jours suivant le 31 oct. : PMI et balances commerciales (mémoire du calendrier), flux ETF GLD/IAU (2e jour de bourse), inflation sous-jacente du Japon (« National Core CPI »).
- Idées proposées à l'utilisateur, non commencées : alertes Telegram, application installable (PWA), bloc NAS100 (résultats des entreprises, heatmap), backtest réel des scores quand l'historique sera assez long, base macro élargie (82 économies).
- Second projet annoncé par l'utilisateur le 31 oct. 2026 (à définir).

## 5. Mission de recherche demandée
Avant d'ajouter des indicateurs, **rechercher sur le web et citer des sources** :
- Quels indicateurs et tableaux de bord les **grands fonds macro, banques d'investissement et desks de trading** regardent réellement pour l'or, le dollar et les devises (rendements réels, anticipations d'inflation/breakevens, courbe de taux, positionnement COT, flux d'ETF d'or, achats des banques centrales, volatilité implicite, spreads de crédit, indices de surprise économique, conditions financières, etc.).
- Les modèles de scorecard publics : par exemple le tableau de bord à six piliers pour l'or (taux réels, anticipations de la Fed, dollar, flux, risque/refuge, positionnement).
- Pour chacun : **où l'obtenir gratuitement**, la fréquence de mise à jour, la facilité d'automatisation, et la qualité (officielle ou non).
- Rendre un tableau « indicateur → source gratuite → automatisable ? → priorité » et **attendre la validation** de l'utilisateur.
- Distinguer clairement : ce qui est publié par les fonds/banques (rapports publics, notes de recherche gratuites, prévisions publiées) et ce qui est réservé aux abonnés (ne pas le simuler).

## 6. Contraintes techniques apprises (important)
- **Le premier robot a été écrit sans pouvoir joindre Internet** : formats attendus d'après la documentation, **non vérifiés**. À tester d'abord :
  - FRED : `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SÉRIE>&cosd=<AAAA-MM-JJ>` (CSV, sans clé ; vérifier le nom de la 1re colonne : `DATE` ou `observation_date`).
  - OCDE via FRED : séries `IRSTCB01xxM156N` (taux) et `CPALTT01xxM659N` (inflation) : certaines peuvent être abandonnées ; prévoir des remplaçants.
  - CFTC : `https://publicreporting.cftc.gov/resource/6dca-aqww.json` (rapport « Legacy », futures seuls). Codes : or 088691, EUR 099741, GBP 096742, JPY 097741, CHF 092741, AUD 232741, CAD 090741, NZD 112741. Vérifier les noms de champs (`noncomm_positions_long_all`, `comm_positions_long_all`, `report_date_as_yyyy_mm_dd`).
  - CNN Fear & Greed : `https://production.dataviz.cnn.io/index/fearandgreed/graphdata` (peut refuser les IP de datacenter ; repli actuel = approximation étiquetée à partir du VIX).
  - Flux RSS des banques centrales : adresses à vérifier une par une (Fed, BCE, BoE, BoJ, BNS, RBA, BoC ; RBNZ manquant).
  - Frankfurter (taux BCE) : `https://api.frankfurter.dev/v1/<début>..?base=EUR&symbols=...` (fonctionne).
  - Binance (PAXGUSDT, BTCUSDT) via `data-api.binance.vision` : non testé depuis le navigateur de l'utilisateur ; prévoir un repli.
- GitHub Pages est **statique** : pas de serveur, CORS limité. D'où le robot Actions qui écrit `data/macro.json` ; le site lit l'adresse brute (`raw.githubusercontent.com`, CORS ouvert) puis le fichier relatif.
- Les widgets TradingView s'injectent par `<script>` avec la configuration JSON en `innerHTML` ; ils ne marchent pas dans un aperçu sandboxé. Le symbole MOVE (`TVC:MOVE`) est **à vérifier**.
- Les actions planifiées GitHub peuvent être désactivées après 60 jours sans activité du dépôt, et les crons ont du retard : le panneau « Fraîcheur » doit rester l'arbitre.
- Formule DXY recalculée depuis les taux BCE : 50,14348112 × EURUSD^-0,576 × USDJPY^0,136 × GBPUSD^-0,119 × USDCAD^0,091 × USDSEK^0,042 × USDCHF^0,036.
- Dimensionnement de position : risque $ = capital × risque % ; valeur du pip = taille du pip × 100 000 × (USD par unité de la devise de cotation) ; lots arrondis **vers le bas** à 0,01 (avec epsilon 1e-6).

## 7. Façon de travailler attendue
- Commencer par lire `index.html`, `scripts/update_data.py` et le workflow, puis **exécuter le robot pour de vrai** et lire `data/macro.json`.
- Un changement = un commit clair en français ; tester en local (serveur statique + Playwright si possible) avant de pousser ; valider la syntaxe JS (`node --check`).
- Expliquer à l'utilisateur en **français simple** (débutant), avec des étapes concrètes à cliquer quand il doit agir lui-même (réglages GitHub : Pages, Actions → permissions « Read and write », Run workflow).
- À la fin de chaque étape : dire ce qui marche, ce qui est en échec (avec la source et la cause), et proposer l'étape suivante.

## 8. Installation de zéro (rappel pour l'utilisateur)
1. Créer le dépôt public (ex. `macro-forex`) avec README.
2. Mettre `index.html` à la racine (le nom doit être exactement `index.html`).
3. Créer `scripts/update_data.py` et `.github/workflows/update-data.yml`.
4. Settings → Pages → Deploy from a branch → `main` / root.
5. Settings → Actions → General → Workflow permissions → **Read and write permissions**.
6. Actions → « Mise à jour des données » → Run workflow.
7. Ouvrir le site → panneau « Fraîcheur des données » → détail des sources.

## 9. Résultats vérifiés en réel (30 oct. 2026, GitHub Actions)
- **Bloqués depuis les serveurs GitHub** : FRED (délai dépassé sur toutes les séries), FMI datamapper (403), RBA CSV et RSS (403), Dukascopy (403). Ne pas les réessayer sans raison.
- **Remplaçants qui marchent** : Trésor US (2/5/10 ans nominaux et réels), CBOE (VIX, GVZ), Fed de New York (fourchette cible), BCE (taux de dépôt, taux de change → DXY recalculé), BLS v1 sans clé (IPC, chômage, NFP), Fed de Chicago (NFCI, fichier arrêté au 24 avr. 2026), FMI PortWatch via ArcGIS (requête « year >= … », 28 détroits), Banque mondiale, Eurostat/BCE IPCH (s'arrêtent à déc. 2025 côté source), BoE, BoC, BNS, ONS, CFTC, CNN, Kalshi, Polymarket, Forex Factory (semaine en cours uniquement, sans chiffres réels), flux RSS des 7 autres banques, YouTube (BFM Business, Bloomberg, CNBC, Kitco).
- **Sans remplaçant gratuit** : spreads ICE (HY, IG), STLFSI, bilan de la Fed, PCE sous-jacent, taux RBA (saisie manuelle).
- Le robot se relance tout seul à chaque envoi de `scripts/update_data.py` ou du workflow ; chronomètre global 7 min ; enregistrement sans conflit.
- **31 oct. 2026** : Yahoo Finance (`query1.finance.yahoo.com/v8/finance/chart`, sans clé, non officiel) marche depuis GitHub : clôtures quotidiennes de 12 actifs et bougies 15 min (mémoire des réactions, `data/reactions.json`). Calendrier BLS (`bls.ics`) : 403, même avec un robot identifié. Les passages programmés GitHub sont retardés (≈ 30 min) ou sautés : cron passé à toutes les 30 min ; en pratique ≈ 1 passage par heure, vérifié la nuit du 31 oct. (6 passages automatiques réussis).
- **31 oct. 2026 (suite)** : taux de la RBA récupéré via la BRI (`stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.AU`, CSV, sans clé, quelques jours de retard) quand le site de la RBA répond 403. Séries OCDE via FRED classées « non gratuit » (remplacées par les sources nationales). Or : si Binance est injoignable dans le navigateur, le site bascule sur les clôtures du contrat à terme relevées par le robot (étiqueté). Données mensuelles BLS : octobre 2025 manquant (shutdown) → calculs par date, jamais par position.
- **31 oct. 2026 (matin)** : BRI `WS_CBPOL` marche pour les 8 banques (US, XM, GB, JP, CH, AU, CA, NZ) avec ~4 jours de retard ; le site prend la source la plus récente (nationale ou BRI). CFTC « Disaggregated » (`72hh-3qpy`, or/argent) et « TFF » (`gpe5-46if`, devises) marchent. Yahoo 60 min sur 8 jours marche (force des devises 1 h / 4 h). Fichier historique GLD de SPDR : renvoie maintenant un PDF → pas de flux d'ETF gratuits.
- **31 oct. 2026 (8 h)** : contrats à terme fed funds `ZQ<mois><aa>.CBT` (Yahoo, 16 mois) marchent → probabilités recalculées avec la méthode CME FedWatch sur le site. Archive GLD (SPDR) : PDF/404 depuis GitHub ; fiche iShares : HTML. Remplacement : encours et capitalisation GLD/IAU via `api.nasdaq.com/api/quote/<ETF>/summary?assetclass=etf` (mémorisés dans `data/gold-etf.json`, flux = variation de capitalisation non expliquée par le prix). Demande physique : chiffres WGC T1-T2 2026 saisis avec source, veille de la page Gold Demand Trends (gold.org marche depuis GitHub).
- **31 oct. 2026 (9 h)** : remplaçants des séries FRED vérifiés en réel : OFR Financial Stress Index (`financialresearch.gov/financial-stress-index/data/fsi.csv`, + composante crédit), BEA `apps.bea.gov/national/Release/TXT/NipaDataM.txt` (DPCERG, DPCCRG → PCE et PCE sous-jacente), API OCDE SDMX `DSD_PRICES@DF_PRICES_ALL` (inflation AU, NZ, CH, GB, CA, EA20 ; JPN absent). `api.stlouisfed.org` répond depuis GitHub (400 sans clé) : écarts ICE HY/IG et NFCI récupérés si le secret facultatif `FRED_API_KEY` est rempli.
- **31 oct. 2026 (10 h) — tout automatique** : Eurostat a remplacé `prc_hicp_manr` (arrêté en déc. 2025) par `prc_hicp_minr` (`coicop18=TOTAL` / `TOT_X_NRG_FOOD`, `geo=EA`) → inflation zone euro à jour. Inflation sous-jacente : OCDE `..CPI.PA._TXCP01_NRG.N.GY`, Banque du Canada Valet `CPI_TRIM`, BNS cube `plkoprinfla` (K1 = sous-jacente, TLK = globale). Japon : BRI `WS_LONG_CPI/M.JP.771`. Dates FOMC : page `fomccalendars.htm`. Projections BCE : tableau HTML de la publication. WGC : chiffres lus dans le résumé de l'édition. Consensus des banques : Google Actualités RSS (titres, chiffre de prévision filtré). Indicateurs : OCDE KEI (`DSD_KEI@DF_KEI,4.0`, csvfilewithlabels) + mémoire `ffmem` des champs « previous » de Forex Factory (nextweek/lastweek : 404). Myfxbook : page publique 403 → seul le mot de passe (secret) permet le sentiment.
