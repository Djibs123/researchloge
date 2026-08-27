# Héberger le CROUS Watcher (Oracle Cloud, gratuit à vie)

Le projet tourne 24/7 sur une VM **Oracle Cloud Free Tier** (Ubuntu 24.04, x86
E2.1.Micro, 1GB RAM). Deux services systemd tournent en parallèle :

- **`crous-watcher`** — le script de surveillance + alertes Telegram
- **`crous-dashboard`** — le dashboard web (suivi + validation manuelle des demandes),
  exposé en HTTPS via **Caddy** (reverse proxy, certificat automatique via sslip.io)

---

## Infos serveur actuel

- IP publique : `141.145.223.184`
- Utilisateur SSH : `ubuntu` (pas root)
- Chemin projet : `/home/ubuntu/researchloge`
- Connexion : `ssh -i ssh-key-2026-08-07.key ubuntu@141.145.223.184`
- Dashboard : `https://141-145-223-184.sslip.io`

---

## Déployer une modification (workflow habituel)

Depuis PowerShell sur ton PC, dans le dossier du projet :

```powershell
$key = "ssh-key-2026-08-07.key"
scp -i $key crous_watcher.py subscriber_store.py telegram_client.py dashboard.py `
    requirements.txt ubuntu@141.145.223.184:/home/ubuntu/researchloge/
scp -i $key -r templates ubuntu@141.145.223.184:/home/ubuntu/researchloge/templates
```

⚠️ **Ne jamais écraser le `.env` distant par scp** (il contient des secrets live qui
peuvent avoir divergé du local) — édite-le à la main sur le serveur (`nano .env`).

Puis sur le serveur (`ssh ubuntu@141.145.223.184`) :

```bash
cd /home/ubuntu/researchloge
pip3 install -r requirements.txt --break-system-packages
sudo systemctl restart crous-watcher crous-dashboard
sudo systemctl status crous-watcher crous-dashboard
```

---

## Installation complète depuis zéro

### 1. Dépendances Python
```bash
sudo apt update && sudo apt install -y python3-pip
cd /home/ubuntu/researchloge
pip3 install -r requirements.txt --break-system-packages
```

### 2. Les deux services systemd
```bash
sudo cp crous-watcher.service crous-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crous-watcher
sudo systemctl enable --now crous-dashboard
```

### 3. Caddy (reverse proxy HTTPS pour le dashboard)
Le dashboard écoute en interne sur `127.0.0.1:8080` (jamais exposé directement).
Caddy sert le HTTPS sur `141-145-223-184.sslip.io` (domaine gratuit qui pointe
automatiquement vers l'IP du serveur — pas besoin d'acheter un nom de domaine).

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy

sudo cp Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

### 4. Pare-feu Oracle Cloud (étape manuelle, dans le navigateur)
Console Oracle Cloud → **Compute → Instances → crous-watcher** → VCN associé →
**Subnet → Security Lists → Add Ingress Rules** :
- Source CIDR `0.0.0.0/0`, TCP, port **80** (challenge Let's Encrypt + redirection)
- Source CIDR `0.0.0.0/0`, TCP, port **443** (HTTPS du dashboard)

(Le port 22 pour SSH est déjà ouvert par défaut. Pas besoin d'ouvrir le 8080 —
Caddy est le seul point d'entrée public, le dashboard reste interne.)

### 5. Config `.env` du dashboard
Génère un mot de passe et une clé secrète (en local ou sur le serveur) :
```bash
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('TON_MOT_DE_PASSE'))"
python3 -c "import secrets; print(secrets.token_hex(32))"
```
Ajoute dans `.env` (voir `.env.example` pour la liste complète) :
`DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, `DASHBOARD_SECRET_KEY`,
`DASHBOARD_PORT=8080`, `DASHBOARD_HOST=127.0.0.1`, `DASHBOARD_COOKIE_SECURE=true`
(⚠️ `true` en prod puisque c'est servi en HTTPS derrière Caddy).

---

## Au quotidien

- **Logs watcher** : `journalctl -u crous-watcher -f`
- **Logs dashboard** : `journalctl -u crous-dashboard -f`
- **Logs Caddy** (si le HTTPS ne marche pas) : `journalctl -u caddy -f`
- **Redémarrer** : `sudo systemctl restart crous-watcher` / `crous-dashboard`
- **Modifier la config** : édite `/home/ubuntu/researchloge/.env` puis redémarre le(s) service(s) concerné(s)

## Quand tu as trouvé ton logement 🎉

1. `sudo systemctl stop crous-watcher crous-dashboard caddy`
2. Console Oracle Cloud → supprime l'instance (`crous-watcher`) pour libérer les ressources
   (même en Always Free, propre de nettoyer).
