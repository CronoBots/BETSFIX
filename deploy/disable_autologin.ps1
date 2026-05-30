# ============================================================================
#  Desactive l'ouverture de session automatique de Windows (AutoAdminLogon)
#  qui, avec un compte Microsoft + PIN, creait un PROFIL FANTOME au reboot.
#
#  Apres ca : au boot, le PC affiche l'ecran de connexion normal (ton profil
#  + ton PIN). PAS GRAVE : ton API, ton tunnel et Claude Remote Control
#  demarrent quand meme SANS login (ce sont des services / taches "sans
#  ouverture de session"). Tu pilotes tout depuis le mobile, sans toucher au PC.
#
#  LANCER (PowerShell EN ADMINISTRATEUR) :
#       powershell -ExecutionPolicy Bypass -File .\deploy\disable_autologin.ps1
# ============================================================================

$ErrorActionPreference = "Continue"
$w = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"

Set-ItemProperty -Path $w -Name AutoAdminLogon -Value "0" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path $w -Name DefaultPassword -ErrorAction SilentlyContinue
Remove-ItemProperty -Path $w -Name AutoLogonCount  -ErrorAction SilentlyContinue

$auto = (Get-ItemProperty $w -Name AutoAdminLogon -ErrorAction SilentlyContinue).AutoAdminLogon
if ($auto -eq "0" -or $null -eq $auto) {
  Write-Host "OK : ouverture de session automatique DESACTIVEE." -ForegroundColor Green
  Write-Host "Au prochain reboot, plus de profil fantome : l'ecran de connexion normal" -ForegroundColor Gray
  Write-Host "s'affichera, mais l'API / le tunnel / Claude tourneront deja en arriere-plan." -ForegroundColor Gray
} else {
  Write-Host "Attention : AutoAdminLogon vaut encore '$auto'. Relance ce script en ADMINISTRATEUR." -ForegroundColor Red
}

Write-Host ""
Write-Host "NETTOYAGE OPTIONNEL du profil fantome :" -ForegroundColor Cyan
Write-Host "  Ouvre 'sysdm.cpl' -> onglet 'Parametres systeme avances' -> 'Profil des" -ForegroundColor Gray
Write-Host "  utilisateurs' -> 'Parametres'. Supprime UNIQUEMENT le profil en double" -ForegroundColor Gray
Write-Host "  de TRES PETITE taille (le fantome vide), surtout PAS ton vrai profil." -ForegroundColor Gray
