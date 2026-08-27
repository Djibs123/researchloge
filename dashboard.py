#!/usr/bin/env python3
"""
Dashboard web du CROUS Watcher : suivi de l'état du système + validation
manuelle des demandes d'accès Telegram.

Usage :
    python dashboard.py          (Waitress, identique en local et en prod)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
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

    return render_template(
        "dashboard.html",
        status=status,
        online=online,
        age_seconds=int(age) if age is not None else None,
        pending=by_status(subscriber_store.STATUS_PENDING),
        approved=by_status(subscriber_store.STATUS_APPROVED),
        rejected=by_status(subscriber_store.STATUS_REJECTED),
    )


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
