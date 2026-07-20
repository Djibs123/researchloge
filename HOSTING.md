# Héberger le CROUS Watcher sur un VPS Hetzner (24/7)

Guide pas-à-pas. Objectif : le script tourne tout seul, même PC éteint, et redémarre
automatiquement en cas de reboot ou plantage.

---

## Partie A — Créer le serveur (dans ton navigateur)

1. Va sur **[hetzner.com/cloud](https://www.hetzner.com/cloud)** → **Sign Up**.
2. Crée le compte, valide ton email, ajoute un moyen de paiement (carte ou PayPal).
   > Hetzner peut demander une petite vérification d'identité au 1er compte — c'est normal.
3. Une fois connecté à la **Cloud Console** : **+ New Project** → nomme-le `crous`.
4. Dans le projet → **Add Server** :
   - **Location** : Nuremberg ou Falkenstein (Allemagne).
   - **Image** : **Ubuntu 24.04**.
   - **Type** : le moins cher — **CAX11** (ARM, ~3,79 €/mois) suffit largement.
   - **Networking** : laisse IPv4 coché.
   - **SSH Key** : ignore pour l'instant (on utilisera un mot de passe).
   - **Name** : `crous-watcher`.
   - Clique **Create & Buy now**.
5. Hetzner t'envoie par **email** l'**adresse IP** du serveur et le **mot de passe root**.
   (ou l'IP s'affiche dans la console). Garde-les sous la main.

➡️ **Reviens avec l'adresse IP** et on fait la suite ensemble.

---

## Partie B — Se connecter et installer (depuis ton PC Windows)

Ouvre **PowerShell** sur ton PC et remplace `IP_DU_SERVEUR` par la vraie IP.

### 1. Copier le projet sur le serveur
```powershell
scp -r "c:\Users\samba\projetPerso\researchloge" root@IP_DU_SERVEUR:/root/
```
(Il demandera `yes` la 1ère fois, puis le mot de passe root.)

### 2. Se connecter au serveur
```powershell
ssh root@IP_DU_SERVEUR
```

### 3. Installer Python et les dépendances (sur le serveur)
```bash
apt update && apt install -y python3-pip
cd /root/researchloge
pip3 install -r requirements.txt --break-system-packages
```

### 4. Vérifier que tout est là
```bash
python3 test_search.py        # doit interroger le CROUS sans erreur
```

### 5. Lancer en service permanent
```bash
cp crous-watcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now crous-watcher
```

### 6. Vérifier que ça tourne
```bash
systemctl status crous-watcher      # doit afficher "active (running)"
journalctl -u crous-watcher -f      # logs en direct (Ctrl+C pour quitter)
```

---

## Au quotidien

- **Voir les logs** : `journalctl -u crous-watcher -f`
- **Redémarrer** : `systemctl restart crous-watcher`
- **Arrêter** : `systemctl stop crous-watcher`
- **Modifier la config** : édite `/root/researchloge/.env` puis `systemctl restart crous-watcher`

## Quand tu as trouvé ton logement 🎉

1. Arrête le service : `systemctl stop crous-watcher`
2. Dans la Cloud Console Hetzner → supprime le serveur (**Delete**) pour arrêter la facturation.

## ⚠️ Rappel WhatsApp sandbox

Le sandbox Twilio se désactive après **72h sans activité**. Pour rester connecté,
chaque numéro renvoie de temps en temps `join period-hunter` au **+1 415 523 8886**.
(Le script t'enverra quand même l'appel même si le WhatsApp expire.)
