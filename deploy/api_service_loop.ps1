# Superviseur de l'API : maintient uvicorn en vie, en boucle.
# Lancé par la tâche planifiée "API-SPORT-api" (compte SYSTEM, au démarrage du
# PC, AVANT toute ouverture de session). Si uvicorn s'arrête ou plante, il est
# relancé automatiquement après quelques secondes.
#
# Paramètre -Python : chemin complet vers python.exe (détecté à l'installation
# par setup_full_service.ps1, car SYSTEM n'a pas forcément Python dans le PATH).

param(
  [string]$Python = "python",
  [int]$Port = 8000
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# Journal simple (utile pour diagnostiquer sans session ouverte)
$log = Join-Path $root "deploy\api_service.log"
function Write-Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -ErrorAction SilentlyContinue
}

if (-not (Test-Path $Python)) { $Python = "python" }   # repli si le chemin a bougé

Write-Log "Superviseur démarré (python=$Python, port=$Port, dir=$root)"

while ($true) {
  Write-Log "Lancement de l'API uvicorn..."
  try {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
  } catch {
    Write-Log "Exception: $($_.Exception.Message)"
  }
  Write-Log "L'API s'est arrêtée. Relance dans 5 s."
  Start-Sleep -Seconds 5
}
