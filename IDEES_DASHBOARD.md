Tu es l'assistant en charge de mon 2ᵉ projet de veille de logements CROUS (plus
grande échelle que Nancy, multi-villes/régions, inscription possible par email).
Je préfère largement le dashboard construit sur mon petit projet Nancy — je veux
que tu construises l'équivalent (adapté à ton projet, ses données, son canal de
notif) sur ton projet. **Ne parle pas de déploiement/hébergement** (pas de serveur,
pas de reverse proxy, pas d'HTTPS, pas de service système) — uniquement
l'architecture applicative et les fonctionnalités du dashboard lui-même.

## Principe central : le dashboard ne parle JAMAIS à l'API de recherche de logements

Le dashboard est un **process web séparé** du collecteur (le script qui interroge
l'API CROUS en boucle). Il ne fait **aucun appel** à l'API de logements — il lit
uniquement un **instantané d'état** que le collecteur réécrit à chaque cycle
(zones surveillées, logements actuellement disponibles, dernière erreur par zone,
timestamp de dernière mise à jour). Ça évite de doubler la charge sur l'API
externe, et ça découple complètement les deux composants (le dashboard peut
planter/redémarrer sans jamais affecter la collecte, et inversement).

**Détection "en ligne / hors ligne" sans accès système** : déduite uniquement de
la fraîcheur du timestamp de l'instantané (si `maintenant - dernière_maj > 3x
l'intervalle de vérification`, considérer "hors ligne"). Pas besoin d'accès
`systemctl`/process/infra depuis le dashboard — juste lire un fichier/une valeur.

## Deuxième principe central : validation manuelle des inscriptions, jamais d'auto-accept

Toute nouvelle inscription (email, ou tout autre canal) arrive dans un état
**"en attente"**, PAS automatiquement active. L'admin voit la liste des demandes
en attente sur le dashboard et clique Accepter/Refuser. Seuls les contacts
"approuvés" reçoivent les alertes de logements. Sur mon petit projet, ça a
remplacé un ancien système où n'importe qui pouvait s'auto-inscrire sans contrôle
— important pour éviter les abus/spam de la liste de diffusion.

### Modèle de données des abonnés/contacts
Un magasin partagé entre le collecteur (qui ajoute des demandes) et le dashboard
(qui les valide/refuse), avec pour chaque contact :
- un statut : `pending` / `approved` / `rejected`
- une origine (`source`) : distinguer les contacts "fixes" (configurés en dur,
  non modifiables depuis l'UI, juste affichés) des contacts inscrits dynamiquement
  (modifiables/révocables depuis le dashboard)
- nom/identifiant affichable, date de demande, date de décision

**Accès concurrent sûr** : le collecteur ET le dashboard lisent/modifient ce même
magasin. Pour éviter toute corruption/perte d'écriture :
- Un verrou de fichier (ou équivalent selon ton stockage — un verrou DB/transaction
  si tu es sur une vraie base) qui protège tout le cycle lecture→modification→écriture
  d'un coup, pas des fonctions `load()`/`save()` séparées (qui recréent une race
  condition entre les deux process).
- Écriture **atomique** (écrire dans un fichier temporaire puis remplacer, jamais
  écrire directement par-dessus le fichier final) pour ne jamais laisser un état
  corrompu si le process est interrompu en plein milieu.
- L'envoi de la notification à l'utilisateur (email de confirmation, etc.) se fait
  **après** avoir relâché le verrou — un appel réseau lent ne doit jamais bloquer
  l'autre process qui attend le verrou.

## Pages / fonctionnalités du dashboard

(Pas de mécanisme de connexion à prévoir — dashboard en localhost uniquement,
non exposé.)

- **Page principale** :
  - Bandeau statut en ligne/hors ligne
  - Petites cartes de stats : nombre de logements dispo actuellement, nombre de
    zones surveillées, nombre de destinataires actifs, nombre de demandes en
    attente
  - Bandeau d'erreur si le dernier cycle a rencontré un souci (idéalement précis :
    QUELLE zone/ville a échoué, pas juste "une erreur est survenue")
  - Table des **demandes en attente** (nom, identifiant, date de demande, boutons
    Accepter/Refuser)
  - Table des **destinataires actifs** (avec indication visuelle des contacts
    "fixes" non modifiables vs ceux ajoutés dynamiquement, bouton pour révoquer
    ces derniers)
  - Table des **demandes refusées** (avec possibilité de ré-accepter)
  - Table des **logements actuellement disponibles** (nom, adresse, type, surface,
    loyer, lien direct)
  - Table des **zones surveillées** avec leur statut individuel (nombre de
    logements trouvés par zone) — utile pour repérer une zone précise qui
    dysfonctionne au milieu de beaucoup d'autres qui marchent
- **Actions** : accepter/refuser une demande déclenche un envoi de notification
  au contact concerné (email dans ton cas) l'informant de la décision, puis
  rafraîchit la page.
- **Rafraîchissement** : un simple rafraîchissement automatique de page toutes les
  60s suffit pour un usage admin — pas besoin de WebSocket/JS complexe pour ce
  cas d'usage.

## Adaptations nécessaires pour ton projet à grande échelle

- Le modèle de contact doit être **générique par canal** (pas juste "chat_id
  Telegram" comme sur mon petit projet) — un identifiant + un type de canal
  (email, Telegram, etc. si tu comptes supporter plusieurs canaux comme évoqué
  dans l'autre prompt), pour rester extensible.
- Les tables "logements disponibles" et "zones surveillées" doivent gérer
  plusieurs villes/régions, pas une seule zone — prévoir un regroupement/filtre
  par ville dans l'affichage si le nombre de zones devient important.
- Le magasin des contacts (fichier JSON chez moi) devrait probablement devenir une
  vraie table en base de données chez toi vu l'échelle — le principe (statut,
  source, verrouillage/atomicité des écritures concurrentes) reste identique,
  seule l'implémentation du stockage change.

**Ce que j'attends de toi** : propose (puis implémente si je valide) un dashboard
équivalent sur ton projet, avec ces principes adaptés à ta stack technique et ton
canal de notification actuel. Precise les fichiers/modules à créer ou modifier.
