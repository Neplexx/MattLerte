# Contexte & Architecture du Projet — MattLerte

Ce document résume l'ensemble des choix techniques, des particularités de l'API Ilévia/MEL découvertes lors du développement, ainsi que le fonctionnement complet du système pour reprendre le projet rapidement lors d'une prochaine session.

---

## 1. Objectif du projet
Envoyer des notifications sur téléphone via un **Bot Discord** lorsqu'une interruption (🔴) ou une perturbation (🟠) survient sur les **2 lignes de Métro (M1, M2)** ou les **2 lignes de Tramway (R, T)** du réseau **Ilévia (Lille)**, avec :
- **Maximum 3 messages actifs simultanés** : 1 résumé pour **Métro Ligne 1**, 1 pour **Métro Ligne 2**, et 1 pour **Tramway (R / T)**.
- Un salon Discord qui reste **100% propre** (pas d'accumulation d'historique au fil des jours).
- Un hébergement **100% gratuit et sans serveur allumé** via **GitHub Actions** (`https://github.com/Neplexx/MattLerte`) déclenché toutes les 5 minutes exactement via **cron-job.org** (`workflow_dispatch`).

---

## 2. Particularités de l'API Open Data MEL (`dsp_ilevia:perturbations`)
L'API interrogée est le flux OGC Features du GeoServer de la MEL :
`https://data.lillemetropole.fr/geoserver/ogc/features/v1/collections/dsp_ilevia:perturbations/items?f=application/json&limit=200`

### Découvertes techniques importantes gérées dans `src/mattlerte/ilevia.py` :
1. **Codes des lignes (`cible`)** :
   - Métro Ligne 1 : `ILEVIA:LineRef::ME1:LOC` -> `"Métro Ligne 1"`
   - Métro Ligne 2 : `ILEVIA:LineRef::ME2:LOC` -> `"Métro Ligne 2"`
   - Tramway (Roubaix R & Tourcoing T regroupés) : `ILEVIA:LineRef::71:LOC`, `TRAM`, `TRAMR`, `TRAMT` -> `"Tramway (R / T)"`.
2. **Fuseau horaire (`parse_ilevia_datetime`)** :
   - Bien que les champs `date_modification` et `heure_fin_prevue` se terminent par un `Z`, le serveur GeoServer de la MEL exporte directement **l'heure locale de Lille (`Europe/Paris`)**. On rattache donc directement `ZoneInfo("Europe/Paris")` à l'heure reçue.
3. **Détection des fins "Indéterminées" (`_is_default_24h_ttl`) & extraction depuis le texte (`_extract_text_end_time`)** :
   - Quand Ilévia ne connaît pas l'heure de fin d'un incident, leur système fixe automatiquement `heure_fin_prevue = date_modification + 24h` (à la minute et seconde près).
   - Si le texte mentionne explicitement une heure de fin (ex: *"jusque 09h 40 environ"*), le code l'extrait et l'utilise comme `end_time`. Sinon, il affiche **`Fin estimée : Indéterminée`**.
4. **Nettoyage des reliquats de la veille (`service_day_cutoff`)** :
   - Les alertes sans heure de fin connue (TTL auto de 24h) antérieures à la journée de service en cours (coupure à 04h00) sont ignorées, tandis que les vraies coupures multi-jours avec `heure_fin_prevue` explicite sont conservées.
5. **Fusion par ligne (`compute_fingerprint(line_name, line_name)`)** :
   - Toutes les entrées actives d'une même ligne (`Métro Ligne 1`, `Métro Ligne 2`, `Tramway (R / T)`) sont fusionnées en un seul message : raisons distinctes concaténées par ` / ` et stations/sections dédupliquées.
6. **Affichage de la Date (`format_date_str`)** :
   - Affiche `JJ/MM (Aujourd'hui)` pour le jour même, `JJ/MM (hier)` pour la veille, et `JJ/MM` au-delà, juste en dessous de `Fin estimée`.

---

## 3. Cycle de vie des messages Discord (`src/mattlerte/discord_client.py`)
Le bot fonctionne **sans base de données** en utilisant directement l'API REST v10 de Discord (`requests`) et en lisant les 50 derniers messages du salon :

1. **Nouvelle panne détectée (`send_ongoing_notification`)** :
   - Envoie uniquement un Embed (sans texte `content` au-dessus) :
     - Titre : `🔴 Métro Ligne 1 - Interruption en cours` (ou 🟠)
     - Description : `Début`, `Fin estimée`, `Date`, `Raison`, `Stations non desservies`
     - Footer : `réf: <fingerprint>`
2. **Panne toujours en cours (`update_ongoing_message`)** :
   - Met à jour (`PATCH`) l'Embed existant pour actualiser le temps écoulé / restant, les raisons et les stations sans renvoyer de notification push sur le téléphone.
3. **Fin de la panne (`resolve_incident`)** :
   - **Supprime (`DELETE`)** l'encadré 🔴/🟠 de la panne.
   - **Poste (`POST`)** un nouvel encadré vert **`🟢 <Ligne> - Fin de l'interruption`** avec la date, la durée totale de l'interruption et les stations rétablies.
   - Footer : `réf: <fingerprint> • résolu`
4. **Auto-destruction au bout d'1h (`sync_channel_state`)** :
   - À chaque passage du script, tout message vert portant `• résolu` dont l'âge dépasse `RESOLVED_RETENTION_MINUTES = 60` est **automatiquement supprimé**.

---

## 4. Commandes utiles (Environnement `uv`)
- **Tester sans envoyer sur Discord** : `uv run mattlerte --dry-run`
- **Exécuter avec envoi/synchronisation Discord** : `uv run mattlerte`
- **Lancer les tests unitaires** : `uv run pytest -v`
