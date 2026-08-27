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
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

import subscriber_store
import telegram_client

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
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status.json")
API_BASE = "https://trouverunlogement.lescrous.fr/api/fr/search"
STARTED_AT = datetime.now(timezone.utc).isoformat()

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
def zone_label(search_url):
    """Nom lisible de la zone, extrait du locationName de l'URL (pour le dashboard)."""
    qs = parse_qs(urlparse(search_url).query)
    name = (qs.get("locationName") or [""])[0].strip()
    return name or "Zone sans nom"


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
# Mémoire des logements déjà vus (suivie PAR ZONE, pas globalement)
# --------------------------------------------------------------------------- #
# Pourquoi par zone : si une zone échoue (erreur réseau/CROUS) pendant qu'une
# autre réussit, on ne doit PAS effacer l'historique de la zone en échec — sinon,
# à son prochain succès, tout ce qu'elle contient réapparaît comme "nouveau" et
# spam des fausses alertes. Chaque zone garde donc son propre instantané, mis à
# jour uniquement quand ELLE réussit.
def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(data, list):
        # Ancien format (un seul set global) : les IDs CROUS sont uniques au
        # niveau global, donc chaque zone actuelle repart avec ce socle commun
        # pour éviter une salve de "nouveau" sur tout ce qui était déjà connu.
        legacy = set(data)
        return {url: set(legacy) for url in SEARCH_URLS}
    return {url: set(ids) for url, ids in data.items()}


def save_seen(seen_by_zone):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({url: sorted(ids) for url, ids in seen_by_zone.items()},
                   f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Abonnés Telegram (demande via /start -> validée manuellement via le dashboard)
# --------------------------------------------------------------------------- #
def sync_telegram_subscribers():
    """Regarde les nouveaux messages reçus par le bot. Un /start d'un inconnu
    crée une demande "pending" (à valider depuis le dashboard) au lieu de
    l'accepter automatiquement. Un /start répété rappelle juste le statut actuel."""
    if not TELEGRAM_BOT_TOKEN:
        return

    last_update_id = subscriber_store.get_last_update_id()
    try:
        updates = telegram_client.get_updates(TELEGRAM_BOT_TOKEN, last_update_id + 1)
    except Exception as e:
        log.error("Échec récupération des messages Telegram : %s", e)
        return

    if not updates:
        return

    new_update_id = last_update_id
    to_notify = []  # [(chat_id, texte), ...] envoyés APRÈS avoir relâché le verrou

    with subscriber_store.locked_store() as store:
        for upd in updates:
            new_update_id = max(new_update_id, upd.get("update_id", new_update_id))
            msg = upd.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if not chat_id:
                continue
            name = (msg.get("chat") or {}).get("first_name", "")
            info = store["subscribers"].get(chat_id)

            if info is None:
                store["subscribers"][chat_id] = {
                    "status": subscriber_store.STATUS_PENDING,
                    "name": name,
                    "source": subscriber_store.SOURCE_TELEGRAM,
                    "requested_at": subscriber_store.now_iso(),
                    "decided_at": None,
                }
                log.info("Nouvelle demande Telegram : %s (%s)", chat_id, name)
                to_notify.append((chat_id,
                    "🕐 Merci ! Ta demande d'accès a bien été reçue et est en attente "
                    "de validation par l'administrateur. Tu recevras un message dès "
                    "qu'elle sera acceptée."))
            elif info["status"] == subscriber_store.STATUS_PENDING:
                to_notify.append((chat_id, "🕐 Ta demande est toujours en attente de validation."))
            elif info["status"] == subscriber_store.STATUS_REJECTED:
                to_notify.append((chat_id, "❌ Ta demande d'accès a été refusée par l'administrateur."))
            # approved -> pas de message, pas de spam

        store["last_update_id"] = new_update_id

    for chat_id, text in to_notify:
        telegram_client.send_message(TELEGRAM_BOT_TOKEN, chat_id, text)


# --------------------------------------------------------------------------- #
# Interrogation du CROUS
# --------------------------------------------------------------------------- #
def fetch_logements(tool_id, body, max_attempts=3, retry_delay=2):
    """Interroge l'API CROUS. Réessaie automatiquement (l'API renvoie parfois
    une erreur 400/5xx passagère, sans rapport avec la requête elle-même —
    la même requête repasse souvent quelques secondes plus tard)."""
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
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
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_attempts:
                log.warning("CROUS : tentative %d/%d échouée (%s), nouvel essai dans %ds...",
                            attempt, max_attempts, e, retry_delay)
                time.sleep(retry_delay)
    raise last_exc


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


def _extract_listing_info(item, tool_id):
    """Champs structurés d'un logement — réutilisés pour le message Telegram
    (describe) ET pour l'instantané status.json lu par le dashboard."""
    residence = (item.get("residence") or {})
    rent_eur = None
    modes = item.get("occupationModes") or []
    if modes and modes[0].get("rent"):
        rent_cents = modes[0]["rent"].get("min")
        if rent_cents:
            rent_eur = rent_cents / 100

    return {
        "id": str(item.get("id")),
        "tool_id": tool_id,
        "name": residence.get("label", "Résidence inconnue"),
        "address": residence.get("address", ""),
        "type": item.get("label", ""),
        "area_m2": (item.get("area") or {}).get("min"),
        "rent_eur": rent_eur,
        "link": f"https://trouverunlogement.lescrous.fr/tools/{tool_id}/accommodations/{item.get('id')}",
    }


def describe(item, tool_id):
    info = _extract_listing_info(item, tool_id)
    lines = [f"🏠 {info['name']} — {info['type']}"]
    if info["area_m2"]:
        lines.append(f"📐 {info['area_m2']:.0f} m²")
    if info["rent_eur"]:
        lines.append(f"💶 {info['rent_eur']:.0f}€/mois")
    if info["address"]:
        lines.append(f"📍 {info['address']}")
    lines.append(f"👉 {info['link']}")
    return info["name"], "\n".join(lines), info["link"]


# --------------------------------------------------------------------------- #
# Notifications (Telegram)
# --------------------------------------------------------------------------- #
def get_all_chat_ids():
    """Destinataires des alertes : uniquement les abonnés "approved"
    (fixes du .env + validés manuellement via le dashboard)."""
    return subscriber_store.get_approved_chat_ids()


def notify(message, link):
    """Envoie le message à tous les abonnés approuvés."""
    chat_ids = get_all_chat_ids()
    if not (TELEGRAM_BOT_TOKEN and chat_ids):
        log.warning("Telegram non configuré — alerte non envoyée :\n%s", message)
        return

    for chat_id in chat_ids:
        if telegram_client.send_message(TELEGRAM_BOT_TOKEN, chat_id, message):
            log.info("Telegram envoyé à %s", chat_id)


# --------------------------------------------------------------------------- #
# Instantané d'état (lu par le dashboard, jamais par le watcher lui-même)
# --------------------------------------------------------------------------- #
def save_status(zones, listings, last_cycle_new_count, total_available, last_error=None):
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": STARTED_AT,
        "pid": os.getpid(),
        "check_interval": CHECK_INTERVAL,
        "residence_filter": RESIDENCE_FILTER,
        "zones": zones,
        "listings": listings,
        "last_cycle_new_count": last_cycle_new_count,
        "total_available": total_available,
        "last_error": last_error,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN),
    }
    tmp_path = STATUS_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATUS_FILE)
    except OSError as e:
        log.error("Échec écriture status.json : %s", e)


# --------------------------------------------------------------------------- #
# Boucle principale
# --------------------------------------------------------------------------- #
def main():
    if not SEARCH_URLS:
        log.error("CROUS_SEARCH_URL manquant dans .env — arrêt.")
        sys.exit(1)

    searches = [build_search_request(u) for u in SEARCH_URLS]  # [(tool_id, body), ...]
    labels = [zone_label(u) for u in SEARCH_URLS]
    seen_by_zone = load_seen()  # {search_url: set(ids)}

    # Les destinataires fixes du .env sont enregistrés comme "approved" pour que
    # le dashboard affiche la liste complète de qui reçoit les alertes.
    subscriber_store.upsert_env_subscribers(TELEGRAM_CHAT_IDS)

    log.info("Démarrage du watcher CROUS")
    log.info("  Campagnes surveillées (tools) : %s", ", ".join(str(t) for t, _ in searches))
    log.info("  Zones : %s", ", ".join(labels))
    log.info("  Filtre résidence : %s", RESIDENCE_FILTER or "AUCUN (tous les logements)")
    log.info("  Intervalle : %ss", CHECK_INTERVAL)
    log.info("  Telegram : %s | destinataires approuvés -> %s",
             "OK" if TELEGRAM_BOT_TOKEN else "NON configuré",
             get_all_chat_ids() or "—")
    log.info("  %d logement(s) déjà connu(s) (toutes zones confondues).",
             len(set().union(*seen_by_zone.values())) if seen_by_zone else 0)

    while True:
        try:
            sync_telegram_subscribers()
        except Exception as e:
            log.exception("Erreur inattendue (sync Telegram) : %s", e)

        zones = []
        currently_available = {}  # id -> (tool_id, item), pour CE tour uniquement
        new_found = []
        zone_errors = []

        for (tool_id, body), url, label in zip(searches, SEARCH_URLS, labels):
            try:
                items = fetch_logements(tool_id, body)
            except requests.RequestException as e:
                # Une zone en échec (souvent transitoire côté CROUS) n'empêche pas
                # de vérifier les autres — et son historique reste intact (voir
                # load_seen) pour ne pas déclencher de fausses alertes plus tard.
                log.error("Erreur réseau (%s) : %s", label, e)
                zone_errors.append(f"{label} : {e}")
                zones.append({"tool_id": tool_id, "label": label, "search_url": url,
                              "raw_count": 0, "matched_count": 0, "error": str(e)})
                continue
            except Exception as e:
                log.exception("Erreur inattendue (%s) : %s", label, e)
                zone_errors.append(f"{label} : {e}")
                zones.append({"tool_id": tool_id, "label": label, "search_url": url,
                              "raw_count": 0, "matched_count": 0, "error": str(e)})
                continue

            old_seen = seen_by_zone.get(url, set())
            zone_current_ids = set()
            matched = 0
            for it in items:
                if not matches_filter(it):
                    continue
                matched += 1
                item_id = str(it.get("id"))
                zone_current_ids.add(item_id)
                currently_available[item_id] = (tool_id, it)
                if item_id not in old_seen:
                    new_found.append((tool_id, it))

            # Instantané ACTUEL de CETTE zone uniquement (elle a réussi ce tour) :
            # un logement qui disparaît puis revient redéclenchera une alerte.
            seen_by_zone[url] = zone_current_ids
            zones.append({
                "tool_id": tool_id, "label": label, "search_url": url,
                "raw_count": len(items), "matched_count": matched,
            })

        # Notifs + sauvegardes dans leur propre filet de sécurité : une panne ici
        # (ex. Telegram indisponible) ne doit jamais arrêter la boucle.
        try:
            if new_found:
                log.info("🎉 %d NOUVEAU(X) logement(s) trouvé(s) !", len(new_found))
                for tool_id, it in new_found:
                    name, message, link = describe(it, tool_id)
                    log.info("Nouveau : %s (tool %s)", name, tool_id)
                    notify(message, link)
            else:
                log.info("Rien de neuf (%d logement(s) au total, %d après filtre).",
                         sum(z["raw_count"] for z in zones), len(currently_available))

            # Oublie les zones qui n'existent plus (config modifiée entretemps).
            seen_by_zone = {u: ids for u, ids in seen_by_zone.items() if u in SEARCH_URLS}
            save_seen(seen_by_zone)

            save_status(
                zones=zones,
                listings=[_extract_listing_info(it, tid) for tid, it in currently_available.values()],
                last_cycle_new_count=len(new_found),
                total_available=len(currently_available),
                last_error="; ".join(zone_errors) if zone_errors else None,
            )
        except Exception as e:
            log.exception("Erreur inattendue (post-traitement) : %s", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Arrêt demandé. Bye 👋")
