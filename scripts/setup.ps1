#requires -Version 5.1
<#
    local-agents - cai dat / sua chua toan bo moi truong.

    Chay qua setup.bat (Run as Administrator).
    Idempotent: chay lai nhieu lan cung duoc. Khong pha vong dung cai
    dang chay - chi sua khi that su hong.
#>
[CmdletBinding()]
param(
    # 'all' = tai day du bo model (giong thu muc models/), 'core' = chi model mac dinh + vision, 'none' = bo qua
    [ValidateSet('all', 'core', 'none')] [string] $Models = 'all',
    [switch] $SkipSmoke,   # bo qua bai kiem tra end-to-end (load model + 1 completion)
    [switch] $Force,       # xoa va tao lai .venv / tai lai llama.cpp
    [switch] $KeepOpen     # giu cua so terminal lai sau khi xong
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

# ============================ paths =========================================
$Root        = Split-Path -Parent $PSScriptRoot
$VenvDir     = Join-Path $Root '.venv'
$VenvPy      = Join-Path $VenvDir 'Scripts\python.exe'
$LlamaDir    = Join-Path $Root 'runtime\llama.cpp'
$LlamaExe    = Join-Path $LlamaDir 'llama-server.exe'
$ModelsDir   = Join-Path $Root 'models'
$DataDir     = Join-Path $Root 'data'
$ConfigFile  = Join-Path $Root '.config'
$LogFile     = Join-Path $DataDir 'setup.log'
$ReportFile  = Join-Path $Root 'setup-report.txt'
$TmpDir      = Join-Path $env:TEMP ('la-setup-' + ([guid]::NewGuid().ToString('N').Substring(0, 8)))

foreach ($d in @($DataDir, $ModelsDir, $TmpDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# ============================ helpers =======================================
$script:Results = New-Object System.Collections.ArrayList
$script:Fails   = 0

function Write-Log {
    param([string]$Text, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Text
    try { Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 } catch { }
}

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host ('  ' + $Title.ToUpper()) -ForegroundColor Cyan
    Write-Host ('  ' + ('-' * 68)) -ForegroundColor DarkCyan
}

function Write-Ok   { param([string]$Text) Write-Host ('  [ OK ] ' + $Text) -ForegroundColor Green;  Write-Log $Text 'OK' }
function Write-Info { param([string]$Text) Write-Host ('  [INFO] ' + $Text) -ForegroundColor Gray;   Write-Log $Text 'INFO' }
function Write-Warn { param([string]$Text) Write-Host ('  [WARN] ' + $Text) -ForegroundColor Yellow; Write-Log $Text 'WARN' }
function Write-Fail { param([string]$Text) Write-Host ('  [FAIL] ' + $Text) -ForegroundColor Red;    Write-Log $Text 'FAIL'; $script:Fails++ }

function Add-Result {
    param([string]$Step, [string]$Status, [string]$Detail)
    [void]$script:Results.Add([pscustomobject]@{ Step = $Step; Status = $Status; Detail = $Detail })
}

function Flat-Text {
    param($Value)
    if ($null -eq $Value) { return '' }
    if ($Value -is [array]) { return (($Value | ForEach-Object { [string]$_ }) -join ' ') }
    return [string]$Value
}

function Quote-Arg {
    param([string]$Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -match '[\s"]') { return '"' + $Value.Replace('"', '\"') + '"' }
    return $Value
}

function Invoke-Native {
    <#
        Chay executable, tra ve ExitCode / Output / Error. Co timeout that.
        Dung System.Diagnostics.Process de lay ExitCode that (Start-Process -PassThru tra null).
        -Live: output cua lenh in thang ra man hinh (dung cho curl co progress bar).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSec = 300,
        [switch]$Live
    )
    $out = ''
    $err = ''
    $code = 127
    $timedOut = $false
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FilePath
        $psi.Arguments = (($ArgumentList | ForEach-Object { Quote-Arg $_ }) -join ' ')
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        if (-not $Live) {
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
        }
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        [void]$proc.Start()
        if ($Live) {
            if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
                $timedOut = $true
                try { $proc.Kill() } catch { }
            }
        } else {
            $outTask = $proc.StandardOutput.ReadToEndAsync()
            $errTask = $proc.StandardError.ReadToEndAsync()
            if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
                $timedOut = $true
                try { $proc.Kill() } catch { }
                try { [void]$proc.WaitForExit(5000) } catch { }
            } else {
                try { $proc.WaitForExit() } catch { }
            }
            try { $out = $outTask.Result } catch { $out = '' }
            try { $err = $errTask.Result } catch { $err = '' }
        }
        try { $code = $proc.ExitCode } catch { $code = -1 }
        if ($null -eq $code) { $code = -1 }
        if ($timedOut) { $code = 124 }
    } catch {
        return [pscustomobject]@{ ExitCode = 127; Output = ''; Error = $_.Exception.Message; TimedOut = $false }
    }
    return [pscustomobject]@{ ExitCode = $code; Output = $out; Error = $err; TimedOut = $timedOut }
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Find-NvidiaSmi {
    $c = Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $paths = @(
        (Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'),
        'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe',
        'C:\Windows\System32\nvidia-smi.exe'
    )
    foreach ($p in $paths) { if (Test-Path $p) { return $p } }
    return $null
}

function Get-PythonVersion {
    param([string]$Exe, [string[]]$ExtraArgs = @())
    if (-not $Exe) { return 0 }
    $r = Invoke-Native -FilePath $Exe -ArgumentList ($ExtraArgs + @('-c', 'import sys;print("VER=%d" % (sys.version_info[0]*100+sys.version_info[1]))')) -TimeoutSec 60
    if ($r.ExitCode -ne 0) { return 0 }
    if ($r.Output -match 'VER=(\d+)') { return [int]$Matches[1] }
    return 0
}

function Get-ConfigValue {
    param([string]$Key, [string]$Default = '')
    if (-not (Test-Path $ConfigFile)) { return $Default }
    foreach ($line in (Get-Content -LiteralPath $ConfigFile)) {
        $l = $line.Trim()
        if (-not $l -or $l.StartsWith('#') -or $l.StartsWith(';')) { continue }
        if ($l -notmatch '=') { continue }
        $parts = $l.Split('=')
        if ($parts[0].Trim() -eq $Key) { return $parts[1].Trim().Trim('"').Trim("'") }
    }
    return $Default
}

function Get-FreeSpaceGB {
    param([string]$Path)
    try {
        $full = $Path
        if (-not (Test-Path $full)) { $full = Split-Path -Parent $Path }
        $root = [IO.Path]::GetPathRoot($full)
        if (-not $root) { return -1 }
        $drive = Get-PSDrive -Name $root.TrimEnd('\').TrimEnd(':') -ErrorAction SilentlyContinue
        if ($drive) { return [math]::Round($drive.Free / 1GB, 1) }
    } catch { }
    return -1
}

function Write-Block {
    param([string]$Text)
    foreach ($line in ($Text -split "`n")) {
        if ($line.Trim()) { Write-Host ('       ' + $line.TrimEnd()) -ForegroundColor DarkGray }
    }
}

# ============================ 0. preflight ==================================
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '    local-agents :: SETUP / REPAIR MOI TRUONG' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ('  Thu muc project : ' + $Root) -ForegroundColor Gray
Write-Host ('  Bat dau         : ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -ForegroundColor Gray

$isAdmin = $false
try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }
if ($isAdmin) {
    Write-Info 'Quyen Administrator: OK'
} else {
    Write-Warn 'Khong chay voi quyen Administrator. Hay chay setup.bat voi "Run as administrator".'
}

$free = Get-FreeSpaceGB -Path $Root
if ($free -ge 0) {
    Write-Info ('Dung luong con trong : ' + $free + ' GB')
    if ($free -lt 25) { Write-Warn 'Con it hon 25 GB - viec tai model lon co that bai.' }
}
Write-Log ('setup bat dau tai ' + $Root)

# ============================ 1. python toolchain ===========================
Write-Section '1. Python toolchain'

$UvExe = $null
if (Test-Command 'uv') {
    $UvExe = (Get-Command 'uv').Source
    $r = Invoke-Native -FilePath $UvExe -ArgumentList @('--version') -TimeoutSec 60
    Write-Ok ('uv: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim())
} else {
    Write-Warn 'Khong tim thay uv - dang cai dat...'
    $installed = $false
    if (Test-Command 'winget') {
        Write-Info 'dung winget de cai uv...'
        $r = Invoke-Native -FilePath 'winget' -ArgumentList @(
            'install', '--id', 'astral-sh.uv', '-e', '--source', 'winget',
            '--accept-package-agreements', '--accept-source-agreements',
            '--disable-interactivity') -TimeoutSec 900
        if ($r.ExitCode -eq 0) { $installed = $true }
        else { Write-Warn ('winget that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim()) }
    }
    if (-not $installed) {
        Write-Info 'dung installer chinh thuc cua uv...'
        $r = Invoke-Native -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
            'irm https://astral.sh/uv/install.ps1 | iex') -TimeoutSec 900
        if ($r.ExitCode -eq 0) { $installed = $true }
        else { Write-Warn ('uv installer that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim()) }
    }
    $uvDir = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path $uvDir) { $env:PATH = $uvDir + ';' + $env:PATH }
    $wingetLink = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    if (Test-Path $wingetLink) { $env:PATH = $wingetLink + ';' + $env:PATH }
    if (Test-Command 'uv') {
        $UvExe = (Get-Command 'uv').Source
        Write-Ok ('uv da cai dat: ' + $UvExe)
    } else {
        Write-Warn 'Khong co uv - se dung python he thong de tao venv.'
    }
}

function Find-BasePython {
    $found = New-Object System.Collections.ArrayList
    if ($UvExe) {
        foreach ($v in @('3.11', '3.12', '3.13', '3.14')) {
            $r = Invoke-Native -FilePath $UvExe -ArgumentList @('python', 'find', $v) -TimeoutSec 60
            if ($r.ExitCode -eq 0) {
                $p = ((Flat-Text $r.Output) -replace '\s+', ' ').Trim()
                if ($p -and (Test-Path $p)) { [void]$found.Add($p) }
            }
        }
    }
    if (Test-Command 'py') {
        foreach ($v in @('3.11', '3.12', '3.13', '3.14')) {
            $r = Invoke-Native -FilePath 'py' -ArgumentList @("-$v", '-c', 'import sys;print(sys.executable)') -TimeoutSec 60
            if ($r.ExitCode -eq 0) {
                $p = ((Flat-Text $r.Output) -replace '\s+', ' ').Trim()
                if ($p -and (Test-Path $p)) { [void]$found.Add($p) }
            }
        }
    }
    foreach ($c in @('python', 'python3')) {
        if (Test-Command $c) {
            $p = (Get-Command $c).Source
            if ($p -and (Get-PythonVersion -Exe $p) -ge 311) { [void]$found.Add($p) }
        }
    }
    return $found
}

$BasePy = @(Find-BasePython)
if ($BasePy.Count -eq 0 -and $UvExe) {
    Write-Info 'khong co python 3.11+ - dang tai python do uv quan ly...'
    $r = Invoke-Native -FilePath $UvExe -ArgumentList @('python', 'install', '3.11') -TimeoutSec 1200
    if ($r.ExitCode -eq 0) {
        $BasePy = @(Find-BasePython)
        Write-Ok 'da tai python 3.11 qua uv'
    } else {
        Write-Warn 'khong tai duoc python 3.11 qua uv'
    }
}

if ($BasePy.Count -gt 0) {
    Write-Ok ('python base: ' + $BasePy[0] + ' (version ' + (Get-PythonVersion -Exe $BasePy[0]) + ')')
} else {
    Write-Fail 'khong tim thay python 3.11+ tren may nay'
    Add-Result 'Python base' 'FAIL' 'khong co python >= 3.11'
}

# ============================ 2. virtualenv =================================
Write-Section '2. Virtual environment (.venv)'

function Test-Venv {
    if (-not (Test-Path $VenvPy)) { return $false }
    $cfg = Join-Path $VenvDir 'pyvenv.cfg'
    if (Test-Path $cfg) {
        $venvHome = ''
        foreach ($line in (Get-Content -LiteralPath $cfg)) {
            if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { $venvHome = $Matches[1] }
        }
        if ($venvHome -and -not (Test-Path $venvHome)) {
            Write-Warn ('venv tro toi base python khong con: ' + $venvHome)
            return $false
        }
    }
    if ((Get-PythonVersion -Exe $VenvPy) -lt 311) { return $false }
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @(
        '-c', 'import fastapi, uvicorn, httpx, mcp, multipart; print("deps-ok")') -TimeoutSec 120
    if ($r.ExitCode -ne 0) {
        Write-Warn 'venv thieu thu vien (fastapi/uvicorn/httpx/mcp)'
        return $false
    }
    return $true
}

if ((-not $Force) -and (Test-Venv)) {
    Write-Ok ('venv hop le: ' + $VenvPy)
    Add-Result 'Python venv' 'OK' $VenvPy
} else {
    if (Test-Path $VenvDir) {
        $bak = Join-Path $Root ('.venv.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Write-Warn ('venv khong dung -> giu lai ban ' + (Split-Path -Leaf $bak))
        try { Rename-Item -LiteralPath $VenvDir -NewName (Split-Path -Leaf $bak) -ErrorAction Stop } catch {
            try { Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
    $created = $false
    if ($UvExe) {
        foreach ($v in @('3.11', '3.12', '3.13', '3.14')) {
            $r = Invoke-Native -FilePath $UvExe -ArgumentList @('venv', '--python', $v, $VenvDir) -TimeoutSec 900
            if ($r.ExitCode -eq 0 -and (Test-Path $VenvPy)) { $created = $true; break }
        }
    }
    if (-not $created -and $BasePy.Count -gt 0) {
        foreach ($p in $BasePy) {
            $r = Invoke-Native -FilePath $p -ArgumentList @('-m', 'venv', $VenvDir) -TimeoutSec 900
            if ($r.ExitCode -eq 0 -and (Test-Path $VenvPy)) { $created = $true; break }
        }
    }
    if ($created) {
        Write-Ok ('da tao lai venv: ' + $VenvPy)
        Add-Result 'Python venv' 'OK' 'da tao lai'
    } else {
        Write-Fail 'khong tao duoc venv'
        Add-Result 'Python venv' 'FAIL' 'khong tao duoc .venv'
    }
}

# ============================ 3. dependencies ===============================
Write-Section '3. Thu vien Python (fastapi, uvicorn, httpx, mcp)'

$ReqFile = Join-Path $TmpDir 'requirements.txt'
$depList = @()
if (Test-Path $VenvPy) {
    $dumpPy = Join-Path $TmpDir 'dump_deps.py'
    $dumpCode = @'
import json, sys
try:
    import tomllib
except Exception:
    tomllib = None
deps = []
if tomllib:
    with open(sys.argv[1], "rb") as f:
        deps = tomllib.load(f).get("project", {}).get("dependencies", [])
print(json.dumps(deps))
'@
    [IO.File]::WriteAllText($dumpPy, $dumpCode, (New-Object System.Text.UTF8Encoding($false)))
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @($dumpPy, (Join-Path $Root 'pyproject.toml')) -TimeoutSec 120
    if ($r.ExitCode -eq 0) {
        try { $depList = @((ConvertFrom-Json ($r.Output.Trim()))) } catch { $depList = @() }
    }
}
if ($depList.Count -eq 0) {
    $depList = @('fastapi>=0.115', 'uvicorn>=0.30', 'httpx>=0.27', 'python-multipart>=0.0.9', 'mcp>=1.10,<2.0')
    Write-Info 'dung danh sach dep mac dinh (khong doc duoc pyproject.toml)'
}
foreach ($d in $depList) { Add-Content -LiteralPath $ReqFile -Value ([string]$d) -Encoding ASCII }
Write-Info ('can cai: ' + (($depList | ForEach-Object { [string]$_ }) -join ', '))

$installedOk = $false
if ($UvExe) {
    $r = Invoke-Native -FilePath $UvExe -ArgumentList @('sync', '--frozen') -TimeoutSec 1200
    if ($r.ExitCode -eq 0) { $installedOk = $true; Write-Ok 'uv sync --frozen: OK' }
    else { Write-Warn ('uv sync that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim()) }
    if (-not $installedOk) {
        $r = Invoke-Native -FilePath $UvExe -ArgumentList @('pip', 'install', '--python', $VenvPy, '-r', $ReqFile) -TimeoutSec 1200
        if ($r.ExitCode -eq 0) { $installedOk = $true; Write-Ok 'uv pip install: OK' }
        else { Write-Warn ('uv pip install that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim()) }
    }
}
if (-not $installedOk -and (Test-Path $VenvPy)) {
    Write-Info 'dung pip trong venv...'
    [void](Invoke-Native -FilePath $VenvPy -ArgumentList @('-m', 'pip', 'install', '--upgrade', 'pip') -TimeoutSec 900)
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @('-m', 'pip', 'install', '-r', $ReqFile) -TimeoutSec 1800
    if ($r.ExitCode -eq 0) { $installedOk = $true; Write-Ok 'pip install: OK' }
}
if ($installedOk) {
    Add-Result 'Dependencies' 'OK' (($depList | ForEach-Object { [string]$_ }) -join ', ')
} else {
    Write-Fail 'khong cai duoc thu vien Python'
    Add-Result 'Dependencies' 'FAIL' 'pip/uv install that bai'
}
if (Test-Path $VenvPy) {
    $depsCheck = Invoke-Native -FilePath $VenvPy -ArgumentList @(
        '-c', 'import fastapi, uvicorn, httpx, mcp, multipart; print("deps-ok")') -TimeoutSec 120
    if ($depsCheck.ExitCode -eq 0) { Write-Ok 'kiem tra import: OK' } else { Write-Fail 'import thu vien that bai' }
}

# ============================ 4. llama.cpp runtime ==========================
Write-Section '4. llama.cpp runtime (llama-server.exe)'

function Get-GpuInfo {
    $res = [pscustomobject]@{ Vendor = ''; Name = ''; DriverMajor = 0 }
    $smi = Find-NvidiaSmi
    if ($smi) {
        $r = Invoke-Native -FilePath $smi -ArgumentList @(
            '--query-gpu=name,driver_version', '--format=csv,noheader') -TimeoutSec 60
        if ($r.ExitCode -eq 0 -and $r.Output) {
            $line = (((Flat-Text $r.Output) -split "`n")[0]).Trim()
            if ($line) {
                $parts = $line.Split(',')
                $res.Vendor = 'nvidia'
                $res.Name = $parts[0].Trim()
                if ($parts.Count -gt 1 -and $parts[1].Trim() -match '^(\d+)') { $res.DriverMajor = [int]$Matches[1] }
                return $res
            }
        }
    }
    $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -and $_.Name -notmatch 'Microsoft|Basic|Remote' } | Select-Object -First 1
    if ($gpu) {
        $res.Name = $gpu.Name
        if ($gpu.Name -match 'NVIDIA|GeForce|RTX|GTX') { $res.Vendor = 'nvidia' }
        elseif ($gpu.Name -match 'AMD|Radeon') { $res.Vendor = 'amd' }
        elseif ($gpu.Name -match 'Intel|Arc') { $res.Vendor = 'intel' }
    }
    return $res
}

function Get-BuildVariant {
    param($Gpu)
    if ($Gpu.Vendor -eq 'nvidia') {
        if ($Gpu.DriverMajor -ge 580) { return 'cuda-13.5' }
        if ($Gpu.DriverMajor -ge 550) { return 'cuda-12.4' }
        return 'cpu'
    }
    if ($Gpu.Vendor -ne '' -and (Test-Path (Join-Path $env:SystemRoot 'System32\vulkan-1.dll'))) { return 'vulkan' }
    return 'cpu'
}

function Get-LlamaDevices {
    if (-not (Test-Path $LlamaExe)) { return @() }
    $r = Invoke-Native -FilePath $LlamaExe -ArgumentList @('--list-devices') -TimeoutSec 180
    $devs = New-Object System.Collections.ArrayList
    foreach ($line in (((Flat-Text ($r.Output + $r.Error))) -split "`n")) {
        $l = $line.Trim()
        if (-not $l) { continue }
        if ($l -match '^Available') { continue }
        if ($l -match '^([A-Za-z0-9_]+)\s*:\s*(.+)$') {
            [void]$devs.Add(($Matches[1] + ' = ' + $Matches[2].Trim()))
        }
    }
    return $devs
}

function Install-LlamaRuntime {
    param([string]$Variant)
    Write-Info ('tai llama.cpp ban ' + $Variant + ' tu github...')
    $releases = $null
    try {
        $releases = Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15' `
            -Headers @{ 'User-Agent' = 'local-agents-setup' } -TimeoutSec 90
    } catch {
        Write-Fail ('khong goi duoc github: ' + $_.Exception.Message)
        return $false
    }
    $assets = @()
    foreach ($rel in $releases) {
        if ($rel.draft) { continue }
        $assets += $rel.assets
    }
    $pat = '^llama-b\d+-bin-win-' + [regex]::Escape($Variant) + '-x64\.zip$'
    $main = $assets | Where-Object { $_.name -match $pat } | Select-Object -First 1
    if (-not $main) {
        Write-Fail ('khong tim thay asset llama.cpp cho ' + $Variant)
        return $false
    }
    $zips = @($main)
    if ($Variant -like 'cuda*') {
        $cpat = '^cudart-llama-bin-win-' + [regex]::Escape($Variant) + '-x64\.zip$'
        $cud = $assets | Where-Object { $_.name -match $cpat } | Select-Object -First 1
        if ($cud) { $zips += $cud } else { Write-Warn 'khong tim thay goi CUDA runtime (co the loi khi chay)' }
    }
    $extract = Join-Path $TmpDir 'llama-extract'
    if (-not (Test-Path $extract)) { New-Item -ItemType Directory -Force -Path $extract | Out-Null }
    foreach ($z in $zips) {
        $zipPath = Join-Path $TmpDir $z.name
        Write-Info ('   tai ' + $z.name + ' (' + [math]::Round($z.size / 1MB, 1) + ' MB)')
        $r = Invoke-Native -FilePath 'curl.exe' -ArgumentList @(
            '-L', '--fail', '--retry', '3', '--retry-delay', '5', '-C', '-', '-sS',
            '-o', $zipPath, $z.browser_download_url) -TimeoutSec 3600
        if ($r.ExitCode -ne 0 -or -not (Test-Path $zipPath)) {
            Write-Fail ('tai that bai: ' + $z.name + ' (' + ((Flat-Text $r.Error) -replace '\s+', ' ').Trim() + ')')
            return $false
        }
        try { Expand-Archive -LiteralPath $zipPath -DestinationPath $extract -Force } catch {
            Write-Fail ('giai nen that bai: ' + $z.name)
            return $false
        }
    }
    $exe = Get-ChildItem -Path $extract -Filter 'llama-server.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $exe) { Write-Fail 'trong file zip khong co llama-server.exe'; return $false }
    $srcDir = $exe.Directory.FullName
    Write-Info ('   copy tu ' + $srcDir)
    if (Test-Path $LlamaDir) {
        try { Remove-Item -LiteralPath $LlamaDir -Recurse -Force -ErrorAction SilentlyContinue } catch { }
    }
    New-Item -ItemType Directory -Force -Path $LlamaDir | Out-Null
    Copy-Item -Path (Join-Path $srcDir '*') -Destination $LlamaDir -Recurse -Force
    foreach ($extra in (Get-ChildItem -Path $extract -Filter '*.dll' -Recurse -ErrorAction SilentlyContinue)) {
        $target = Join-Path $LlamaDir $extra.Name
        if (-not (Test-Path $target)) { Copy-Item -LiteralPath $extra.FullName -Destination $target -Force }
    }
    return (Test-Path $LlamaExe)
}

$Gpu = Get-GpuInfo
if ($Gpu.Name) {
    Write-Info ('GPU: ' + $Gpu.Name + ' | vendor=' + $Gpu.Vendor + ' | driver ' + $Gpu.DriverMajor + '.x')
} else {
    Write-Info 'khong tim thay GPU (chay tren CPU)'
}
$variant = Get-BuildVariant -Gpu $Gpu
Write-Info ('ban llama.cpp phu hop: ' + $variant)

$runtimeOk = $false
$runtimeNote = $variant
if ((Test-Path $LlamaExe) -and (-not $Force)) {
    $v = Invoke-Native -FilePath $LlamaExe -ArgumentList @('--version') -TimeoutSec 90
    if ($v.ExitCode -eq 0) {
        $devs = @(Get-LlamaDevices)
        if ($devs.Count -gt 0) {
            Write-Ok ('llama-server da co, chay tren: ' + (($devs | Select-Object -First 3) -join ', '))
            $runtimeOk = $true
            $runtimeNote = 'da co (device: ' + (($devs | Select-Object -First 1)) + ')'
        } else {
            $hasCuda = Test-Path (Join-Path $LlamaDir 'ggml-cuda.dll')
            if ($hasCuda -and $Gpu.Vendor -ne 'nvidia') {
                Write-Warn 'ban CUDA khong chay duoc tren may nay (khong co GPU NVIDIA) - tai ban ' + $variant
                if (Install-LlamaRuntime -Variant $variant) {
                    $devs = @(Get-LlamaDevices)
                    Write-Ok ('da doi sang ban ' + $variant + ' (' + $devs.Count + ' thiet bi)')
                    $runtimeOk = $true
                    $runtimeNote = $variant
                }
            } elseif ($hasCuda -and $Gpu.Vendor -eq 'nvidia') {
                Write-Warn 'ban CUDA chay nhung khong thay GPU nao - kiem tra lai driver NVIDIA (co the driver cu hon ban build)'
                Write-Info ('goi y: tai ban cuda-12.4 bang setup.bat -Force neu driver >= 550')
                $runtimeOk = $true
                $runtimeNote = 'cuda (can kiem tra driver)'
            } else {
                Write-Ok 'runtime se chay tren CPU'
                $runtimeOk = $true
                $runtimeNote = 'cpu'
            }
        }
    } else {
        Write-Warn ('llama-server.exe khong chay duoc (exit ' + $v.ExitCode + ') - se tai lai')
    }
}
if (-not $runtimeOk) {
    if (Install-LlamaRuntime -Variant $variant) {
        Write-Ok ('da cai llama.cpp (' + $variant + ')')
        $runtimeOk = $true
        $runtimeNote = $variant
    } else {
        Write-Fail 'cai llama.cpp that bai'
    }
}
if ($runtimeOk) { Add-Result 'llama.cpp runtime' 'OK' $runtimeNote }
else { Add-Result 'llama.cpp runtime' 'FAIL' $variant }

# ============================ 5. models =====================================
Write-Section '5. Model gguf'

$ModelManifestFile = Join-Path $PSScriptRoot 'models.json'
$ModelManifest = @()
if (Test-Path $ModelManifestFile) {
    try {
        $raw = Get-Content -LiteralPath $ModelManifestFile -Raw
        $ModelManifest = @((ConvertFrom-Json $raw))
    } catch {
        Write-Warn ('khong doc duoc ' + $ModelManifestFile + ': ' + $_.Exception.Message)
    }
}
if ($ModelManifest.Count -eq 0) {
    Write-Warn 'manifest model rong - dung danh sach du phong trong setup.ps1'
    $ModelManifest = @(
        @{ File = 'Qwen2.5-Coder-3B-Instruct-abliterated-Q4_K_M.gguf'; Repo = 'bartowski/Qwen2.5-Coder-3B-Instruct-abliterated-GGUF'; Group = 'core'; Note = 'text model mac dinh' },
        @{ File = 'Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf'; Repo = 'ggml-org/Qwen2.5-VL-3B-Instruct-GGUF'; Group = 'core'; Note = 'vision model' },
        @{ File = 'mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf'; Repo = 'ggml-org/Qwen2.5-VL-3B-Instruct-GGUF'; Group = 'core'; Note = 'mmproj' }
    )
}

$modelList = @()
if ($Models -eq 'none') {
    Write-Info 'bo qua tai model (-Models none)'
} elseif ($Models -eq 'core') {
    $modelList = @($ModelManifest | Where-Object { $_.Group -eq 'core' })
} else {
    $modelList = @($ModelManifest)
}

$modelOk = 0; $modelSkip = 0; $modelFail = 0
foreach ($m in $modelList) {
    $dest = Join-Path $ModelsDir $m.File
    if (Test-Path $dest) {
        $len = (Get-Item -LiteralPath $dest).Length
        if ($len -gt 50MB) {
            Write-Ok ($m.File + ' - da co san (' + [math]::Round($len / 1GB, 2) + ' GB)')
            $modelSkip++
            continue
        }
        Write-Warn ($m.File + ' - file khong hop le, tai lai')
        try { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue } catch { }
    }
    $url = 'https://huggingface.co/' + $m.Repo + '/resolve/main/' + [uri]::EscapeDataString($m.File)
    Write-Info ('tai ' + $m.File)
    Write-Info ('   ' + $m.Repo)
    $part = $dest + '.part'
    $r = Invoke-Native -FilePath 'curl.exe' -ArgumentList @(
        '-L', '--fail', '--retry', '3', '--retry-delay', '5', '-C', '-', '-#',
        '-o', $part, $url) -TimeoutSec 7200 -Live
    if ($r.ExitCode -eq 0 -and (Test-Path $part)) {
        $len = (Get-Item -LiteralPath $part).Length
        if ($len -gt 50MB) {
            Move-Item -LiteralPath $part -Destination $dest -Force
            Write-Ok ('xong ' + $m.File + ' (' + [math]::Round($len / 1GB, 2) + ' GB) - ' + $m.Note)
            $modelOk++
        } else {
            Write-Fail ('file tai ve qua nho: ' + $m.File)
            $modelFail++
        }
    } else {
        Write-Fail ('tai that bai: ' + $m.File + ' (exit ' + $r.ExitCode + ')')
        $modelFail++
    }
}
if ($modelList.Count -gt 0) {
    $mStatus = 'OK'
    if ($modelFail -gt 0) { $mStatus = 'WARN' }
    Add-Result 'Models' $mStatus ("tai moi: $modelOk | co san: $modelSkip | that bai: $modelFail")
}

# ============================ 6. launcher + PATH ============================
Write-Section '6. Lenh global + PATH'

$CmdName = Get-ConfigValue 'LA_COMMAND_NAME' 'ngu'
$installOk = $false
if (Test-Path $VenvPy) {
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @('-m', 'app.cli', 'install', '--name', $CmdName, '--scope', 'user') -TimeoutSec 240
    if ($r.ExitCode -eq 0) {
        $installOk = $true
        Write-Ok ('lenh global: ' + $CmdName)
    } else {
        Write-Warn ('app.cli install that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim())
    }
}
if (-not $installOk) {
    $shim = Join-Path $Root ($CmdName + '.cmd')
    $shimText = "@echo off`r`nsetlocal`r`nchcp 65001 >nul`r`n" +
        "set ""PYTHONUTF8=1""`r`n" +
        "set ""LA_PROJECT=%~dp0""`r`n" +
        "if ""%LA_PROJECT:~-1%""==""\"" set ""LA_PROJECT=%LA_PROJECT:~0,-1%""`r`n" +
        "if not defined LA_WORKDIR set ""LA_WORKDIR=%CD%""`r`n" +
        "set ""PYTHONPYCACHEPREFIX=%LA_PROJECT%\cache\python""`r`n" +
        "if not exist ""%PYTHONPYCACHEPREFIX%"" mkdir ""%PYTHONPYCACHEPREFIX%"" >nul 2>&1`r`n" +
        "cd /d ""%LA_PROJECT%""`r`n" +
        "set ""PYTHON=%LA_PROJECT%\.venv\Scripts\python.exe""`r`n" +
        "if not exist ""%PYTHON%"" (`r`n" +
        "  echo [ERROR] Chua cai moi truong Python. Chay setup.bat trong %LA_PROJECT%`r`n" +
        "  exit /b 1`r`n" +
        ")`r`n" +
        """%PYTHON%"" -m app.cli %*`r`n"
    try {
        [IO.File]::WriteAllText($shim, $shimText, (New-Object System.Text.UTF8Encoding($false)))
        Write-Ok ('da tao launcher: ' + $shim)
        $installOk = $true
    } catch { Write-Fail ('khong tao duoc launcher: ' + $_.Exception.Message) }
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @()
if ($userPath) { $parts = @($userPath -split ';' | Where-Object { $_ -ne '' }) }
if ($parts -notcontains $Root) {
    try {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $Root) -join ';'), 'User')
        Write-Ok ('da them vao User PATH: ' + $Root)
        Add-Result 'Lenh global + PATH' 'OK' ('User PATH: ' + $Root)
    } catch {
        Write-Warn ('khong sua duoc User PATH: ' + $_.Exception.Message)
        Add-Result 'Lenh global + PATH' 'WARN' 'khong sua duoc User PATH'
    }
} else {
    Write-Ok ('User PATH da co san: ' + $Root)
    Add-Result 'Lenh global + PATH' 'OK' ('User PATH: ' + $Root)
}

# ============================ 7. opencode MCP ===============================
Write-Section '7. opencode MCP (local-agents)'

$Host_ = Get-ConfigValue 'LA_HOST' '127.0.0.1'
$Port_ = Get-ConfigValue 'LA_PORT' '8765'
$mcpUrl = 'http://' + $Host_ + ':' + $Port_ + '/mcp'
$mcpOk = $false
if (Test-Command 'opencode') {
    $r = Invoke-Native -FilePath 'cmd.exe' -ArgumentList @(
        '/c', 'opencode', 'mcp', 'add', 'local-agents', '--url', $mcpUrl, '--global') -TimeoutSec 180
    if ($r.ExitCode -eq 0) {
        $mcpOk = $true
        Write-Ok ('da dang ky MCP: ' + $mcpUrl)
    } else {
        Write-Warn ('opencode mcp add that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim())
    }
}
if (-not $mcpOk -and (Test-Path $VenvPy)) {
    $patch = Join-Path $PSScriptRoot 'patch_opencode_mcp.py'
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @($patch, $mcpUrl) -TimeoutSec 120
    if ($r.ExitCode -eq 0) {
        $mcpOk = $true
        Write-Ok ('da dang ky MCP (tu sua file config): ' + $mcpUrl)
    } else {
        Write-Warn ('ghi file opencode.json that bai: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim())
    }
}
if (-not $mcpOk) {
    Write-Warn ('chua dang ky duoc MCP. Chay tay: opencode mcp add local-agents --url ' + $mcpUrl + ' --global')
    Add-Result 'opencode MCP' 'WARN' 'chua dang ky'
} else {
    Add-Result 'opencode MCP' 'OK' $mcpUrl
}

# ============================ 8. verification ===============================
Write-Section '8. Kiem tra'

if (Test-Path $VenvPy) {
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @('-m', 'app.cli', 'models') -TimeoutSec 240
    if ($r.ExitCode -eq 0) {
        Write-Ok 'lenh "models" chay OK'
        Write-Block $r.Output
    } else {
        Write-Fail ('lenh "models" that bai (exit ' + $r.ExitCode + ')')
    }

    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @('-m', 'app.cli', 'status') -TimeoutSec 240
    if ($r.ExitCode -eq 0) {
        Write-Ok 'lenh "status" chay OK'
        Write-Block ($r.Output + $r.Error)
    } else {
        Write-Warn ('lenh "status" co loi: ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim())
    }
}

$smokeOk = $false
if ($SkipSmoke) {
    Write-Info 'bo qua bai kiem tra end-to-end (-SkipSmoke)'
} elseif (-not (Test-Path $VenvPy)) {
    Write-Warn 'khong co venv nen bo qua bai kiem tra end-to-end'
} else {
    $smoke = Join-Path $PSScriptRoot 'smoke_test.py'
    Write-Info 'bat dau kiem tra end-to-end (load model + 1 completion, co the vai phut)...'
    $r = Invoke-Native -FilePath $VenvPy -ArgumentList @($smoke) -TimeoutSec 2400
    if ($r.ExitCode -eq 0) {
        $smokeOk = $true
        Write-Ok 'kiem tra end-to-end: OK'
        Write-Block ($r.Output + $r.Error)
    } else {
        Write-Warn ('kiem tra end-to-end that bai (exit ' + $r.ExitCode + '): ' + ((Flat-Text ($r.Output + $r.Error)) -replace '\s+', ' ').Trim())
    }
}
if ($smokeOk) { Add-Result 'Kiem tra end-to-end' 'OK' 'model + completion' }
else { Add-Result 'Kiem tra end-to-end' 'WARN' 'xem log' }

# ============================ 9. report =====================================
Write-Section '9. Bao cao'

$lines = New-Object System.Collections.ArrayList
[void]$lines.Add('local-agents - SETUP REPORT')
[void]$lines.Add('Thoi diem   : ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
[void]$lines.Add('Project    : ' + $Root)
[void]$lines.Add('May        : ' + $env:COMPUTERNAME + ' | ' + [Environment]::OSVersion.VersionString)
[void]$lines.Add('Python env : ' + $VenvPy)
[void]$lines.Add('llama.cpp  : ' + $LlamaDir + ' (' + $runtimeNote + ')')
[void]$lines.Add('Models dir : ' + $ModelsDir)
[void]$lines.Add('MCP        : ' + $mcpUrl)
[void]$lines.Add('')
[void]$lines.Add('--- KET QUA CAC BUOC ---')
foreach ($r in $script:Results) {
    [void]$lines.Add(('  [{0}] {1} :: {2}' -f $r.Status, $r.Step, $r.Detail))
}
[void]$lines.Add('')
[void]$lines.Add('--- SU DUNG ---')
[void]$lines.Add('  ' + $CmdName + '                    # chat (agent lam viec trong thu muc hien tai)')
[void]$lines.Add('  ' + $CmdName + ' run "task"          # chay 1 task')
[void]$lines.Add('  ' + $CmdName + ' models              # danh sach model')
[void]$lines.Add('  ' + $CmdName + ' status              # trang thai runtime')
[void]$lines.Add('  ' + $CmdName + ' serve               # web board + MCP (' + $mcpUrl + ')')
[void]$lines.Add('')
[void]$lines.Add('  Mo terminal moi truoc khi dung lenh "' + $CmdName + '" (PATH moi).')
[void]$lines.Add('  Web board : http://' + $Host_ + ':' + $Port_ + '/')
try { [IO.File]::WriteAllLines($ReportFile, $lines, (New-Object System.Text.UTF8Encoding($false))) } catch { }
Write-Ok ('bao cao: ' + $ReportFile)
Write-Ok ('log     : ' + $LogFile)

Write-Host ''
if ($script:Fails -eq 0) {
    Write-Host '  ================================================================' -ForegroundColor Green
    Write-Host ('   SETUP THANH CONG - MO TERMINAL MOI VA CHAY:  ' + $CmdName) -ForegroundColor Green
    Write-Host '  ================================================================' -ForegroundColor Green
} else {
    Write-Host '  ================================================================' -ForegroundColor Yellow
    Write-Host ('   SETUP XONG NHUNG CO ' + $script:Fails + ' BUOC LOI - XEM setup-report.txt') -ForegroundColor Yellow
    Write-Host '  ================================================================' -ForegroundColor Yellow
}
Write-Host ''

if ($KeepOpen) { exit 0 }
if ($script:Fails -gt 0) { exit 1 }
exit 0
