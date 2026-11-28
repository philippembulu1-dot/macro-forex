# Audit complet du terminal Macro Forex — Étape 1

*Date : 28 novembre 2026. Version auditée : `main` @ `c7cb058` (sauvegardée dans la branche `sauvegarde-avant-refonte`).*

Méthode : lecture du code (site, robot, workflows, données), analyse de `data/macro.json`, historique Git et exécutions GitHub Actions, test automatisé des 22 pages dans un navigateur (PC 1400 px et téléphone 390 px), captures d'écran. Les appels réseau externes étaient coupés pendant le test local : les cours en direct n'ont donc pas pu être vérifiés ici, seulement le comportement du site quand ils manquent.

---

## 1. Verdict en une phrase

Le terminal est **riche et techniquement ambitieux** (157 sources, 22 pages, cours en direct, probabilités de taux recalculées, géopolitique, COT…), mais il souffre de **quatre défauts structurels** : (1) un fichier unique de 1,1 Mo impossible à maintenir, (2) une navigation en trois systèmes superposés avec trop de pages, (3) des **étiquettes de fiabilité parfois fausses** (« EN DIRECT » sur des données qui ne le sont pas, « 157/157 OK » alors que des chiffres clés sont anciens), (4) une infrastructure de mise à jour qui sollicite GitHub bien au-delà d'un usage normal.

**Recommandation : ne pas reconstruire de zéro.** Les calculs et les sources ont une vraie valeur. Il faut les extraire progressivement dans une structure propre, corriger la fiabilité des étiquettes, puis refaire l'interface.

---

## 2. Inventaire

### 2.1 Fichiers

| Élément | Taille | Rôle |
|---|---|---|
| `index.html` | **1,1 Mo, 8 947 lignes, ~2 140 fonctions** | Tout le site : CSS (109 Ko), JS (990 Ko), HTML |
| `scripts/update_data.py` | 5 339 lignes, 56 collecteurs `do_*` | Robot de collecte (Python standard) |
| `scripts/ai_analyst.py` | 935 lignes | Débriefing IA (Gemini, offre gratuite) |
| `scripts/strategy_analysis.py` | 484 lignes | Analyse de stratégie IA |
| `worker/agent.js` | 84 lignes | Chat IA sur Cloudflare Workers (gratuit) |
| `desktop/` | — | Application Windows (Electron) |
| `sw.js`, `manifest.webmanifest` | — | Application installable (PWA) |
| `data/macro.json` | **2,6 Mo** | Données principales, chargées à chaque ouverture |
| `data/robot-cache.json` | 2,3 Mo | Cache du robot (publié dans le dépôt) |
| autres `data/*.json` | 0,1–0,5 Mo chacun | Historique, géopolitique, réactions, saisonnalité… |

### 2.2 Les 22 pages actuelles

| Groupe | Pages |
|---|---|
| Mon biais | Mon biais XAUUSD (guide en 8 parties) |
| Marché | Briefing du matin · En direct · Or · Drivers et devises · Analyse technique · Calendrier économique · Actualités et analyses · Géopolitique |
| Macro | Données macro · Carte du monde · Banques centrales · Taux et courbes · Courbes · Saisonnalité |
| Positionnement | COT · Particuliers · Corrélations · Confluences |
| Outils | Alertes · Outils · Données |

Plus : un tableau de bord personnalisable, une recherche globale, des fiches de détail au clic sur les chiffres, une traduction FR/EN.

### 2.3 Automatisations GitHub Actions

| Workflow | Déclenchement | Constat |
|---|---|---|
| Mise à jour des données | cron toutes les 30 min **+ boucle qui se relance elle-même** (jobs de 75 min) | Tourne en continu |
| Analyste IA | cron toutes les 30 min | ~1 commit toutes les 10–20 min |
| Pages (déploiement) | à chaque commit | **28 déploiements annulés sur 61** (trop de commits rapprochés) |
| Audit du site, stratégie, Windows, agent | manuel / à la modification | OK |

---

## 3. Ce qui fonctionne bien (à conserver)

- **Collecte côté serveur** par GitHub Actions avec sources isolées : une panne n'arrête pas les autres. Bonne architecture pour un budget nul.
- **Choix de sources officielles** quand c'est possible : Trésor US (courbe nominale et réelle), BLS, BEA, Fed, Fed de New York, BCE (CISS, IRS, TARGET), Eurostat, OCDE, BRI, CFTC, Banque mondiale, FMI PortWatch, OFR.
- **Calculs de qualité** : probabilités de taux méthode CME à partir des contrats fed funds, DXY recalculé, fragmentation zone euro, event study des réactions (1 → 240 min), z-scores des moteurs, COT en percentiles 3 ans, correction du volume de presse pour GDELT.
- **Honnêteté de principe** : panneau de fraîcheur, saisies manuelles marquées « à vérifier » et « PÉRIMÉ », mention « non validé statistiquement » sur les scores, avertissement légal.
- **Aucune clé secrète dans le code** (vérifié) : FRED, Gemini, Myfxbook, Cloudflare passent par les secrets GitHub.
- **Aucun texte cassé** (`undefined`, `NaN`, `[object…]`) et **aucun débordement horizontal** sur les 22 pages, PC et téléphone.
- Application installable et application Windows fonctionnelles.

---

## 4. Problèmes constatés

Classement : **P0** = fiabilité ou risque à corriger d'abord · **P1** = structure et ergonomie · **P2** = finition.

### 4.1 Fiabilité des données (P0)

| # | Problème | Preuve | Conséquence |
|---|---|---|---|
| D1 | **Composantes de l'inflation US calculées par position au lieu de la date** *(corrigé le 28 nov.)*. Octobre 2025 manque (shutdown) : logement, supercore et prix à l'import de nov. 2025 à oct. 2026 étaient des hausses **sur 13 mois** présentées comme sur 12 mois. L'IPC global, lui, était juste (3,7 % en sept. = dernier glissement officiel : le BLS **ne publie pas** le glissement annuel d'octobre 2026), mais le tableau « Indicateurs » le jugeait trop ancien et affichait la saisie manuelle sans explication. | `do_bls_infl` : `obs[i - step]` ; tableau : « 3,7 % saisie · sept. » | Chiffres d'inflation faux d'environ un mois de hausse ; absence d'explication sur octobre. |
| D2 | **« OK » ne veut pas dire « à jour ».** L'état d'une source est `ok = true` dès que l'appel réussit, même si la donnée a 7 mois. | NFCI arrêté au 24 avr. 2026 (217 j), projections BCE de sept., inflation Japon à sept., 13 séries annuelles à fin 2025 — toutes « OK ». Bandeau : « 157/157 OK ». | Fausse impression de fiabilité totale. |
| D3 | **Étiquettes de nature fixes.** Le badge « EN DIRECT » est écrit en dur pour chaque panneau (`NATURE` dans le code), il ne change pas quand la source directe tombe. | Panneau Or : badge « EN DIRECT » + texte « Binance injoignable : dernière clôture du contrat à terme ». Le statut d'en-tête dit « EN DIRECT · taux BCE » pour des fixings **quotidiens**. | Viole la règle « ne jamais afficher une fausse donnée en temps réel ». |
| D4 | **Même indicateur, valeurs différentes selon la page.** Le tableau « Indicateurs » mélange automatique, repli (« relevé du 25 oct. ») et saisie manuelle ; d'autres panneaux lisent la valeur du robot. | IPC US « saisie », IPC zone euro « auto » dans la même ligne. | Incohérences difficiles à repérer. |
| D5 | **Blocs manuels périmés** (taux et inflation des banques centrales, indicateurs, réunions, consensus : 28 à 34 jours) signalés seulement dans la page « Données ». | Capture « Données ». | L'avertissement n'apparaît pas là où le chiffre est lu. |
| D6 | **Sources non officielles au cœur du site** : Yahoo Finance (cours, contrats fed funds, bougies), CNBC (cours en direct, historiques), calendrier TradingView (consensus, ISM, Michigan), Forex Factory, Bing/Google Actualités, `r.jina.ai` et `api.rss2json.com` (relais tiers). Aucune n'a d'API publique autorisée pour cet usage. | 34 appels Yahoo dans le robot ; `pollLive()` CNBC toutes les 60 s. | Peuvent être coupées du jour au lendemain ; conditions d'utilisation non respectées pour certaines. À classer et à doubler par une source officielle quand elle existe. |
| D7 | **PAXG affiché comme XAUUSD.** Le « direct » de l'or est un jeton crypto adossé à l'or (Binance), pas le cours au comptant. Il est marqué d'un astérisque, mais l'écart avec le spot n'est ni mesuré ni affiché. | Bandeau « XAUUSD* ». | Approximation acceptable si elle est clairement expliquée. |
| D8 | **Pas d'historique des révisions** : seule la dernière valeur est gardée. | Aucune structure « première publication / révision ». | Exigence D de la Partie 10 non couverte. |

### 4.2 Infrastructure et coûts cachés (P0)

| # | Problème | Preuve | Risque |
|---|---|---|---|
| I1 | **Le dépôt grossit sans arrêt.** Chaque passage du robot réécrit `macro.json` (2,6 Mo) et `robot-cache.json` (2,3 Mo) dans un nouveau commit. | **3 422 commits**, dont ~300 en 30 heures, tous automatiques. Dépôt : **98 Mo**. | GitHub recommande de rester sous 1 Go ; le clonage et l'historique deviennent lourds ; les vrais changements de code sont noyés. |
| I2 | **Boucle qui se relance elle-même** (jobs de 75 min qui relancent le suivant) + IA toutes les 30 min. | `update-data.yml` (`gh workflow run` en fin de job). | Les conditions d'utilisation de GitHub Actions interdisent les usages qui chargent les serveurs de façon disproportionnée. Risque de désactivation des Actions, voire du compte. |
| I3 | **Déploiements Pages annulés en série** (28/61). | API GitHub Actions. | Le site publié peut avoir du retard sur les données. |
| I4 | **2,6 Mo chargés à chaque ouverture**, sur téléphone en Afrique centrale. | `macro.json` lu en entier au démarrage, relu toutes les 5 min. | Lenteur et consommation de forfait mobile. |

### 4.3 Architecture du code (P1)

- **Un seul fichier de 1,1 Mo** mêlant style, interface, calculs, accès aux données et textes. Des lignes jusqu'à 2 133 caractères. Toute modification risque de casser autre chose ; impossible à tester par morceaux.
- **Variables globales** et fonctions à noms courts (`AQ`, `TA_X`, `LMAP`, `ffmem`…) : lecture très difficile.
- **301 `innerHTML`** : rendu par concaténation de texte. Fonctionne, mais fragile ; les titres de presse insérés ainsi doivent être échappés partout (à vérifier systématiquement à l'étape 4).
- **Graphiques maison en SVG** sans axe de dates, avec 2 graduations seulement (ex. graphique de l'or) : peu exploitables pour une vraie analyse.
- **57 usages de `localStorage`** (30 clés) : notes, guide « Mon biais », alertes, réglages. **Aucune sauvegarde** : un nettoyage du navigateur efface ton travail personnel.
- **Code d'accès décoratif** : l'empreinte est publique (`data/acces.json`) et les données sont lisibles par tous sur GitHub. Ce n'est pas une protection, seulement une porte d'entrée.

### 4.4 Navigation et organisation (P1)

- **Trois systèmes de navigation en même temps** : barre latérale + rangée d'onglets horizontale + tuiles d'accueil, plus un bouton « Accueil ». Sur téléphone, la rangée d'onglets défile horizontalement sur 22 entrées.
- **22 pages** (12 au 31 oct.) : le site a grandi par ajouts successifs. Exemples de doublons :
  - « Taux et courbes » et « Courbes » ;
  - « En direct », « Or » et « Drivers » affichent tous des cours ;
  - « Briefing du matin » contient déjà régime, scénarios, biais, moteurs, que l'on retrouve ailleurs ;
  - « Confluences » et « Mon biais » se recoupent ;
  - actualités dans « Actualités », dans le bandeau défilant et dans le briefing.
- **Le haut de page occupe environ 400 px avant le contenu** sur PC et **tout le premier écran sur téléphone** : horloge, recherche, message TradingView, statut direct, bandeau d'actualités, rangée de cours, phrase d'explication, onglets, titre, description, « En clair », barre d'état.
- **Pages très longues** : le briefing fait 11 600 px sur PC et **24 400 px sur téléphone** (≈ 29 écrans).

### 4.5 Visuel (P1–P2)

- Identité sombre/ambre cohérente et lisible, typographie IBM Plex adaptée : **à garder comme base**.
- Effets « Ultra » (verre, profondeur, tuiles 3D) qui alourdissent sans aider la lecture.
- Densité d'information mal hiérarchisée : beaucoup de texte explicatif au même niveau que les chiffres.
- Bandeau d'actualités défilant mêlant des titres sans rapport avec les marchés (ex. « Elderly 'Scrooge' severs neighbour's Christmas lights… », The Guardian).
- En-têtes de panneaux hétérogènes : la source, la période et la date de mise à jour ne sont pas toujours au même endroit.

### 4.6 Performance mesurée (test local, sans réseau externe)

| Mesure | PC | Téléphone |
|---|---|---|
| Ouverture complète | ~6,4 s | ~6,2 s |
| Éléments dans la page | 15 569 | 15 569 |
| Mémoire JS | 16 Mo | 16 Mo |

Les 22 pages sont toutes construites dans la même page, même si elles ne sont pas affichées.

---

## 5. Couverture du cahier des charges

✅ existe et fonctionne · 🟡 partiel ou fragile · ❌ absent

| Demande | État | Remarque |
|---|---|---|
| PIB, IPC, core, PCE, core PCE, NFP, chômage | 🟡 | IPC bloqué en sept. (D1) |
| Supercore, logement, PPI, salaires, ECI | ✅ | BLS v1 |
| JOLTS, productivité, coûts unitaires de main-d'œuvre | ❌ | Disponibles gratuitement au BLS |
| ISM | 🟡 | Via calendrier TradingView (non officiel) |
| SLOOS, délinquance, défauts | ❌ | Fed (SLOOS, taux de délinquance) ; pas de taux de défaut officiel gratuit |
| Immobilier (mises en chantier, ventes) | ❌ | Census (gratuit) |
| Conditions financières | 🟡 | OFR à jour ; NFCI arrêté en avril |
| Taux directeurs et probabilités | ✅ | BRI + nationales ; ZQ (Yahoo) |
| Courbes, taux réels 5/10 ans | ✅ | Trésor US |
| Taux forward | ❌ | Calculable à partir de la courbe du Trésor |
| Prime de terme | ❌ | Fed de New York (modèle ACM), gratuit |
| Adjudications du Trésor | ❌ | API Fiscal Data / TreasuryDirect, gratuite |
| MOVE, VIX, GVZ | 🟡 | MOVE via Yahoo/CNBC (non officiel) |
| Spreads de crédit | 🟡 | Seulement avec la clé FRED (gratuite) |
| Flux ETF | 🟡 | Estimation via Nasdaq (non officiel) |
| Largeur du marché actions | ❌ | Pas de source officielle gratuite |
| Corrélations, divergences | ✅ | Moniteur multi-actifs |
| COT | ✅ | CFTC |
| Stocks, production, exports de pétrole (EIA) | ❌ | API EIA gratuite (clé gratuite) |
| Trafic maritime, détroits | ✅ | FMI PortWatch |
| Chronologie géopolitique, sources | 🟡 | GDELT, GPR, calendrier ; chronologie à structurer |
| Calendrier, consensus, surprises | 🟡 | Consensus non officiel (TradingView / Forex Factory) |
| Réactions des marchés | ✅ | Event study |
| Révisions (1re valeur / révisée) | ❌ | — |
| Scénarios avec confirmation et invalidation | 🟡 | Or/dollar seulement |
| Synthèse par actif | 🟡 | « Confluences » + biais du matin |
| Validation des données par indicateur | 🟡 | Panneau « Données » sans période de référence ni fréquence attendue |
| Historique de tes analyses | ❌ | Notes sans historique ni sauvegarde |
| Comparateur de régimes historiques | ❌ | — |
| Macro Heatmap / Reaction Tracker / Risk Matrix | 🟡 / 🟡 / ❌ | Heatmap devises existe ; tracker = event study |
| Recherche globale, favoris, vues | ✅ / 🟡 / ❌ | Recherche et tableau de bord OK ; pas de vue compacte |
| Documentation technique | 🟡 | `CLAUDE.md` = journal de bord, pas une documentation |

---

## 6. Correctifs prioritaires

1. **D1** : calcul par date des glissements BLS + explication d'octobre (fait le 28 nov.).
2. **D2 + D3** : statut calculé = fréquence attendue + date de la dernière valeur → « à jour / en retard / périmé / échec », et badge de nature **dynamique**.
3. **I1 + I2** : arrêter la croissance du dépôt et la boucle continue (voir `ARCHITECTURE.md` §5).
4. **D5** : afficher l'alerte « périmé » directement sur le chiffre concerné.
5. **Sauvegarde de tes données personnelles** (export/import) avant toute refonte de l'interface.

Le reste (structure, navigation, design, nouveaux modules) suit l'ordre de `ARCHITECTURE.md`.

---

## 7. Points à vérifier pendant les étapes suivantes

- Comportement réel des cours en direct (CNBC, Binance) depuis ton navigateur : non testable ici sans réseau.
- Échappement des titres de presse dans tous les `innerHTML`.
- Fonctionnement du chat IA (Cloudflare) et renouvellement du jeton `CLOUDFLARE_API_TOKEN` exposé (action de ta part, notée dans `CLAUDE.md`).
- Taux directeur de la Fed affiché (4,00 % borne haute) : à contrôler par rapport à la dernière décision du FOMC.
