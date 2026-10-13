param(
    [string]$PackagePath = (Join-Path $PSScriptRoot 'dist\VideoEditorIndependent'),
    [string]$Version = '1.3.1'
)
$ErrorActionPreference = 'Stop'
$taskPackageRoot = (Resolve-Path -LiteralPath $PackagePath).Path
$taskPackageExe = Join-Path $taskPackageRoot 'VideoEditorIndependent.exe'
if (-not (Test-Path -LiteralPath $taskPackageExe -PathType Leaf)) {
    throw 'The packaged application executable is missing.'
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Version must use three numeric components.' }
$taskApplicationRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Video Editor Independent'
$taskInstallRoot = Join-Path $taskApplicationRoot ('versions\' + $Version)
New-Item -ItemType Directory -Path $taskInstallRoot -Force | Out-Null
Get-ChildItem -LiteralPath $taskPackageRoot -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $taskInstallRoot -Recurse -Force
}
$taskInstalledExe = Join-Path $taskInstallRoot 'VideoEditorIndependent.exe'
if ((Get-FileHash -LiteralPath $taskInstalledExe).Hash -ne (Get-FileHash -LiteralPath $taskPackageExe).Hash) {
    throw 'The installed executable did not match the verified package.'
}
$taskShortcutShell = New-Object -ComObject WScript.Shell
$taskShortcutFolders = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
)
foreach ($taskShortcutFolder in $taskShortcutFolders) {
    if (-not (Test-Path -LiteralPath $taskShortcutFolder -PathType Container)) { continue }
    $taskShortcut = $taskShortcutShell.CreateShortcut((Join-Path $taskShortcutFolder 'Video Editor Independent.lnk'))
    $taskShortcut.TargetPath = $taskInstalledExe
    $taskShortcut.WorkingDirectory = $taskInstallRoot
    $taskShortcut.IconLocation = $taskInstalledExe + ',0'
    $taskShortcut.Description = 'Video editor with local data and owner control'
    $taskShortcut.Save()
}
Write-Output $taskInstalledExe
