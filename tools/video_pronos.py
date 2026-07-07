"""Génère une VIDÉO YouTube (MP4 16:9) des pronos du jour, avec VOIX qui commente chaque analyse.

Pipeline 100 % local (aucun upload) :
  1. Collecte les pronos RETENUS du dernier scan (mêmes sidecars que Telegram, via app.card_data).
  2. Rend chaque prono en CARTE brandée (réutilise tools/card_image → Chrome/CDP).
  3. Compose une image 1920×1080 par prono (fond flouté de la carte + carte nette + en-tête/pied).
  4. Synthétise la NARRATION française (edge-tts, voix neuronale) : intro + analyse de chaque pari + outro.
  5. Assemble le tout avec ffmpeg (une séquence par carte, durée = longueur de sa narration) → un seul MP4.

Sortie : data/videos/pronos_AAAA-MM-JJ.mp4 (+ vignette .jpg). Aucune publication : TU l'uploades.

Prérequis (installés une fois) : `pip install edge-tts` et ffmpeg (winget install Gyan.FFmpeg).

Usage :
    python tools/video_pronos.py                     # pronos des dernières 24 h
    python tools/video_pronos.py --hours 36
    python tools/video_pronos.py --ids a,b,c         # ids de match précis (ordre conservé)
    python tools/video_pronos.py --voice fr-FR-DeniseNeural --rate +8%
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import card_image  # noqa: E402
from app import analyses  # noqa: E402
from app import card_data as _cd  # noqa: E402  (POINT UNIQUE de construction des cartes)

VOICE_DEFAULT = "fr-FR-HenriNeural"     # voix masculine FR posée ; alt : fr-FR-DeniseNeural (féminine)
RATE_DEFAULT = "+6%"                     # léger tempo « présentateur »
W, H = 1920, 1080                        # 16:9 Full HD
BG_TOP, BG_BOT = (16, 27, 41), (5, 8, 13)   # dégradé du fond (assorti aux cartes)
PAD_TAIL = 0.7                           # silence ajouté après chaque narration (respiration)
FONTS = (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf")
FONTS_REG = (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf")
_SPORT_NOM = {"⚽": "Football", "🎾": "Tennis", "🏀": "Basket"}


# ───────────────────────────── binaires ffmpeg / ffprobe ─────────────────────────────
def _resolve_bin(name: str) -> str:
    """Trouve ffmpeg/ffprobe : PATH -> shim WinGet Links -> dossier Packages Gyan.FFmpeg."""
    p = shutil.which(name) or shutil.which(name + ".exe")
    if p:
        return p
    links = os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Links\{name}.exe")
    if os.path.exists(links):
        return links
    g = glob.glob(os.path.expandvars(
        rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\{name}.exe"), recursive=True)
    if g:
        return g[0]
    raise RuntimeError(f"{name} introuvable — installe-le : winget install Gyan.FFmpeg")


FFMPEG = ffprobe = None                   # résolus dans main() (message clair si absent)


def _dur(mp3: str) -> float:
    """Durée d'un fichier audio (s) via ffprobe. 3.0 s par défaut si illisible."""
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nk=1:nw=1", mp3], capture_output=True, text=True, timeout=30)
        return max(0.8, float(out.stdout.strip()))
    except Exception:
        return 3.0


# ───────────────────────────── polices (PIL) ─────────────────────────────
def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    for f in (FONTS if bold else FONTS_REG):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ───────────────────────────── narration (texte FR) ─────────────────────────────
def _spoken_match(match: str) -> str:
    return re.sub(r"\s*[—-]\s*", " contre ", match).replace(" (F)", "")


def _spoken_odds(cote: str) -> str:
    return str(cote).replace(".", ",")            # « 1,64 » -> le TTS FR dit « un virgule … »


def _clean_speech(t: str) -> str:
    """Nettoie un texte pour la voix : retire emoji/puces, normalise les espaces."""
    t = re.sub(r"[•·✅❌➖⚽🎾🏀→↳]", " ", str(t or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if t and t[-1] not in ".!?":
        t += "."
    return t


def _sport_of(card: dict) -> str:
    cat = str(card.get("cat", ""))
    if " · " in cat:
        return cat.split(" · ", 1)[0]
    return _SPORT_NOM.get(card.get("emoji", ""), "")


def _compet_of(card: dict) -> str:
    cat = str(card.get("cat", ""))
    return cat.split(" · ", 1)[1] if " · " in cat else ""


def _narration(card: dict, i: int, n: int) -> str:
    """Script parlé d'un prono : annonce + analyse (le cœur de la demande = commenter l'analyse)."""
    sport, compet = _sport_of(card), _compet_of(card)
    match = _spoken_match(card.get("match", ""))
    head = f"Pari numéro {i}. {sport}." + (f" {compet}." if compet else "")
    parts = [head, f"{match}."]
    if card.get("type") == "combo":
        legs = card.get("legs", [])
        parts.append(f"Un combiné de {len(legs)} sélections, pour une cote totale de "
                     f"{_spoken_odds(card.get('cote', ''))}.")
        if card.get("synth"):
            parts.append(_clean_speech(card["synth"]))
        for k, leg in enumerate(legs, 1):
            mkt, pk = leg[0], leg[1]
            why = leg[3] if len(leg) > 3 else ""
            sel = f"{mkt} : {pk}" if mkt else pk
            parts.append(f"Sélection {k} : {_clean_speech(sel)}")
            if why:
                parts.append(_clean_speech(why))
    else:
        mkt, pk = card.get("market", ""), card.get("pick", "")
        sel = f"{mkt} : {pk}" if mkt else pk
        parts.append(f"Notre sélection : {_clean_speech(sel)}")
        if card.get("cote"):
            parts.append(f"À la cote de {_spoken_odds(card['cote'])}.")
        if card.get("conf"):
            parts.append(f"Confiance estimée : {card['conf']} pour cent.")
        if card.get("why"):
            parts.append(_clean_speech(card["why"]))
    return " ".join(parts)


def _intro_text(n: int, date_fr: str) -> str:
    return (f"Bonjour et bienvenue sur BETSFIX. Voici nos pronostics analysés du {date_fr}. "
            f"{n} pari{'s' if n > 1 else ''} au programme aujourd'hui, chacun passé au crible de nos données. "
            f"C'est parti.")


def _outro_text() -> str:
    return ("Voilà pour les pronostics du jour. Retrouvez les analyses complètes et les résultats en direct "
            "sur BETSFIX. Pensez à jouer de manière responsable, avec modération. À très vite pour de "
            "nouveaux pronostics.")


# ───────────────────────────── TTS ─────────────────────────────
async def _tts_async(text: str, out_mp3: str, voice: str, rate: str):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(out_mp3)


def _tts(text: str, out_mp3: str, voice: str, rate: str):
    asyncio.run(_tts_async(text, out_mp3, voice, rate))


# ───────────────────────────── images 1920×1080 (PIL) ─────────────────────────────
def _gradient_bg():
    from PIL import Image
    bg = Image.new("RGB", (W, H))
    px = bg.load()
    for y in range(H):
        t = y / (H - 1)
        r = round(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        for x in range(0, W, 4):                  # pas de 4 px : dégradé assez fin, 4× plus rapide
            px[x, y] = px[x + 1, y] = px[x + 2, y] = px[x + 3, y] = (r, g, b)
    return bg


def _blurred_cover(card_img):
    """Fond plein cadre : la carte agrandie pour COUVRIR 1920×1080, floutée et assombrie (rendu premium)."""
    from PIL import Image, ImageFilter, ImageEnhance
    iw, ih = card_img.size
    scale = max(W / iw, H / ih)
    rc = card_img.resize((round(iw * scale), round(ih * scale)), Image.LANCZOS)
    left, top = (rc.width - W) // 2, (rc.height - H) // 2
    cover = rc.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(46))
    return ImageEnhance.Brightness(cover).enhance(0.38)


def _draw_text(draw, xy, text, font, fill, anchor="la", spacing=0):
    if spacing and len(text) > 1:                 # letter-spacing manuel (Pillow n'a pas de tracking)
        x, y = xy
        if anchor.startswith("r"):
            total = sum(draw.textlength(c, font=font) + spacing for c in text) - spacing
            x -= total
        for c in text:
            draw.text((x, y), c, font=font, fill=fill)
            x += draw.textlength(c, font=font) + spacing
    else:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _frame_prono(card_png: str, i: int, n: int, date_fr: str, out_png: str):
    """Compose la scène d'un prono : fond flouté + carte nette centrée + « PRONO i/N », date, marque."""
    from PIL import Image
    card = Image.open(card_png).convert("RGB")
    frame = _blurred_cover(card)
    # carte nette, hauteur ≈ 96 % de l'écran, centrée
    th = int(H * 0.955)
    tw = round(card.width * th / card.height)
    sharp = card.resize((tw, th), Image.LANCZOS)
    frame.paste(sharp, ((W - tw) // 2, (H - th) // 2))
    from PIL import ImageDraw
    d = ImageDraw.Draw(frame)
    _draw_text(d, (60, 46), f"PRONO {i} / {n}", _font(44), (95, 208, 255), spacing=2)
    _draw_text(d, (W - 60, 52), date_fr, _font(34, bold=False), (150, 176, 208), anchor="ra")
    _draw_text(d, (W - 60, H - 74), "BETSFIX", _font(34), (255, 255, 255, 60), anchor="ra", spacing=6)
    frame.save(out_png)


def _frame_title(title: str, subtitle: str, out_png: str, accent=(95, 208, 255)):
    """Écran intro/outro : dégradé + wordmark BETSFIX + titre + sous-titre, centrés."""
    from PIL import Image, ImageDraw
    frame = _gradient_bg()
    d = ImageDraw.Draw(frame)
    cy = H // 2
    wm_path = os.path.join(ROOT, "static", "wordmark.png")
    if os.path.exists(wm_path):
        wm = Image.open(wm_path).convert("RGBA")
        ww = 620
        wm = wm.resize((ww, round(wm.height * ww / wm.width)), Image.LANCZOS)
        frame.paste(wm, ((W - ww) // 2, cy - wm.height - 40), wm)
    _draw_text(d, (W // 2, cy + 30), title, _font(76), (240, 247, 251), anchor="ma")
    if subtitle:
        _draw_text(d, (W // 2, cy + 140), subtitle, _font(40, bold=False), accent, anchor="ma")
    frame.save(out_png)


# ───────────────────────────── ffmpeg : segments + concat ─────────────────────────────
def _segment(frame_png: str, mp3: str, out_mp4: str, fade_in=False, fade_out=False):
    """Une image fixe + sa narration -> un segment MP4 (image tenue pour toute la durée de l'audio)."""
    dur = _dur(mp3) + PAD_TAIL
    vf = ["scale=1920:1080:flags=lanczos", "format=yuv420p"]
    if fade_in:
        vf.insert(0, "fade=t=in:st=0:d=0.5")
    if fade_out:
        vf.append(f"fade=t=out:st={max(0, dur - 0.6):.2f}:d=0.6")
    cmd = [FFMPEG, "-y", "-loop", "1", "-i", frame_png, "-i", mp3,
           "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-r", "30",
           "-pix_fmt", "yuv420p", "-vf", ",".join(vf),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-af", f"apad=pad_dur={PAD_TAIL}", "-t", f"{dur:.2f}", out_mp4]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dur


def _concat(segments: list, out_mp4: str, workdir: str):
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"file '{s.replace(os.sep, '/')}'\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", out_mp4], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ───────────────────────────── collecte des pronos du jour ─────────────────────────────
def _collect(hours: float, ids: str) -> list:
    """LES PARIS DU JOUR = pronos À VENIR (pas encore joués), dans la fenêtre du scan (prochaines `hours`),
    exactement comme le board « À venir » / la publication Telegram. ⚠️ On NE filtre PAS sur le mtime du
    sidecar : le règlement le touche en permanence -> ça ramassait des matchs DÉJÀ JOUÉS de la semaine.
    On filtre sur le COUP D'ENVOI : `status_of == 'notstarted'` ET start ≤ maintenant + `hours`."""
    now = datetime.now(timezone.utc)
    sides = []
    if ids:                                        # sélection EXPLICITE : pas de filtre temporel
        wanted = [s.strip() for s in ids.split(",") if s.strip()]
        for f in glob.glob(os.path.join(analyses.DIR, "*.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(d.get("id")) in wanted:
                sides.append((wanted.index(str(d.get("id"))), d))
        sides = [d for _, d in sorted(sides, key=lambda x: x[0])]
    else:
        horizon = now + timedelta(hours=hours)
        for f in glob.glob(os.path.join(analyses.DIR, "*.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if analyses.status_of(d) != "notstarted":     # à venir seulement (exclut en cours / terminés)
                continue
            dt = _cd._dt(d)
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if now <= dt <= horizon:                       # dans la fenêtre du scan (paris DU JOUR)
                sides.append(d)
    sides.sort(key=lambda d: d.get("start") or "")
    seen = {}
    for d in sides:
        key = re.sub(r"[^a-z0-9]+", " ", str(d.get("name", "")).lower()).strip()
        seen[key] = d                             # garde le plus récent par match
    cards = []
    for d in sorted(seen.values(), key=lambda d: d.get("start") or ""):
        c = _cd.build_prono_card(d)               # None = calibration seule -> ignoré
        if c:
            cards.append(c)
    return cards


def main() -> int:
    global FFMPEG, ffprobe
    ap = argparse.ArgumentParser(description="Vidéo YouTube des pronos du jour (voix + analyses).")
    ap.add_argument("--hours", type=float, default=24.0,
                    help="fenêtre des matchs À VENIR : coup d'envoi dans les N prochaines heures (défaut 24)")
    ap.add_argument("--ids", default="", help="ids de match précis, séparés par des virgules")
    ap.add_argument("--voice", default=VOICE_DEFAULT, help="voix edge-tts (défaut fr-FR-HenriNeural)")
    ap.add_argument("--rate", default=RATE_DEFAULT, help="tempo de la voix (ex. +6%%)")
    ap.add_argument("--out", default="", help="chemin MP4 de sortie (défaut data/videos/pronos_DATE.mp4)")
    args = ap.parse_args()

    try:
        FFMPEG, ffprobe = _resolve_bin("ffmpeg"), _resolve_bin("ffprobe")
    except RuntimeError as e:
        print(f"✗ {e}")
        return 2
    try:                                  # pré-check edge-tts (comme ffmpeg) : message clair plutôt qu'un
        import edge_tts  # noqa: F401     # traceback en pleine génération avec un dossier de travail partiel.
    except ImportError:
        print("✗ edge-tts introuvable — installe-le : pip install edge-tts")
        return 2

    cards = _collect(args.hours, args.ids)
    if not cards:
        print("Aucun prono retenu à mettre en vidéo (rien de récent).")
        return 0

    date_fr = _cd.fr_date(datetime.now())
    day = datetime.now().strftime("%Y-%m-%d")
    out_mp4 = args.out or os.path.join(ROOT, "data", "videos", f"pronos_{day}.mp4")
    os.makedirs(os.path.dirname(out_mp4), exist_ok=True)
    work = os.path.join(ROOT, "data", "_video_work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    print(f"{len(cards)} prono(s) -> vidéo. Voix {args.voice} {args.rate}.")
    segments = []

    # 1) INTRO
    print("  · intro…")
    intro_png = os.path.join(work, "intro.png")
    _frame_title("Pronostics du jour", date_fr, intro_png)
    intro_mp3 = os.path.join(work, "intro.mp3")
    _tts(_intro_text(len(cards), date_fr), intro_mp3, args.voice, args.rate)
    segments.append(os.path.join(work, "seg_00_intro.mp4"))
    _segment(intro_png, intro_mp3, segments[-1], fade_in=True)

    # 2) PRONOS
    thumb = None
    for idx, card in enumerate(cards, 1):
        print(f"  · prono {idx}/{len(cards)} : {card.get('match','')}")
        card_png = os.path.join(work, f"card_{idx}.png")
        card_image.render_card_sync(card, card_png)
        frame_png = os.path.join(work, f"frame_{idx}.png")
        _frame_prono(card_png, idx, len(cards), date_fr, frame_png)
        if thumb is None:
            thumb = frame_png                     # 1re carte = vignette
        mp3 = os.path.join(work, f"narr_{idx}.mp3")
        _tts(_narration(card, idx, len(cards)), mp3, args.voice, args.rate)
        seg = os.path.join(work, f"seg_{idx:02d}.mp4")
        _segment(frame_png, mp3, seg)
        segments.append(seg)

    # 3) OUTRO
    print("  · outro…")
    outro_png = os.path.join(work, "outro.png")
    _frame_title("Jouez responsable", "BETSFIX", outro_png, accent=(159, 231, 192))
    outro_mp3 = os.path.join(work, "outro.mp3")
    _tts(_outro_text(), outro_mp3, args.voice, args.rate)
    seg = os.path.join(work, "seg_99_outro.mp4")
    _segment(outro_png, outro_mp3, seg, fade_out=True)
    segments.append(seg)

    # 4) ASSEMBLAGE
    print("  · assemblage ffmpeg…")
    _concat(segments, out_mp4, work)

    # vignette YouTube (1re carte)
    thumb_jpg = os.path.splitext(out_mp4)[0] + ".jpg"
    if thumb:
        try:
            from PIL import Image
            Image.open(thumb).convert("RGB").save(thumb_jpg, quality=90)
        except Exception:
            thumb_jpg = None

    dur = _dur_video(out_mp4)
    size_mb = os.path.getsize(out_mp4) / 1e6
    print(f"\n✓ Vidéo prête : {out_mp4}")
    print(f"  {len(cards)} pronos · {int(dur // 60)} min {int(dur % 60):02d} s · {size_mb:.1f} Mo")
    if thumb_jpg:
        print(f"  Vignette : {thumb_jpg}")
    return 0


def _dur_video(mp4: str) -> float:
    return _dur(mp4)


if __name__ == "__main__":
    raise SystemExit(main())
