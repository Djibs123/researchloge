#!/usr/bin/env python3
"""Petit client Telegram partagé entre crous_watcher.py et dashboard.py."""

import logging

import requests

log = logging.getLogger("crous")


def send_message(bot_token, chat_id, text):
    """Envoie un message Telegram. Retourne True si envoyé avec succès."""
    if not bot_token:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Échec envoi Telegram vers %s : %s", chat_id, e)
        return False


def get_updates(bot_token, offset, timeout_s=15):
    """Récupère les nouveaux messages reçus par le bot depuis `offset`."""
    resp = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates",
        params={"offset": offset, "timeout": 0},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])
