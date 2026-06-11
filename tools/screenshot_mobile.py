"""Screenshot MOBILE fiable d'une page de l'app, via CDP Emulation (pas --window-size).

Pourquoi : `chrome --headless --screenshot --window-size=390,...` rogne à droite sous Windows
en DPI 125 % (piège connu). La voie fiable est CDP `Emulation.setDeviceMetricsOverride`
(390×844 @2x, mobile=true) puis `Page.captureScreenshot`.

Usage :
    python tools/screenshot_mobile.py [url] [sortie.png] [--wait 2.5]
    (défauts : http://127.0.0.1:8000/  ->  data/screenshot_mobile.png)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)


def _chrome() -> str:
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if not found:
        raise SystemExit("Chrome introuvable")
    return found


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def shoot(url: str, out: str, wait: float = 2.5) -> None:
    import websockets

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="shot_cdp_")
    proc = subprocess.Popen([_chrome(), "--headless=new", "--disable-gpu",
                             f"--remote-debugging-port={port}",
                             f"--user-data-dir={profile}", "--no-first-run"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(50):
            try:
                data = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2))
                for t in data:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
            except Exception:
                pass
            if ws_url:
                break
            await asyncio.sleep(0.3)
        if not ws_url:
            raise SystemExit("CDP : aucun onglet exposé")
        async with websockets.connect(ws_url, max_size=80_000_000) as ws:
            mid = 0

            async def cmd(method, params=None):
                nonlocal mid
                mid += 1
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == mid:
                        return m

            await cmd("Emulation.setDeviceMetricsOverride",
                      {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
            await cmd("Emulation.setTouchEmulationEnabled", {"enabled": True})
            await cmd("Page.enable")
            await cmd("Page.navigate", {"url": url})
            await asyncio.sleep(wait)   # rendu + polices + animations d'entrée
            r = await cmd("Page.captureScreenshot", {"format": "png"})
            png = base64.b64decode(r["result"]["data"])
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            with open(out, "wb") as f:
                f.write(png)
            print(f"OK -> {out} ({len(png) // 1024} Ko)")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="http://127.0.0.1:8000/")
    ap.add_argument("out", nargs="?", default=os.path.join("data", "screenshot_mobile.png"))
    ap.add_argument("--wait", type=float, default=2.5)
    a = ap.parse_args()
    asyncio.run(shoot(a.url, a.out, a.wait))
