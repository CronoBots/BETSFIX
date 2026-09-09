# Migration hors-PC → VPS — plan actionnable

> **But** : sortir le MOTEUR (scan + API + règlement) du PC vers un VPS à IP publique, et **couper le
> tunnel Cloudflare**. Débloqué par la migration API-Football (2026-09-09) : **plus aucun scraping via proxy
> résidentiel iProyal** → le moteur n'a plus besoin d'une IP « maison ». État : prêt côté données/code.

## 1. Ce qui clouait au PC — et qui est LEVÉ
| Verrou | Avant | Maintenant |
|---|---|---|
| Scraping bloqué en IP datacenter | Pinnacle/SofaScore via proxy résidentiel iProyal | **API REST API-Football** (IP publique OK) — iProyal RETIRÉ |
| Cotes / ancre sharp / règlement / enrichissement / live | scraping | **API-Football** (4 chaînes + live) |
| `claude -p` (scan) authentifié interactif | session claude.ai locale | **clé API Anthropic** (portable, à poser en env sur le VPS) |

**Seul reste PC-spécifique** : les **tâches planifiées Windows** (→ cron/systemd) et la **session Remote Control**
(optionnelle sur VPS ; le scan tourne en `claude -p` via clé API, sans session interactive).

## 2. Ce qui est 100 % portable (suit le code/les données)
API uvicorn · scans (`generate_analyses`) · règlement/reconcile · Telegram · Stripe · comptes (SQLite→Supabase) ·
stats/ROI · site. Tout lit `.env` + `data/`. Rien n'exige Windows.

## 3. Décisions à prendre (TON input)
1. **VPS** : ~5 €/mo (Hetzner CX22 / OVH / Scaleway), Ubuntu 22.04, 2 vCPU / 4 Go. Région EU (proche Cloudflare/Brevo).
2. **Clé API Anthropic** pour `claude -p` headless (le scan pilote Claude) — à générer (console Anthropic), posée en env VPS. ⚠️ coût tokens = comme aujourd'hui.
3. **Clé API-Football** : REGÉNÉRER (a fuité en chat) + poser dans `.env` VPS.
4. **DNS/façade** : `betsfix.com` pointera vers l'IP du VPS (A record Cloudflare, proxied) → **on coupe le tunnel** (`Cloudflared` service). Le worker failover KV reste devant en filet.
5. **Comptes** : activer Supabase (miroir `public.users`) OU garder SQLite sur le VPS (simple au début).

## 4. Séquence de cut-over (staged, réversible)
1. **Provisionner** le VPS (Ubuntu, Python 3.12, git clone, `pip install`, `.env` avec clés régénérées).
2. **DOUBLE-RUN** : lancer l'API + un scan sur le VPS EN PARALLÈLE du PC (ne pas encore basculer le DNS).
   Comparer `/health/sources`, un scan complet (picks identiques ?), le règlement. Le VPS n'écrit PAS sur le
   `data/` du PC (dossiers séparés) → aucune collision.
3. **Tâches** : porter les tâches Windows en **cron** (ou systemd timers) : scan jour ~10h, scan soir ~19h,
   vagues KO-1h, reconcile, snapshot KV. Fuseau Europe/Brussels.
4. **Bascule DNS** : `betsfix.com`/`www` → IP VPS (Cloudflare proxied). Vérifier TLS (réémission certif ~≤1 h).
   Étendre le worker failover `betsfix-failover` à `betsfix.com` (aujourd'hui devant `api.betsfix.com` seul).
5. **Couper le tunnel** : désactiver le service `Cloudflared` du PC une fois le VPS stable 24-48 h.
6. **Éteindre le moteur PC** (garder le repo comme sauvegarde/dev).

## 5. Garde-fous
- **Ne PAS basculer le DNS avant** que le double-run confirme picks/règlement identiques (protéger le phare).
- **Secrets** : `.env` uniquement sur le VPS (jamais commité) ; clés régénérées.
- **Filet** : worker KV failover devant → si le VPS tombe, snapshot servi (comme aujourd'hui pour le PC).
- **Rollback** : re-pointer le DNS vers le tunnel PC (le service Cloudflared reste installé jusqu'à J+2).

## 6. Reste optionnel (post-VPS)
- Retirer physiquement le code mort Understat/Flashscore (gardé gaté aujourd'hui).
- `/odds/live` (ancre sharp collée au KO) · value screener (couverture élargie) · détecteur live.
- Migration comptes SQLite → Supabase (free tier se met en pause si inactif → à activer au déménagement).
