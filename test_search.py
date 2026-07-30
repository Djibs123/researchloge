#!/usr/bin/env python3
"""
Test rapide SANS notif : vérifie que la recherche CROUS marche et affiche
ce qui est trouvé dans ta zone. Lance : python test_search.py
"""
from crous_watcher import build_search_request, fetch_logements, matches_filter, describe, SEARCH_URLS

if not SEARCH_URLS:
    raise SystemExit("Configure d'abord CROUS_SEARCH_URL dans .env")

total_items = 0
total_matching = 0

for url in SEARCH_URLS:
    tool_id, body = build_search_request(url)
    items = fetch_logements(tool_id, body)
    matching = [it for it in items if matches_filter(it)]
    total_items += len(items)
    total_matching += len(matching)
    print(f"\n--- Campagne tool {tool_id} : {len(items)} logement(s), {len(matching)} après filtre ---")
    for it in matching:
        name, message, _ = describe(it, tool_id)
        print(message)
        print("-" * 40)

print(f"\nTOTAL : {total_items} logement(s) dans la zone, {total_matching} après filtre.")
if total_items == 0:
    print("(Aucun logement dispo pour le moment — c'est normal, le script attend qu'il y en ait un.)")
