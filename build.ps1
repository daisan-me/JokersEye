$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = $PSScriptRoot
$source = [Drawing.Image]::FromFile((Join-Path $root 'assets\icon-master.png'))
try {
    $image = New-Object Drawing.Bitmap 256,256
    $graphics = [Drawing.Graphics]::FromImage($image)
    $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.DrawImage($source,0,0,256,256)
    $graphics.Dispose()
    $image.Save((Join-Path $root 'web\icon.png'),[Drawing.Imaging.ImageFormat]::Png)
    $image.Dispose()
    $sizes = @(16,24,32,48,64,128,256)
    $frames = @()
    foreach($size in $sizes) {
        $bitmap = New-Object Drawing.Bitmap $size,$size
        $g = [Drawing.Graphics]::FromImage($bitmap)
        $g.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($source,0,0,$size,$size)
        $g.Dispose()
        $stream = New-Object IO.MemoryStream
        $bitmap.Save($stream,[Drawing.Imaging.ImageFormat]::Png)
        $frames += ,$stream.ToArray()
        $stream.Dispose()
        $bitmap.Dispose()
    }
    $file = [IO.File]::Create((Join-Path $root 'web\icon.ico'))
    $writer = New-Object IO.BinaryWriter $file
    try {
        $writer.Write([UInt16]0); $writer.Write([UInt16]1); $writer.Write([UInt16]$sizes.Count)
        $offset = 6+16*$sizes.Count
        for($i=0;$i -lt $sizes.Count;$i++) {
            $dimension = if($sizes[$i] -eq 256){0}else{$sizes[$i]}
            $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
            $writer.Write([byte]0); $writer.Write([byte]0)
            $writer.Write([UInt16]1); $writer.Write([UInt16]32)
            $writer.Write([UInt32]$frames[$i].Length); $writer.Write([UInt32]$offset)
            $offset += $frames[$i].Length
        }
        foreach($frame in $frames){$writer.Write([byte[]]$frame)}
    } finally { $writer.Dispose(); $file.Dispose() }
} finally { $source.Dispose() }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /optimize+ /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll "/reference:$root\Microsoft.Web.WebView2.Core.dll" "/reference:$root\Microsoft.Web.WebView2.WinForms.dll" "/win32icon:$root\web\icon.ico" "/out:$root\Joker's eye.exe" "$root\launcher\Launcher.cs" "$root\launcher\SourceBrowser.cs"
if($LASTEXITCODE -ne 0){throw 'Launcher build failed'}
Write-Output "Built: $root\Joker's eye.exe"
