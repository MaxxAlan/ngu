# Tao shortcut desktop "ngu" mo thang Windows Terminal
$WshShell = New-Object -ComObject WScript.Shell
$Desktop   = [Environment]::GetFolderPath("Desktop")
$Shortcut  = $WshShell.CreateShortcut("$Desktop\ngu.lnk")

$Shortcut.TargetPath       = "wt.exe"
$Shortcut.Arguments        = "-d `"$PSScriptRoot`" cmd /k ngu"
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.Description      = "ngu - local AI agent"

# Dung icon cua Windows Terminal
$Shortcut.IconLocation = "wt.exe,0"

$Shortcut.Save()
Write-Host "[OK] Shortcut 'ngu' da duoc tao tren Desktop." -ForegroundColor Green
Write-Host "     Double-click la mo thang ngu trong Windows Terminal." -ForegroundColor Cyan
