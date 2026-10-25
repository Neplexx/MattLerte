from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import html
import re
import unicodedata
import requests

from mattlerte.config import ILEVIA_PERTURBATIONS_URL, TIMEZONE, WATCHED_LINES

# Deux niveaux d'alerte retenus
SEVERITY_RED = "RED"        # 🔴 Interruption / Station non desservie
SEVERITY_ORANGE = "ORANGE"  # 🟠 Trafic ralenti / Perturbé

# Mots-clés indiquant une information non-urgente ou une résolution (à ignorer comme nouvelle alerte)
IGNORED_PATTERNS = (
    r"\bdu \d{1,2} au \d{1,2}\b",
    r"\ba partir du \d{1,2}\b",
    r"\brenforce son offre\b",
    r"\bdistributeurs? automatiques?\b",
    r"\bvalideurs?\b",
    r"\barmoires techniques\b",
    r"evolution\s*:\s*realisation",
    r"\btrafic retabli\b",
    r"\breprise normale\b",
)

# Mots-clés indiquant une interruption (🔴 Point Rouge)
RED_KEYWORDS = (
    "interrompu",
    "interruption",
    "ne circule pas",
    "ne circulent pas",
    "a l'arret",
    "pas desservi",
    "plus desservi",
    "station fermee",
    "stations fermees",
    "devie",
    "deviation",
    "bus relais",
)

# Mots-clés indiquant un ralentissement ou trafic perturbé (🟠 Point Orange)
ORANGE_KEYWORDS = (
    "ralenti",
    "perturbe",
    "perturbation",
    "retard",
    "temps d'attente",
    "intervalle",
    "incident",
    "panne",
    "intervention",
    "ne sont pas realises",
    "n'est pas realise",
)


@dataclass
class Perturbation:
    fingerprint: str
    line_code: str
    line_name: str
    severity: str  # "RED" ou "ORANGE"
    message: str
    reason: str
    stations: list[str]
    start_time: datetime
    end_time: datetime
    has_known_end_time: bool

    @property
    def severity_emoji(self) -> str:
        return "🔴" if self.severity == SEVERITY_RED else "🟠"

    @property
    def severity_label(self) -> str:
        return "Interruption en cours" if self.severity == SEVERITY_RED else "Perturbation en cours"

    def format_start_str(self, now: datetime | None = None) -> str:
        if now is None:
            now = datetime.now(TIMEZONE)
        elapsed = format_duration(now - self.start_time)
        return f"{self.start_time.strftime('%Hh%M')} (depuis {elapsed})"

    def format_end_str(self, now: datetime | None = None) -> str:
        if now is None:
            now = datetime.now(TIMEZONE)
        if self.has_known_end_time:
            remaining = format_duration(self.end_time - now)
            return f"{self.end_time.strftime('%Hh%M')} (reste ~{remaining})"
        return "Indéterminée"

    def format_date_str(self, now: datetime | None = None) -> str:
        return format_date_str(self.start_time, now=now)


def format_date_str(dt: datetime, now: datetime | None = None) -> str:
    """Formate une date en 'JJ/MM (Aujourd'hui)', 'JJ/MM (hier)' ou 'JJ/MM'."""
    if now is None:
        now = datetime.now(TIMEZONE)
    date_part = dt.strftime("%d/%m")
    days_diff = (now.date() - dt.date()).days
    if days_diff == 0:
        return f"{date_part} (Aujourd'hui)"
    if days_diff == 1:
        return f"{date_part} (hier)"
    return date_part


def format_duration(delta: timedelta) -> str:
    """Formate un timedelta en texte lisible (ex: '45 min', '1h15')."""
    total_minutes = max(1, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}" if minutes > 0 else f"{hours}h"
    return f"{minutes} min"


def clean_message(raw_text: str) -> str:
    """Retire les balises HTML, caractères invisibles et normalise les espaces."""
    text = html.unescape(raw_text or "")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _format_station_name(name: str) -> str:
    """Nettoie et met en forme proprement le nom d'une station ou d'une section."""
    cleaned = re.sub(r"\s*(?:=>|->)\s*report.*$", "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+en direction de\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+jusqu(?:e|['’][aà])\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*(?:Le\s+tramway\s+circule|Des\s+bus\s+relais).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*<+>+?\s*", " - ", cleaned)
    cleaned = re.sub(r"\s*/\s*", " / ", cleaned)
    cleaned = cleaned.strip(" .,:;►▶•-")
    # Si tout est en majuscules (ex: RÉPUBLIQUE BEAUX-ARTS), passe en Title Case plus lisible
    if cleaned.isupper() and len(cleaned) > 3:
        cleaned = cleaned.title()
    return cleaned


def parse_reason_and_stations(message: str) -> tuple[str, list[str]]:
    """
    Extrait la raison de l'incident et la liste des stations non desservies
    à partir du message brut d'Ilévia en supprimant tout le texte de report.
    """
    # 1. Supprime les phrases de report/conseils inutiles
    stripped = re.sub(
        r"(?:▶|►|•)?\s*(?:Vous pouvez vous reporter|Retrouvez le plan|Des bus relais sont|Nous vous invitons|Le Tramway circule uniquement|Calculez votre trajet|il[eé]via vous prie)[^.\n]*(?:\.|$)",
        "\n",
        message,
        flags=re.IGNORECASE,
    )

    # 2. Extraction de la Raison ("En raison de / d'une / des ...")
    reason = "Incident d'exploitation"
    reason_match = re.search(
        r"en raison\s+(?:d['’]une?\s+|des\s+|de\s+la\s+|du\s+|de\s+)([^,.\n:]+)",
        stripped,
        flags=re.IGNORECASE,
    )
    if reason_match:
        raw_reason = reason_match.group(1).strip()
        raw_reason = re.sub(r"^l['’]", "", raw_reason, flags=re.IGNORECASE).strip()
        if raw_reason:
            reason = raw_reason[0].upper() + raw_reason[1:]

    stations: list[str] = []

    def _add_station(candidate: str) -> None:
        st = _format_station_name(candidate)
        norm = _normalize_for_match(st)
        if st and not any(_normalize_for_match(existing) == norm for existing in stations):
            stations.append(st)

    # 3. Cas 1 : Liste à puces explicite (►, ▶, •)
    bullet_matches = re.findall(r"[►▶•]\s*([^\n►▶•]+)", stripped)
    for b in bullet_matches:
        _add_station(b)

    # 4. Cas 2 : "les stations (de métro) X et Y ne sont (actuellement) pas desservies"
    if not stations:
        m_multi = re.search(
            r"les stations(?:\s+de\s+m[eé]tro)?\s+(.+?)\s+ne\s+sont\s+(?:actuellement\s+)?(?:pas|plus)\s+desservi",
            stripped,
            flags=re.IGNORECASE,
        )
        if m_multi:
            raw_list = re.split(r"\s+et\s+|,\s*", m_multi.group(1), flags=re.IGNORECASE)
            for item in raw_list:
                _add_station(item)

    # 5. Cas 3 : "la station X n'est pas desservie" ou "ne desservent pas la station X"
    if not stations:
        m_single = re.search(
            r"(?:ne\s+desservent\s+pas\s+la\s+station|l['’]arr[eê]t\s+|la\s+station\s+)([^,.\n:]+?)(?:\s+n['’]est\s+(?:pas|plus)\s+desservi|\s+en\s+direction|\s*$|\.)",
            stripped,
            flags=re.IGNORECASE,
        )
        if m_single:
            _add_station(m_single.group(1))

    # 6. Cas 4 : "interrompu entre X et Y" ou "Toute la ligne"
    if not stations:
        between_matches = re.findall(
            r"interrompu[^.\n:]*?\bentre\s+(?:les\s+stations\s*:?\s*)?([^.\n:]+)",
            stripped,
            flags=re.IGNORECASE,
        )
        for m_between in between_matches:
            _add_station(re.sub(r"\s+et\s+", " - ", m_between, flags=re.IGNORECASE))

        if not stations and re.search(r"ensemble du trafic|toute la ligne|est interrompue?\b", stripped, flags=re.IGNORECASE):
            _add_station("Toute la ligne")

    if not stations:
        first_sentence = stripped.split(".")[0].strip()
        _add_station(first_sentence)

    return reason, stations


def _normalize_for_match(text: str) -> str:
    """Supprime les accents et met en minuscules pour faciliter la détection de mots-clés."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def classify_severity(message: str) -> str | None:
    """
    Retourne 'RED' (interruption), 'ORANGE' (ralentissement/perturbation),
    ou None si c'est une prévision/info à ignorer.
    """
    if "✅" in message:
        return None

    normalized = _normalize_for_match(message)

    for pattern in IGNORED_PATTERNS:
        if re.search(pattern, normalized):
            return None

    if any(keyword in normalized for keyword in RED_KEYWORDS):
        return SEVERITY_RED

    if any(keyword in normalized for keyword in ORANGE_KEYWORDS):
        return SEVERITY_ORANGE

    return None


def compute_fingerprint(line_code: str, cleaned_message: str) -> str:
    """
    Crée un identifiant unique court (10 caractères) basé sur la ligne et le texte.
    Permet de fusionner les doublons créés par l'API Ilévia pour un même incident.
    """
    normalized_msg = _normalize_for_match(cleaned_message)
    payload = f"{line_code}|{normalized_msg}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:10]


def parse_ilevia_datetime(iso_str: str | None, fallback: datetime) -> datetime:
    """
    Les horodatages de l'API GeoServer MEL sont en heure locale (Europe/Paris) avec un suffixe 'Z'.
    """
    if not iso_str:
        return fallback
    clean_iso = iso_str.rstrip("Z")
    if "+" in clean_iso:
        clean_iso = clean_iso.split("+")[0]
    dt_naive = datetime.fromisoformat(clean_iso)
    return dt_naive.replace(tzinfo=TIMEZONE)


def _is_default_24h_ttl(start_time: datetime, end_time: datetime) -> bool:
    """
    Détecte si `heure_fin_prevue` est simplement le TTL automatique de 24h (ou 23h)
    ajouté par Ilévia quand l'heure de fin réelle est inconnue.
    """
    hours_diff = (end_time - start_time).total_seconds() / 3600.0
    same_min_sec = (
        end_time.minute == start_time.minute
        and abs(end_time.second - start_time.second) <= 2
    )
    return 22.5 <= hours_diff <= 25.5 and same_min_sec


def _extract_text_end_time(message: str, start_time: datetime) -> datetime | None:
    """Extrait une heure de fin explicite dans le texte (ex: 'jusque 09h 40 environ')."""
    m = re.search(r"\bjusqu(?:e|['’][aà])\s*(\d{1,2})\s*h\s*(\d{0,2})\b", message, flags=re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    candidate = start_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < start_time - timedelta(minutes=15):
        candidate += timedelta(days=1)
    return candidate


def fetch_active_perturbations(now: datetime | None = None) -> list[Perturbation]:
    """
    Interroge l'API de la MEL, filtre sur Métro 1, Métro 2 et Tramway,
    et fusionne toutes les perturbations actives par ligne (1 message max par ligne).
    """
    if now is None:
        now = datetime.now(TIMEZONE)

    service_day_cutoff = (now - timedelta(hours=4)).date()

    response = requests.get(
        ILEVIA_PERTURBATIONS_URL,
        headers={"User-Agent": "MattLerte/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    deduped: dict[str, Perturbation] = {}

    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        line_code = props.get("cible", "")

        if line_code not in WATCHED_LINES:
            continue

        message = clean_message(props.get("message", ""))
        if not message:
            continue

        severity = classify_severity(message)
        if severity is None:
            continue

        start_time = parse_ilevia_datetime(props.get("date_modification"), fallback=now)
        end_time = parse_ilevia_datetime(props.get("heure_fin_prevue"), fallback=now)
        has_known_end = not _is_default_24h_ttl(start_time, end_time)

        if not has_known_end:
            text_end = _extract_text_end_time(message, start_time)
            if text_end is not None:
                end_time = text_end
                has_known_end = True

        if end_time <= now or start_time.date() < service_day_cutoff:
            continue

        line_name = WATCHED_LINES[line_code]
        fp = compute_fingerprint(line_name, line_name)
        reason, stations = parse_reason_and_stations(message)

        if fp in deduped:
            existing = deduped[fp]
            if severity == SEVERITY_RED:
                existing.severity = SEVERITY_RED
            existing.start_time = min(existing.start_time, start_time)

            if has_known_end:
                if existing.has_known_end_time:
                    existing.end_time = max(existing.end_time, end_time)
                else:
                    existing.end_time = end_time
                    existing.has_known_end_time = True
            elif not existing.has_known_end_time:
                existing.end_time = max(existing.end_time, end_time)

            existing_reasons = [_normalize_for_match(r) for r in existing.reason.split(" / ")]
            if _normalize_for_match(reason) not in existing_reasons:
                existing.reason = f"{existing.reason} / {reason}"

            existing_stations = {_normalize_for_match(s) for s in existing.stations}
            for st in stations:
                if _normalize_for_match(st) not in existing_stations:
                    existing.stations.append(st)
                    existing_stations.add(_normalize_for_match(st))
        else:
            deduped[fp] = Perturbation(
                fingerprint=fp,
                line_code=line_code,
                line_name=line_name,
                severity=severity,
                message=message,
                reason=reason,
                stations=stations,
                start_time=start_time,
                end_time=end_time,
                has_known_end_time=has_known_end,
            )

    return list(deduped.values())
