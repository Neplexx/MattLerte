import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Charge les variables du fichier .env en local (sans effet sur GitHub Actions si absent)
load_dotenv()

# Configuration Discord
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID: str = os.getenv("DISCORD_CHANNEL_ID", "")

# Durée (en minutes) pendant laquelle la notification verte "Fin de l'interruption"
# reste visible dans le salon avant d'être automatiquement supprimée (1h = 60 min)
RESOLVED_RETENTION_MINUTES: int = 60

# Fuseau horaire local (Lille) pour l'affichage des heures et calcul des durées
TIMEZONE = ZoneInfo("Europe/Paris")

# Endpoint Open Data MEL pour les perturbations Ilévia
ILEVIA_PERTURBATIONS_URL = (
    "https://data.lillemetropole.fr/geoserver/ogc/features/v1/"
    "collections/dsp_ilevia:perturbations/items?f=application/json&limit=200"
)

# Lignes surveillées dans l'API Ilévia (2 lignes de Métro + 2 lignes de Tramway)
WATCHED_LINES: dict[str, str] = {
    "ILEVIA:LineRef::ME1:LOC": "Métro Ligne 1",
    "ILEVIA:LineRef::ME2:LOC": "Métro Ligne 2",
    "ILEVIA:LineRef::71:LOC": "Tramway (R / T)",
    "ILEVIA:LineRef::TRAM:LOC": "Tramway (R / T)",
    "ILEVIA:LineRef::TRAMR:LOC": "Tramway R (Roubaix)",
    "ILEVIA:LineRef::TRAMT:LOC": "Tramway T (Tourcoing)",
}
