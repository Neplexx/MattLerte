# MattLerte 🚇🚊

Système d'alertes Discord pour les interruptions et perturbations en temps réel sur les **2 lignes de Métro (M1, M2)** et les **2 lignes de Tramway (R, T)** du réseau **Ilévia (Lille / MEL)**.

## Fonctionnement
- **Source de données** : Interroge le flux Open Data de la MEL (`dsp_ilevia:perturbations`, API OGC Features avec suivi de pagination) toutes les 5 minutes.
- **Surveillance ciblée** : Métro Ligne 1, Métro Ligne 2, Tramway Roubaix / Tourcoing (`R` et `T`).
- **Filtrage & déduction intelligente** :
  - Détecte les incidents en cours (🔴 Interruption / Station non desservie / Trafic suspendu, 🟠 Ralentissement / Perturbation / Panne / Travaux actifs).
  - Ignore les négations (*« aucune perturbation »*), les prévisions futures sans coupure immédiate, les pannes de valideurs/distributeurs et les reprises totales (tout en conservant les reprises partielles avec restriction active).
  - Fusionne les incidents multiples d'une même ligne, gère les coupures nocturnes (< 8h) après 04h00 et conserve une fin `Indéterminée` tant qu'un des incidents n'a pas d'heure de reprise fiable.
- **Cycle de notification Discord** :
  1. **Incident détecté** : Embed 🔴 ou 🟠 avec début, fin estimée (ou `Indéterminée`), date, raison et stations concernées.
  2. **Mise à jour silencieuse** : L'embed actif est actualisé (`PATCH`) sans renvoyer de notification push.
  3. **Escalade `🟠 → 🔴`** : Si une perturbation dégénère en interruption, une nouvelle alerte 🔴 est envoyée (avec notification push) et l'ancien message 🟠 est supprimé.
  4. **Fin de l'incident** : Poste d'abord un embed vert (`🟢 Fin de l'interruption` ou `Fin de la perturbation`) avec la durée totale, puis supprime l'alerte active. Les messages verts sont nettoyés automatiquement après **60 minutes**.
- **Zéro base de données** : Le bot reconstruit son état depuis ses propres messages récents dans le salon Discord (avec gestion des rate-limits `429` et retries HTTP).

---

## 1. Créer le Bot Discord (2 minutes)

1. Va sur le [Portail Développeur Discord](https://discord.com/developers/applications) et clique sur **New Application** (ex: `MattLerte`).
2. Dans le menu **Bot** à gauche :
   - Clique sur **Reset Token** pour récupérer ton **`DISCORD_BOT_TOKEN`**.
3. Dans le menu **OAuth2** :
   - Dans **OAuth2 URL Generator**, coche `bot`.
   - Dans **Bot Permissions** qui apparaît en dessous, coche :
     - `View Channels` (Voir les salons)
     - `Send Messages` (Envoyer des messages)
     - `Embed Links` (Intégrer des liens)
     - `Read Message History` (Voir les anciens messages — indispensable pour détecter les pannes déjà envoyées)
   - Copie l'URL générée en bas et ouvre-la pour inviter le bot sur ton serveur Discord.
4. Récupère le **`DISCORD_CHANNEL_ID`** :
   - Dans Discord : *Paramètres utilisateur -> Avancés -> Activer le Mode développeur*.
   - Clic droit sur le salon où tu veux recevoir les alertes -> **Copier l'identifiant du salon**.

---

## 2. Tester en local avec `uv`

1. Copie `.env.example` vers `.env` et remplis tes identifiants :
   ```powershell
   Copy-Item .env.example .env
   ```
2. Tester en mode aperçu (sans envoyer sur Discord) :
   ```powershell
   uv run mattlerte --dry-run
   ```
3. Tester avec envoi réel sur ton salon Discord (échoue avec code `1` si `DISCORD_BOT_TOKEN` ou `DISCORD_CHANNEL_ID` est manquant) :
   ```powershell
   uv run mattlerte
   ```
4. Lancer les tests unitaires :
   ```powershell
   uv run pytest
   ```

---

## 3. Déployer et automatiser gratuitement sur GitHub Actions

1. Pousse ce dépôt sur ton compte GitHub (de préférence en dépôt **Public** pour que les minutes GitHub Actions soient 100% illimitées et gratuites).
2. Dans ton dépôt GitHub, va dans **Settings -> Secrets and variables -> Actions -> New repository secret** et ajoute :
   - `DISCORD_BOT_TOKEN`
   - `DISCORD_CHANNEL_ID`
3. Va dans l'onglet **Actions** de ton dépôt, clique sur **Vérification Métro & Tram Ilévia** (`.github/workflows/check_metro.yml`), puis **Run workflow** pour faire un premier test immédiat.
4. **Automatisation toutes les 5 minutes via l'API GitHub (`workflow_dispatch`)** :
   - Le workflow `check_metro.yml` est déclenché à la demande via `workflow_dispatch` (sans `schedule` GitHub, qui subit des délais variables).
   - Configure un cron externe (ex. **cron-job.org**) toutes les 5 minutes vers :
     - **URL** : `https://api.github.com/repos/<USER>/<REPO>/actions/workflows/check_metro.yml/dispatches`
     - **Méthode** : `POST`
     - **Headers** :
       - `Authorization: Bearer <GITHUB_PAT>` (Fine-grained PAT avec permission **Actions: Read and write**)
       - `Accept: application/vnd.github+json`
       - `X-GitHub-Api-Version: 2022-11-28`
     - **Body (JSON)** : `{"ref":"main"}`
5. **Intégration continue (CI)** : Le workflow `.github/workflows/ci.yml` exécute automatiquement `uv run pytest -v` à chaque `push` et `pull_request`.
