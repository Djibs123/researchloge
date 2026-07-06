#!/usr/bin/env python3
"""
Test rapide SANS Twilio : vérifie que la recherche CROUS marche et affiche
ce qui est trouvé dans ta zone. Lance : python test_search.py
"""
from crous_watcher import build_search_request, fetch_logements, matches_filter, describe, SEARCH_URL

if not SEARCH_URL:
    raise SystemExit("Configure d'abord CROUS_SEARCH_URL dans .env")

tool_id, body = build_search_request(SEARCH_URL)
items = fetch_logements(tool_id, body)
matching = [it for it in items if matches_filter(it)]

print(f"\nTotal logements dans la zone : {len(items)}")
print(f"Après filtre résidence       : {len(matching)}\n")

for it in matching:
    name, message, _ = describe(it, tool_id)
    print(message)
    print("-" * 40)

if not items:
    print("(Aucun logement dispo pour le moment — c'est normal, le script attend qu'il y en ait un.)")
