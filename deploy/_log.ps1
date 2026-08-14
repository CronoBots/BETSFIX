# BETSFIX — helper de LOG concurrent-safe (2026-08-14).
# Plusieurs scripts (scan_daily / scan_evening / scan_sweep / scan_wave / reconcile_loop) écrivent dans le
# MÊME fichier data\scan_cron.log. L'ancien « … | Out-File -Append » gardait le fichier ouvert en EXCLUSIF
# pendant TOUTE la durée du sous-processus (des minutes) -> tout autre writer échouait (FileOpenFailure,
# ~39 lignes de log perdues/jour). Ici : chaque écriture est un append ATOMIQUE et BREF (open/append/close)
# avec RETRY sur verrou -> deux process peuvent écrire au même instant sans jamais perdre une ligne.
#
# Dot-sourcer en tête de script :  . (Join-Path $root 'deploy\_log.ps1')

$script:_BfxUtf8 = [System.Text.UTF8Encoding]::new($false)   # UTF-8 SANS BOM (pas de BOM parasite en milieu de fichier)

function Write-BfxLog {
    <# Append ROBUSTE d'un bloc de texte. Retry sur IOException (verrou d'un autre writer). #>
    param([Parameter(Mandatory)][string]$Path, [string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return }
    if (-not $Text.EndsWith("`n")) { $Text += "`r`n" }
    for ($i = 0; $i -lt 60; $i++) {
        try {
            [System.IO.File]::AppendAllText($Path, $Text, $script:_BfxUtf8)
            return
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds 40        # fichier momentanément verrouillé -> on réessaie (~2,4 s max)
        }
    }
    # Ultime tentative après ~2,4 s (ne PAS avaler l'erreur : un échec ici serait anormal et doit se voir).
    [System.IO.File]::AppendAllText($Path, $Text, $script:_BfxUtf8)
}

function Write-BfxLogLine {
    <# Une ligne horodatée (remplace l'ancienne fonction Log). #>
    param([Parameter(Mandatory)][string]$Path, [string]$Message)
    Write-BfxLog -Path $Path -Text ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Add-BfxStream {
    <# Cible de pipeline (remplace « Out-File -Append -Encoding utf8 $log »). Chaque objet du flux est
       ajouté en un append bref -> ne tient JAMAIS le fichier ouvert longtemps. Préserve $LASTEXITCODE
       (la commande native reste la dernière commande native du pipeline). #>
    param(
        [Parameter(Mandatory, Position = 0)][string]$Path,
        [Parameter(ValueFromPipeline = $true)]$InputObject
    )
    process { if ($null -ne $InputObject) { Write-BfxLog -Path $Path -Text ([string]$InputObject) } }
}
