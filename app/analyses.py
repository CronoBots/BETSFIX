"""Analyses « analyste » pré-générées (par tools/generate_analyses.py via Claude headless).

Chargement depuis data/analyses/{sport}_{id}.md (id = clé du store = id Unibet) + rendu
markdown -> HTML minimal (titres, gras, listes, tableaux, citations, liens) pour l'affichage
en fiche match. Aucune dépendance externe.
"""

from __future__ import annotations

import html
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(_ROOT, "data", "analyses")

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LIST = re.compile(r"^\s*([-*]|\d+[.)])\s+")
_BLOCK = re.compile(r"^(#{1,6}\s|\s*[-*]\s|\s*\d+[.)]\s|>|\|)")


def load(sport: str, match_id) -> str | None:
    """Markdown de l'analyse pour ce match (None si absente)."""
    if match_id is None:
        return None
    try:
        with open(os.path.join(DIR, f"{sport}_{match_id}.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _inline(s: str) -> str:
    s = html.escape(s)
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def _table(rows: list) -> str:
    """`rows` = lignes « | a | b | » ; la 2e ligne est le séparateur |---| (ignoré)."""
    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]
    if len(rows) < 2:
        return ""
    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    th = "".join(f"<th>{_inline(c)}</th>" for c in head)
    trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f'<table class="da-tbl"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def to_html(md: str) -> str:
    """Markdown -> HTML pour le sous-ensemble produit par les analyses Claude."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)          # vire l'en-tête commentaire
    md = re.sub(r"^---+\s*$", "", md, flags=re.M)            # séparateurs ---
    out, i, lines = [], 0, md.splitlines()
    n = len(lines)
    while i < n:
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        if ln.lstrip().startswith("|"):                     # tableau
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.append(_table(rows))
            continue
        m = re.match(r"(#{1,6})\s+(.*)", ln)                 # titre
        if m:
            lvl = min(len(m.group(1)), 3)
            out.append(f'<div class="da-h da-h{lvl}">{_inline(m.group(2))}</div>')
            i += 1
            continue
        if ln.lstrip().startswith(">"):                     # citation
            out.append(f'<div class="da-quote">{_inline(ln.lstrip("> ").strip())}</div>')
            i += 1
            continue
        if _LIST.match(ln):                                 # liste
            items = []
            while i < n and _LIST.match(lines[i]):
                items.append("<li>" + _inline(_LIST.sub("", lines[i]).rstrip()) + "</li>")
                i += 1
            out.append("<ul class='da-ul'>" + "".join(items) + "</ul>")
            continue
        para = [ln]                                         # paragraphe
        i += 1
        while i < n and lines[i].strip() and not _BLOCK.match(lines[i]):
            para.append(lines[i].rstrip())
            i += 1
        out.append("<p class='da-p'>" + _inline(" ".join(para)) + "</p>")
    return '<div class="da">' + "".join(out) + "</div>"


def render(sport: str, match_id) -> str | None:
    """HTML prêt à afficher de l'analyse de ce match, ou None si pas d'analyse."""
    md = load(sport, match_id)
    return to_html(md) if md else None
