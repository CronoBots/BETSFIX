@echo off
REM ============================================================
REM  Construit les notes Elo (force des joueurs) du modele.
REM
REM  Double-clique ce fichier pour lancer tools/build_elo.py :
REM  il collecte l'historique SofaScore, calcule l'Elo global +
REM  terre battue, et ecrit data/elo_ratings.json.
REM
REM  Une fois ce fichier cree, le facteur Elo s'active tout seul
REM  dans l'analyse. A relancer de temps en temps pour rafraichir.
REM  Duree : quelques minutes (beaucoup de requetes reseau).
REM ============================================================

title Build Elo - BETSFIX
cd /d "%~dp0"

REM Choisit "python" si dispo, sinon "py" (lanceur Windows)
set "PY=python"
where python >nul 2>nul || set "PY=py"
where %PY% >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERREUR] Python est introuvable.
  echo Installe-le depuis https://www.python.org puis relance.
  echo.
  pause
  exit /b 1
)

echo Construction des notes Elo (peut prendre quelques minutes)...
echo.
%PY% tools\build_elo.py
echo.
echo Termine. Le fichier data\elo_ratings.json est a jour.
pause
