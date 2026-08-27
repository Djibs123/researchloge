# CROUS Watcher 🏠

Surveille les logements CROUS ([trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr))
et alerte par **Telegram** dès qu'un logement correspondant apparaît sur une ou
plusieurs zones surveillées.

## Comment ça marche

1. `crous_watcher.py` interroge l'API du CROUS toutes les `CHECK_INTERVAL` secondes,
   pour chaque zone configurée dans `CROUS_SEARCH_URL` (une ou plusieurs URLs de
   recherche, séparées par des virgules).
2. Chaque zone est vérifiée **indépendamment des autres** : une erreur passagère sur
   une zone (l'API CROUS renvoie parfois une erreur 400 aléatoire) n'empêche jamais
   de vérifier les autres au même cycle, et un réessai automatique (3 tentatives)
   rattrape la plupart de ces erreurs transitoires.
3. L'historique des logements déjà vus est suivi **par zone** (`seen_logements.json`) :
   un logement qui disparaît puis redevient disponible (repris par quelqu'un puis
   relibéré) redéclenche une alerte.
4. Nouveau logement trouvé → message **Telegram** avec le lien direct, envoyé à tous
   les destinataires **approuvés**.
5. Le watcher écrit aussi un instantané d'état (`status.json`) à chaque cycle — lu
   par le dashboard, jamais par le watcher lui-même.

### Inscription et validation des destinataires

Il n'y a plus d'auto-acceptation. Le fonctionnement :
- Un noyau fixe de destinataires est configuré dans `.env` (`TELEGRAM_CHAT_IDS`),
  toujours actif.
- N'importe qui peut envoyer `/start` au bot Telegram → sa demande arrive **en
  attente de validation** (pas d'accès immédiat).
- L'admin valide/refuse chaque demande depuis le **dashboard web** (`dashboard.py`).
  Seuls les destinataires "approved" reçoivent les alertes.

---

## Installation locale

```bash
pip install -r requirements.txt
cp .env.example .env      # (PowerShell : copy .env.example .env)
```

Remplis `.env` :
- `CROUS_SEARCH_URL` : une ou plusieurs URLs de recherche CROUS (voir plus bas
  comment les obtenir), séparées par des virgules
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_IDS` : voir la section Telegram ci-dessous
- Les `DASHBOARD_*` : voir la section Dashboard ci-dessous

## Tester la recherche (sans notifier)

```bash
python test_search.py
```
Affiche les logements actuellement disponibles sur les zones configurées, sans
envoyer aucune notification. Normal si c'est vide (le watcher attend simplement
qu'un logement apparaisse).

## Tester une alerte Telegram réelle

```bash
python test_alert.py
```
Envoie un vrai message de test à tous les destinataires approuvés — utile pour
vérifier que le circuit de notification fonctionne de bout en bout.

## Lancer le watcher

```bash
python crous_watcher.py
```
Logs affichés à l'écran + écrits dans `watcher.log`. `Lancer_surveillance.bat`
(Windows) fait la même chose avec redémarrage automatique en cas de plantage —
pratique pour un test local, mais **la façon normale de faire tourner ça en continu
est le déploiement 24/7 décrit dans [HOSTING.md](HOSTING.md)**, pas un PC allumé
en permanence.

---

## Configurer Telegram

1. Crée un bot via [@BotFather](https://t.me/BotFather) (commande `/newbot`) →
   il te donne un `TELEGRAM_BOT_TOKEN`.
2. Envoie n'importe quel message à ton bot pour qu'il connaisse ton `chat_id`.
3. Récupère-le via `https://api.telegram.org/bot<TOKEN>/getUpdates`.
4. Mets ce chat_id dans `TELEGRAM_CHAT_IDS` (noyau fixe, plusieurs possibles,
   séparés par des virgules) — ces destinataires sont toujours actifs, non
   modifiables depuis le dashboard (seulement via `.env`).
5. Toute autre personne s'inscrit en envoyant `/start` au bot — sa demande apparaît
   "en attente" sur le dashboard.

## Configurer le dashboard

Le dashboard (Flask + Waitress) affiche le statut du watcher (zones surveillées,
logements dispo, dernière erreur) et permet d'accepter/refuser les demandes
Telegram en attente.

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('TON_MOT_DE_PASSE'))"
python -c "import secrets; print(secrets.token_hex(32))"
```
Renseigne le résultat dans `DASHBOARD_PASSWORD_HASH` / `DASHBOARD_SECRET_KEY`
(voir `.env.example` pour la liste complète des variables `DASHBOARD_*`), puis :
```bash
python dashboard.py
```

---

## Trouver une URL de recherche

Va sur [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr),
filtre par ville/zone et prix (zoome sur la carte pour ajuster la zone exacte).
L'URL dans la barre d'adresse contient `bounds=...&locationName=...`. Copie-la
entièrement dans `CROUS_SEARCH_URL`. Pour surveiller plusieurs zones (ou plusieurs
campagnes CROUS), sépare les URLs par des virgules.

> ⚠️ Le CROUS gère plusieurs campagnes en parallèle (`tool_id` dans l'URL, ex
> `/tools/47/`), avec des inventaires totalement indépendants les unes des autres
> — vérifie régulièrement laquelle est active (les campagnes changent avec le
> calendrier universitaire).

## Filtrer une résidence précise (optionnel)

Dans `.env`, `RESIDENCE_FILTER=monbois,libération` → alerte seulement pour cette
résidence. Laisse **vide** (recommandé) pour être alerté de tous les logements des
zones surveillées.

---

## Faire tourner ça 24/7

Voir **[HOSTING.md](HOSTING.md)** — déploiement complet sur Oracle Cloud Free Tier
(gratuit à vie), avec les deux services systemd (`crous-watcher` + `crous-dashboard`)
et le dashboard exposé en HTTPS via Caddy.

## Structure du projet

| Fichier | Rôle |
|---|---|
| `crous_watcher.py` | Boucle principale : interroge le CROUS, détecte les nouveautés, notifie |
| `subscriber_store.py` | Gestion des destinataires (pending/approved/rejected), accès concurrent sûr |
| `telegram_client.py` | Envoi/lecture de messages Telegram |
| `dashboard.py` + `templates/` | Dashboard web (statut + validation des demandes) |
| `test_search.py` / `test_alert.py` | Scripts de test manuels |
| `HOSTING.md` | Déploiement 24/7 (Oracle Cloud + Caddy) |
