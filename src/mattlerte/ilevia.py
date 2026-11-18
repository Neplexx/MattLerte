"""Collecte, filtrage et normalisation des perturbations Ilévia depuis l'API Open Data MEL."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import html
import re
import unicodedata

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mattlerte.config import ILEVIA_PERTURBATIONS_URL, TIMEZONE, WATCHED_LINES

_ORIGINAL_REQUESTS_GET = requests.get

SEVERITY_RED = "RED"
SEVERITY_ORANGE = "ORANGE"

NEGATION_PATTERN = re.compile(
    r"\b(?:aucune?|sans)\s+(?:perturbation|interruption|incident)s?\b"
    r"|\bpas\s+d(?:e\s+|['’]\s*)(?:perturbation|interruption|incident)s?\b"
)

# Motifs d'annonces commerciales ou pannes d'équipements à exclure inconditionnellement.
IGNORED_PATTERNS = (
    r"\brenforce son offre\b",
    r"\bdistributeurs? automatiques?\b",
    r"\bvalideurs?\b",
    r"\barmoires techniques\b",
    r"evolution\s*:\s*realisation",
)

END_OF_PERTURBATION_PATTERN = re.compile(
    r"\b(?:trafic\s+retabli|reprise\s+normale|fin\s+de\s+perturbation|trafic\s+normal|retabli|termine|reprise)\b"
)

RESTRICTION_CLAUSE_PATTERN = re.compile(
    r"\b(?:mais|cependant|toutefois|reste|sauf)\b"
)

CLOSED_STATION_PATTERN = re.compile(
    r"\b(?:stations?\s+fermees?|non\s+desservi(?:e|es|s)?|pas\s+desservi(?:e|es|s)?|plus\s+desservi(?:e|es|s)?)\b"
)

DATE_RANGE_PATTERN = re.compile(r"\bdu\s+\d{1,2}\b")
FUTURE_SCHEDULE_PATTERN = re.compile(r"\b(?:a\s+partir\s+du|prochainement)\b")
IMMEDIATE_INDICATOR_PATTERN = re.compile(r"\b(?:actuellement|en\s+ce\s+moment|immediatement|en\s+cours)\b")
FUTURE_TENSE_PATTERN = re.compile(r"\b(?:sera|seront|aura\s+lieu|auront\s+lieu|va\s+etre|vont\s+etre)\b")

RED_KEYWORDS = (
    "trafic suspendu",
    "suspendu",
    "coupure",
    "interrompu",
    "interruption",
    "bloque",
    "bloquee",
    "ne circule pas",
    "ne circulent pas",
    "a l'arret",
    "pas desservi",
    "plus desservi",
    "non desservi",
    "non desservie",
    "non desservis",
    "non desservies",
    "station fermee",
    "stations fermees",
    "fermeture",
    "accident",
    "colis suspect",
    "malaise",
    "bus relais",
)

ORANGE_KEYWORDS = (
    "perturbe",
    "perturbation",
    "ralenti",
    "ralentissement",
    "retard",
    "irregulier",
    "temps d'attente",
    "intervalle",
    "incident",
    "panne",
    "ascenseur",
    "travaux",
    "intervention",
    "devie",
    "deviation",
    "ne sont pas realises",
    "n'est pas realise",
)

RED_KEYWORD_PATTERNS = tuple(
    re.compile(rf"\b{re.escape(kw)}(?:e|es|s)?\b") for kw in RED_KEYWORDS
)
ORANGE_KEYWORD_PATTERNS = tuple(
    re.compile(rf"\b{re.escape(kw)}(?:e|es|s)?\b") for kw in ORANGE_KEYWORDS
)


@dataclass
class Perturbation:
    """Représente l'état consolidé des perturbations actives sur une ligne."""

    fingerprint: str
    line_code: str
    line_name: str
    severity: str
    message: str
    reason: str
    stations: list[str]
    start_time: datetime
    end_time: datetime
    has_known_end_time: bool

    @property
    def severity_emoji(self) -> str:
        """Retourne l'icône correspondant au niveau de gravité."""
        return "🔴" if self.severity == SEVERITY_RED else "🟠"

    @property
    def severity_label(self) -> str:
        """Retourne l'intitulé court du niveau de gravité."""
        return "Interruption en cours" if self.severity == SEVERITY_RED else "Perturbation en cours"

    def format_start_str(self, now: datetime | None = None) -> str:
        """Formate l'heure de début et le temps écoulé (ex: '13h45 (depuis 1h15)')."""
        if now is None:
            now = datetime.now(TIMEZONE)
        elapsed = format_duration(now - self.start_time)
        return f"{self.start_time.strftime('%Hh%M')} (depuis {elapsed})"

    def format_end_str(self, now: datetime | None = None) -> str:
        """Formate l'heure de fin estimée et le temps restant, ou 'Indéterminée'."""
        if now is None:
            now = datetime.now(TIMEZONE)
        if self.has_known_end_time:
            remaining = format_duration(self.end_time - now)
            return f"{self.end_time.strftime('%Hh%M')} (reste ~{remaining})"
        return "Indéterminée"

    def format_date_str(self, now: datetime | None = None) -> str:
        """Formate la date de début de la perturbation."""
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
    """Convertit une durée en format court lisible (ex: '45 min', '1h15', '2h')."""
    total_minutes = max(1, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}" if minutes > 0 else f"{hours}h"
    return f"{minutes} min"


def clean_message(raw_text: str) -> str:
    """Supprime le balisage HTML, les caractères invisibles (BOM/ZWSP) et normalise les espaces."""
    text = html.unescape(raw_text or "")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _format_station_name(name: str) -> str:
    """Nettoie et harmonise la casse d'un nom de station ou d'un tronçon."""
    cleaned = re.sub(r"\s*(?:=>|->)\s*report.*$", "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+en direction de\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+jusqu(?:e|['’][aà])\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*(?:Le\s+tramway\s+circule|Des\s+bus\s+relais).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*<+>+?\s*", " - ", cleaned)
    cleaned = re.sub(r"\s*/\s*", " / ", cleaned)
    cleaned = cleaned.strip(" .,:;►▶•-")
    if cleaned.isupper() and len(cleaned) > 3:
        cleaned = cleaned.title()
    return cleaned


def parse_reason_and_stations(message: str) -> tuple[str, list[str]]:
    """Extrait le motif et la liste des stations non desservies depuis un message Ilévia."""
    # Remplace les consignes de report par un retour à la ligne pour éviter de coller deux phrases.
    stripped = re.sub(
        r"(?:▶|►|•)?\s*(?:Vous pouvez vous reporter|Retrouvez le plan|Des bus relais sont|Nous vous invitons|Le Tramway circule uniquement|Calculez votre trajet|il[eé]via vous prie)[^.\n]*(?:\.|$)",
        "\n",
        message,
        flags=re.IGNORECASE,
    )

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

    non_station_bullet_re = re.compile(
        r"^(?:des\s+bus\s+relais|bus\s+relais|navette|plus\s+d['’]info|info|report|voir|retrouvez|calculez|le\s+tramway\s+circule|nous\s+vous)"
    )
    bullet_matches = re.findall(r"[►▶•]\s*([^\n►▶•]+)", stripped)
    for b in bullet_matches:
        if not non_station_bullet_re.match(_normalize_for_match(b.strip())):
            _add_station(b)

    if not stations:
        m_multi = re.search(
            r"les stations(?:\s+de\s+m[eé]tro)?\s+(.+?)\s+ne\s+sont\s+(?:actuellement\s+)?(?:pas|plus)\s+desservi",
            stripped,
            flags=re.IGNORECASE,
        )
        if m_multi:
            for item in re.split(r"\s+et\s+|,\s*", m_multi.group(1), flags=re.IGNORECASE):
                _add_station(item)

    if not stations:
        m_single = re.search(
            r"(?:ne\s+desservent\s+pas\s+la\s+station|l['’]arr[eê]t\s+|la\s+station\s+)([^,.\n:]+?)(?:\s+n['’]est\s+(?:pas|plus)\s+desservi|\s+en\s+direction|\s*$|\.)",
            stripped,
            flags=re.IGNORECASE,
        )
        if m_single:
            _add_station(m_single.group(1))

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
        first_sentence = _format_station_name(stripped.split(".")[0].strip())
        if (
            not first_sentence
            or len(first_sentence) > 70
            or re.search(
                r"\b(?:trafic|perturbe|ralenti|incident|panne|travaux|retard|circulation|ligne)\b",
                _normalize_for_match(first_sentence),
            )
        ):
            _add_station("Toute la ligne")
        else:
            _add_station(first_sentence)

    return reason, stations


def _normalize_for_match(text: str) -> str:
    """Normalise une chaîne en minuscules sans accents pour les comparaisons insensibles."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _matches_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    """Vérifie si au moins un motif regex correspond dans le texte normalisé."""
    return any(p.search(text) for p in patterns)


def classify_severity(message: str) -> str | None:
    """Classifie un message en 'RED', 'ORANGE' ou None s'il doit être ignoré."""
    if "✅" in message:
        return None

    normalized = _normalize_for_match(message)

    if NEGATION_PATTERN.search(normalized):
        return None

    for pattern in IGNORED_PATTERNS:
        if re.search(pattern, normalized):
            return None

    if END_OF_PERTURBATION_PATTERN.search(normalized):
        restriction_match = RESTRICTION_CLAUSE_PATTERN.search(normalized)
        restriction_tail = normalized[restriction_match.start():] if restriction_match else ""
        has_tail_alert = bool(restriction_tail) and (
            _matches_any(RED_KEYWORD_PATTERNS, restriction_tail)
            or _matches_any(ORANGE_KEYWORD_PATTERNS, restriction_tail)
        )
        has_closed_station = bool(CLOSED_STATION_PATTERN.search(normalized))
        if not (has_tail_alert or has_closed_station):
            return None
        target_text = restriction_tail if has_tail_alert else normalized
        if _matches_any(RED_KEYWORD_PATTERNS, target_text):
            return SEVERITY_RED
        if _matches_any(ORANGE_KEYWORD_PATTERNS, target_text):
            return SEVERITY_ORANGE
        return None

    if DATE_RANGE_PATTERN.search(normalized):
        has_immediate_red = (
            _matches_any(RED_KEYWORD_PATTERNS, normalized)
            and not FUTURE_TENSE_PATTERN.search(normalized)
        )
        if not has_immediate_red:
            return None

    if FUTURE_SCHEDULE_PATTERN.search(normalized):
        has_immediate_red = (
            _matches_any(RED_KEYWORD_PATTERNS, normalized)
            and bool(IMMEDIATE_INDICATOR_PATTERN.search(normalized))
        )
        if not has_immediate_red:
            return None

    if _matches_any(RED_KEYWORD_PATTERNS, normalized):
        return SEVERITY_RED

    if _matches_any(ORANGE_KEYWORD_PATTERNS, normalized):
        return SEVERITY_ORANGE

    return None


def compute_fingerprint(line_code: str, cleaned_message: str) -> str:
    """Génère une empreinte SHA-1 courte (10 caractères) servant d'identifiant de suivi."""
    normalized_msg = _normalize_for_match(cleaned_message)
    payload = f"{line_code}|{normalized_msg}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:10]


def parse_ilevia_datetime(iso_str: str | None, fallback: datetime | None = None) -> datetime:
    """Convertit un horodatage ISO de l'API MEL en datetime local (Europe/Paris).

    Le GeoServer MEL ajoute un suffixe 'Z' à des heures déjà en heure locale de Lille.
    Les offsets explicites (+01:00, +02:00, etc.) sont en revanche convertis vers Europe/Paris.
    """
    if not iso_str:
        return fallback if fallback is not None else datetime.now(TIMEZONE)
    raw = iso_str.strip()
    if raw.endswith("Z"):
        dt_naive = datetime.fromisoformat(raw.rstrip("Z"))
        return dt_naive.replace(tzinfo=TIMEZONE)
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is not None:
        return dt.astimezone(TIMEZONE)
    return dt.replace(tzinfo=TIMEZONE)


def _is_ttl_diff(hours_diff: float) -> bool:
    """Retourne True si l'écart en heures correspond à 23h, 24h ou 25h (±5 min, gère le DST)."""
    nearest_hour = round(hours_diff)
    return nearest_hour in (23, 24, 25) and abs(hours_diff - nearest_hour) * 60 <= 5


def _is_default_24h_ttl(
    start_time: datetime,
    end_time: datetime,
    props: dict | None = None,
) -> bool:
    """Détecte si `heure_fin_prevue` correspond au TTL automatique de 24h appliqué par Ilévia."""
    hours_diff = (end_time - start_time).total_seconds() / 3600.0
    if _is_ttl_diff(hours_diff):
        return True
    if props:
        for key in ("date_debut", "date_creation"):
            raw_ref = props.get(key)
            if raw_ref:
                ref_dt = parse_ilevia_datetime(raw_ref, fallback=start_time)
                ref_diff = (end_time - ref_dt).total_seconds() / 3600.0
                if _is_ttl_diff(ref_diff):
                    return True
    return False


def _extract_text_end_time(
    message: str,
    start_time: datetime,
    now: datetime | None = None,
) -> datetime | None:
    """Extrait une heure de fin mentionnée dans le corps du message (ex: 'jusque 09h 40')."""
    m = re.search(r"\bjusqu(?:e|['’][aà])\s*(\d{1,2})\s*h\s*(\d{0,2})\b", message, flags=re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    candidate = start_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if start_time.hour >= 18 and candidate.hour <= 6:
        candidate += timedelta(days=1)
    if now is not None and candidate <= now:
        return None
    return candidate


def _create_http_session() -> requests.Session:
    """Crée une session HTTP avec politique de retry sur les erreurs transitoires."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_active_perturbations(now: datetime | None = None) -> list[Perturbation]:
    """Récupère et fusionne par ligne les perturbations actives depuis l'API Open Data MEL."""
    if now is None:
        now = datetime.now(TIMEZONE)

    # Le service Ilévia redémarre vers 04h00 : les alertes sans fin connue de la veille sont expirées,
    # sauf les incidents nocturnes démarrés il y a moins de 8 heures.
    service_day_cutoff = (now - timedelta(hours=4)).date()

    use_patched_get = requests.get is not _ORIGINAL_REQUESTS_GET
    session = None if use_patched_get else _create_http_session()

    all_features: list[dict] = []
    next_url: str | None = ILEVIA_PERTURBATIONS_URL
    pages_fetched = 0
    max_pages = 5

    while next_url and pages_fetched < max_pages:
        if use_patched_get:
            response = requests.get(
                next_url,
                headers={"User-Agent": "MattLerte/1.0"},
                timeout=15,
            )
        else:
            assert session is not None
            response = session.get(
                next_url,
                headers={"User-Agent": "MattLerte/1.0"},
                timeout=15,
            )
        response.raise_for_status()
        data = response.json()
        features = data.get("features", [])
        all_features.extend(features)
        pages_fetched += 1

        next_url = None
        if len(features) == 200:
            for link in data.get("links", []):
                if link.get("rel") == "next" and link.get("href"):
                    next_url = link["href"]
                    break

    deduped: dict[str, Perturbation] = {}

    for feature in all_features:
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
        raw_end = props.get("heure_fin_prevue")
        if not raw_end:
            has_known_end = False
            end_time = now + timedelta(hours=2)
        else:
            end_time = parse_ilevia_datetime(raw_end, fallback=now + timedelta(hours=2))
            has_known_end = not _is_default_24h_ttl(start_time, end_time, props=props)

        if not has_known_end:
            text_end = _extract_text_end_time(message, start_time, now=now)
            if text_end is not None:
                end_time = text_end
                has_known_end = True

        is_stale_no_end = (
            not has_known_end
            and start_time.date() < service_day_cutoff
            and (now - start_time).total_seconds() >= 8 * 3600
        )
        if end_time <= now or is_stale_no_end:
            continue

        line_name = WATCHED_LINES[line_code]
        fp = compute_fingerprint(line_name, line_name)
        reason, stations = parse_reason_and_stations(message)

        if fp in deduped:
            existing = deduped[fp]
            if severity == SEVERITY_RED:
                existing.severity = SEVERITY_RED
            existing.start_time = min(existing.start_time, start_time)
            existing.has_known_end_time = existing.has_known_end_time and has_known_end
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
