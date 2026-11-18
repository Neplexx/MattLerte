"""Tests unitaires du parsing Ilévia, de la fusion par ligne et du formatage Discord."""

from datetime import datetime, timedelta

from mattlerte.config import TIMEZONE
from mattlerte.discord_client import (
    FOOTER_REGEX,
    TrackedIncident,
    build_ongoing_embed,
    build_resolved_embed,
)
from mattlerte.ilevia import (
    Perturbation,
    SEVERITY_ORANGE,
    SEVERITY_RED,
    _extract_text_end_time,
    _is_default_24h_ttl,
    classify_severity,
    clean_message,
    fetch_active_perturbations,
    format_date_str,
    format_duration,
    parse_ilevia_datetime,
    parse_reason_and_stations,
)


def test_clean_message_removes_html():
    raw = "<b>Actuellement</b>, station non desservie.<br/>Reportez-vous à <b>Gambetta</b>."
    assert clean_message(raw) == "Actuellement, station non desservie.\nReportez-vous à Gambetta."


def test_parse_reason_and_stations():
    msg_metro = (
        "En raison d'une demande des services de police, les stations de métro "
        "RÉPUBLIQUE BEAUX-ARTS et RIHOUR ne sont actuellement pas desservies."
        "Vous pouvez vous reporter aux stations GAMBETTA et GARE LILLE FLANDRES."
    )
    reason, stations = parse_reason_and_stations(msg_metro)
    assert reason == "Demande des services de police"
    assert stations == ["République Beaux-Arts", "Rihour"]

    msg_tram = (
        "En raison des dégâts causés par les conditions météorologiques du 23/10, "
        "le trafic Tramway est interrompu ce samedi 24/10, jusqu’à la fin de service, entre les stations :\n"
        "► Foch <> Tourcoing Centre\n"
        "► Acacias <> Roubaix Eurotéléport\n"
        "Le Tramway circule uniquement entre Gare Lille Flandres et Croisé Laroche."
    )
    reason_t, stations_t = parse_reason_and_stations(msg_tram)
    assert "Dégâts causés par les conditions météorologiques" in reason_t
    assert stations_t == ["Foch - Tourcoing Centre", "Acacias - Roubaix Eurotéléport"]


def test_classify_severity_red_orange_and_ignored():
    assert classify_severity(
        "En raison d'une demande des services de police, les stations RIHOUR ne sont actuellement pas desservies."
    ) == SEVERITY_RED
    assert classify_severity("Le trafic Tramway est interrompu entre Foch et Tourcoing.") == SEVERITY_RED
    assert classify_severity("En raison d'un incident technique, le trafic est ralenti sur la ligne.") == SEVERITY_ORANGE

    assert classify_severity("Du 2 au 30 novembre, les valideurs seront indisponibles.") is None
    assert classify_severity("ilévia renforce son offre Tramway à l'occasion du Marathon.") is None
    assert classify_severity("✅ Liane - 24/10 – Evolution : Réalisation du départ") is None


def test_is_default_24h_ttl():
    start = datetime(2026, 10, 24, 13, 50, 35, tzinfo=TIMEZONE)
    end_ttl = datetime(2026, 10, 25, 13, 50, 35, tzinfo=TIMEZONE)
    assert _is_default_24h_ttl(start, end_ttl) is True

    end_known = datetime(2026, 10, 24, 16, 15, 0, tzinfo=TIMEZONE)
    assert _is_default_24h_ttl(start, end_known) is False


def test_format_duration():
    assert format_duration(timedelta(minutes=45)) == "45 min"
    assert format_duration(timedelta(hours=1, minutes=15)) == "1h15"
    assert format_duration(timedelta(hours=2)) == "2h"


def test_format_date_str():
    now = datetime(2026, 10, 25, 19, 0, 0, tzinfo=TIMEZONE)
    assert format_date_str(datetime(2026, 10, 25, 9, 0, 0, tzinfo=TIMEZONE), now=now) == "25/10 (Aujourd'hui)"
    assert format_date_str(datetime(2026, 10, 24, 21, 0, 0, tzinfo=TIMEZONE), now=now) == "24/10 (hier)"
    assert format_date_str(datetime(2026, 10, 23, 18, 0, 0, tzinfo=TIMEZONE), now=now) == "23/10"


def test_extract_text_end_time():
    start = datetime(2026, 10, 25, 9, 9, 53, tzinfo=TIMEZONE)
    msg = "La ligne 1 du métro est interrompue entre CHU EURASANTE et REPUBLIQUE BEAUX-ARTS jusque 09h 40 environ."
    assert _extract_text_end_time(msg, start) == datetime(2026, 10, 25, 9, 40, 0, tzinfo=TIMEZONE)


def test_discord_embeds_and_footer_parsing():
    now = datetime(2026, 10, 24, 15, 0, 0, tzinfo=TIMEZONE)
    start = datetime(2026, 10, 24, 13, 45, 0, tzinfo=TIMEZONE)
    end = datetime(2026, 10, 24, 16, 0, 0, tzinfo=TIMEZONE)

    p = Perturbation(
        fingerprint="abc1234567",
        line_code="ILEVIA:LineRef::ME1:LOC",
        line_name="Métro Ligne 1",
        severity=SEVERITY_RED,
        message="Trafic interrompu entre Quatre Cantons et Gare Lille Flandres.",
        reason="Incident technique",
        stations=["Quatre Cantons - Gare Lille Flandres"],
        start_time=start,
        end_time=end,
        has_known_end_time=True,
    )

    ongoing_embed = build_ongoing_embed(p, now=now)
    assert "**Début :** 13h45 (depuis 1h15)" in ongoing_embed["description"]
    assert "**Fin estimée :** 16h00 (reste ~1h)" in ongoing_embed["description"]
    assert "**Date :** 24/10 (Aujourd'hui)" in ongoing_embed["description"]
    assert "**Raison :** Incident technique" in ongoing_embed["description"]
    assert "**Stations non desservies :**\n• **Quatre Cantons - Gare Lille Flandres**" in ongoing_embed["description"]

    match = FOOTER_REGEX.match(ongoing_embed["footer"]["text"])
    assert match is not None
    assert match.group("fp") == "abc1234567"
    assert match.group("resolved") is None

    tracked = TrackedIncident(
        fingerprint="abc1234567",
        message_id="999",
        line_name="Métro Ligne 1",
        stations=["République Beaux-Arts", "Rihour"],
        start_time=start,
    )
    resolved_embed = build_resolved_embed(tracked, now=now)
    assert "Fin de l'interruption" in resolved_embed["title"]
    assert "**Date :** 24/10 (Aujourd'hui)" in resolved_embed["description"]
    assert "1h15" in resolved_embed["description"]
    match_res = FOOTER_REGEX.match(resolved_embed["footer"]["text"])
    assert match_res is not None
    assert match_res.group("resolved") == "résolu"


def test_fetch_active_perturbations_merges_per_line(monkeypatch):
    now = datetime(2026, 10, 25, 19, 0, 0, tzinfo=TIMEZONE)

    fake_payload = {
        "features": [
            {
                "properties": {
                    "cible": "ILEVIA:LineRef::71:LOC",
                    "date_modification": "2026-10-24T22:15:00Z",
                    "heure_fin_prevue": "2026-10-25T23:00:00Z",
                    "message": "En raison des dégâts météo, le trafic Tramway est interrompu entre ► Foch <> Tourcoing Centre",
                }
            },
            {
                "properties": {
                    "cible": "ILEVIA:LineRef::TRAMR:LOC",
                    "date_modification": "2026-10-25T10:30:00Z",
                    "heure_fin_prevue": "2026-10-26T01:00:00Z",
                    "message": "En raison de l'intervention sur la caténaire, le trafic Tramway est interrompu entre Brossolette <> Roubaix Eurotéléport",
                }
            },
        ]
    }

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_payload

    monkeypatch.setattr("mattlerte.ilevia.requests.get", lambda *a, **kw: DummyResponse())

    results = fetch_active_perturbations(now=now)
    assert len(results) == 1
    tram = results[0]
    assert tram.line_name == "Tramway (R / T)"
    assert tram.format_date_str(now=now) == "24/10 (hier)"
    assert tram.reason == "Dégâts météo / Intervention sur la caténaire"
    assert tram.stations == ["Foch - Tourcoing Centre", "Brossolette - Roubaix Eurotéléport"]


def test_classify_severity_negations_boundaries_and_active_exceptions():
    # Négations explicites
    assert classify_severity("Aucune perturbation en cours sur la ligne 1.") is None
    assert classify_severity("Il n'y a pas d'interruption de trafic.") is None

    # Bornes \b : "deviendra" ne doit pas matcher "devie", "dépannage" ne doit pas matcher "panne"
    assert classify_severity("Cette station deviendra accessible demain.") is None
    assert classify_severity("Opération de dépannage prévue la semaine prochaine.") is None

    # Nouveaux mots-clés RED
    assert classify_severity("Le trafic est suspendu sur la ligne 2.") == SEVERITY_RED
    assert classify_severity("La station GAMBETTA est non desservie.") == SEVERITY_RED

    # Fin de perturbation avec clause restrictive active
    assert (
        classify_severity(
            "Le trafic est rétabli sur la ligne 1, mais la station RIHOUR reste non desservie."
        )
        == SEVERITY_RED
    )
    assert (
        classify_severity(
            "Fin de perturbation sur le métro, toutefois le trafic reste ralenti."
        )
        == SEVERITY_ORANGE
    )
    assert classify_severity("Le trafic est rétabli sur l'ensemble de la ligne 1.") is None


def test_extract_text_end_time_expired_and_midnight():
    # Heure texte déjà dépassée par rapport à `now` -> None
    start = datetime(2026, 10, 25, 9, 0, tzinfo=TIMEZONE)
    now_after = datetime(2026, 10, 25, 10, 0, tzinfo=TIMEZONE)
    msg = "Le trafic est interrompu jusque 09h40 environ."
    assert _extract_text_end_time(msg, start, now=now_after) is None

    # Passage minuit : début le soir (>= 18h), fin tôt le matin (<= 6h)
    evening_start = datetime(2026, 10, 25, 22, 30, tzinfo=TIMEZONE)
    now_evening = datetime(2026, 10, 25, 23, 0, tzinfo=TIMEZONE)
    msg_midnight = "Interruption du trafic jusqu'à 01h30."
    assert _extract_text_end_time(msg_midnight, evening_start, now=now_evening) == datetime(
        2026, 10, 26, 1, 30, tzinfo=TIMEZONE
    )


def test_parse_reason_and_stations_ignores_non_station_bullet():
    msg = (
        "En raison d'un incident technique, la station RIHOUR n'est pas desservie.\n"
        "► Bus relais mis en place entre Gambetta et Gare Lille Flandres."
    )
    reason, stations = parse_reason_and_stations(msg)
    assert reason == "Incident technique"
    assert stations == ["Rihour"]


def test_fetch_active_perturbations_none_end_time_and_nocturnal_cutoff(monkeypatch):
    # À 05h00, un incident nocturne sans fin connue démarré à 02h00 (< 8h) doit être conservé
    now = datetime(2026, 10, 25, 5, 0, tzinfo=TIMEZONE)
    fake_payload = {
        "features": [
            {
                "properties": {
                    "cible": "ILEVIA:LineRef::ME1:LOC",
                    "date_modification": "2026-10-25T01:00:00Z",  # 02:00 CET (après changement d'heure)
                    "heure_fin_prevue": None,
                    "message": "En raison d'un incident technique, la station RIHOUR n'est pas desservie.",
                }
            }
        ]
    }

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_payload

    monkeypatch.setattr("mattlerte.ilevia.requests.get", lambda *a, **kw: DummyResponse())
    results = fetch_active_perturbations(now=now)
    assert len(results) == 1
    assert results[0].has_known_end_time is False
    assert results[0].end_time == now + timedelta(hours=2)


def test_fetch_active_perturbations_merges_unknown_end_time(monkeypatch):
    now = datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE)
    fake_payload = {
        "features": [
            {
                "properties": {
                    "cible": "ILEVIA:LineRef::ME1:LOC",
                    "date_modification": "2026-10-25T13:00:00Z",
                    "heure_fin_prevue": None,
                    "message": "En raison d'un incident technique, la station RIHOUR n'est pas desservie.",
                }
            },
            {
                "properties": {
                    "cible": "ILEVIA:LineRef::ME1:LOC",
                    "date_modification": "2026-10-25T13:30:00Z",
                    "heure_fin_prevue": "2026-10-25T16:00:00Z",
                    "message": "En raison d'un malaise voyageur, la station GAMBETTA n'est pas desservie.",
                }
            },
        ]
    }

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_payload

    monkeypatch.setattr("mattlerte.ilevia.requests.get", lambda *a, **kw: DummyResponse())
    results = fetch_active_perturbations(now=now)
    assert len(results) == 1
    assert results[0].has_known_end_time is False


def test_fetch_active_perturbations_ogc_pagination(monkeypatch):
    now = datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE)

    filler_feature = {
        "properties": {
            "cible": "ILEVIA:LineRef::BUS99:LOC",
            "date_modification": "2026-10-25T13:00:00Z",
            "heure_fin_prevue": "2026-10-25T16:00:00Z",
            "message": "Bus dévié.",
        }
    }
    page1 = {
        "features": [filler_feature] * 200,
        "links": [{"rel": "next", "href": "https://example.org/items?offset=200"}],
    }
    page2 = {
        "features": [
            {
                "properties": {
                    "cible": "ILEVIA:LineRef::ME2:LOC",
                    "date_modification": "2026-10-25T13:30:00Z",
                    "heure_fin_prevue": "2026-10-25T16:30:00Z",
                    "message": "En raison d'un colis suspect, la station LOMME n'est pas desservie.",
                }
            }
        ],
        "links": [],
    }

    called_urls: list[str] = []

    class DummyResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        called_urls.append(url)
        if "offset=200" in url:
            return DummyResponse(page2)
        return DummyResponse(page1)

    monkeypatch.setattr("mattlerte.ilevia.requests.get", fake_get)
    results = fetch_active_perturbations(now=now)
    assert len(called_urls) == 2
    assert called_urls[1] == "https://example.org/items?offset=200"
    assert len(results) == 1
    assert results[0].line_name == "Métro Ligne 2"


def test_parse_ilevia_datetime_fake_z_and_explicit_offset():
    # Faux suffixe Z traité comme heure locale Europe/Paris
    dt_z = parse_ilevia_datetime("2026-10-24T22:15:00Z")
    assert dt_z == datetime(2026, 10, 24, 22, 15, tzinfo=TIMEZONE)

    # Offset explicite (+02:00 / +00:00) converti vers Europe/Paris
    dt_offset = parse_ilevia_datetime("2026-10-24T20:15:00+00:00")
    assert dt_offset == datetime(2026, 10, 24, 22, 15, tzinfo=TIMEZONE)

    dt_paris_offset = parse_ilevia_datetime("2026-10-24T22:15:00+02:00")
    assert dt_paris_offset == datetime(2026, 10, 24, 22, 15, tzinfo=TIMEZONE)


