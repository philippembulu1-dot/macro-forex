# Macro Forex — terminal macro personnel

Site gratuit (GitHub Pages) pour suivre l'or, le dollar et les devises majeures. Un robot (GitHub Actions) récupère des données publiques toutes les 2 heures et les écrit dans `data/macro.json`.

## Fichiers
- `index.html` : le site complet.
- `scripts/update_data.py` : le robot (Python, sans clé API).
- `.github/workflows/update-data.yml` : lance le robot toutes les 2 h et sur demande.
- `data/seasonal.json` : saisonnalité 20 ans (recalculée par le robot une fois par semaine).
- `data/world-map.json` : contours des pays (Natural Earth, domaine public).
- `CLAUDE.md` : consignes de travail.

## Réglages GitHub (une seule fois)
1. **Settings → Pages** → Source : *Deploy from a branch* → branche `main`, dossier `/ (root)` → Save.
2. **Settings → Actions → General** → *Workflow permissions* → **Read and write permissions** → Save.
3. **Actions** → *Mise à jour des données* → **Run workflow**.
4. Ouvrir le site → panneau **Fraîcheur des données** : chaque source y est marquée à jour, ancienne ou en échec.

## Règles
Aucun chiffre inventé. Ce qui n'est pas automatisable gratuitement est une saisie manuelle datée, avec lien de source et mention « à vérifier ». Ce site n'est pas un conseil en investissement.
