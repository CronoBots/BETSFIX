@echo off
REM ============================================================
REM  Back-test walk-forward du modele (Elo + classement).
REM
REM  Double-clique ce fichier pour lancer tools/backtest_model.py :
REM  il rejoue l'historique SofaScore dans l'ordre, mesure la
REM  precision / Brier / log-loss du modele, l'ablation par
REM  facteur, la calibration et le SHRINK optimal anti-surconfiance.
REM
REM  Rien n'est ecrit sur disque : c'est un diagnostic. Reporte le
REM  CALIB_SHRINK affiche dans app/analysis.py si tu veux l'appliquer.
REM  Duree : quelques minutes (beaucoup de requetes reseau).
REM ============================================================

title Backtest modele - BETSFIX
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

echo Back-test du modele (peut prendre quelques minutes)...
echo.
%PY% tools\backtest_model.py
echo.
echo Termine.
pause
