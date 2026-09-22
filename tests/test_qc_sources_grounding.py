"""QC — comptage des sources par DOMAINES cités (anti faux-rouge SOURCES).

Le compteur legacy ne reconnaissait que 7 providers en dur : une analyse honnête citant
« [Sofascore](…), [scores24](…), [prvaliga.rs](…) » n'en voyait qu'UNE -> faux « <2 sources » -> 🔴.
On compte désormais les domaines RÉELLEMENT cités (en plus des providers, jamais à leur place) :
- ≥2 domaines distincts cités  -> n_src ≥ 2 (le faux rouge s'éteint) ;
- 0 lien réel (texte verbeux)   -> reste < 2 (anti « beau texte creux »).
Aucun seuil n'est assoupli : on n'ajoute que de la reconnaissance de sources réelles.
"""

import tools.analysis_quality as AQ


def _sig(md_text):
    # md=None -> pas de lecture disque ; on passe le texte directement (comme _qc_collect en prod).
    return AQ._qc_collect({}, None, md_text)


def test_cited_domains_distinct_roots():
    txt = ("Voir [Sofascore](https://www.sofascore.com/x) et "
           "[scores24](https://scores24.live/en/y) et [ligue](https://www.prvaliga.rs/en/).")
    assert AQ._cited_domains(txt) == {"sofascore.com", "scores24.live", "prvaliga.rs"}


def test_subdomains_collapse_to_root():
    txt = ("[a](https://us.women.soccerway.com/m) [b](https://en.wikipedia.org/wiki/z) "
           "[c](https://www.soccerway.com/other)")
    # us.women.soccerway.com et www.soccerway.com -> même racine soccerway.com
    assert AQ._cited_domains(txt) == {"soccerway.com", "wikipedia.org"}


def test_two_cited_domains_count_as_two_sources():
    # Aucun provider legacy reconnu, mais 3 domaines cités -> n_src >= 2 (faux rouge éteint).
    txt = ("## 📋 Les faits\n- fait A ([scores24](https://scores24.live/a), "
           "[prvaliga](https://www.prvaliga.rs/b))\n- fait B ([aiscore](https://www.aiscore.com/c))")
    sig = _sig(txt)
    assert sig["n_cited"] == 3
    assert sig["n_src"] >= 2


def test_verbose_without_links_stays_poor():
    # Le mot « sportradar » apparaît (token) mais AUCUN lien réel -> reste 1 source, pas 2 (anti-camouflage).
    txt = "## Les faits\n- selon sportradar l'équipe est en forme, blabla verbeux sans la moindre source.\n"
    sig = _sig(txt)
    assert sig["n_cited"] == 0
    assert sig["n_src"] < 2


def test_structured_sources_still_counted():
    # Une fiche avec sources structurées garde son compte, indépendamment des liens du .md.
    sig = AQ._qc_collect({"sources": {"fotmob": 1, "understat": 1}}, None, "aucun lien ici")
    assert sig["n_src"] >= 2
