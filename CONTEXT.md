# Contexte & Architecture du Projet — MattLerte

Ce document résume l'ensemble des choix techniques, des particularités de l'API Ilévia/MEL découvertes lors du développement, ainsi que le fonctionnement complet du système pour reprendre le projet rapidement lors d'une prochaine session.

---

## 1. Objectif du projet
Envoyer des notifications sur téléphone via un **Bot Discord** lorsqu'une interruption (🔴) ou une perturbation (🟠) survient sur les **2 lignes de Métro (M1, M2)** ou les **2 lignes de Tramway (R, T)** du réseau **Ilévia (Lille)**, avec :
- Un salon Discord qui reste **100% propre** (pas d'accumulation d'historique au fil des jours).
- Un hébergement **100% gratuit et sans serveur allumé** via **GitHub Actions** (`https://github.com/Neplexx/MattLerte`).

---

## 2. Particularités de l'API Open Data MEL (`dsp_ilevia:perturbations`)
L'API interrogée est le flux OGC Features du GeoServer de la MEL :
`https://data.lillemetropole.fr/geoserver/ogc/features/v1/collections/dsp_ilevia:perturbations/items?f=application/json&limit=200`

### Découvertes techniques importantes gérées dans `src/mattlerte/ilevia.py` :
1. **Codes des lignes (`cible`)** :
   - Métro Ligne 1 : `ILEVIA:LineRef::ME1:LOC`
   - Métro Ligne 2 : `ILEVIA:LineRef::ME2:LOC`
   - Tramway (Roubaix R & Tourcoing T regroupés) : `ILEVIA:LineRef::71:LOC` (ainsi que `TRAM`, `TRAMR`, `TRAMT` par sécurité).
2. **Fuseau horaire (`parse_ilevia_datetime`)** :
   - Bien que les champs `date_modification` et `heure_fin_prevue` se terminent par un `Z`, le serveur GeoServer de la MEL exporte directement **l'heure locale de Lille (`Europe/Paris`)**. Il ne faut donc pas convertir d'UTC vers Paris (+2h), mais rattacher directement `ZoneInfo("Europe/Paris")` à l'heure reçue.
3. **Détection des fins "Indéterminées" (`_is_default_24h_ttl`)** :
   - Quand Ilévia ne connaît pas l'heure de fin d'un incident, leur système fixe automatiquement `heure_fin_prevue = date_modification + 24h` (à la minute et seconde près).
   - Notre code détecte ce TTL de 24h par défaut pour afficher **`Fin estimée : Indéterminée`** au lieu d'une fausse durée restante de 23h50.
4. **Nettoyage des reliquats de la veille (`service_day_cutoff`)** :
   - Chaque matin vers `03h11`, le système Ilévia republie toutes les perturbations encore actives pour la nouvelle journée de service et laisse les entrées de la veille expirer pendant 24h. On ignore donc les entrées dont `date_modification` est antérieure à la journée de service en cours.
5. **Déduplication par empreinte (`compute_fingerprint`)** :
   - Ilévia crée parfois 2 entrées distinctes avec des `identifiant_perturbation` différents mais le même message à quelques minutes d'écart. On déduplique avec un hash SHA-1 sur 10 caractères basé sur `cible + message normalisé`.
6. **Filtrage et Parsing (`classify_severity` & `parse_reason_and_stations`)** :
   - Seuls les incidents **🔴 RED** (interruption, station non desservie, déviation, bus relais) et **🟠 ORANGE** (ralentissement, trafic perturbé, retard, incident, panne) sont retenus.
   - Les annonces de travaux futurs (*"Du 2 au 30 novembre..."*), renforts d'offre (*"renforce son offre"*) et pannes d'équipements (*"valideurs"*, *"distributeurs"*) sont ignorés.
   - Les phrases de report (*"Vous pouvez vous reporter..."*, *"Retrouvez le plan..."*) sont supprimées pour extraire uniquement :
     - **Raison** (ex: *Demande des services de police*, *Travaux*, *Dégâts causés par les conditions météorologiques*)
     - **Stations non desservies** sous forme de liste à puces en gras (`• **Station**`).

---

## 3. Cycle de vie des messages Discord (`src/mattlerte/discord_client.py`)
Le bot fonctionne **sans base de données** en utilisant directement l'API REST v10 de Discord (`requests`) et en lisant les 50 derniers messages du salon :

1. **Nouvelle panne détectée (`send_ongoing_notification`)** :
   - Envoie uniquement un Embed (sans texte `content` au-dessus) :
     - Titre : `🔴 Métro Ligne 1 - Interruption en cours` (ou 🟠)
     - Description : `Début`, `Fin estimée`, `Raison`, `Stations non desservies`
     - Footer : `réf: <fingerprint>`
2. **Panne toujours en cours (`update_ongoing_message`)** :
   - Met à jour (`PATCH`) l'Embed existant pour actualiser le temps écoulé / restant sans renvoyer de notification push sur le téléphone.
3. **Fin de la panne (`resolve_incident`)** :
   - **Supprime (`DELETE`)** l'encadré 🔴/🟠 de la panne.
   - **Poste (`POST`)** un nouvel encadré vert **`🟢 <Ligne> - Fin de l'interruption`** avec la durée totale de l'interruption et les stations rétablies (ce qui envoie une notification push de retour à la normale sur le téléphone).
   - Footer : `réf: <fingerprint> • résolu`
4. **Auto-destruction au bout d'1h (`sync_channel_state`)** :
   - À chaque passage du script, tout message vert portant `• résolu` dont l'âge dépasse `RESOLVED_RETENTION_MINUTES = 60` (configurable dans `config.py`) est **automatiquement supprimé**.
   - Quand le trafic est normal, le salon Discord est donc totalement vide.

---

## 4. Commandes utiles (Environnement `uv`)
- **Tester sans envoyer sur Discord** : `uv run mattlerte --dry-run`
- **Exécuter avec envoi/synchronisation Discord** : `uv run mattlerte`
- **Lancer les tests unitaires** : `uv run pytest -v`

---

## 5. Pistes d'évolutions futures évoquées
- **Plan B automatique en cas de panne** : Interroger `dsp_ilevia:vlille_temps_reel` (vélos V'Lille disponibles aux stations proches) ou `dsp_ilevia:prochains_passages` (prochains bus) pour l'afficher dans l'encadré de panne.
- **Plages horaires personnalisées** : Adapter les heures ou jours de notification selon les trajets quotidiens.
