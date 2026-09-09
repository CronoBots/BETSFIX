# Déploiement VPS — runbook

Scripts prêts pour porter le moteur BETSFIX sur un VPS Linux (cf. `docs/VPS_MIGRATION.md`). Ordre :

## 0. Prérequis (décisions user)
- VPS Ubuntu 22.04+ (~5 €/mo), **clé API-Football RÉGÉNÉRÉE**, abonnement **Claude Pro Max** (pour `claude -p`).
- Node installé si le CLI `claude` n'est pas déjà là (`apt install nodejs npm`).

## 1. Provisionner
```bash
sudo useradd -m -s /bin/bash betsfix && sudo su - betsfix
git clone <repo> /opt/betsfix && cd /opt/betsfix
bash deploy/vps/setup.sh          # paquets + chromium + venv + pip + vérifs
nano .env                          # secrets (clé API-Football régénérée, SMTP, Stripe, KV…)
```

## 2. Authentifier Claude (les DEUX sessions partagent cette auth)
```bash
claude login                       # flux device : ouvre l'URL, connecte ton compte Pro Max. Persiste dans ~/.claude
claude -p "dis OK" --model sonnet  # test headless : doit répondre sans navigateur
```
> `claude -p` (analyses du scan) ET `claude --remote-control` (toi qui me pilotes) utilisent CE login. Pas de clé
> API payée au token : c'est l'abonnement. ⚠️ Vérifier au double-run que l'auth **persiste** en headless (sinon relogin).

## 3. Services (systemd)
```bash
sudo cp deploy/vps/betsfix-api.service /etc/systemd/system/      # API uvicorn (relance auto)
sudo systemctl enable --now betsfix-api
sudo cp deploy/vps/betsfix-remote.service /etc/systemd/system/   # OPTIONNEL : remote control (me piloter tel, PC éteint)
sudo systemctl enable --now betsfix-remote
```

## 4. Cron (scans + reconcile + KV)
```bash
crontab -e   # coller deploy/vps/crontab.txt (adapter /opt/betsfix). Vérifier : timedatectl (Europe/Brussels)
```

## 5. Façade publique — 2 options
- **(A) LE PLUS SIMPLE = cloudflared SUR le VPS** : installe `cloudflared`, rattache le MÊME tunnel (origin →
  `localhost:8000`). Résultat : la façade `betsfix.com` marche **sans exposer de port**, TLS géré par Cloudflare,
  **et le PC n'est plus dans la boucle** (le vrai but). On n'a PAS à toucher le DNS. ← recommandé pour le cut-over.
- **(B) COUPER le tunnel complètement** : reverse-proxy nginx/caddy (443 → 127.0.0.1:8000, certif Cloudflare origin)
  + basculer le DNS `betsfix.com`/`www` (A record) vers l'IP du VPS (Cloudflare proxied). Étendre le worker
  failover `betsfix-failover` à `betsfix.com`.

## 6. Cut-over sûr (protéger le phare)
1. **DOUBLE-RUN** : VPS en parallèle du PC (dossier `data/` SÉPARÉ, PAS de bascule façade). Comparer un scan complet
   (picks identiques ?), `/health/sources`, le règlement, + que l'auth Claude tient 24-48 h.
2. Basculer la façade (A ou B) quand tout concorde.
3. Couper l'ancien moteur PC (service Cloudflared PC + tâches) après 24-48 h VPS stables.
4. **Rollback** = re-pointer la façade vers le PC (garder le PC prêt jusqu'à J+2).

## Vérifs
```bash
systemctl status betsfix-api betsfix-remote
curl -s localhost:8000/health
tail -f data/scan_cron.log data/reconcile_cron.log
```

## ⚠️ Pièges
- **Chromium** obligatoire (cartes Telegram + repli sofa) : `chrome --version` doit répondre.
- **iProyal RETIRÉ** : `sofa_proxy` vide OK ; aucune dépendance proxy résidentiel (c'est ce qui débloque le VPS).
- **Chicken-egg** : si `betsfix-remote` (moi) casse le VPS, on rentre par **SSH** — garde toujours cet accès.
- **Secrets** : `.env` jamais commité ; clé API-Football régénérée (l'ancienne a fuité en chat).
