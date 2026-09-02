#!/usr/bin/env python3
"""
Dashboard web unifié : suivi et administration des DEUX bots CROUS.

- Section « Nancy » : ce projet-ci (crous_watcher.py).
- Section « National » : le projet projetCrous, qui surveille toute la France.

Les deux collecteurs restent des programmes indépendants ; ce dashboard ne fait
que lire leurs instantanés respectifs et écrire dans leurs magasins d'abonnés.
Il n'appelle jamais l'API du CROUS lui-même.

Usage :
    python dashboard.py          (Waitress, identique en local et en prod)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from functools import wraps

from dotenv import dotenv_values, load_dotenv
from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash

import subscriber_store
import telegram_client

load_dotenv()

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, "status.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "").strip()
DASHBOARD_PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "").strip()
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip()
COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "true").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crous")


# --------------------------------------------------------------------------- #
# Le second projet (projetCrous, surveillance nationale)
# --------------------------------------------------------------------------- #
# On réutilise SES modules plutôt que de réécrire sa logique : son magasin de
# contacts est protégé par un verrou fichier que son collecteur respecte aussi.
# Réimplémenter ce protocole ici, c'est prendre le risque qu'il diverge et que
# deux écritures concurrentes se perdent.
def _resoudre_projet_national():
    """Dossier de projetCrous : à côté de ce projet en production, dedans en
    développement local. Surchargeable par PROJETCROUS_DIR."""
    explicite = os.getenv("PROJETCROUS_DIR", "").strip()
    candidats = [explicite] if explicite else [
        os.path.join(BASE_DIR, "projetCrous"),
        os.path.join(os.path.dirname(BASE_DIR), "projetCrous"),
    ]
    for chemin in candidats:
        if chemin and os.path.isdir(os.path.join(chemin, "modules")):
            return chemin
    return None


NAT_DIR = _resoudre_projet_national()
nat_contacts = nat_zones = nat_telegram = nat_email = None

if NAT_DIR:
    if NAT_DIR not in sys.path:
        sys.path.insert(0, NAT_DIR)
    try:
        # Ces 4 modules sont autonomes : aucun n'importe la config de projetCrous,
        # donc les importer ne charge pas son .env dans notre environnement.
        from modules import contacts as nat_contacts
        from modules import email_sender as nat_email
        from modules import telegram_sender as nat_telegram
        from modules import zones as nat_zones
    except Exception as e:  # projet absent ou incomplet : la section disparaît
        log.warning("Projet national inutilisable (%s) : %s", NAT_DIR, e)
        nat_contacts = None

# Ses secrets sont lus SANS les injecter dans os.environ : plusieurs noms de
# variables sont communs aux deux projets (DASHBOARD_PORT, CHECK_INTERVAL…) et
# se marcheraient dessus.
NAT_ENV = dotenv_values(os.path.join(NAT_DIR, ".env")) if NAT_DIR else {}
NAT_STATUS_FILE = os.path.join(NAT_DIR, "data", "status.json") if NAT_DIR else None
NAT_CONTACTS_FILE = os.path.join(NAT_DIR, "data", "contacts.json") if NAT_DIR else None

app = Flask(__name__)
app.secret_key = DASHBOARD_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def read_status():
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def watcher_health(status):
    """(en_ligne, secondes_depuis_maj) — déduit de la fraîcheur de status.json,
    sans avoir besoin d'un accès systemctl."""
    if not status or not status.get("updated_at"):
        return False, None
    try:
        updated = datetime.fromisoformat(status["updated_at"])
    except ValueError:
        return False, None
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    threshold = max(3 * status.get("check_interval", 60), 180)
    return age <= threshold, age


# --------------------------------------------------------------------------- #
# Projet national : lecture de l'état, décisions sur les demandes
# --------------------------------------------------------------------------- #
def lire_national():
    """État du projet national, ou None s'il n'est pas installé.

    Son collecteur écrit un horodatage local (pas UTC) dans son status.json :
    on compare donc avec l'heure locale, sans quoi le décalage ferait croire à
    une panne permanente.
    """
    if not (NAT_DIR and nat_contacts):
        return None

    statut = {}
    try:
        with open(NAT_STATUS_FILE, "r", encoding="utf-8") as f:
            statut = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    en_ligne, age = False, None
    if statut.get("updated"):
        try:
            maj = datetime.fromisoformat(statut["updated"])
            age = (datetime.now() - maj).total_seconds()
            cadence = statut.get("expected_cycle_seconds", 60)
            en_ligne = age <= max(3 * cadence, 180)
        except ValueError:
            pass

    bruts = nat_contacts.read_only(NAT_CONTACTS_FILE) if NAT_CONTACTS_FILE else {}
    par_statut = {"pending": [], "approved": [], "rejected": []}
    for cle, contact in bruts.items():
        fiche = dict(contact)
        fiche["cle"] = cle
        fiche["zones_libelles"] = [nat_zones.label(z) for z in contact.get("zones", [])]
        if fiche.get("status") in par_statut:
            par_statut[fiche["status"]].append(fiche)
    for liste in par_statut.values():
        liste.sort(key=lambda c: c.get("requested_at") or "", reverse=True)

    # « subscribers » vient du collecteur et compte AUSSI les destinataires fixes
    # définis dans le .env de projetCrous, que ce dashboard ne peut pas modifier.
    actifs_total = statut.get("subscribers", 0)
    return {
        "statut": statut,
        "en_ligne": en_ligne,
        "age": int(age) if age is not None else None,
        "pending": par_statut["pending"],
        "approved": par_statut["approved"],
        "rejected": par_statut["rejected"],
        "actifs_total": actifs_total,
        "fixes": max(0, actifs_total - len(par_statut["approved"])),
        "zones": sorted(
            statut.get("listings_par_zone", {}).items(),
            key=lambda kv: kv[1], reverse=True,
        ),
    }


def _notifier_national(contact, sujet, texte):
    """Prévient la personne sur son canal. Appelé HORS du verrou."""
    canal = contact.get("channel")
    adresse = contact.get("address", "")
    try:
        if canal == "telegram":
            token = (NAT_ENV.get("TELEGRAM_TOKEN") or "").strip()
            if token and nat_telegram:
                nat_telegram.send_message(texte, token, adresse)
        elif canal == "email" and nat_email:
            nat_email.send_email(sujet, texte, {
                "SMTP_USER": NAT_ENV.get("SMTP_USER", ""),
                "SMTP_PASS": NAT_ENV.get("SMTP_PASS", ""),
                "SMTP_HOST": NAT_ENV.get("SMTP_HOST", "smtp.gmail.com"),
                "SMTP_PORT": int(NAT_ENV.get("SMTP_PORT", "587")),
                "ALERT_TO": adresse,
            })
    except Exception as e:
        log.error("Notification (national) impossible vers %s : %s", adresse, e)


def decider_national(cle, nouveau_statut):
    """Change le statut d'un contact du projet national, puis le prévient."""
    if not (nat_contacts and NAT_CONTACTS_FILE):
        return False
    try:
        with nat_contacts.transaction(NAT_CONTACTS_FILE) as store:
            contact = nat_contacts.set_status(store, cle, nouveau_statut)
            copie = dict(contact) if contact else None
    except Exception as e:
        log.error("Magasin national indisponible : %s", e)
        return False

    if copie is None:
        return False

    if nouveau_statut == nat_contacts.APPROVED:
        _notifier_national(
            copie, "Inscription validée — alertes CROUS",
            "✅ Ton inscription aux alertes logements CROUS est validée.\n"
            f"Zones suivies : {len(copie.get('zones', []))}.")
    else:
        _notifier_national(
            copie, "Inscription aux alertes CROUS",
            "❌ Ton inscription aux alertes logements CROUS a été refusée "
            "ou révoquée. Tu ne recevras plus de notifications.")

    log.info("National : %s -> %s", cle, nouveau_statut)
    return True


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (username == DASHBOARD_USERNAME
                and DASHBOARD_PASSWORD_HASH
                and check_password_hash(DASHBOARD_PASSWORD_HASH, password)):
            session["logged_in"] = True
            return redirect(url_for("index"))
        flash("Identifiants incorrects.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    status = read_status()
    online, age = watcher_health(status)

    with subscriber_store.locked_store() as store:
        subs = dict(store["subscribers"])

    def by_status(wanted):
        return sorted(
            ({"chat_id": cid, **info} for cid, info in subs.items()
             if info.get("status") == wanted),
            key=lambda s: s.get("requested_at") or "",
            reverse=True,
        )

    national = lire_national()
    # Vue affichée, choisie dans le menu de gauche. Le paramètre est dans l'URL
    # (et non en session) pour que le rafraîchissement automatique de la page
    # reste sur la vue en cours.
    vue = request.args.get("vue", "nancy")
    if vue not in ("nancy", "national"):
        vue = "nancy"
    if vue == "national" and not national:
        vue = "nancy"

    return render_template(
        "dashboard.html",
        status=status,
        online=online,
        age_seconds=int(age) if age is not None else None,
        pending=by_status(subscriber_store.STATUS_PENDING),
        approved=by_status(subscriber_store.STATUS_APPROVED),
        rejected=by_status(subscriber_store.STATUS_REJECTED),
        national=national,
        vue=vue,
    )


@app.route("/national/decision", methods=["POST"])
@login_required
def national_decision():
    """Validation/refus d'une demande du projet national.

    La clé passe par le formulaire et non par l'URL : elle peut être une adresse
    email ou « telegram:123 », donc contenir @ et :.
    """
    cle = request.form.get("cle", "").strip()
    action = request.form.get("action", "")
    retour = redirect(url_for("index", vue="national"))
    if not (cle and nat_contacts):
        flash("Action impossible.")
        return retour

    statut = (nat_contacts.APPROVED if action == "approve"
              else nat_contacts.REJECTED)
    ok = decider_national(cle, statut)
    flash(("Demande acceptée." if statut == nat_contacts.APPROVED else "Demande refusée.")
          if ok else "Action impossible sur ce destinataire.")
    return retour


def _decide(chat_id, new_status, message):
    """Change le statut d'un abonné puis le prévient (hors du verrou)."""
    with subscriber_store.locked_store() as store:
        info = store["subscribers"].get(chat_id)
        if not info:
            return False
        if info.get("source") == subscriber_store.SOURCE_ENV:
            return False  # destinataires fixes : modifiables uniquement via .env
        info["status"] = new_status
        info["decided_at"] = subscriber_store.now_iso()

    telegram_client.send_message(TELEGRAM_BOT_TOKEN, chat_id, message)
    log.info("Abonné %s -> %s", chat_id, new_status)
    return True


@app.route("/subscribers/<chat_id>/approve", methods=["POST"])
@login_required
def approve(chat_id):
    ok = _decide(chat_id, subscriber_store.STATUS_APPROVED,
                 "✅ Ta demande a été acceptée ! Tu recevras désormais une alerte "
                 "ici dès qu'un logement correspondant apparaît.")
    flash("Demande acceptée." if ok else "Action impossible sur ce destinataire.")
    return redirect(url_for("index"))


@app.route("/subscribers/<chat_id>/reject", methods=["POST"])
@login_required
def reject(chat_id):
    ok = _decide(chat_id, subscriber_store.STATUS_REJECTED,
                 "❌ Ta demande d'accès a été refusée par l'administrateur.")
    flash("Demande refusée." if ok else "Action impossible sur ce destinataire.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    missing = [n for n, v in [
        ("DASHBOARD_USERNAME", DASHBOARD_USERNAME),
        ("DASHBOARD_PASSWORD_HASH", DASHBOARD_PASSWORD_HASH),
        ("DASHBOARD_SECRET_KEY", DASHBOARD_SECRET_KEY),
    ] if not v]
    if missing:
        log.error("Variables manquantes dans .env : %s", ", ".join(missing))
        sys.exit(1)

    from waitress import serve
    log.info("Dashboard sur http://%s:%s", DASHBOARD_HOST, DASHBOARD_PORT)
    serve(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, threads=4)
