# CROUS Watcher 🏠

Surveille les logements CROUS (site [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr))
et t'alerte **par WhatsApp + appel téléphonique** dès qu'un logement correspondant
à ta recherche apparaît. Pensé pour ne **pas rater** la résidence **Monbois Libération** à Nancy.

## Comment ça marche

1. Le script lit l'URL de recherche que tu vois dans ton navigateur (ville, prix…).
2. Toutes les 60 s, il interroge l'API du CROUS.
3. Il compare avec les logements déjà vus (`seen_logements.json`).
4. Nouveau logement (filtré sur "Monbois Libération" par défaut) → **WhatsApp avec le lien direct + appel** pour te réveiller.

---

## 1. Installation

```bash
pip install -r requirements.txt
cp .env.example .env      # (Windows PowerShell : copy .env.example .env)
```

## 2. Tester la recherche (sans Twilio)

Remplis d'abord `CROUS_SEARCH_URL` dans `.env` (l'URL de ta recherche Nancy est déjà pré-remplie), puis :

```bash
python test_search.py
```

Ça affiche les logements actuellement dispo dans ta zone. Normal si c'est vide : ça veut dire qu'il n'y a rien en ce moment, et c'est justement le boulot du script d'attendre qu'il y en ait un.

## 3. Configurer Twilio (WhatsApp + appel)

Twilio est le service qui envoie les WhatsApp et passe les appels. Compte d'essai gratuit avec crédits.

1. Crée un compte sur [twilio.com](https://www.twilio.com/try-twilio).
2. Sur la [console](https://console.twilio.com), récupère **Account SID** et **Auth Token** → dans `.env`.
3. **WhatsApp sandbox** : Console → *Messaging → Try it out → Send a WhatsApp message*.
   Suis les instructions : depuis **chaque** téléphone destinataire, envoie le code `join xxxx-xxxx`
   au numéro Twilio indiqué. (⚠️ à refaire si tu ne l'utilises pas pendant 72 h.)
4. **Appels** : Console → *Phone Numbers* → prends le numéro Twilio offert → mets-le dans `TWILIO_CALL_FROM`.
   En compte d'essai, **vérifie chaque numéro** que tu veux appeler (Console → *Verified Caller IDs*).
5. Mets tes numéros (format `+33…`) dans `MY_WHATSAPP_NUMBERS` et `MY_PHONE_NUMBERS`
   (plusieurs numéros = séparés par des virgules).

## 4. Lancer

```bash
python crous_watcher.py
```

Laisse la fenêtre ouverte. Les logs s'affichent et sont aussi écrits dans `watcher.log`.

---

## Trouver ton URL de recherche

Va sur [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr), filtre par ville
(Nancy) et prix. L'URL dans la barre d'adresse contient `?maxPrice=...&bounds=...`.
Copie-la entièrement dans `CROUS_SEARCH_URL`.

## Filtrer une résidence précise

Dans `.env`, `RESIDENCE_FILTER=monbois,libération` → tu n'es alerté que pour cette résidence.
Laisse **vide** pour être alerté de **tous** les logements de la zone (recommandé si tu es flexible).

---

## Le faire tourner 24/7

Le script doit tourner en continu → ton PC doit rester allumé et connecté.
Pour du 24/7 fiable, héberge-le sur une machine toujours allumée :

- **Raspberry Pi** (si tu en as un) — idéal.
- **VPS pas cher** : Hetzner / OVH (~3-4 €/mois), ou **Oracle Cloud Free Tier** (gratuit à vie).

Sur un serveur Linux, utilise le service systemd fourni (`crous-watcher.service`)
pour qu'il redémarre tout seul après un reboot ou un plantage. Instructions en tête du fichier.

Lancement rapide en arrière-plan (sans systemd) :
```bash
nohup python3 crous_watcher.py > watcher.log 2>&1 &
```

---

## ⚠️ Notes importantes

- **Sandbox WhatsApp** : gratuit mais chaque numéro doit "rejoindre" et re-rejoindre toutes les 72 h.
  Pour du définitif, il faut un numéro WhatsApp Business validé (payant).
- **Compte d'essai Twilio** : appels seulement vers des numéros **vérifiés**, avec un court message d'intro Twilio.
- Reste **raisonnable sur la fréquence** (60 s est correct). Un intervalle trop agressif pourrait
  faire bloquer ton IP par le CROUS.
