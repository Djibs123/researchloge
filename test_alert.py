#!/usr/bin/env python3
"""
Test RÉEL du circuit d'alerte Telegram. Ne touche pas au CROUS.
Lance : python test_alert.py
"""
from crous_watcher import notify

message = (
    "✅ TEST CROUS Watcher\n"
    "Ceci est un test. Si tu reçois ce message, tout fonctionne ! 🎉\n"
    "👉 https://trouverunlogement.lescrous.fr"
)

print("Envoi du test Telegram...")
notify(message, "https://trouverunlogement.lescrous.fr")
print("Terminé. Regarde les logs ci-dessus pour le résultat de l'envoi.")
