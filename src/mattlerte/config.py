"""Configuration globale et constantes du projet MattLerte."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID: str = os.getenv("DISCORD_CHANNEL_ID", "")

# Durée (en minutes) avant suppression automatique des messages "Fin de l'interruption".
RESOLVED_RETENTION_MINUTES: int = 60

TIMEZONE = ZoneInfo("Europe/Paris")

ILEVIA_PERTURBATIONS_URL = (
    "https://data.lillemetropole.fr/geoserver/ogc/features/v1/"
    "collections/dsp_ilevia:perturbations/items?f=application/json&limit=200"
)

# Correspondance entre les identifiants GTFS/SIRI Ilévia et les 3 groupes affichés sur Discord.
WATCHED_LINES: dict[str, str] = {
    "ILEVIA:LineRef::ME1:LOC": "Métro Ligne 1",
    "ILEVIA:LineRef::ME2:LOC": "Métro Ligne 2",
    "ILEVIA:LineRef::71:LOC": "Tramway (R / T)",
    "ILEVIA:LineRef::TRAM:LOC": "Tramway (R / T)",
    "ILEVIA:LineRef::TRAMR:LOC": "Tramway (R / T)",
    "ILEVIA:LineRef::TRAMT:LOC": "Tramway (R / T)",
}
