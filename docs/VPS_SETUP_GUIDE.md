# Guide VPS de A à Z (pour débutant total)

> On déplace le moteur BETSFIX du PC vers un petit serveur Linux (VPS) toujours allumé, puis on coupe la
> dépendance au PC. Durée ~1 h, en 8 phases. Tu copies-colles, tu me dis « fait » ou tu colles l'erreur.
> **Le PC reste allumé pendant toute la manip** (on copie des choses depuis lui + filet de sécurité).

## Ce qu'il te faut
- Une carte bancaire (Hetzner ~4 €/mois).
- Ce PC allumé.
- ~1 h devant toi.

---

## PHASE 1 — Créer le serveur Hetzner
1. Va sur **https://console.hetzner.com** → crée un compte (email + carte).
2. **New project** → nomme-le `betsfix`.
3. Dans le projet → **Add Server** :
   - **Location** : Nuremberg ou Falkenstein (Allemagne, EU).
   - **Image** : **Ubuntu 24.04**.
   - **Type** : onglet **Shared vCPU (x86)** → **CX22** (2 vCPU, 4 Go). ⚠️ PAS l'ARM.
   - **SSH Key** : on l'ajoute à la Phase 2 (garde l'onglet ouvert) — OU coche « root password » (Hetzner
     t'enverra un mot de passe par email ; plus simple pour commencer).
   - **Name** : `betsfix-vps`.
   - **Create & Buy now**.
4. Note l'**adresse IP** du serveur (ex. `91.99.x.x`) affichée après création.

---

## PHASE 2 — Se connecter au serveur depuis Windows
Windows a `ssh` intégré. Ouvre **PowerShell** (menu Démarrer → « PowerShell »).

**Option A — mot de passe root** (si tu l'as reçu par email) :
```powershell
ssh root@IP_DU_SERVEUR
```
(remplace `IP_DU_SERVEUR`). Tape `yes` à la 1re question, colle le mot de passe (invisible à la frappe, normal).
Il te demandera d'en choisir un nouveau au 1er login.

**Option B — clé SSH** (plus propre ; à faire AVANT de créer le serveur, ou recrée-le avec la clé) :
```powershell
ssh-keygen -t ed25519 -C "betsfix"       # Entrée x3 (pas de passphrase)
type $env:USERPROFILE\.ssh\id_ed25519.pub  # affiche la clé PUBLIQUE -> copie-la dans Hetzner (SSH Keys)
ssh root@IP_DU_SERVEUR
```

Quand tu vois `root@betsfix-vps:~#`, tu es **dans le serveur**. 🎉

---

## PHASE 3 — Préparer le serveur (copier-coller EN BLOC dans le SSH)
```bash
# 1) mises à jour + créer un utilisateur non-root 'betsfix'
apt update && apt -y upgrade
adduser --disabled-password --gecos "" betsfix
usermod -aG sudo betsfix
# 2) outils de base + Node (pour le CLI Claude)
apt -y install git python3.12 python3.12-venv python3-pip chromium-browser tzdata curl nodejs npm
timedatectl set-timezone Europe/Brussels
```
→ dis-moi « phase 3 ok » (ou colle l'erreur).

---

## PHASE 4 — Récupérer le code + les secrets
**A. Le code** (dans le SSH, en tant que betsfix) :
```bash
su - betsfix
# Clone via un TOKEN GitHub (repo privé). Génère un token : github.com -> Settings -> Developer settings ->
# Personal access tokens -> Fine-grained -> repo CronoBots/BETSFIX, permission "Contents: Read". Colle-le ci-dessous.
git clone https://TON_TOKEN@github.com/CronoBots/BETSFIX.git /opt/betsfix 2>/dev/null || \
  sudo git clone https://TON_TOKEN@github.com/CronoBots/BETSFIX.git /opt/betsfix
sudo chown -R betsfix:betsfix /opt/betsfix
cd /opt/betsfix && bash deploy/vps/setup.sh
```

**B. Les secrets (`.env`)** — depuis TON PC (nouvelle fenêtre **PowerShell**, PAS le SSH) :
```powershell
scp C:\Users\vince\BETSFIX\.env betsfix@IP_DU_SERVEUR:/opt/betsfix/.env
```
→ ça copie ton `.env` (avec la nouvelle clé API-Football + tous les secrets) sur le serveur, chiffré. Ne colle
JAMAIS le `.env` dans un chat.

→ « phase 4 ok ».

---

## PHASE 5 — Authentifier Claude (les 2 sessions partagent ce login)
Dans le SSH (betsfix) :
```bash
cd /opt/betsfix
claude login        # ouvre une URL : ouvre-la sur ton tel/PC, connecte ton compte Pro Max
claude -p "réponds juste: OK" --model sonnet   # test : doit répondre "OK" sans navigateur
```
→ « phase 5 ok ».

---

## PHASE 6 — Lancer l'API + les scans automatiques
Dans le SSH :
```bash
# API (uvicorn) en service qui redémarre tout seul
sudo cp /opt/betsfix/deploy/vps/betsfix-api.service /etc/systemd/system/
sudo systemctl enable --now betsfix-api
sleep 3 && curl -s localhost:8000/health          # doit afficher {"status":"ok"}
# Scans automatiques (cron) : édite et colle le contenu de deploy/vps/crontab.txt
crontab -e        # (choisis nano si demandé) -> colle le fichier -> Ctrl+O Entrée, Ctrl+X
```
→ « phase 6 ok » + colle ce que `curl` a répondu.

---

## PHASE 7 — DOUBLE-RUN (valider AVANT de basculer — on ne coupe RIEN)
Le VPS tourne en parallèle du PC, SANS toucher la façade `betsfix.com`. On compare :
```bash
curl -s localhost:8000/health/sources | grep -o 'apifootball[^}]*ok[^,]*'   # apifootball ok:true ?
# lance un scan de test à la main :
cd /opt/betsfix && . .venv/bin/activate && python tools/generate_analyses.py --sport foot --top 3 --hours 24 --programme --no-notify --ko-from 6 --ko-to 21
```
→ colle-moi le résultat : je vérifie que les picks/sources sont identiques au PC. **On ne bascule que si c'est bon.**
Laisse tourner ~24-48 h (vérifier que l'auth Claude tient, que les scans cron passent).

---

## PHASE 8 — Bascule (couper le PC de la boucle)
La façade `betsfix.com` passe par un **tunnel Cloudflare**. Le plus simple = déplacer ce tunnel sur le VPS.
1. Cloudflare **Zero Trust** → Networks → Tunnels → ton tunnel BETSFIX → **Configure / install connector** → copie
   la commande `cloudflared service install <TOKEN>`.
2. Sur le VPS :
   ```bash
   # installe cloudflared
   curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cf
   sudo mv /tmp/cf /usr/local/bin/cloudflared && sudo chmod +x /usr/local/bin/cloudflared
   sudo cloudflared service install <TOKEN>     # LE MÊME tunnel -> origin = ce VPS
   ```
3. **Sur le PC** : arrête le tunnel PC pour ne pas avoir 2 origines : `Stop-Service Cloudflared` (PowerShell admin).
4. (optionnel) remote control sur le VPS pour me piloter PC éteint : `sudo cp /opt/betsfix/deploy/vps/betsfix-remote.service /etc/systemd/system/ && sudo systemctl enable --now betsfix-remote`.
5. Après 24-48 h VPS stable : désactive les tâches planifiées de scan du PC.

**Rollback** à tout moment : redémarre le tunnel du PC (`Start-Service Cloudflared`) et arrête celui du VPS.

---

## Dépannage (colle-moi l'erreur, je réponds)
- `curl localhost:8000/health` ne répond pas → `sudo systemctl status betsfix-api` + `journalctl -u betsfix-api -n 50`.
- `chrome --version` vide → `sudo apt install -y chromium-browser` + `sudo ln -sf $(command -v chromium-browser) /usr/local/bin/chrome`.
- `claude` demande sans cesse de se reconnecter → l'auth headless ne tient pas ; on avisera (repli clé API).
- Scan cron ne part pas → `grep CRON /var/log/syslog` + vérifie les chemins dans `crontab -e`.
