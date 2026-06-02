"""Drapeaux des sélections nationales (foot) en emoji — zéro réseau, jamais d'image cassée.

On mappe le nom FRANÇAIS de l'équipe (tel qu'envoyé par Unibet) vers son code ISO-3166 alpha-2,
puis on convertit en emoji drapeau (indicateurs régionaux). `flag(name)` renvoie '' si inconnu.
"""

from __future__ import annotations

from app.textutil import fold

# nom français (replié : minuscule + sans accents) -> code ISO alpha-2
_ISO: dict[str, str] = {
    # UEFA
    "albanie": "AL", "allemagne": "DE", "andorre": "AD", "angleterre": "GB",
    "armenie": "AM", "autriche": "AT", "azerbaidjan": "AZ", "belgique": "BE",
    "bielorussie": "BY", "bosnie-herzegovine": "BA", "bosnie herzegovine": "BA",
    "bulgarie": "BG", "chypre": "CY", "croatie": "HR", "danemark": "DK",
    "ecosse": "GB", "espagne": "ES", "estonie": "EE", "finlande": "FI",
    "france": "FR", "georgie": "GE", "gibraltar": "GI", "grece": "GR",
    "hongrie": "HU", "iles feroe": "FO", "irlande": "IE", "irlande du nord": "GB",
    "islande": "IS", "israel": "IL", "italie": "IT", "kazakhstan": "KZ",
    "kosovo": "XK", "lettonie": "LV", "liechtenstein": "LI", "lituanie": "LT",
    "luxembourg": "LU", "macedoine du nord": "MK", "malte": "MT", "moldavie": "MD",
    "montenegro": "ME", "norvege": "NO", "pays de galles": "GB", "pays-bas": "NL",
    "pologne": "PL", "portugal": "PT", "republique tcheque": "CZ", "tchequie": "CZ",
    "roumanie": "RO", "russie": "RU", "saint-marin": "SM", "serbie": "RS",
    "slovaquie": "SK", "slovenie": "SI", "suede": "SE", "suisse": "CH",
    "turquie": "TR", "ukraine": "UA",
    # CONMEBOL
    "argentine": "AR", "bolivie": "BO", "bresil": "BR", "chili": "CL",
    "colombie": "CO", "equateur": "EC", "paraguay": "PY", "perou": "PE",
    "uruguay": "UY", "venezuela": "VE",
    # CONCACAF
    "canada": "CA", "costa rica": "CR", "cuba": "CU", "curacao": "CW",
    "el salvador": "SV", "etats-unis": "US", "guadeloupe": "GP", "guatemala": "GT",
    "haiti": "HT", "honduras": "HN", "jamaique": "JM", "martinique": "MQ",
    "mexique": "MX", "nicaragua": "NI", "panama": "PA", "porto rico": "PR",
    "republique dominicaine": "DO", "trinite-et-tobago": "TT",
    "iles vierges britanniques": "VG", "iles vierges americaines": "VI",
    # CAF (Afrique)
    "afrique du sud": "ZA", "algerie": "DZ", "angola": "AO", "benin": "BJ",
    "botswana": "BW", "burkina faso": "BF", "burundi": "BI", "cameroun": "CM",
    "cap-vert": "CV", "comores": "KM", "congo": "CG", "cote d'ivoire": "CI",
    "egypte": "EG", "eswatini": "SZ", "ethiopie": "ET", "gabon": "GA",
    "gambie": "GM", "ghana": "GH", "guinee": "GN", "guinee-bissau": "GW",
    "guinee equatoriale": "GQ", "kenya": "KE", "lesotho": "LS", "liberia": "LR",
    "libye": "LY", "madagascar": "MG", "malawi": "MW", "mali": "ML",
    "maroc": "MA", "maurice": "MU", "mauritanie": "MR", "mozambique": "MZ",
    "namibie": "NA", "niger": "NE", "nigeria": "NG", "ouganda": "UG",
    "republique centrafricaine": "CF", "republique democratique du congo": "CD",
    "rd congo": "CD", "rwanda": "RW", "senegal": "SN", "sierra leone": "SL",
    "somalie": "SO", "soudan": "SD", "soudan du sud": "SS", "tanzanie": "TZ",
    "tchad": "TD", "togo": "TG", "tunisie": "TN", "zambie": "ZM", "zimbabwe": "ZW",
    # AFC (Asie)
    "afghanistan": "AF", "arabie saoudite": "SA", "australie": "AU", "bahrein": "BH",
    "bangladesh": "BD", "bhoutan": "BT", "birmanie": "MM", "myanmar": "MM",
    "cambodge": "KH", "chine": "CN", "coree du nord": "KP", "coree du sud": "KR",
    "emirats arabes unis": "AE", "inde": "IN", "indonesie": "ID", "irak": "IQ",
    "iran": "IR", "japon": "JP", "jordanie": "JO", "kirghizistan": "KG",
    "koweit": "KW", "laos": "LA", "liban": "LB", "malaisie": "MY", "maldives": "MV",
    "nepal": "NP", "oman": "OM", "ouzbekistan": "UZ", "pakistan": "PK",
    "palestine": "PS", "philippines": "PH", "qatar": "QA", "singapour": "SG",
    "sri lanka": "LK", "syrie": "SY", "tadjikistan": "TJ", "thailande": "TH",
    "turkmenistan": "TM", "vietnam": "VN", "yemen": "YE", "hong kong": "HK",
    # OFC (Océanie)
    "fidji": "FJ", "guam": "GU", "nouvelle-caledonie": "NC", "nouvelle caledonie": "NC",
    "nouvelle-zelande": "NZ", "nouvelle zelande": "NZ", "papouasie-nouvelle-guinee": "PG",
    "tahiti": "PF", "samoa": "WS", "tonga": "TO", "vanuatu": "VU",
    "iles salomon": "SB",
}


# Nations britanniques : vrais drapeaux (séquences « tag », pas un code ISO simple).
_SUBDIV = {"angleterre": "gbeng", "ecosse": "gbsct", "pays de galles": "gbwls"}


def flag(name: str) -> str:
    """Emoji drapeau de la sélection (ou '' si inconnue). Insensible aux accents/casse."""
    key = fold(name or "").strip()
    sub = _SUBDIV.get(key)
    if sub:
        return "\U0001F3F4" + "".join(chr(0xE0000 + ord(c)) for c in sub) + "\U000E007F"
    iso = _ISO.get(key)
    if not iso or len(iso) != 2:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso)
