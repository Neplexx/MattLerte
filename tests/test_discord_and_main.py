"""Tests unitaires pour le client Discord et l'orchestration principale."""

from datetime import datetime
import pytest
import requests

from mattlerte.config import TIMEZONE
from mattlerte import discord_client, main as main_module
from mattlerte.discord_client import (
    COLOR_ORANGE,
    COLOR_RED,
    TrackedIncident,
    build_ongoing_embed,
    build_resolved_embed,
    resolve_incident,
    sync_channel_state,
)
from mattlerte.ilevia import Perturbation, SEVERITY_ORANGE, SEVERITY_RED, compute_fingerprint


def _make_perturbation(
    *,
    line_code: str = "ILEVIA:LineRef::ME1:LOC",
    line_name: str = "Métro Ligne 1",
    severity: str = SEVERITY_RED,
    stations: list[str] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> Perturbation:
    now = datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE)
    return Perturbation(
        fingerprint=compute_fingerprint(line_name, line_name),
        line_code=line_code,
        line_name=line_name,
        severity=severity,
        message="Trafic perturbé.",
        reason="Incident technique",
        stations=stations if stations is not None else ["Rihour"],
        start_time=start_time or now,
        end_time=end_time,
        has_known_end_time=end_time is not None,
    )


def test_build_ongoing_embed_truncates_over_4096(monkeypatch):
    monkeypatch.setattr(discord_client, "DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(discord_client, "DISCORD_CHANNEL_ID", "chan")

    now = datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE)
    many_stations = [f"Station Numéro {i:03d} Avec Un Nom Très Long" for i in range(150)]
    p = _make_perturbation(stations=many_stations, start_time=now)

    embed = build_ongoing_embed(p, now=now)
    assert len(embed["description"]) == 4096
    assert embed["description"].endswith("...")


def test_build_resolved_embed_severity_labels():
    now = datetime(2026, 10, 25, 16, 0, tzinfo=TIMEZONE)
    start = datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE)

    tracked_orange = TrackedIncident(
        fingerprint="fp1",
        message_id="m1",
        line_name="Métro Ligne 1",
        stations=["Rihour"],
        start_time=start,
        severity=SEVERITY_ORANGE,
    )
    embed_orange = build_resolved_embed(tracked_orange, now=now)
    assert "Fin de la perturbation" in embed_orange["title"]

    tracked_red = TrackedIncident(
        fingerprint="fp2",
        message_id="m2",
        line_name="Métro Ligne 1",
        stations=["Rihour"],
        start_time=start,
        severity=SEVERITY_RED,
    )
    embed_red = build_resolved_embed(tracked_red, now=now)
    assert "Fin de l'interruption" in embed_red["title"]


def test_sync_channel_state_filters_non_bot_detects_severity_and_leap_day(monkeypatch):
    monkeypatch.setattr(discord_client, "DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(discord_client, "DISCORD_CHANNEL_ID", "chan")

    # Année non bissextile (2025) lisant un message du 29/02 (2024)
    now = datetime(2025, 3, 1, 10, 0, tzinfo=TIMEZONE)
    fp_m1 = compute_fingerprint("Métro Ligne 1", "Métro Ligne 1")
    fp_m2 = compute_fingerprint("Métro Ligne 2", "Métro Ligne 2")

    messages = [
        {
            "id": "human-msg",
            "author": {"bot": False},
            "timestamp": "2025-03-01T09:00:00+01:00",
            "embeds": [
                {
                    "title": "🔴 Métro Ligne 1 - Interruption",
                    "description": "**Début :** 08h00\n**Date :** 01/03",
                    "color": COLOR_RED,
                    "footer": {"text": f"réf: {fp_m1}"},
                }
            ],
        },
        {
            "id": "bot-orange",
            "author": {"bot": True},
            "timestamp": "2024-02-29T23:30:00+01:00",
            "embeds": [
                {
                    "title": "🟠 Métro Ligne 1 - Perturbation",
                    "description": "**Début :** 23h15\n**Date :** 29/02\n• **Rihour**",
                    "color": COLOR_ORANGE,
                    "footer": {"text": f"réf: {fp_m1}"},
                }
            ],
        },
        {
            "id": "bot-red",
            "author": {"bot": True},
            "timestamp": "2025-03-01T09:10:00+01:00",
            "embeds": [
                {
                    "title": "🔴 Métro Ligne 2 - Interruption",
                    "description": "**Début :** 09h05\n**Date :** 01/03\n• **Lomme**",
                    "color": COLOR_RED,
                    "footer": {"text": f"réf: {fp_m2}"},
                }
            ],
        },
    ]

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return messages

    monkeypatch.setattr(discord_client.requests, "request", lambda *a, **kw: FakeResp())

    open_incidents = sync_channel_state(now=now)
    assert set(open_incidents.keys()) == {fp_m1, fp_m2}

    inc1 = open_incidents[fp_m1]
    assert inc1.message_id == "bot-orange"
    assert inc1.severity == SEVERITY_ORANGE
    assert inc1.start_time.year == 2024
    assert inc1.start_time.month == 2
    assert inc1.start_time.day == 29
    assert inc1.start_time.hour == 23
    assert inc1.start_time.minute == 15

    inc2 = open_incidents[fp_m2]
    assert inc2.message_id == "bot-red"
    assert inc2.severity == SEVERITY_RED


def test_resolve_incident_posts_before_deleting(monkeypatch):
    monkeypatch.setattr(discord_client, "DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(discord_client, "DISCORD_CHANNEL_ID", "chan")

    calls: list[tuple[str, str, dict | None]] = []

    class FakeResp:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        if method == "POST":
            return FakeResp(200, {"id": "resolved-123"})
        return FakeResp(204, {})

    monkeypatch.setattr(discord_client.requests, "request", fake_request)

    tracked = TrackedIncident(
        fingerprint="fp1",
        message_id="old-msg-1",
        line_name="Métro Ligne 1",
        stations=["Rihour"],
        start_time=datetime(2026, 10, 25, 14, 0, tzinfo=TIMEZONE),
        severity=SEVERITY_ORANGE,
    )
    resolved_id = resolve_incident(tracked, now=datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE))
    assert resolved_id == "resolved-123"
    assert [c[0] for c in calls] == ["POST", "DELETE"]
    assert calls[1][1].endswith("/messages/old-msg-1")
    assert "Fin de la perturbation" in calls[0][2]["embeds"][0]["title"]


def test_resolve_incident_does_not_delete_if_post_fails(monkeypatch):
    monkeypatch.setattr(discord_client, "DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(discord_client, "DISCORD_CHANNEL_ID", "chan")

    calls: list[str] = []

    class FailPostResp:
        status_code = 400
        headers = {}

        def raise_for_status(self):
            raise requests.HTTPError("400 Bad Request")

        def json(self):
            return {}

    def fake_request(method, url, **kwargs):
        calls.append(method)
        return FailPostResp()

    monkeypatch.setattr(discord_client.requests, "request", fake_request)

    tracked = TrackedIncident(
        fingerprint="fp1",
        message_id="old-msg-1",
        line_name="Métro Ligne 1",
        stations=["Rihour"],
        start_time=datetime(2026, 10, 25, 14, 0, tzinfo=TIMEZONE),
        severity=SEVERITY_RED,
    )
    with pytest.raises(requests.HTTPError):
        resolve_incident(tracked, now=datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE))
    assert calls == ["POST"]


def test_discord_request_retries_on_429(monkeypatch):
    monkeypatch.setattr(discord_client, "DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(discord_client, "DISCORD_CHANNEL_ID", "chan")

    slept: list[float] = []
    monkeypatch.setattr(discord_client.time, "sleep", lambda s: slept.append(s))

    attempts = {"count": 0}

    class RateLimitedResp:
        status_code = 429
        headers = {"Retry-After": "1.5"}

        def json(self):
            return {"retry_after": 0.25}

    class OkResp:
        status_code = 200
        headers = {}

        def json(self):
            return {"ok": True}

    def fake_request(method, url, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return RateLimitedResp()
        return OkResp()

    monkeypatch.setattr(discord_client.requests, "request", fake_request)

    resp = discord_client._discord_request("GET", "https://discord.com/api/v10/test")
    assert resp.status_code == 200
    assert attempts["count"] == 2
    assert slept == [0.25]


def test_main_run_exits_when_secrets_missing_without_dry_run(monkeypatch):
    monkeypatch.setattr(main_module, "DISCORD_BOT_TOKEN", "")
    monkeypatch.setattr(main_module, "DISCORD_CHANNEL_ID", "")

    with pytest.raises(SystemExit) as exc_info:
        main_module.run(dry_run=False)
    assert exc_info.value.code == 1


def test_main_run_escalates_orange_to_red_with_new_notification_and_delete(monkeypatch):
    monkeypatch.setattr(main_module, "DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(main_module, "DISCORD_CHANNEL_ID", "chan")

    now = datetime(2026, 10, 25, 15, 0, tzinfo=TIMEZONE)
    pert_red = _make_perturbation(severity=SEVERITY_RED, start_time=now)
    tracked_orange = TrackedIncident(
        fingerprint=pert_red.fingerprint,
        message_id="old-orange-msg",
        line_name=pert_red.line_name,
        stations=["Rihour"],
        start_time=now,
        severity=SEVERITY_ORANGE,
    )

    monkeypatch.setattr(main_module, "fetch_active_perturbations", lambda now=None: [pert_red])
    monkeypatch.setattr(main_module, "sync_channel_state", lambda now=None: {pert_red.fingerprint: tracked_orange})

    actions: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_module,
        "send_ongoing_notification",
        lambda p, now=None: actions.append(("send", p.severity)) or "new-red-msg",
    )
    monkeypatch.setattr(
        main_module,
        "delete_message",
        lambda msg_id: actions.append(("delete", msg_id)),
    )
    monkeypatch.setattr(
        main_module,
        "update_ongoing_message",
        lambda msg_id, p, now=None: actions.append(("update", msg_id)),
    )

    main_module.run(dry_run=False)
    assert actions == [("send", SEVERITY_RED), ("delete", "old-orange-msg")]
