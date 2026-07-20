#!/usr/bin/env python3
"""
Test RÉEL du circuit d'alerte : envoie un WhatsApp + passe un appel de test
sur les numéros du .env. Ne touche pas au CROUS. Lance : python test_alert.py
"""
from crous_watcher import get_twilio, notify

message = (
    "✅ TEST CROUS Watcher\n"
    "Ceci est un test. Si tu reçois ce message ET un appel, "
    "tout fonctionne ! 🎉\n"
    "👉 https://trouverunlogement.lescrous.fr"
)

client = get_twilio()
if client is None:
    raise SystemExit("Twilio non configuré dans .env")

print("Envoi du test (WhatsApp + appel)...")
notify(client, message, "https://trouverunlogement.lescrous.fr")
print("Terminé. Regarde les logs ci-dessus pour le résultat de chaque envoi.")
