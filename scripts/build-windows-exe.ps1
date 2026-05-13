[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$scriptPath = $PSCommandPath
if (-not $scriptPath) {
    $scriptPath = $MyInvocation.MyCommand.Path
}

$projectRoot = Resolve-Path (Join-Path (Split-Path -Parent $scriptPath) "..")
$specPath = Join-Path $projectRoot "build\windows\1C_cf_cfu_merge.spec"
$workPath = Join-Path $projectRoot "build\pyinstaller"
$distPath = Join-Path $projectRoot "dist"
$exePath = Join-Path $distPath "1C_cf_cfu_merge.exe"

function Assert-InProject {
    param(
        [Parameter(Mandatory)]
        [string]$PathToCheck
    )

    $fullPath = [System.IO.Path]::GetFullPath($PathToCheck)
    $rootPath = [System.IO.Path]::GetFullPath($projectRoot.Path)
    if (-not $fullPath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch path outside project: $fullPath"
    }
}

if (-not (Test-Path $specPath -PathType Leaf)) {
    throw "PyInstaller spec not found: $specPath"
}

Assert-InProject $workPath
Assert-InProject $distPath

Push-Location $projectRoot
try {
    if (Test-Path $workPath) {
        Remove-Item -LiteralPath $workPath -Recurse -Force
    }
    if (Test-Path $distPath) {
        Remove-Item -LiteralPath $distPath -Recurse -Force
    }

    if (-not $SkipInstall) {
        & $Python -m pip install -e ".[build]"
        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed with code $LASTEXITCODE"
        }
    }

    & $Python -m PyInstaller `
        --clean `
        --noconfirm `
        --workpath $workPath `
        --distpath $distPath `
        $specPath

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with code $LASTEXITCODE"
    }

    if (-not (Test-Path $exePath -PathType Leaf)) {
        throw "Build completed, but exe was not created: $exePath"
    }

    Write-Host "Built: $exePath"
} finally {
    Pop-Location
}
