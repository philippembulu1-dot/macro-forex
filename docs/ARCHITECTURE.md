# Architecture proposée — Étape 2

*À valider avant toute modification du site. Rien de ce document n'est encore appliqué.*

Principes : **garder ce qui marche, déplacer avant de réécrire, une étape = un site qui fonctionne.** Budget : 0 $. Hébergement : GitHub Pages. Pas d'outil de compilation obligatoire.

---

## 1. Nouvelle organisation : 22 pages → 8 sections

Chaque section a des **sous-onglets** (6 au maximum). Une seule navigation : barre latérale sur PC, barre du bas à 5 boutons + menu sur téléphone.

| # | Section | Sous-onglets | Vient de (pages actuelles) |
|---|---|---|---|
| 1 | **Tableau de bord** | Synthèse · Par actif · Alertes · État des données | Briefing du matin (régime, diagnostic, moteurs, catalyseurs), Confluences, Tableau de bord personnalisable, résumé des alertes |
| 2 | **Macro** | Croissance · Emploi · Inflation · Crédit et conditions financières · Indicateurs avancés · International | Données macro, Courbes (intégrées à chaque onglet), Carte du monde (comparaisons G8), Déficits |
| 3 | **Banques centrales et taux** | Fed · BCE · BoJ et autres · Probabilités et trajectoires · Courbes et taux réels · Différentiels | Banques centrales, Taux et courbes |
| 4 | **Marchés et intermarket** | Vue d'ensemble · Or · Dollar et devises · Taux, indices, BTC, pétrole · Volatilité et crédit · Corrélations et divergences · Positionnement | En direct, Or, Drivers et devises, Analyse technique (en outil de graphique), Corrélations, COT, Particuliers, Saisonnalité |
| 5 | **Géopolitique et énergie** | Chronologie · Carte · Énergie · Maritime · Transmission aux marchés | Géopolitique, Carte du monde (modes géopolitiques) |
| 6 | **Actualités et calendrier** | Calendrier · Surprises et révisions · Réactions des marchés · Banques centrales · Fil d'actualités | Calendrier économique, Actualités et analyses |
| 7 | **Analyses et scénarios** | Mon biais (guide) · Scénarios · Historique de mes analyses · Notes · Outils de trading | Mon biais XAUUSD, Outils (lots, checklist, pivots) |
| 8 | **Sources et qualité** | État des sources · Validation par indicateur · Révisions · Documentation | Données |

**En haut de chaque page, une seule barre compacte** (≈ 48 px) : horloge Kinshasa/New York, statut global des données, recherche (`/`), cloche d'alertes, préférences. Le bandeau de cours devient optionnel (réglage). Le bandeau d'actualités défilant est supprimé : les actualités utiles vont dans la section 6 et dans le tableau de bord, filtrées par pertinence pour les marchés.

**Retirés ou fusionnés :** tuiles d'accueil, rangée d'onglets horizontale, bouton « Accueil », page « Courbes » séparée, page « En direct » séparée, Bloomberg TV intégré (remplacé par un lien), message « Barre TradingView non chargée ». Aucune fonction utile ne disparaît : chaque élément est rangé dans la table ci-dessus.

Adresses : `#/macro/inflation`, `#/taux/fed`… Les anciennes adresses (`#/matin`, `#/or`…) redirigent vers la nouvelle place, pour que tes favoris continuent de marcher.

---

## 2. Structure du code

Modules JavaScript natifs (`<script type="module">`), **sans étape de compilation** : GitHub Pages les sert tels quels et tous les navigateurs récents les comprennent.

```
index.html                 ← coquille légère (≈ 5 Ko) : barre, navigation, zone de contenu
styles/
  tokens.css               ← couleurs, typographie, espacements (un seul endroit)
  base.css  layout.css  components.css
src/
  app.js                   ← démarrage, routeur, préférences
  core/                    ← services communs, sans interface
    store.js               ← état partagé
    data.js                ← chargement des fichiers JSON + cache + statut
    freshness.js           ← calcul « à jour / en retard / périmé / échec »
    time.js                ← fuseaux, heures d'été, jours fériés, calendriers BC
    format.js  escape.js
  calc/                    ← calculs purs, testables seuls
    stats.js (z-score, percentiles, corrélations)  rates.js (forwards, FedWatch, Taylor)
    fx.js (DXY, force des devises)  regime.js  surprises.js  divergences.js
  ui/                      ← composants réutilisables
    panel.js (en-tête standard : titre · nature · source · période · mise à jour)
    table.js  chart.js  badge.js  tooltip.js  tabs.js
  pages/                   ← un dossier par section, un fichier par sous-onglet
    dashboard/  macro/  rates/  markets/  geo/  news/  analysis/  sources/
vendor/
  uplot/                   ← bibliothèque de graphiques (copie locale)
scripts/                   ← robot (Python), découpé lui aussi
  update_data.py           ← orchestrateur
  sources/  us.py  euro.py  rates.py  markets.py  cot.py  geo.py  news.py …
docs/                      ← documentation technique (Partie 10-I)
tests/                     ← tests des calculs + parcours Playwright
```

Règles : chaque fichier < 400 lignes ; les calculs ne touchent jamais à l'interface ; les pages ne vont jamais chercher les données directement, elles passent par `core/data.js`.

**Méthode de migration (« étrangleur ») :** on extrait un morceau d'`index.html` (par exemple le style, puis une section), on vérifie que tout marche, on enregistre. L'ancien code reste en place tant que le nouveau n'a pas pris le relais. À aucun moment le site n'est cassé.

---

## 3. Graphiques

**uPlot** (licence MIT, ≈ 50 Ko, très rapide sur les séries temporelles), copié dans `vendor/`, donc pas de dépendance à un CDN. Il apporte de vrais axes de dates, des graduations, une infobulle avec date et valeur, le zoom, plusieurs séries et deux axes. Un seul composant `ui/chart.js` l'enveloppe pour que tous les graphiques du site se ressemblent. Les mini-courbes (sparklines) restent en SVG maison, qui suffit à cet usage.

Alternative écartée : Apache ECharts, plus complet mais 10 fois plus lourd.

---

## 4. Modèle de données et fiabilité

### 4.1 Découpage des fichiers
`macro.json` (2,6 Mo) est découpé par domaine, chaque section chargeant **seulement ce dont elle a besoin** :

```
data/manifest.json   ← petit (≈ 20 Ko) : liste des séries + statut, lu en premier
data/macro.json  rates.json  markets.json  cb.json  calendar.json  geo.json  cot.json  news.json
data/history/<série>.json   ← historiques longs, chargés à la demande
data/vintages/<série>.json  ← premières publications et révisions
```

### 4.2 Chaque série porte ses métadonnées
`source` · `url` · `officielle | non officielle` · `période de référence` · `date de publication` · `relevée le` · `fréquence attendue` · `prochaine publication` · `statut` · `méthode` (si calculée).

### 4.3 Statut calculé, jamais déclaré
- **À jour** : dernière valeur conforme au calendrier de publication.
- **En retard** : une publication attendue n'a pas encore été relevée.
- **Périmé** : source arrêtée ou saisie manuelle trop ancienne.
- **Échec** : l'appel a échoué ; on affiche la dernière valeur valide avec sa date.

Le badge de nature (DIRECT / DIFFÉRÉ / QUOTIDIEN…) est **calculé au moment de l'affichage** : si le direct tombe, le badge passe à « DIFFÉRÉ » ou « CLÔTURE ».

### 4.4 Révisions
À chaque publication, le robot enregistre la valeur dans `vintages/`. Avec la **clé FRED gratuite** (déjà prévue dans tes secrets, sans carte bancaire), ALFRED fournit l'historique officiel des révisions des principales séries américaines.

### 4.5 Registre des sources
Chaque source est classée **officielle / API publique autorisée / non officielle tolérée / à remplacer**. Les non officielles (Yahoo, CNBC, calendrier TradingView, Forex Factory) restent utilisées seulement là où aucune alternative officielle gratuite n'existe, avec l'étiquette « non officiel » visible. Les relais tiers (`r.jina.ai`, `rss2json`) sont supprimés.

---

## 5. Infrastructure de mise à jour (correctif I1 à I3)

| Aujourd'hui | Proposé |
|---|---|
| Boucle continue qui se relance elle-même | **Calendrier par type de source** : marchés toutes les 30 min en séance ; données officielles quotidiennes une fois par jour après leur heure de publication ; mensuelles les jours de publication ; rien le week-end hors crypto et géopolitique |
| Commit de 5 Mo dans `main` à chaque passage | Données publiées sur une **branche `data` séparée, réécrite à chaque passage** (un seul commit, aucune accumulation) ; un **instantané quotidien** seulement dans l'historique |
| `robot-cache.json` (2,3 Mo) publié | Cache gardé par `actions/cache`, hors du dépôt |
| IA toutes les 30 min | Après les publications importantes et 2 à 4 fois par jour |
| 28 déploiements Pages annulés sur 61 | Le site ne se redéploie que quand le code change ; les données sont lues sur la branche `data` |

Effet attendu : le dépôt arrête de grossir, beaucoup moins de passages Actions, un usage conforme aux conditions de GitHub. Les cours en direct restent assurés par le navigateur.

---

## 6. Personnalisation et données personnelles

- **Préférences** : actifs prioritaires, favoris (étoile sur tout panneau), vue compacte ou détaillée, fuseau affiché, bandeau de cours visible ou non.
- **Tes analyses** (biais, scénarios, notes, historique) : enregistrées localement **avec export/import en fichier** dès la première étape de refonte. En option plus tard : synchronisation gratuite entre appareils via un Gist GitHub privé avec ton propre jeton.

---

## 7. Design (aperçu de l'étape 3)

- **On garde** l'identité sombre et ambre, ainsi que IBM Plex Sans et Plex Mono (chiffres à chasse fixe), mais avec les polices **hébergées dans le dépôt**.
- **On retire** les effets « Ultra » (verre, 3D).
- **Une grille, une échelle d'espacements, une échelle typographique**, définies dans `tokens.css`.
- **Couleurs à sens unique** : vert = favorable à l'actif étudié, rouge = défavorable, ambre = attention, bleu = information. Elles sont toujours doublées d'un signe ou d'une flèche, pour rester lisibles par les daltoniens.
- **En-tête de panneau identique partout** : titre · badge de nature · source · période · mise à jour · menu (détail, copier, favori). Les explications sont repliées derrière un « ? ».
- **Téléphone** : 1re ligne = la réponse (chiffre, statut), détails en accordéon, aucune page de plus de 4 à 5 écrans.

---

## 8. Plan des étapes suivantes

| Étape | Contenu | Point de contrôle |
|---|---|---|
| 3a | Correctifs P0 de fiabilité : IPC US, statut calculé, badge dynamique, alertes « périmé » sur place, export/import des données personnelles | Tests + comparaison aux sources officielles |
| 3b | Infrastructure : branche `data`, calendrier du robot, fin de la boucle | 48 h d'observation des passages |
| 3c | Coquille : `tokens.css`, nouvelle navigation 8 sections, redirections, extraction du CSS | Les 22 anciennes pages restent accessibles dans les nouvelles sections |
| 4 | Migration section par section vers `src/pages/…` + uPlot | Audit Playwright à chaque section |
| 5 | Nouveaux modules dans l'ordre de ta Partie 9 | — |
| 6–8 | Données, tests, documentation, déploiement sur `main` | Fusion seulement quand tout est vert |

Tout se fait sur la branche `refonte`. Ton site actuel reste en ligne, intact, jusqu'à la fusion. L'état d'origine est conservé dans la branche `sauvegarde-avant-refonte`.

---

## 9. Décisions à valider

1. L'organisation en 8 sections et la table de correspondance (§1).
2. Les modules JavaScript sans compilation, et le découpage de `CLAUDE.md` (« un seul `index.html` ») remplacé par la Partie 10-A.
3. uPlot pour les graphiques.
4. La branche `data` et la fin de la boucle continue du robot (§5).
5. La clé FRED gratuite à remplir si ce n'est pas déjà fait (pour les révisions et les spreads de crédit), ainsi qu'une clé EIA gratuite pour l'énergie.
6. Le maintien de l'analyste IA (Gemini gratuit), à une fréquence réduite.
