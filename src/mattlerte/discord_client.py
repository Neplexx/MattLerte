"""Client REST Discord v10 pour la publication et la synchronisation sans base de données."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re

import requests

from mattlerte.config import (
    DISCORD_BOT_TOKEN,
    DISCORD_CHANNEL_ID,
    RESOLVED_RETENTION_MINUTES,
    TIMEZONE,
)
from mattlerte.ilevia import (
    Perturbation,
    SEVERITY_RED,
    compute_fingerprint,
    format_date_str,
    format_duration,
)

DISCORD_API_BASE = "https://discord.com/api/v10"

COLOR_RED = 0xE30613
COLOR_ORANGE = 0xF39200
COLOR_GREEN = 0x2ECC71

FOOTER_REGEX = re.compile(
    r"^réf:\s*(?P<fp>[a-f0-9]+)(?:\s*•\s*(?P<resolved>résolu))?$"
)
START_TIME_REGEX = re.compile(r"\*\*Début\s*:\*\*\s*(\d{2})h(\d{2})")
DATE_REGEX = re.compile(r"\*\*Date\s*:\*\*\s*(\d{2})/(\d{2})")


@dataclass
class TrackedIncident:
    """Représente un message d'alerte actif déjà publié dans le salon Discord."""

    fingerprint: str
    message_id: str
    line_name: str
    stations: list[str]
    start_time: datetime


def _headers() -> dict[str, str]:
    """Construit les en-têtes d'authentification pour l'API Discord."""
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("La variable d'environnement DISCORD_BOT_TOKEN n'est pas définie.")
    if not DISCORD_CHANNEL_ID:
        raise RuntimeError("La variable d'environnement DISCORD_CHANNEL_ID n'est pas définie.")
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "MattLerte (https://github.com/Neplexx/MattLerte, 0.1.0)",
    }


def build_ongoing_embed(p: Perturbation, now: datetime | None = None) -> dict:
    """Construit l'Embed Discord rouge ou orange pour une perturbation en cours."""
    if now is None:
        now = datetime.now(TIMEZONE)

    color = COLOR_RED if p.severity == SEVERITY_RED else COLOR_ORANGE
    title = f"{p.severity_emoji} {p.line_name} - {p.severity_label}"
    stations_block = "\n".join(f"• **{st}**" for st in p.stations)

    description = (
        f"**Début :** {p.format_start_str(now=now)}\n"
        f"**Fin estimée :** {p.format_end_str(now=now)}\n"
        f"**Date :** {p.format_date_str(now=now)}\n"
        f"**Raison :** {p.reason}\n"
        f"**Stations non desservies :**\n{stations_block}"
    )

    return {
        "title": title,
        "description": description,
        "color": color,
        "footer": {"text": f"réf: {p.fingerprint}"},
    }


def build_resolved_embed(incident: TrackedIncident, now: datetime | None = None) -> dict:
    """Construit l'Embed Discord vert annonçant le rétablissement du trafic."""
    if now is None:
        now = datetime.now(TIMEZONE)

    total_duration = format_duration(now - incident.start_time)
    start_str = incident.start_time.strftime("%Hh%M")
    end_str = now.strftime("%Hh%M")
    stations_str = ", ".join(f"**{s}**" for s in incident.stations) if incident.stations else "Toute la ligne"

    description = (
        f"**Trafic rétabli :** {end_str}\n"
        f"**Date :** {format_date_str(incident.start_time, now=now)}\n"
        f"**Durée totale :** {total_duration} (de {start_str} à {end_str})\n"
        f"**Stations rétablies :** {stations_str}"
    )

    return {
        "title": f"🟢 {incident.line_name} - Fin de l'interruption",
        "description": description,
        "color": COLOR_GREEN,
        "footer": {"text": f"réf: {incident.fingerprint} • résolu"},
    }


def delete_message(message_id: str) -> None:
    """Supprime un message du salon Discord (ignore silencieusement s'il a déjà été supprimé)."""
    url = f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}"
    response = requests.delete(url, headers=_headers(), timeout=15)
    if response.status_code not in (200, 204, 404):
        response.raise_for_status()


def sync_channel_state(
    now: datetime | None = None,
    limit: int = 50,
) -> dict[str, TrackedIncident]:
    """Lit l'historique récent du salon, purge les messages expirés et retourne les pannes actives."""
    if now is None:
        now = datetime.now(TIMEZONE)

    url = f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages?limit={limit}"
    response = requests.get(url, headers=_headers(), timeout=15)
    response.raise_for_status()
    messages = response.json()

    open_incidents: dict[str, TrackedIncident] = {}

    for msg in messages:
        msg_id = msg["id"]
        embeds = msg.get("embeds") or []
        if not embeds:
            continue

        embed = embeds[0]
        footer_text = (embed.get("footer") or {}).get("text", "")

        if footer_text.startswith("ID:"):
            delete_message(msg_id)
            continue

        match = FOOTER_REGEX.match(footer_text)
        if not match:
            continue

        is_resolved = bool(match.group("resolved"))
        msg_ts = datetime.fromisoformat(msg["timestamp"]).astimezone(TIMEZONE)

        if is_resolved:
            age_minutes = (now - msg_ts).total_seconds() / 60.0
            if age_minutes >= RESOLVED_RETENTION_MINUTES:
                delete_message(msg_id)
            continue

        raw_title = embed.get("title", "")
        line_name = re.sub(r"^[🔴🟠🟢]\s*", "", raw_title).split(" - ")[0].strip() or "Réseau Ilévia"
        line_fp = compute_fingerprint(line_name, line_name)

        # Les messages sont lus du plus récent au plus ancien : tout second message d'une même ligne est un doublon.
        if line_fp in open_incidents:
            delete_message(msg_id)
            continue

        raw_desc = embed.get("description", "")
        stations = [
            re.sub(r"^\•\s*\*\*|\*\*$", "", line.strip())
            for line in raw_desc.splitlines()
            if line.strip().startswith("•")
        ]

        start_dt = msg_ts
        m_start = START_TIME_REGEX.search(raw_desc)
        if m_start:
            h, m = int(m_start.group(1)), int(m_start.group(2))
            m_date = DATE_REGEX.search(raw_desc)
            if m_date:
                day, month = int(m_date.group(1)), int(m_date.group(2))
                candidate = now.replace(month=month, day=day, hour=h, minute=m, second=0, microsecond=0)
                if candidate > now + timedelta(minutes=5):
                    candidate = candidate.replace(year=now.year - 1)
            else:
                candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if candidate > now + timedelta(minutes=5):
                    candidate -= timedelta(days=1)
            start_dt = candidate

        open_incidents[line_fp] = TrackedIncident(
            fingerprint=line_fp,
            message_id=msg_id,
            line_name=line_name,
            stations=stations,
            start_time=start_dt,
        )

    return open_incidents


def send_ongoing_notification(p: Perturbation, now: datetime | None = None) -> str:
    """Publie un nouvel Embed d'alerte sur Discord et retourne l'identifiant du message créé."""
    url = f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages"
    embed = build_ongoing_embed(p, now=now)
    response = requests.post(url, headers=_headers(), json={"embeds": [embed]}, timeout=15)
    response.raise_for_status()
    return response.json()["id"]


def update_ongoing_message(message_id: str, p: Perturbation, now: datetime | None = None) -> None:
    """Met à jour silencieusement un Embed d'alerte existant sans déclencher de notification push."""
    url = f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}"
    embed = build_ongoing_embed(p, now=now)
    response = requests.patch(url, headers=_headers(), json={"embeds": [embed]}, timeout=15)
    response.raise_for_status()


def resolve_incident(incident: TrackedIncident, now: datetime | None = None) -> str:
    """Remplace l'Embed d'alerte par un Embed vert de fin d'interruption."""
    delete_message(incident.message_id)
    url = f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages"
    embed = build_resolved_embed(incident, now=now)
    response = requests.post(url, headers=_headers(), json={"embeds": [embed]}, timeout=15)
    response.raise_for_status()
    return response.json()["id"]
