"""Lecteur SofaScore via Chrome headless + proxy IPRoyal — DERNIER RECOURS quand l'API JSON est
bloquée par un challenge Cloudflare.

Contexte : depuis 2026-06, Cloudflare sert un CHALLENGE JS sur `api.sofascore.com` (403 `reason:
challenge`) que curl_cffi ne peut pas résoudre — direct ET via proxy, et même un `fetch()` lancé
DEPUIS une page sofascore.com (same-origin) reste 403. L'IP résidentielle (IPRoyal) n'y change rien :
ce n'est pas un bannissement d'IP mais un défi navigateur.

MAIS le SITE WEB, lui, rend ses données en SSR (Next.js) : la page `/event/{id}` embarque
`__NEXT_DATA__.props.pageProps.{event, incidents}` — soit EXACTEMENT le contenu de `event/{id}` et
`event/{id}/incidents`. On pilote donc un vrai Chrome headless (sorti par le proxy résidentiel, IP
NON challengée + navigateur qui exécute le JS) via CDP, on lit le `__NEXT_DATA__`, on en extrait le
JSON. C'est LOURD (process Chrome + navigation) -> réservé au RÈGLEMENT / rafraîchissement d'un id
SofaScore DÉJÀ CONNU. La liste `scheduled-events` (résolution d'id au scan) N'EST PAS couverte : ces
pages chargent leur planning via l'API bloquée, pas en SSR.

Endpoints servis depuis UN seul chargement de page (cache court par id) :
  - `event/{id}`            -> pageProps.event
  - `event/{id}/incidents`  -> pageProps.incidents
Les autres (`/statistics`, `/point-by-point`, `/votes`) ne sont PAS en SSR -> non couverts (les paris
cartons/corners et jeu-par-jeu tennis ne se règlent donc pas par ce canal ; les scores/sets, si).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("uvicorn")

# Chrome (même binaire que les screenshots). On tente les emplacements standards puis le PATH.
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
_SITE = "https://www.sofascore.com"
_NAV_TIMEOUT = 22.0        # s max pour qu'une page rende son __NEXT_DATA__ (challenge + SSR)
_IDLE_CLOSE = 90.0         # s d'inactivité avant de fermer le Chrome (libère la RAM)
_PROPS_TTL = 120.0         # s de cache des pageProps par event id (réutilisé entre sous-endpoints)

_EVENT_RE = re.compile(r"/event/(\d+)(?:/(\w+))?(?:[/?#].*)?$")


def _chrome_path() -> str | None:
    for c in _CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return shutil.which("chrome") or shutil.which("chrome.exe") or shutil.which("chromium")


def _proxy() -> str:
    from app.config import get_settings
    return (get_settings().sofa_proxy or "").strip()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _enabled() -> bool:
    """Désactivé pendant les tests (aucun process Chrome réel) ou si Chrome est introuvable."""
    return "pytest" not in sys.modules and _chrome_path() is not None


def _purge_stale_profiles(current: str | None = None) -> None:
    """Supprime les profils `sofa_cdp_*` abandonnés dans %TEMP% (Chrome tué sans cleanup : reload
    uvicorn, crash, terminate avant déverrouillage Windows). Les profils encore tenus par un Chrome
    vivant résistent au rmtree (verrous NTFS) -> seuls les morts partent. Appelé une fois par
    process, hors event loop."""
    base = tempfile.gettempdir()
    try:
        names = os.listdir(base)
    except OSError:
        return
    n = 0
    for name in names:
        if not name.startswith("sofa_cdp_"):
            continue
        path = os.path.join(base, name)
        if current and os.path.normcase(path) == os.path.normcase(current):
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.isdir(path):
            n += 1
    if n:
        log.info("sofa_browser : %d profil(s) Chrome abandonné(s) purgé(s)", n)


class _Session:
    """Une instance Chrome headless pilotée par CDP (via websocket). Gère l'auth proxy et continue
    automatiquement les requêtes interceptées (sinon la page ne finit jamais de charger)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.ws = None
        self._reader: asyncio.Task | None = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._profile: str | None = None
        self._user = self._pass = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.ws is not None

    async def start(self) -> None:
        chrome = _chrome_path()
        if not chrome:
            raise RuntimeError("Chrome introuvable")
        import websockets  # import paresseux (dispo : dépendance déjà installée)

        port = _free_port()
        self._profile = tempfile.mkdtemp(prefix="sofa_cdp_")
        global _purged
        if not _purged:                 # ménage des profils abandonnés (1 fois par process)
            _purged = True
            await asyncio.to_thread(_purge_stale_profiles, self._profile)
        args = [chrome, "--headless", f"--remote-debugging-port={port}",
                f"--user-data-dir={self._profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions",
                "--blink-settings=imagesEnabled=false"]   # pas d'images : page plus légère/rapide
        proxy = _proxy()
        if proxy:
            u = urlparse(proxy)
            args.append(f"--proxy-server={u.hostname}:{u.port}")
            self._user, self._pass = u.username, u.password
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ws_url = await self._discover_ws(port)
        self.ws = await websockets.connect(ws_url, max_size=80_000_000, open_timeout=15)
        self._reader = asyncio.create_task(self._read_loop())
        if proxy:                       # interception requise pour répondre au challenge d'auth proxy
            await self.cmd("Fetch.enable", {"handleAuthRequests": True})
        await self.cmd("Page.enable")
        await self.cmd("Runtime.enable")
        log.info("sofa_browser : Chrome headless prêt (%s)", "proxy" if proxy else "direct")

    async def _discover_ws(self, port: int) -> str:
        loop = asyncio.get_event_loop()
        for _ in range(50):
            try:
                data = await loop.run_in_executor(
                    None, lambda: json.load(urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json", timeout=2)))
                for t in data:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except Exception:
                pass
            await asyncio.sleep(0.3)
        raise RuntimeError("CDP : aucun onglet exposé")

    async def _raw_send(self, method: str, params: dict) -> None:
        self._id += 1
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                mid = m.get("id")
                if mid in self._pending:
                    self._pending.pop(mid).set_result(m)
                    continue
                meth = m.get("method")
                if meth == "Fetch.authRequired":
                    await self._raw_send("Fetch.continueWithAuth", {
                        "requestId": m["params"]["requestId"],
                        "authChallengeResponse": {"response": "ProvideCredentials",
                                                  "username": self._user or "",
                                                  "password": self._pass or ""}})
                elif meth == "Fetch.requestPaused":
                    await self._raw_send("Fetch.continueRequest",
                                         {"requestId": m["params"]["requestId"]})
        except Exception:
            pass

    async def cmd(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(mid, None)

    async def event_props(self, event_id: int) -> dict | None:
        """Charge `/event/{id}` et renvoie `pageProps` (event + incidents…) une fois le bon event
        rendu dans __NEXT_DATA__. None si la page n'aboutit pas dans le délai."""
        await self.cmd("Page.navigate", {"url": f"{_SITE}/event/{event_id}"})
        deadline = time.monotonic() + _NAV_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(0.6)
            try:
                r = await self.cmd("Runtime.evaluate", {
                    "expression": "document.getElementById('__NEXT_DATA__')?.textContent||''",
                    "returnByValue": True})
            except Exception:
                continue
            txt = (((r.get("result") or {}).get("result") or {}).get("value")) or ""
            if not txt or '"pageProps"' not in txt:
                continue
            try:
                pp = json.loads(txt).get("props", {}).get("pageProps", {})
            except ValueError:
                continue
            ev = pp.get("event")
            if isinstance(ev, dict) and str(ev.get("id")) == str(event_id):
                return pp
        return None

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:  # attendre la mort réelle : tant que Chrome vit, ses verrous NTFS
                    await asyncio.to_thread(self.proc.wait, 5)  # font échouer le rmtree en silence
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    await asyncio.to_thread(self.proc.wait, 3)
        except Exception:
            pass
        if self._profile:
            for _ in range(3):  # Windows relâche les verrous avec un léger différé
                await asyncio.to_thread(shutil.rmtree, self._profile, ignore_errors=True)
                if not os.path.isdir(self._profile):
                    break
                await asyncio.sleep(0.5)
        self.ws = self.proc = None


# --- singleton + cache --------------------------------------------------------------------------
_purged = False
_session: _Session | None = None
_lock = asyncio.Lock()
_last_used = 0.0
_reaper: asyncio.Task | None = None
_props_cache: dict[int, tuple[float, dict]] = {}


async def _reap() -> None:
    """Ferme Chrome après _IDLE_CLOSE s sans usage (hygiène mémoire)."""
    global _session
    while True:
        await asyncio.sleep(15)
        if _session is not None and time.monotonic() - _last_used > _IDLE_CLOSE:
            async with _lock:
                if _session is not None and time.monotonic() - _last_used > _IDLE_CLOSE:
                    await _session.close()
                    _session = None
                    log.info("sofa_browser : Chrome fermé (inactif)")
            return


async def fetch_event_props(event_id: int) -> dict | None:
    """pageProps de `/event/{id}` (event + incidents), via Chrome headless + proxy. Caché _PROPS_TTL s
    par id pour qu'un même match serve plusieurs sous-endpoints sans recharger. None si indispo."""
    global _session, _last_used, _reaper
    if not _enabled():
        return None
    hit = _props_cache.get(event_id)
    if hit and time.monotonic() - hit[0] < _PROPS_TTL:
        return hit[1]
    async with _lock:
        hit = _props_cache.get(event_id)
        if hit and time.monotonic() - hit[0] < _PROPS_TTL:
            return hit[1]
        try:
            if _session is None or not _session.alive():
                _session = _Session()
                await _session.start()
            _last_used = time.monotonic()
            pp = await _session.event_props(int(event_id))
        except Exception as e:
            log.info("sofa_browser : échec event %s (%s)", event_id, type(e).__name__)
            try:
                if _session:
                    await _session.close()
            except Exception:
                pass
            _session = None
            return None
        _last_used = time.monotonic()
        if _reaper is None or _reaper.done():
            _reaper = asyncio.create_task(_reap())
        if pp:
            _props_cache[event_id] = (time.monotonic(), pp)
        return pp


class _Resp:
    """Réponse minimale compatible avec ce que `sofa_http.get` renvoie (status_code/json/content)."""

    def __init__(self, data) -> None:
        self._data = data
        self.status_code = 200
        self.headers: dict = {}

    def json(self):
        return self._data

    @property
    def content(self) -> bytes:
        return json.dumps(self._data).encode()

    @property
    def text(self) -> str:
        return json.dumps(self._data)


async def response_for(url: str):
    """Si `url` est un endpoint event COUVERT (`event/{id}` ou `event/{id}/incidents`), renvoie une
    réponse SSR équivalente (via navigateur). Sinon None -> la cascade laisse tomber proprement."""
    if not _enabled():
        return None
    m = _EVENT_RE.search(url)
    if not m:
        return None
    sub = m.group(2)
    if sub not in (None, "incidents"):     # /statistics, /point-by-point, /votes : pas en SSR
        return None
    pp = await fetch_event_props(int(m.group(1)))
    if not pp:
        return None
    if sub == "incidents":
        return _Resp({"incidents": pp.get("incidents") or []})
    return _Resp({"event": pp.get("event")})


async def aclose() -> None:
    """Fermeture explicite (tests / arrêt)."""
    global _session
    async with _lock:
        if _session is not None:
            await _session.close()
            _session = None
