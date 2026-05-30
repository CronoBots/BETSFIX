@echo off
REM ============================================================
REM  Rafraichit les donnees du modele (double-clic).
REM
REM  Reconstruit, a partir du cache de stats deja telecharge :
REM   - les notes Elo (force des joueurs)
REM   - les tendances d'aces (marche aces)
REM   - les notes service/retour (facteur surface du modele)
REM
REM  Rapide si les caches existent. Pour rafraichir les caches
REM  eux-memes (plus long) : lance tools/explore_aces.py et
REM  tools/explore_breaks.py.
REM ============================================================

title Build data - API-SPORT
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=py"
where %PY% >nul 2>nul
if errorlevel 1 (
  echo [ERREUR] Python introuvable. Installe-le depuis https://www.python.org
  pause
  exit /b 1
)

echo [1/3] Notes Elo...
%PY% tools\build_elo.py
echo.
echo [2/3] Tendances d'aces...
%PY% tools\build_tendencies.py
echo.
echo [3/3] Notes service/retour...
%PY% tools\build_serve_return.py
echo.
echo Termine. Les fichiers data\*.json sont a jour.
pause
