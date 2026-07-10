# packaging/build_windows.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
if (-not $env:VERSION) { $env:VERSION = "0.1.0" }

Write-Host "==> [1/6] 构建前端"
npm --prefix frontend-enterprise run build

Write-Host "==> [2/6] 后端 venv + 运行依赖 + 打包工具"
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -U pip
# 从 pyproject 提取 runtime 依赖（不 editable 安装本项目，见 B3 修复）
Push-Location backend
$deps = python -c "import tomllib,pathlib; print('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))"
$deps | Out-File -Encoding utf8 ..\packaging\_win_reqs.txt
Pop-Location
backend\.venv\Scripts\python -m pip install -r packaging\_win_reqs.txt
backend\.venv\Scripts\python -m pip install "pyinstaller>=6.6.0" "certifi>=2024.2.2"

Write-Host "==> [3/6] PyInstaller 打包"
Push-Location backend
.\.venv\Scripts\pyinstaller ..\packaging\ultrarag.spec --noconfirm --distpath ..\packaging\out --workpath ..\packaging\build
Pop-Location

Write-Host "==> [4/6] 附带 python 运行时"
python packaging\fetch_runtime_python.py packaging\runtime_dl --expect-arch x86_64
if (Test-Path packaging\out\staffdeck\runtime) { Remove-Item -Recurse -Force packaging\out\staffdeck\runtime }
Copy-Item -Recurse -Force packaging\runtime_dl\python packaging\out\staffdeck\runtime

Write-Host "==> [5/6] Inno Setup 打 .exe 安装包"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
& "$iscc" packaging\installer\ultrarag.iss

Write-Host "==> [6/6] 重命名产物"
$out = "packaging\out\URStaff-$($env:VERSION)-windows-x64-setup.exe"
if (Test-Path $out) { Remove-Item -Force $out }
Rename-Item packaging\out\URStaff-setup.exe $out
Write-Host "built $out"
Get-ChildItem packaging\out\URStaff-*-windows-x64-setup.exe
