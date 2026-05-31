"""Routeur Basket (WNBA) — page séparée du tennis."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import basket

router = APIRouter(tags=["Basket"], include_in_schema=False)


@router.get("/basket", response_class=HTMLResponse)
async def basket_page() -> HTMLResponse:
    """Tableau WNBA : matchs à venir, proba modèle (Elo) vs cotes Unibet, value."""
    try:
        rows = await basket.board()
    except Exception:
        rows = []
    try:
        fin = await basket.finished()
    except Exception:
        fin = []
    return HTMLResponse(basket.render(rows, fin))
