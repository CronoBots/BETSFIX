"""Routeur Foot (Coupe du Monde + grandes compétitions) — page séparée."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import foot

router = APIRouter(tags=["Foot"], include_in_schema=False)


@router.get("/foot", response_class=HTMLResponse)
async def foot_page() -> HTMLResponse:
    """Matchs des grandes compétitions (dont CdM) : proba 1X2 (Elo) vs cotes Unibet."""
    try:
        rows = await foot.board()
    except Exception:
        rows = []
    try:
        fin = await foot.finished()
    except Exception:
        fin = []
    return HTMLResponse(foot.render(rows, fin))
