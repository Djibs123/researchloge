#!/usr/bin/env python3
"""
Source unique de vérité pour les abonnés Telegram (pending / approved / rejected),
partagée entre crous_watcher.py (qui ajoute des demandes) et dashboard.py (qui les
valide/refuse). Le verrou fichier (filelock) + l'écriture atomique (os.replace)
évitent toute corruption si les deux process écrivent au même moment.
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from filelock import FileLock, Timeout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "telegram_subscribers.json")
LOCK_FILE = SUBSCRIBERS_FILE + ".lock"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

SOURCE_ENV = "env"
SOURCE_TELEGRAM = "telegram"
SOURCE_TELEGRAM_LEGACY = "telegram-legacy"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_and_migrate(path):
    """Lit le fichier ; migre le format v1 ({"chat_ids": [...], "last_update_id": N})
    vers le v2 (pending/approved/rejected) en marquant tout ce qui existait déjà
    comme "approved" — personne n'est coupé rétroactivement."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 2, "last_update_id": 0, "subscribers": {}}

    if data.get("version") == 2:
        data.setdefault("subscribers", {})
        data.setdefault("last_update_id", 0)
        return data

    # Format v1 legacy : {"chat_ids": [...], "last_update_id": N}
    now = now_iso()
    subscribers = {}
    for chat_id in data.get("chat_ids", []):
        subscribers[str(chat_id)] = {
            "status": STATUS_APPROVED,
            "name": "",
            "source": SOURCE_TELEGRAM_LEGACY,
            "requested_at": now,
            "decided_at": now,
        }
    return {
        "version": 2,
        "last_update_id": data.get("last_update_id", 0),
        "subscribers": subscribers,
    }


def _atomic_write(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


@contextmanager
def locked_store(timeout=5):
    """Tient le verrou pendant tout le cycle lecture->modification->écriture.
    N'écrit QUE si le bloc `with` se termine sans exception."""
    lock = FileLock(LOCK_FILE, timeout=timeout)
    with lock:
        store = _read_and_migrate(SUBSCRIBERS_FILE)
        yield store
        _atomic_write(SUBSCRIBERS_FILE, store)


def upsert_env_subscribers(env_chat_ids):
    """Ajoute (sans écraser) les chat_ids fixes du .env comme approved/env.
    Appelé au démarrage du watcher pour que le dashboard ait une vue complète."""
    if not env_chat_ids:
        return
    now = now_iso()
    try:
        with locked_store() as store:
            for chat_id in env_chat_ids:
                chat_id = str(chat_id)
                if chat_id not in store["subscribers"]:
                    store["subscribers"][chat_id] = {
                        "status": STATUS_APPROVED,
                        "name": "",
                        "source": SOURCE_ENV,
                        "requested_at": now,
                        "decided_at": now,
                    }
    except Timeout:
        pass  # pas grave, sera retenté au prochain cycle


def get_approved_chat_ids():
    """Chat_ids à qui envoyer les alertes (approved uniquement)."""
    try:
        with locked_store() as store:
            return sorted(
                chat_id for chat_id, info in store["subscribers"].items()
                if info.get("status") == STATUS_APPROVED
            )
    except Timeout:
        return []


def get_last_update_id():
    try:
        with locked_store() as store:
            return store.get("last_update_id", 0)
    except Timeout:
        return 0
