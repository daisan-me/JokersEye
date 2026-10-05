"""Build the existing C# launcher without changing PowerShell/security policy."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiler', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compiler = args.compiler or shutil.which('csc')
    if not compiler and os.environ.get('WINDIR'):
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    if not compiler or not Path(compiler).is_file():
        parser.error('Windows .NET Framework C# compiler is required (--compiler).')
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Net.Http.dll', 'System.Web.Extensions.dll',
                  str(root / 'Microsoft.Web.WebView2.Core.dll'), str(root / 'Microsoft.Web.WebView2.WinForms.dll')]
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/optimize+', '/platform:x64',
                    *('/reference:' + reference for reference in references),
                    '/win32icon:' + str(root / 'web' / 'icon.ico'), '/out:' + str(root / "Joker's eye.exe"),
                    str(root / 'launcher' / 'Launcher.cs'), str(root / 'launcher' / 'SourceBrowser.cs')], cwd=root, check=True)
    print('Windows launcher built; distribute web/source-readiness.js with it.')


if __name__ == '__main__':
    main()
