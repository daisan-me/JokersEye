$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$executable = Join-Path $PSScriptRoot "Joker's eye.exe"
$shortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "Joker's eye.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "Joker's eye.lnk")
)
foreach ($shortcutPath in $shortcutPaths) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ((Test-Path -LiteralPath $shortcutPath) -and [IO.Path]::GetFileName($shortcut.TargetPath) -ne "Joker's eye.exe") {
        throw "別のアプリを指す同名ショートカットがあります: $shortcutPath"
    }
    $shortcut.TargetPath = $executable
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = "$executable,0"
    $shortcut.Description = 'ゴッサムシティのデータ研究ワークスペース'
    $shortcut.Save()
    $registration = Start-Process -FilePath $executable -ArgumentList @('--register-shortcut', ('"' + $shortcutPath + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($registration.ExitCode -ne 0) { throw 'アプリ識別子の登録に失敗しました。' }
    Write-Output "Created: $shortcutPath"
}
