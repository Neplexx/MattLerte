import argparse
from datetime import datetime
import sys

from mattlerte.config import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, TIMEZONE
from mattlerte.discord_client import (
    resolve_incident,
    send_ongoing_notification,
    sync_channel_state,
    update_ongoing_message,
)
from mattlerte.ilevia import fetch_active_perturbations


def run(dry_run: bool = False) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    now = datetime.now(TIMEZONE)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Vérification des perturbations Ilévia (Métro & Tram)...")

    active_perturbations = fetch_active_perturbations(now=now)
    active_by_fp = {p.fingerprint: p for p in active_perturbations}

    print(f"-> {len(active_perturbations)} perturbation(s) active(s) détectée(s) sur l'API MEL.")

    if dry_run or not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        if not dry_run:
            print("⚠️ DISCORD_BOT_TOKEN ou DISCORD_CHANNEL_ID manquant : passage en mode aperçu (--dry-run).")
        for p in active_perturbations:
            stations_str = "\n".join(f"   • {s}" for s in p.stations)
            print(
                f"\n{p.severity_emoji} {p.line_name} - {p.severity_label}\n"
                f"   Début                 : {p.format_start_str(now=now)}\n"
                f"   Fin estimée           : {p.format_end_str(now=now)}\n"
                f"   Date                  : {p.format_date_str(now=now)}\n"
                f"   Raison                : {p.reason}\n"
                f"   Stations non desservies :\n{stations_str}"
            )
        return

    open_incidents = sync_channel_state(now=now)
    print(f"-> {len(open_incidents)} incident(s) actuellement affiché(s) dans le salon Discord.")

    # 1. Nouvelles perturbations ou mises à jour des perturbations toujours en cours
    for fp, p in active_by_fp.items():
        if fp not in open_incidents:
            print(f"[NOUVEAU] Envoi de l'encadré pour {p.line_name} (réf: {fp})...")
            send_ongoing_notification(p, now=now)
        else:
            tracked = open_incidents[fp]
            if tracked.start_time < p.start_time:
                p.start_time = tracked.start_time
            print(f"[EN COURS] Mise à jour de la durée pour {p.line_name} (réf: {fp})...")
            update_ongoing_message(tracked.message_id, p, now=now)

    # 2. Perturbations terminées : suppression de l'encadré rouge + envoi de l'encadré vert (auto-détruit après 1h)
    for fp, tracked in open_incidents.items():
        if fp not in active_by_fp:
            print(f"[RÉSOLU] Suppression de l'alerte et notification de fin pour {tracked.line_name} (réf: {fp})...")
            resolve_incident(tracked, now=now)

    print("Terminé avec succès.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MattLerte — Alertes Métro & Tram Ilévia sur Discord")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche les alertes dans le terminal sans envoyer de message sur Discord",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
