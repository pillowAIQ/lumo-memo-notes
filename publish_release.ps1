param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$releaseWorkspace = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$releaseDist = [IO.Path]::GetFullPath((Join-Path $releaseWorkspace 'dist'))
$releaseDestination = [IO.Path]::GetFullPath((Join-Path $releaseDist 'Lumo'))
$releaseSource = [IO.Path]::GetFullPath((Join-Path $releaseWorkspace 'build\release-stage\Lumo'))
foreach ($releasePath in @($releaseDestination, $releaseSource)) {
    if (-not $releasePath.StartsWith($releaseWorkspace + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Release path outside workspace' }
}
$activeMemo = @(Get-CimInstance Win32_Process -Filter "name = 'Lumo.exe'" | Where-Object { $_.ExecutablePath -like ($releaseDist + '\*') })
if ($activeMemo.Count) { throw 'Exit Lumo from the system tray before building. No process was stopped.' }
if ($CheckOnly) { exit 0 }
foreach ($releaseParent in @($releaseDist, (Join-Path $releaseWorkspace 'build'), (Join-Path $releaseWorkspace 'build\release-stage'))) {
    if ((Test-Path -LiteralPath $releaseParent) -and ((Get-Item -LiteralPath $releaseParent).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Release parent is a link' }
}
if (-not (Test-Path -LiteralPath (Join-Path $releaseSource 'Lumo.exe'))) { throw 'Validated staging bundle missing' }
foreach ($releasePath in @($releaseSource, $releaseDestination)) {
    if (Test-Path -LiteralPath $releasePath) {
        if ((Get-Item -LiteralPath $releasePath).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Release path is a link' }
        if (@(Get-ChildItem -LiteralPath $releasePath -Recurse -Attributes ReparsePoint).Count) { throw 'Linked release content' }
    }
}
if (Test-Path -LiteralPath $releaseDestination) {
    Add-Type -AssemblyName Microsoft.VisualBasic
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($releaseDestination, [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs, [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)
}
New-Item -ItemType Directory -Path $releaseDist -Force | Out-Null
Move-Item -LiteralPath $releaseSource -Destination $releaseDestination
