#!/usr/bin/env python3
"""
CROUS Watcher — surveille trouverunlogement.lescrous.fr et alerte par Telegram
dès qu'un logement correspondant apparaît.

Usage :
    pip install -r requirements.txt
    cp .env.example .env   (puis remplis .env)
    python crous_watcher.py

Le script lit directement l'URL de recherche que tu vois dans ton navigateur
(CROUS_SEARCH_URL), en extrait les coordonnées + le prix max, interroge l'API
du CROUS toutes les CHECK_INTERVAL secondes, et n'alerte qu'une seule fois par
logement (mémorisé dans seen_logements.json).
"""

import json
import os
import sys
import time
import logging
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

# Sur Windows, la console est souvent en cp1252 et plante sur les emojis des logs.
# On force l'UTF-8 (avec repli sûr) pour éviter tout crash d'affichage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# --------------------------------------------------------------------------- #
# Configuration (via .env)
# --------------------------------------------------------------------------- #
# Une ou plusieurs URL de recherche, séparées par des virgules (ex : campagnes 42 ET 47).
SEARCH_URLS = [u.strip() for u in os.getenv("CROUS_SEARCH_URL", "").split(",") if u.strip()]
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

# Filtre optionnel sur le nom de résidence (mots-clés séparés par des virgules).
# Ex : "monbois,libération"  -> n'alerte que pour ces résidences.
# Laisse vide pour être alerté de TOUT logement dans la zone.
RESIDENCE_FILTER = [
    kw.strip().lower()
    for kw in os.getenv("RESIDENCE_FILTER", "").split(",")
    if kw.strip()
]

# Telegram (gratuit, sans limite). Bot créé via @BotFather ; chat_id = à qui envoyer.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_logements.json")
API_BASE = "https://trouverunlogement.lescrous.fr/api/fr/search"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "watcher.log"),
                            encoding="utf-8"),
    ],
)
log = logging.getLogger("crous")


# --------------------------------------------------------------------------- #
# Parsing de l'URL de recherche -> corps de requête API
# --------------------------------------------------------------------------- #
def build_search_request(search_url):
    """Transforme l'URL du navigateur en (tool_id, corps JSON pour l'API)."""
    parsed = urlparse(search_url)
    qs = parse_qs(parsed.query)

    # tool id : .../tools/42/search
    parts = [p for p in parsed.path.split("/") if p]
    try:
        tool_id = int(parts[parts.index("tools") + 1])
    except (ValueError, IndexError):
        tool_id = 42  # valeur par défaut (recherche logement étudiant)

    # bounds=lon1_lat1_lon2_lat2
    location = []
    if "bounds" in qs:
        b = qs["bounds"][0].split("_")
        if len(b) == 4:
            location = [
                {"lon": float(b[0]), "lat": float(b[1])},
                {"lon": float(b[2]), "lat": float(b[3])},
            ]

    max_price = int(qs.get("maxPrice", ["100000000"])[0])

    body = {
        "idTool": tool_id,
        "need_aggregation": False,
        "page": 1,
        "pageSize": 100,
        "sector": None,
        "occupationModes": [],
        "location": location,
        "residence": None,
        "precision": 8,
        "equipment": [],
        "price": {"min": 0, "max": max_price},
    }
    return tool_id, body


# --------------------------------------------------------------------------- #
# Mémoire des logements déjà vus
# --------------------------------------------------------------------------- #
def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Interrogation du CROUS
# --------------------------------------------------------------------------- #
def fetch_logements(tool_id, body):
    resp = requests.post(
        f"{API_BASE}/{tool_id}",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (crous-watcher)",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", {}).get("items", [])


def matches_filter(item):
    """True si le logement passe le filtre résidence (ou si aucun filtre)."""
    if not RESIDENCE_FILTER:
        return True
    residence = (item.get("residence") or {})
    haystack = " ".join(
        str(x).lower()
        for x in (residence.get("label"), residence.get("address"), item.get("label"))
        if x
    )
    return any(kw in haystack for kw in RESIDENCE_FILTER)


def describe(item, tool_id):
    residence = (item.get("residence") or {})
    name = residence.get("label", "Résidence inconnue")
    address = residence.get("address", "")
    typ = item.get("label", "")
    area = (item.get("area") or {}).get("min")

    rent = ""
    modes = item.get("occupationModes") or []
    if modes and modes[0].get("rent"):
        rent_cents = modes[0]["rent"].get("min")
        if rent_cents:
            rent = f"{rent_cents / 100:.0f}€/mois"

    link = f"https://trouverunlogement.lescrous.fr/tools/{tool_id}/accommodations/{item.get('id')}"

    lines = [f"🏠 {name} — {typ}"]
    if area:
        lines.append(f"📐 {area:.0f} m²")
    if rent:
        lines.append(f"💶 {rent}")
    if address:
        lines.append(f"📍 {address}")
    lines.append(f"👉 {link}")
    return name, "\n".join(lines), link


# --------------------------------------------------------------------------- #
# Notifications (Telegram)
# --------------------------------------------------------------------------- #
def notify(message, link):
    """Envoie le message à tous les chats Telegram configurés."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS):
        log.warning("Telegram non configuré — alerte non envoyée :\n%s", message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": message, "disable_web_page_preview": False},
                timeout=15,
            )
            resp.raise_for_status()
            log.info("Telegram envoyé à %s", chat_id)
        except Exception as e:
            log.error("Échec Telegram vers %s : %s", chat_id, e)


# --------------------------------------------------------------------------- #
# Boucle principale
# --------------------------------------------------------------------------- #
def main():
    if not SEARCH_URLS:
        log.error("CROUS_SEARCH_URL manquant dans .env — arrêt.")
        sys.exit(1)

    searches = [build_search_request(u) for u in SEARCH_URLS]  # [(tool_id, body), ...]
    seen = load_seen()

    log.info("Démarrage du watcher CROUS")
    log.info("  Campagnes surveillées (tools) : %s", ", ".join(str(t) for t, _ in searches))
    log.info("  Filtre résidence : %s", RESIDENCE_FILTER or "AUCUN (tous les logements)")
    log.info("  Intervalle : %ss", CHECK_INTERVAL)
    log.info("  Telegram : %s | chats -> %s",
             "OK" if (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS) else "NON configuré",
             TELEGRAM_CHAT_IDS or "—")
    log.info("  %d logement(s) déjà connu(s).", len(seen))

    while True:
        try:
            total_seen_zone = 0
            currently_available = {}  # id -> (tool_id, item), pour CE tour uniquement
            new_found = []

            for tool_id, body in searches:
                items = fetch_logements(tool_id, body)
                total_seen_zone += len(items)
                for it in items:
                    if not matches_filter(it):
                        continue
                    item_id = str(it.get("id"))
                    currently_available[item_id] = (tool_id, it)
                    if item_id not in seen:
                        new_found.append((tool_id, it))

            if new_found:
                log.info("🎉 %d NOUVEAU(X) logement(s) trouvé(s) !", len(new_found))
                for tool_id, it in new_found:
                    name, message, link = describe(it, tool_id)
                    log.info("Nouveau : %s (tool %s)", name, tool_id)
                    notify(message, link)
            else:
                log.info("Rien de neuf (%d logement(s) au total, %d après filtre).",
                         total_seen_zone, len(currently_available))

            # On ne garde que l'instantané ACTUEL : un logement qui disparaît puis
            # revient (repris par qqn puis relibéré) redéclenchera une alerte.
            seen = set(currently_available.keys())
            save_seen(seen)

        except requests.RequestException as e:
            log.error("Erreur réseau : %s", e)
        except Exception as e:
            log.exception("Erreur inattendue : %s", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Arrêt demandé. Bye 👋")
