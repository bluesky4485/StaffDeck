from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from app import paths
from app.main import app


ROOT_DIR = paths.resource_dir()
# frozen: dist 被收集到 _MEIPASS/frontend-enterprise/dist
# dev:    resource_dir()==backend/，需回到仓库根找 frontend-enterprise
ENTERPRISE_DIST = (
    ROOT_DIR / "frontend-enterprise" / "dist"
    if paths.is_frozen()
    else ROOT_DIR.parent / "frontend-enterprise" / "dist"
)
SPA_INDEX_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def spa_index_response(index_path: Path) -> FileResponse:
    return FileResponse(index_path, headers=SPA_INDEX_HEADERS)

app.mount(
    "/assets",
    StaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
    name="assets",
)
app.mount(
    "/enterprise/assets",
    StaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
    name="enterprise-assets",
)
app.mount(
    "/chat/assets",
    StaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
    name="chat-assets",
)
app.mount(
    "/workspace/assets",
    StaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
    name="workspace-assets",
)


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/chat/")


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
@app.get("/staffdeck-icon.png", include_in_schema=False)
def brand_icon(request: Request) -> FileResponse:
    # 品牌图标：从前端 dist 根目录 serve（favicon.ico/png、apple-touch-icon）
    name = request.url.path.lstrip("/")
    target = ENTERPRISE_DIST / name
    if not target.exists():
        target = ENTERPRISE_DIST / "favicon.ico"
    return FileResponse(target)


@app.get("/enterprise", include_in_schema=False)
@app.get("/enterprise/{path:path}", include_in_schema=False)
def enterprise_app(path: str = "") -> FileResponse:
    return spa_index_response(ENTERPRISE_DIST / "index.html")


@app.get("/login", include_in_schema=False)
@app.get("/chat", include_in_schema=False)
@app.get("/chat/{path:path}", include_in_schema=False)
@app.get("/workspace", include_in_schema=False)
@app.get("/workspace/{path:path}", include_in_schema=False)
def chat_app(path: str = "") -> FileResponse:
    return spa_index_response(ENTERPRISE_DIST / "index.html")
