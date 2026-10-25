from datetime import datetime, timedelta

from mattlerte.config import TIMEZONE
from mattlerte.discord_client import FOOTER_REGEX, TrackedIncident, build_ongoing_embed, build_resolved_embed
from mattlerte.ilevia import (
    Perturbation,
    SEVERITY_ORANGE,
    SEVERITY_RED,
    _extract_text_end_time,
    _is_default_24h_ttl,
    classify_severity,
    clean_message,
    format_date_str,
    format_duration,
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
