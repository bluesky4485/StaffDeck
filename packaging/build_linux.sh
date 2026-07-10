#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
VERSION="${VERSION:-0.1.0}"
# 去掉 tag 前缀 v（GitHub ref_name 可能是 v0.1.0），fpm/deb 版本号不能带 v
DEB_VERSION="${VERSION#v}"

echo "==> [1/6] 构建前端"
npm --prefix frontend-enterprise run build

echo "==> [2/6] 后端 venv + 运行依赖 + 打包工具（CI 用标准 pip）"
python -m venv backend/.venv
backend/.venv/bin/python -m pip install -U pip
# 从 pyproject 提取 runtime 依赖（不 editable 安装本项目）
DEPS="$(cd backend && python -c "import tomllib,pathlib; print(' '.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))")"
backend/.venv/bin/python -m pip install $DEPS
backend/.venv/bin/python -m pip install "pyinstaller>=6.6.0" "certifi>=2024.2.2"

echo "==> [3/6] PyInstaller 打包"
( cd backend && .venv/bin/pyinstaller ../packaging/ultrarag.spec --noconfirm \
    --distpath ../packaging/out --workpath ../packaging/build )

echo "==> [4/6] 附带 python 运行时"
python packaging/fetch_runtime_python.py packaging/runtime_dl --expect-arch x86_64
rm -rf packaging/out/staffdeck/runtime
cp -R packaging/runtime_dl/python packaging/out/staffdeck/runtime

echo "==> [5/6] 打 .deb（fpm）"
STAGE="packaging/out/deb"
rm -rf "$STAGE"
mkdir -p "$STAGE/opt/staffdeck" "$STAGE/usr/bin" "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/128x128/apps"
cp -R packaging/out/staffdeck/* "$STAGE/opt/staffdeck/"
cp packaging/assets/staffdeck.png "$STAGE/usr/share/icons/hicolor/128x128/apps/staffdeck.png"
cat > "$STAGE/usr/bin/staffdeck" <<'SH'
#!/bin/sh
exec /opt/staffdeck/staffdeck "$@"
SH
chmod +x "$STAGE/usr/bin/staffdeck"
cat > "$STAGE/usr/share/applications/staffdeck.desktop" <<'DESK'
[Desktop Entry]
Name=URStaff
Exec=staffdeck
Icon=staffdeck
Type=Application
Categories=Utility;
DESK
fpm -s dir -t deb -n staffdeck -v "$DEB_VERSION" -C "$STAGE" \
  --description "URStaff desktop service" \
  -p "packaging/out/URStaff-${VERSION}-linux-x86_64.deb" .

echo "==> [6/6] 打 .AppImage（appimagetool）"
APPDIR="packaging/out/URStaff.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/staffdeck"
cp -R packaging/out/staffdeck/* "$APPDIR/usr/lib/staffdeck/"
cat > "$APPDIR/usr/bin/staffdeck" <<'SH'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/../lib/staffdeck/staffdeck" "$@"
SH
chmod +x "$APPDIR/usr/bin/staffdeck"
cat > "$APPDIR/AppRun" <<'SH'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/staffdeck" "$@"
SH
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/staffdeck.desktop" <<'DESK'
[Desktop Entry]
Name=URStaff
Exec=staffdeck
Icon=staffdeck
Type=Application
Categories=Utility;
DESK
cp packaging/assets/staffdeck.png "$APPDIR/staffdeck.png"

# appimagetool：CI 会预先下载到 ./appimagetool 并 chmod +x（见 workflow）
APPIMAGETOOL="${APPIMAGETOOL:-./appimagetool}"
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" \
  "packaging/out/URStaff-${VERSION}-linux-x86_64.AppImage"

echo "built:"
ls -lh packaging/out/URStaff-*-linux-x86_64.deb packaging/out/URStaff-*-linux-x86_64.AppImage
