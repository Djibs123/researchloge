Tu es l'assistant en charge de mon 2ᵉ projet de veille de logements CROUS, qui
fonctionne actuellement à plus grande échelle que celui-ci (plusieurs villes,
possiblement plusieurs régions), avec une inscription par "nom de ville + email"
et des notifications par mail.

Voici comment ce 2ᵉ projet récupère les données aujourd'hui, d'après ce que j'en
sais : il récupère la page web avec la liste des logements (probablement en
scrapant le HTML rendu plutôt qu'une API), et doit gérer le fait que les résultats
s'étalent sur plusieurs pages (navigation/swipe entre pages).

J'ai un **autre projet, plus petit** (limité à Nancy/Vandœuvre), qui surveille les
mêmes logements CROUS mais **en passant directement par l'API JSON du site**
plutôt que par du scraping HTML. En le construisant, j'ai découvert plusieurs
choses qui remettent en question l'approche actuelle du 2ᵉ projet et qui devraient
permettre de le rendre plus fiable et plus complet (l'objectif final : **ne rater
aucun logement disponible** sur les zones surveillées). Je te donne toutes les
infos techniques ci-dessous — **rédige un plan d'implémentation détaillé** pour
faire évoluer le 2ᵉ projet en conséquence (tu n'as pas besoin de coder tout de
suite, juste de produire le plan).

**État actuel vérifié (20/08/2026)** : seule la campagne CROUS `tool_id=47`
("Phase complémentaire 2026-2027") a de l'inventaire réel au niveau national — la
campagne 42, qui en avait pourtant 57 en juillet, est retombée à 0. C'est une
illustration directe du point 3 ci-dessous : les campagnes actives changent dans
le temps, il faut les re-détecter automatiquement plutôt que coder une liste fixe.

---

## 1. Constat clé : API JSON directe >> scraping de la page HTML

Le site `trouverunlogement.lescrous.fr` utilise en interne une **API JSON publique**
(`POST https://trouverunlogement.lescrous.fr/api/fr/search/{tool_id}`) — c'est elle
qui alimente la carte/liste qu'on voit à l'écran. Trouvée en observant simplement
les requêtes réseau du site dans le navigateur (onglet Réseau des devtools).

**Pourquoi c'est important pour ton projet** : si tu scrapes la page HTML rendue
(d'où le besoin actuel de naviguer entre plusieurs pages de résultats), c'est
probablement inutile et fragile :
- **Pas de pagination réelle nécessaire.** Avec `pageSize=100`, on a toujours reçu
  la totalité des résultats en une seule requête — même en interrogeant la **France
  entière** sur une campagne, jamais dépassé ~70 logements au total. Le volume de
  logements CROUS disponibles à un instant T est faible ; pas besoin de gérer le
  scroll/la pagination si tu passes par l'API.
- **Plus robuste** : le scraping HTML casse dès que le site change son design ; l'API
  JSON est un contrat de données plus stable (utilisé par le site lui-même).
- **Plus rapide et plus léger** : une requête POST + JSON, pas de rendu de page,
  pas de navigateur headless nécessaire.

### Format d'une requête API (vérifié, fonctionne)
```json
POST https://trouverunlogement.lescrous.fr/api/fr/search/{tool_id}
{
  "idTool": 47,
  "need_aggregation": false,
  "page": 1,
  "pageSize": 100,
  "sector": null,
  "occupationModes": [],
  "location": [
    {"lon": 6.134292, "lat": 48.7092349},
    {"lon": 6.2126188, "lat": 48.666906}
  ],
  "residence": null,
  "precision": 8,
  "equipment": [],
  "price": {"min": 0, "max": 100000000}
}
```
- `location` = un **rectangle géographique libre** (2 coins lon/lat), PAS un
  identifiant de ville. On peut dessiner n'importe quelle zone.
- `price.max` est en **centimes** (piège découvert tôt : l'URL du site affiche le
  prix en euros, l'API attend des centimes → ×100).
- Réponse : `results.total.value` (nombre total) + `results.items[]` (logements),
  chaque item a `residence.label/address`, `label` (type T1/T2/studio...), `area.min`
  (m²), `occupationModes[0].rent.min` (loyer en centimes), `id` (unique).
- Lien direct vers un logement : `https://trouverunlogement.lescrous.fr/tools/{tool_id}/accommodations/{id}`

---

## 2. Couverture géographique : UNE zone large > plusieurs zones découpées à la main

Comme `location` est un simple rectangle libre (pas une liste de villes), **une
seule bounding box englobant toute l'agglomération** (ex: Nancy + Vandœuvre +
Laxou + Villers-lès-Nancy + etc.) suffit en UN seul appel API, sans risquer
d'oublier une commune limitrophe entre deux zones découpées à la main. Élargir
légèrement la box ne coûte quasi rien (un peu plus de résultats à filtrer côté
client), alors que la découper trop finement fait courir le risque réel de "trous"
entre les zones — exactement le risque à éviter pour "ne rien rater".

**Recommandation concrète :**
- Une bounding box **par agglomération/métropole** (Nancy métropole, Lyon métropole,
  etc.), pas une par ville/commune.
- **Île-de-France = cas particulier confirmé techniquement simple** : c'est juste
  une box plus grande (englobant Paris + petite + grande couronne). Aucune logique
  spéciale à coder — le même mécanisme s'applique, seule la taille du rectangle
  change. "Chercher toute la région pour Paris, pas pour les autres villes" est
  juste un choix de **taille de box**, pas un choix d'architecture différent.
- Pour obtenir une bounding box juste : le plus fiable reste de zoomer sur la carte
  du site `trouverunlogement.lescrous.fr` pour la zone voulue et copier `bounds=`
  dans l'URL affichée — rapide et fiable, pas besoin de geocoding externe.

---

## 3. Découvrir les campagnes actives ("tool_id") — piège important

Le CROUS gère plusieurs **campagnes** en parallèle (identifiées par `tool_id` dans
l'URL). **Chaque campagne a son propre inventaire de logements, totalement
indépendant** — surveiller seulement une campagne fait rater tout ce qui sort sous
une autre (vécu : le 20/07, la campagne 42 avait 57 logements en France pendant que
la 47 en avait 32 — surveiller seulement la 47 aurait fait rater tout ce qui
sortait via la 42 ce mois-là).

**Piège découvert récemment** : il existe un endpoint
`GET https://trouverunlogement.lescrous.fr/api/fr/tools` qui liste les campagnes
"officielles" avec leur statut (`enabled`, dates) — utile, mais **incomplet** : il
ne liste que 2 campagnes (22 et 47) alors qu'on sait par expérience que la 42 a eu
de l'inventaire réel un mois plus tôt. Se fier uniquement à cet endpoint aurait pu
faire rater la campagne 42.

**Recommandation pour une couverture fiable à grande échelle :**
1. Interroger `GET /api/fr/tools` pour la liste "officielle" (rapide, structuré,
   donne les dates de fin de campagne).
2. **En complément**, faire un balayage numérique périodique (ex: 1×/semaine, pas à
   chaque cycle) des `tool_id` de 1 à ~50 avec une requête France entière
   (`pageSize=1`, juste pour lire `results.total.value`), afin de détecter toute
   nouvelle campagne active non listée par `/tools`. Les campagnes changent avec le
   calendrier universitaire — un système à grande échelle doit re-détecter ça
   automatiquement, pas se figer sur une liste codée en dur.

---

## 4. Fiabilité — encore plus critique à grande échelle

L'API CROUS renvoie **régulièrement une erreur 400 passagère et aléatoire**, sans
rapport avec la requête elle-même (reproduite plusieurs fois, en local et en
production, sur des requêtes pourtant identiques et valides — confirmé que ce n'est
pas un bug côté client). Avec beaucoup de zones/villes en parallèle, la probabilité
qu'AU MOINS une zone échoue à un cycle donné augmente mécaniquement — donc :

- **Chaque zone doit être vérifiée indépendamment des autres** : une erreur sur une
  zone ne doit jamais empêcher de vérifier les autres au même cycle.
- **Réessai automatique avec micro-délai** (ex: 3 tentatives, 2s d'écart) directement
  dans la fonction d'appel API, avant d'abandonner pour ce cycle — rattrape la
  majorité des erreurs transitoires sans attendre le cycle suivant.
- **L'historique "déjà vu" (pour détecter les nouveaux logements) doit être suivi
  PAR ZONE, pas globalement.** Piège important : si une zone en échec perd son
  historique (remplacé par un snapshot vide/partiel), elle génère une salve de
  fausses alertes dès qu'elle refonctionne (tout son contenu "réapparaît" comme
  neuf). Chaque zone ne doit mettre à jour son propre instantané QUE quand elle
  réussit ce cycle ; sinon elle garde son ancien état intact.
- Prévoir un **léger espacement entre les requêtes** (toutes zones/villes
  confondues) pour ne pas aggraver un éventuel rate-limiting côté CROUS.
- Un vrai monitoring de santé (voir point 6) devient important pour repérer
  rapidement une zone qui échoue en boucle (config invalide) au milieu de
  beaucoup d'autres qui fonctionnent.

---

## 5. Notifications : Telegram (avec approbation) vs Email

Mon petit projet Nancy utilise Telegram (gratuit, illimité, notif quasi instantanée),
avec un auto-abonnement à validation manuelle : `/start` crée une demande "en
attente" visible sur un dashboard web, que l'admin accepte/refuse d'un clic — évite
d'ajouter n'importe qui automatiquement à la liste de diffusion.

Le tien fonctionne différemment (ville + email → notif mail). Une **combinaison des
deux** serait idéale à grande échelle :
- **Email** : universel, ne demande aucune app tierce, mais latence/délivrabilité
  moins bonnes que Telegram, et facilement noyé dans la boîte de réception.
- **Telegram** : quasi instantané, mais demande à l'utilisateur d'avoir Telegram et
  de connaître le bot.
- Proposer les deux (au choix de l'utilisateur à l'inscription) couvre plus de monde
  sans complexité disproportionnée si l'envoi de notif est déjà découplé de la
  logique de détection (une fonction générique `notify(message, lien)` par canal).

---

## 6. Suivi / observabilité (presque indispensable à grande échelle)

Un petit dashboard web (statut par zone, logements actuellement dispo, dernière
erreur, demandes en attente) lu depuis un simple fichier d'état réécrit à chaque
cycle — pas besoin d'accès système pour savoir si le service tourne, juste
vérifier la fraîcheur du dernier timestamp. Avec des dizaines de zones/villes,
ce genre de suivi devient presque indispensable pour repérer rapidement une zone
qui échoue en boucle au milieu de beaucoup d'autres qui fonctionnent bien.

---

## 7. Résumé actionnable

| Sujet | Recommandation |
|---|---|
| Récupération des données | API JSON directe (`POST /api/fr/search/{tool_id}`), pas de scraping HTML |
| Pagination | Inutile en pratique — `pageSize=100` suffit toujours largement |
| Découpage géographique | 1 bounding box large par agglomération, pas une par ville/commune |
| Île-de-France | Même mécanisme, juste une box plus grande — pas de cas spécial à coder |
| Campagnes CROUS (`tool_id`) | Combiner `/api/fr/tools` (rapide) + balayage numérique périodique (fiable) |
| Résilience | Indépendance par zone + retry + historique "seen" par zone (pas global) |
| Notifs | Telegram (rapide, avec approbation manuelle) + Email (universel) en option |
| Observabilité | Dashboard/statut minimal dès qu'il y a plusieurs zones à surveiller |

---

**Ce que j'attends de toi maintenant** : à partir de ces informations et de l'état
actuel de mon 2ᵉ projet (que tu connais), rédige un plan d'implémentation détaillé
pour le faire évoluer vers une couverture complète et fiable (multi-villes,
multi-régions avec le cas particulier Île-de-France, multi-campagnes CROUS),
en réutilisant l'API JSON directe plutôt que le scraping HTML actuel. Identifie les
fichiers/modules à modifier, les changements de structure de données nécessaires,
et les points ouverts qui nécessitent mon arbitrage avant de coder.
