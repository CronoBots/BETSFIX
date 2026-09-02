# -*- coding: utf-8 -*-
"""ICÔNES SVG — remplace les emoji COULEUR par des pictos vectoriels dans tout le HTML servi.

Pourquoi (audit ui-ux-pro-max 2026-09-02, règle n°1 « No Emoji as Structural Icons ») : un emoji est
rendu par la POLICE SYSTÈME — donc différent sur iOS / Android / Windows —, il est MULTICOLORE et ne peut
pas hériter de `currentColor`, et il ne se pilote pas par les tokens de design. Sur un fond de marque ou un
état actif, il jure systématiquement.

Choix d'implémentation : substitution au NIVEAU DE LA RÉPONSE HTTP (`main._paywall_dispatch`), pas dans les
~70 chaînes sources. Un seul point de passage, qui couvre les pages ENTIÈRES **et** les fragments AJAX (que
les routeurs renvoient directement, sans passer par la coquille de page). Zéro churn dans web.py (12k
lignes), donc zéro risque de casser un littéral, et retour arrière = une ligne.

Chaque picto est un `<svg>` en **1em** (il hérite donc de la taille de police du contexte, exactement comme
faisait l'emoji) et en **`currentColor`** (il suit la couleur du texte, y compris sur un onglet actif ou un
badge coloré). `aria-hidden` : ces pictos accompagnent TOUJOURS un libellé visible -> décoratifs.

⚠️ NE PAS étendre aux glyphes TYPOGRAPHIQUES (▾ ▸ → ← ↳ ↔ ✓ ✗ ✕) : ils sont déjà monochromes, héritent de
la couleur et sont rendus par la police de texte — les convertir n'apporterait rien et casserait l'alignement.
⚠️ NE PAS appliquer aux messages TELEGRAM : là-bas l'emoji est le SEUL format disponible (pas de SVG).
"""
from __future__ import annotations

import re

# Trait commun : famille homogène (viewBox 24, épaisseur 1.8, bouts arrondis) — cf. règle « Stroke
# Consistency » / « Consistent Icon Sizing » de pro-rules.md.
_S = ('<svg class="eico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">')
# Pictos PLEINS (pastilles d'état) : le disque porte la couleur du texte -> lisible sur tout fond.
_F = ('<svg class="eico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" '
      'focusable="false">')


def _p(d: str) -> str:
    return _S + d + "</svg>"


def _dot() -> str:
    return _F + '<circle cx="12" cy="12" r="7"/></svg>'


# Emoji COULEUR -> picto. Les clés sont les caractères tels qu'écrits dans web.py (avec ou sans
# sélecteur de variante U+FE0F, géré à la compilation du motif).
ICONS: dict[str, str] = {
    # — cibles / paris —
    "🎯": _p('<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3.4"/>'),
    "⭐": _p('<path d="m12 3.6 2.6 5.3 5.8.85-4.2 4.1 1 5.8-5.2-2.73-5.2 2.73 1-5.8-4.2-4.1 5.8-.85z"/>'),
    "💎": _p('<path d="M6 4h12l3.2 5-9.2 11L2.8 9z"/><path d="M2.8 9h18.4M9 4l-2 5 5 11 5-11-2-5"/>'),
    "🎲": _p('<rect x="4" y="4" width="16" height="16" rx="3.4"/><circle cx="9" cy="9" r="1.15" fill="currentColor" stroke="none"/><circle cx="15" cy="15" r="1.15" fill="currentColor" stroke="none"/>'),
    # — statistiques —
    "📊": _p('<path d="M4 20h16"/><path d="M7.6 20v-4.6"/><path d="M12 20v-8.6"/><path d="M16.4 20V7.4"/>'),
    "📈": _p('<path d="M4 16.4 9.4 11l3.4 3.4L20 7.2"/><path d="M15.4 7.2H20v4.6"/>'),
    "📉": _p('<path d="M4 7.6 9.4 13l3.4-3.4L20 16.8"/><path d="M15.4 16.8H20v-4.6"/>'),
    "🔬": _p('<path d="M9.5 4.5h3.2l1.6 7.4H7.9z"/><path d="M6.4 19.5h11.2"/><path d="M8.6 19.5c0-2.6 1.5-4.4 3.4-4.4s3.4 1.8 3.4 4.4"/>'),
    "🧪": _p('<path d="M10 3.2v6.1L5.6 17a2.4 2.4 0 0 0 2.1 3.6h8.6a2.4 2.4 0 0 0 2.1-3.6L14 9.3V3.2"/><path d="M8.8 3.2h6.4M7.6 14.4h8.8"/>'),
    "🧠": _p('<path d="M12 5.2v13.6"/><path d="M12 6.4A3.2 3.2 0 0 0 6.2 8a3 3 0 0 0-1 5.3A3.1 3.1 0 0 0 8.4 19a3.1 3.1 0 0 0 3.6-1.4"/><path d="M12 6.4A3.2 3.2 0 0 1 17.8 8a3 3 0 0 1 1 5.3A3.1 3.1 0 0 1 15.6 19a3.1 3.1 0 0 1-3.6-1.4"/>'),
    "🔎": _p('<circle cx="10.8" cy="10.8" r="6.2"/><path d="m15.4 15.4 4.2 4.2"/>'),
    # — temps / calendrier —
    "🗓": _p('<rect x="3.6" y="5.4" width="16.8" height="15" rx="2.6"/><path d="M8.2 3.4v4M15.8 3.4v4M3.6 10.4h16.8"/>'),
    "📅": _p('<rect x="3.6" y="5.4" width="16.8" height="15" rx="2.6"/><path d="M8.2 3.4v4M15.8 3.4v4M3.6 10.4h16.8"/>'),
    "⏳": _p('<path d="M7 3.4h10M7 20.6h10"/><path d="M8 3.4c0 4 4 4.9 4 8.6 0 3.7-4 4.6-4 8.6"/><path d="M16 3.4c0 4-4 4.9-4 8.6 0 3.7 4 4.6 4 8.6"/>'),
    "⏸": _p('<path d="M9.4 5.5v13M14.6 5.5v13"/>'),
    "🔄": _p('<path d="M20 11.4a8 8 0 0 0-13.7-5"/><path d="M4 12.6a8 8 0 0 0 13.7 5"/><path d="M6.3 3.2v3.2h3.2M17.7 20.8v-3.2h-3.2"/>'),
    # — état / signalement —
    "🔥": _p('<path d="M12 3.2s4.6 3.7 4.6 8.2a4.6 4.6 0 0 1-9.2 0c0-1.5.6-2.7 1.3-3.6.5 1 1.3 1.7 2.1 1.7 1 0 1.2-1 1.2-2.3 0-1.4 0-3 0-4z"/>'),
    "❄": _p('<path d="M12 3.4v17.2M4.6 7.7l14.8 8.6M19.4 7.7 4.6 16.3"/><path d="M12 6.6 9.8 4.6M12 6.6l2.2-2M12 17.4l-2.2 2M12 17.4l2.2 2"/>'),
    "🏆": _p('<path d="M8 4.4h8v4.4a4 4 0 0 1-8 0z"/><path d="M8 5.6H5.4v1.2a3 3 0 0 0 2.9 3M16 5.6h2.6v1.2a3 3 0 0 1-2.9 3"/><path d="M12 12.8v3.4M9 19.6h6M10 16.2h4l.6 3.4H9.4z"/>'),
    "⚠": _p('<path d="M12 4.4 21 19.6H3z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".9" fill="currentColor" stroke="none"/>'),
    "🚫": _p('<circle cx="12" cy="12" r="8.4"/><path d="m6.1 6.1 11.8 11.8"/>'),
    "🔒": _p('<rect x="5" y="10.4" width="14" height="9.8" rx="2.4"/><path d="M8.2 10.4V7.9a3.8 3.8 0 0 1 7.6 0v2.5"/>'),
    "🔔": _p('<path d="M6.4 17.2h11.2l-1.3-2.2v-3.7a4.3 4.3 0 0 0-8.6 0V15z"/><path d="M10.3 19.8a1.9 1.9 0 0 0 3.4 0"/>'),
    "📌": _p('<path d="M14.6 3.4 20.6 9.4"/><path d="M15.4 4.2 9.9 6.4 5 11.3l7.7 7.7 4.9-4.9 2.2-5.5z"/><path d="m9.6 14.4-5.2 5.2"/>'),
    "💡": _p('<path d="M9.4 17.4h5.2"/><path d="M10.2 20.4h3.6"/><path d="M12 3.6a5.6 5.6 0 0 0-3.3 10.1c.5.4.8 1 .8 1.6h5a2 2 0 0 1 .8-1.6A5.6 5.6 0 0 0 12 3.6z"/>'),
    "🤝": _p('<path d="M3.4 12.6 7 9l3.4 3.1a2 2 0 0 0 2.7 0L16.9 9l3.7 3.6"/><path d="m7 9-3.6 3.6 3.4 3.4 2.2-2M17 9l3.6 3.6-3.4 3.4-2.2-2"/>'),
    "📋": _p('<rect x="5" y="4.6" width="14" height="16" rx="2.4"/><path d="M9 4.6a1.6 1.6 0 0 1 1.6-1.6h2.8A1.6 1.6 0 0 1 15 4.6v1.2H9z"/><path d="M9 11h6M9 15h4"/>'),
    "👥": _p('<circle cx="9.4" cy="9" r="3.4"/><path d="M3.6 19.6c0-2.9 2.6-4.8 5.8-4.8s5.8 1.9 5.8 4.8"/><path d="M16.2 6.1a3.4 3.4 0 0 1 0 6.5M17.4 15.2c1.9.6 3.2 2.1 3.2 4.4"/>'),
    "💰": _p('<circle cx="12" cy="13.4" r="6.8"/><path d="M12 10v6.8M10.2 11.6h3a1.5 1.5 0 0 1 0 3h-2.4a1.5 1.5 0 0 0 0 3h3"/><path d="M7.6 5.2h8.8"/>'),
    "⚽": _p('<circle cx="12" cy="12" r="8.4"/><path d="m12 7.6 3.5 2.6-1.3 4.2h-4.4L8.5 10.2z"/>'),
    "🎾": _p('<circle cx="12" cy="12" r="8.4"/><path d="M5.1 7.4A7 7 0 0 1 12 12a7 7 0 0 1-6.9 4.6M18.9 7.4A7 7 0 0 0 12 12a7 7 0 0 0 6.9 4.6"/>'),
    # — VERDICTS (trio homogène : même cercle, marque différente). En SVG ils héritent enfin du vert /
    #   rouge / gris de leur badge, là où l'emoji imposait sa propre couleur. —
    "✅": _p('<circle cx="12" cy="12" r="8.4"/><path d="m8.2 12.2 2.6 2.6 5-5.4"/>'),
    "❌": _p('<circle cx="12" cy="12" r="8.4"/><path d="m9.2 9.2 5.6 5.6M14.8 9.2l-5.6 5.6"/>'),
    "➖": _p('<circle cx="12" cy="12" r="8.4"/><path d="M8.4 12h7.2"/>'),
    "⛔": _p('<circle cx="12" cy="12" r="8.4"/><path d="M6.9 6.9 17.1 17.1"/>'),
    "👁": _p('<path d="M2.6 12S6 6.4 12 6.4 21.4 12 21.4 12 18 17.6 12 17.6 2.6 12 2.6 12z"/><circle cx="12" cy="12" r="2.9"/>'),
    "⏱": _p('<circle cx="12" cy="13.4" r="7.4"/><path d="M12 9.6v3.8l2.4 1.6M9.6 2.8h4.8"/>'),
    "🖥": _p('<rect x="3" y="4.4" width="18" height="12" rx="2.2"/><path d="M8.6 20.6h6.8M12 16.4v4.2"/>'),
    # — foot : évènements de match (cartons = rectangles, pas des pastilles) —
    "🟥": _p('<rect x="7.6" y="3.6" width="8.8" height="12.8" rx="1.6" fill="currentColor" stroke="none"/>'),
    "🟨": _p('<rect x="7.6" y="3.6" width="8.8" height="12.8" rx="1.6" fill="currentColor" stroke="none"/>'),
    "🚩": _p('<path d="M6.4 20.6V3.8"/><path d="M6.4 4.6h11l-2.4 3.8 2.4 3.8h-11z"/>'),
    "🥅": _p('<path d="M3.4 7.4h17.2v9.2H3.4z"/><path d="M7.7 7.4v9.2M12 7.4v9.2M16.3 7.4v9.2M3.4 10.5h17.2M3.4 13.6h17.2"/>'),
    "🧤": _p('<path d="M7.4 20.4v-4.2a9 9 0 0 1-1.6-5.1V7.4a1.5 1.5 0 0 1 3 0v3M9.8 10.4V5.6a1.5 1.5 0 0 1 3 0v4.8M12.8 10.4V6.4a1.5 1.5 0 0 1 3 0v4M15.8 10.6V8.4a1.5 1.5 0 0 1 3 0v3.2a9 9 0 0 1-1.8 5.4v3.4"/>'),
    "🛡": _p('<path d="M12 3.4 5 6.2v5.2c0 4.2 2.9 7.6 7 9.2 4.1-1.6 7-5 7-9.2V6.2z"/>'),
    "🔍": _p('<circle cx="10.8" cy="10.8" r="6.2"/><path d="m15.4 15.4 4.2 4.2"/>'),
    "💳": _p('<rect x="2.8" y="5.4" width="18.4" height="13.2" rx="2.4"/><path d="M2.8 9.8h18.4M6.6 14.8h3.4"/>'),
    "🩺": _p('<path d="M6 3.4v5.2a4 4 0 0 0 8 0V3.4"/><path d="M4.4 3.4h3.2M12.4 3.4h3.2"/><path d="M10 12.6v2.2a4.6 4.6 0 0 0 9.2 0v-1.2"/><circle cx="19.2" cy="11.8" r="1.9"/>'),
    "🏀": _p('<circle cx="12" cy="12" r="8.4"/><path d="M12 3.6v16.8M3.6 12h16.8M6.1 6.1a11.8 11.8 0 0 0 0 11.8M17.9 6.1a11.8 11.8 0 0 1 0 11.8"/>'),
    # — pastilles d'ÉTAT (pleines : la couleur vient du texte du badge) —
    "🟢": _dot(), "🟡": _dot(), "🔴": _dot(), "🟠": _dot(), "🔵": _dot(),
}

# Motif : chaque emoji + son éventuel sélecteur de variante (U+FE0F) qui suit. Trié par longueur pour que
# les séquences les plus longues soient testées d'abord.
_PAT = re.compile("(?:" + "|".join(re.escape(k) for k in sorted(ICONS, key=len, reverse=True)) + ")️?")

# Feuille de style injectée une fois : alignement optique sur la ligne de texte (comme un emoji).
CSS = ('<style>.eico{width:1em;height:1em;display:inline-block;vertical-align:-.14em;flex:none}</style>')


def apply(html: str) -> str:
    """Remplace les emoji couleur par leur picto SVG. No-op si aucun n'est présent (coût nul)."""
    if not html:
        return html
    out, n = _PAT.subn(lambda m: ICONS[m.group(0).rstrip("️")], html)
    if n and "</head>" in out:
        out = out.replace("</head>", CSS + "</head>", 1)
    return out
