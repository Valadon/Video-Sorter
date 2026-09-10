[CmdletBinding()]
param(
    [string]$InstallDirectory,
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($InstallDirectory)) {
    $InstallDirectory = $PSScriptRoot
}
$resolvedInstallDirectory = (Resolve-Path -LiteralPath $InstallDirectory).Path
$executable = Join-Path $resolvedInstallDirectory "video_sorter.exe"

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Video Sorter executable was not found: $executable"
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $resolvedInstallDirectory "config.ini"
}
$resolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path

# An explicit working directory and config path make shortcut and scheduler
# launches deterministic, including the existing folder with spaces.
Set-Location -LiteralPath $resolvedInstallDirectory
& $executable --config $resolvedConfigPath
exit $LASTEXITCODE
