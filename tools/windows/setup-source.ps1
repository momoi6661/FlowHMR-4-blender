param(
    [string]$PythonVersion = '3.10.22',
    [string]$Proxy = $env:HTTPS_PROXY,
    [string]$CorrectionNative,
    [switch]$SkipInstall,
    # Kept for callers of the older script; editable packages are now managed by uv.
    [switch]$SkipEditable,
    [switch]$InstallFbx
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install uv first, then rerun this script.'
}

if ($Proxy) {
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
}

function Invoke-Uv {
    & uv @args
    if ($LASTEXITCODE -ne 0) {
        throw "uv command failed with exit code ${LASTEXITCODE}: uv $($args -join ' ')"
    }
}

$venv = Join-Path $projectRoot '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    Write-Host "Creating Python $PythonVersion environment..."
    Invoke-Uv venv --python $PythonVersion $venv
}

$python = Join-Path $venv 'Scripts\python.exe'
& $python -c 'import sys; print(sys.version)'

if (-not $SkipInstall) {
    Write-Host 'Preparing the verified native contact correction wheel...'
    $correctionArgs = @('tools/windows/install_correction.py', '--prepare-only')
    if ($CorrectionNative) { $correctionArgs += @('--native', $CorrectionNative) }
    & $python @correctionArgs
    if ($LASTEXITCODE -ne 0) { throw 'Contact correction wheel preparation failed. See docs/windows.md.' }
    Write-Host 'Syncing the uv project environment...'
    Invoke-Uv sync --locked --python $python
}

if ($SkipEditable) {
    Write-Host 'SkipEditable is no longer needed: local packages are managed by uv.lock.'
}

& $python tools/windows/adapt_sam.py
if ($LASTEXITCODE -ne 0) { throw 'SAM source adaptation failed.' }

& $python -c 'import sys, torch, torchvision, flask, yolox; assert sys.version_info[:2] == (3, 10); print(torch.__version__, torch.cuda.is_available())'
if ($LASTEXITCODE -ne 0) { throw 'Source environment import verification failed.' }
& $python -c "from flowhmr.utils.runtime_capabilities import native_correction_available; assert native_correction_available(), 'Native contact correction is unavailable'; print('Native contact correction ready')"
if ($LASTEXITCODE -ne 0) { throw 'Contact correction verification failed.' }
if ($InstallFbx) {
    & $python tools/windows/install_fbx.py
    if ($LASTEXITCODE -ne 0) { throw 'FBX binding installation failed. See docs/windows.md.' }
    & $python -c "import fbx; assert hasattr(fbx, 'FbxManager'); print('FBX export ready')"
    if ($LASTEXITCODE -ne 0) { throw 'FBX binding verification failed.' }
}
Write-Host "FlowHMR source environment ready: $python"
Write-Host 'Next: install models, then run start-web.cmd -ServerOnly or run_v2m_demo_generation.py.'
