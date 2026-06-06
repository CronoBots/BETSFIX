"""Test de la remise à zéro au démarrage (sentinelle data/.reset-pending)."""

import json
import os

from app.main import _apply_pending_reset


def test_pending_reset_clears_stores_and_analyses(tmp_path):
    data = tmp_path
    (data / "analyses").mkdir()
    # stores remplis + 2 analyses
    for fn in ("tracking_tennis.json", "tracking_foot.json", "tracking_basket.json"):
        (data / fn).write_text(json.dumps({"1": {"home": "X"}}), encoding="utf-8")
    (data / "analyses" / "foot_1.md").write_text("# x", encoding="utf-8")
    (data / "analyses" / "basket_2.md").write_text("# y", encoding="utf-8")

    # sans sentinelle -> ne touche à rien
    assert _apply_pending_reset(str(data)) is False
    assert json.loads((data / "tracking_foot.json").read_text()) == {"1": {"home": "X"}}

    # avec sentinelle -> vide tout et la retire
    (data / ".reset-pending").write_text("", encoding="utf-8")
    assert _apply_pending_reset(str(data)) is True
    for fn in ("tracking_tennis.json", "tracking_foot.json", "tracking_basket.json"):
        assert json.loads((data / fn).read_text()) == {}
    assert not list((data / "analyses").glob("*.md"))
    assert not (data / ".reset-pending").exists()       # sentinelle consommée (réinitialisation unique)
