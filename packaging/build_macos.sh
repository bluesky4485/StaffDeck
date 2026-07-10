#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
VERSION="${VERSION:-0.1.0}"
ARCH="$(uname -m)"

echo "==> [1/5] 构建前端"
npm --prefix frontend-enterprise run build

echo "==> [2/5] 后端 venv + 运行依赖 + 打包工具"
cd backend
if [ ! -x ".venv/bin/pyinstaller" ]; then
  # 若无 venv（如 CI 全新 checkout），自建并装运行依赖
  if [ ! -x ".venv/bin/python" ]; then
    python3 -m venv .venv
    .venv/bin/python -m ensurepip --upgrade 2>/dev/null || true
  fi
  # 装运行依赖（从 pyproject 提取；本项目不 editable 安装）
  if .venv/bin/python -m pip --version >/dev/null 2>&1; then
    DEPS="$(.venv/bin/python -c "import tomllib,pathlib; print(' '.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))")"
    .venv/bin/python -m pip install -U pip
    .venv/bin/python -m pip install $DEPS "pyinstaller>=6.6.0" "certifi>=2024.2.2"
  elif command -v uv >/dev/null 2>&1; then
    # 本机 venv 由 uv 管理、无 pip：用 uv pip 补装打包工具（运行依赖已在 venv 中）
    VIRTUAL_ENV="$(pwd)/.venv" uv pip install "pyinstaller>=6.6.0" "certifi>=2024.2.2"
  else
    echo "无法安装打包依赖：venv 既无 pip 也无 uv" >&2
    exit 1
  fi
fi
# macOS Dock 壳依赖 pyobjc（幂等，已装则跳过）
if ! .venv/bin/python -c "import AppKit" >/dev/null 2>&1; then
  if .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m pip install "pyobjc-framework-Cocoa>=10.0"
  elif command -v uv >/dev/null 2>&1; then
    VIRTUAL_ENV="$(pwd)/.venv" uv pip install "pyobjc-framework-Cocoa>=10.0"
  fi
fi

echo "==> [3/5] PyInstaller 打包（spec 在 macOS 下同时产出 URStaff.app）"
.venv/bin/pyinstaller ../packaging/ultrarag.spec --noconfirm \
  --distpath ../packaging/out --workpath ../packaging/build
cd "$REPO"
APP="packaging/out/URStaff.app"
test -d "$APP" || { echo "PyInstaller 未产出 $APP"; exit 1; }

echo "==> [4/5] 附带 python 运行时（拷进 .app/Contents/MacOS/runtime）"
python3 packaging/fetch_runtime_python.py packaging/runtime_dl --expect-arch "$ARCH"
rm -rf "$APP/Contents/MacOS/runtime"
cp -R packaging/runtime_dl/python "$APP/Contents/MacOS/runtime"

echo "==> [5/5] 签名（不用 --deep）+ 打 dmg"
# ad-hoc 签名：不用 --deep（会破坏附带 python 二进制原签名，Hardened Runtime 下杀进程）。
# 先签附带 runtime 里的可执行/动态库（可选，best-effort），再签主程序、最后签 bundle 顶层。
find "$APP/Contents/MacOS/runtime" -type f \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) \
  -exec codesign --force --sign - {} \; 2>/dev/null || true
codesign --force --sign - "$APP/Contents/MacOS/staffdeck" 2>/dev/null || echo "主程序 ad-hoc 签名跳过"
codesign --force --sign - "$APP" 2>/dev/null || echo "bundle 顶层 ad-hoc 签名跳过"

DMG="packaging/out/URStaff-${VERSION}-macos-${ARCH}.dmg"
rm -f "$DMG"
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg --volname "URStaff" --window-size 520 320 \
    --app-drop-link 380 170 --icon "URStaff.app" 140 170 \
    --volicon "packaging/assets/staffdeck.icns" \
    "$DMG" "$APP" \
    || hdiutil create -volname URStaff -srcfolder "$APP" -ov -format UDZO "$DMG"
else
  hdiutil create -volname URStaff -srcfolder "$APP" -ov -format UDZO "$DMG"
fi
echo "built $DMG"
ls -lh "$DMG"
