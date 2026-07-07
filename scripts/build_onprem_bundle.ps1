param(
    [string]$BundleDir = "bundle\sync-bundle",
    [string]$AppImageTag = "odt-pipeline:onprem",
    [string]$DbImage = "pgvector/pgvector:pg16",
    [switch]$InstallLibreOffice,
    [string]$ModelSource = "C:\Users\YCM\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots\5617a9f61b028005a4858fdac845db406aefb181",
    [string]$MetadataSource = ""
)

$ErrorActionPreference = "Stop"

function Test-BundlePathSafety {
    param(
        [string]$RepoRoot,
        [string]$TargetPath
    )

    $resolvedRoot = [System.IO.Path]::GetFullPath($RepoRoot)
    $resolvedTarget = [System.IO.Path]::GetFullPath($TargetPath)

    if ($resolvedTarget -eq $resolvedRoot) {
        throw "BundleDir cannot be the repository root."
    }

    $rootWithSeparator = $resolvedRoot.TrimEnd('\') + '\'
    if (-not $resolvedTarget.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "BundleDir must stay under the repository root: $resolvedRoot"
    }
}

function Assert-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker CLI was not found in PATH."
    }

    docker info | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon is not available. Start Docker Desktop or the Docker service and retry."
    }
}

function Assert-LastExitCode {
    param(
        [string]$Action
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE."
    }
}

function Copy-IfProvided {
    param(
        [string]$Source,
        [string]$Destination
    )

    if ([string]::IsNullOrWhiteSpace($Source)) {
        return
    }

    $resolvedSource = (Resolve-Path -LiteralPath $Source).ProviderPath
    $destinationParent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $destinationParent)) {
        New-Item -ItemType Directory -Path $destinationParent | Out-Null
    }

    if ((Get-Item -LiteralPath $resolvedSource).PSIsContainer) {
        if (-not (Test-Path -LiteralPath $Destination)) {
            New-Item -ItemType Directory -Path $Destination | Out-Null
        }

        Copy-Item -Recurse -Force -Path (Join-Path $resolvedSource "*") -Destination $Destination
    }
    else {
        Copy-Item -Force -LiteralPath $resolvedSource -Destination $Destination
    }
}

function Copy-ModelSource {
    param(
        [string]$Source,
        [string]$Destination
    )

    if ([string]::IsNullOrWhiteSpace($Source)) {
        return
    }

    $resolvedSource = Resolve-Path -LiteralPath $Source
    if (-not (Get-Item -LiteralPath $resolvedSource).PSIsContainer) {
        throw "ModelSource must be a directory: $resolvedSource"
    }

    if (-not (Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Path $Destination | Out-Null
    }

    robocopy $resolvedSource $Destination /E /R:2 /W:2 | Out-Host
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy model files failed with exit code $LASTEXITCODE."
    }
    $global:LASTEXITCODE = 0
}

$repoRoot = (Get-Location).ProviderPath
$bundlePath = if ([System.IO.Path]::IsPathRooted($BundleDir)) {
    [System.IO.Path]::GetFullPath($BundleDir)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BundleDir))
}

Test-BundlePathSafety -RepoRoot $repoRoot -TargetPath $bundlePath
Assert-DockerReady

if (Test-Path -LiteralPath $bundlePath) {
    Remove-Item -Recurse -Force -LiteralPath $bundlePath
}

New-Item -ItemType Directory -Path $bundlePath | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundlePath "data") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundlePath "metadata") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundlePath "models") | Out-Null

$buildArgs = @(
    "build",
    "-t", $AppImageTag,
    "-f", "Dockerfile",
    "."
)

if ($InstallLibreOffice) {
    $buildArgs = @(
        "build",
        "--build-arg", "INSTALL_LIBREOFFICE=true",
        "-t", $AppImageTag,
        "-f", "Dockerfile",
        "."
    )
}

Write-Host "[1/4] Building application image: $AppImageTag"
docker @buildArgs
Assert-LastExitCode "docker build"

Write-Host "[2/4] Pulling database image: $DbImage"
docker pull $DbImage
Assert-LastExitCode "docker pull"

$imageTar = Join-Path $bundlePath "docker-images.tar"
Write-Host "[3/4] Saving docker images to $imageTar"
docker save -o $imageTar $AppImageTag $DbImage
Assert-LastExitCode "docker save"

Write-Host "[4/4] Copying deployment assets"
Copy-Item -Force ".env.onprem.example" (Join-Path $bundlePath ".env.onprem.example")
Copy-Item -Force "docker-compose.onprem.yml" (Join-Path $bundlePath "docker-compose.onprem.yml")
Copy-Item -Force "ONPREM_DOCKER.md" (Join-Path $bundlePath "ONPREM_DOCKER.md")
Copy-Item -Force "HOW_TO_RUN.md" (Join-Path $bundlePath "HOW_TO_RUN.md")

Copy-ModelSource -Source $ModelSource -Destination (Join-Path $bundlePath "models\bge-m3")
Copy-IfProvided -Source $MetadataSource -Destination (Join-Path $bundlePath "metadata")

Write-Host "Bundle created:"
Write-Host "  $bundlePath"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Copy the bundle folder to the on-prem host."
Write-Host "  2. Run: docker load -i docker-images.tar"
Write-Host "  3. Copy .env.onprem.example to .env.onprem and adjust values."
Write-Host "  4. Follow ONPREM_DOCKER.md."
