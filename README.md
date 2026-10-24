# MattLerte 🚇🚊

Système d'alertes Discord pour les interruptions et perturbations en temps réel sur les **2 lignes de Métro (M1, M2)** et les **2 lignes de Tramway (R, T)** du réseau **Ilévia (Lille / MEL)**.

## Fonctionnement
- **Surveillance ciblée** : Métro Ligne 1, Métro Ligne 2, Tramway Roubaix / Tourcoing.
- **Filtrage intelligent** : Ne notifie que les incidents en cours (🔴 Interruption / Station non desservie, 🟠 Ralentissement / Perturbation) et ignore les prévisions de travaux ou pannes de valideurs.
- **Cycle de notification** :
  1. **Interruption en cours** : Notification Discord avec la durée écoulée et l'heure de reprise estimée (si connue).
  2. **Fin de l'interruption** : Nouvelle notification Discord dès que l'incident est terminé, avec la durée totale qu'a duré la panne.
- **Zéro base de données** : Le bot lit ses propres messages récents dans le salon Discord pour suivre les incidents en cours, idéal pour tourner gratuitement sur **GitHub Actions**.

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
3. Tester avec envoi réel sur ton salon Discord :
   ```powershell
   uv run mattlerte
   ```
4. Lancer les tests unitaires :
   ```powershell
   uv run pytest
   ```

---

## 3. Déployer gratuitement sur GitHub Actions

1. Pousse ce dépôt sur ton compte GitHub (de préférence en dépôt **Public** pour que les minutes GitHub Actions soient 100% illimitées et gratuites).
2. Dans ton dépôt GitHub, va dans **Settings -> Secrets and variables -> Actions -> New repository secret** et ajoute :
   - `DISCORD_BOT_TOKEN`
   - `DISCORD_CHANNEL_ID`
3. Va dans l'onglet **Actions** de ton dépôt, clique sur **Vérification Métro & Tram Ilévia**, puis **Run workflow** pour faire un premier test immédiat !
