from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def build_server_config() -> dict:
    return {
        "app": "single_port_app:app",
        "host": os.environ.get("ULTRARAG_HOST", "127.0.0.1"),
        "port": int(os.environ.get("ULTRARAG_PORT", "5173")),
    }


def _redirect_logs_when_frozen() -> None:
    # console=False 的 GUI app 没有终端，stdout/stderr 会丢失。
    # 打包态把日志重定向到用户数据目录，启动/运行问题可查文件。
    if not getattr(sys, "frozen", False):
        return
    try:
        from app import paths
        log_dir = paths.user_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "staffdeck.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file
    except Exception:
        pass


def apply_runtime_env() -> None:
    # 时序契约：必须在任何 app.config 被 import 之前调用；仅 frozen 态断言，
    # 开发/测试进程通常已 import 过 app.config，无条件断言会误炸。
    if getattr(sys, "frozen", False):
        assert "app.config" not in sys.modules, "apply_runtime_env 必须在 import app.* 之前调用"

    cfg = build_server_config()
    origin = f"http://{cfg['host']}:{cfg['port']}"
    os.environ.setdefault("TOOL_BASE_URL", origin)
    existing_cors = os.environ.get("CORS_ORIGINS", "")
    if origin not in existing_cors:
        os.environ["CORS_ORIGINS"] = ",".join(filter(None, [existing_cors, origin]))

    # frozen 态把 .env 指向用户数据目录（不存在则 pydantic 不加载），避免误加载启动 cwd 的陌生 .env
    if getattr(sys, "frozen", False):
        from app import paths
        os.environ.setdefault("ULTRARAG_DOTENV", str(paths.user_data_dir() / ".env"))


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _open_browser_when_ready(url: str) -> None:
    import urllib.request

    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1):
                _focus_or_open_browser(url + "/chat/")
                return
        except Exception:
            time.sleep(0.5)


def _focus_or_open_browser(target: str) -> None:
    """macOS：若已有标签打开了本服务地址则聚焦它，否则新开一个（避免每次点图标都开新页签）。
    非 macOS 或 AppleScript 失败时回退到 webbrowser.open。"""
    if sys.platform == "darwin":
        # 匹配同 host:port 的已有标签（忽略路径/hash 差异），命中则激活并聚焦，不新开。
        import subprocess
        from urllib.parse import urlparse

        base = urlparse(target)
        host_port = base.netloc  # 例 127.0.0.1:5173
        script = f'''
        set targetURL to "{target}"
        set matchKey to "{host_port}"
        set browsers to {{"Google Chrome", "Microsoft Edge", "Brave Browser", "Arc", "Safari"}}
        repeat with b in browsers
            try
                if application b is running then
                    tell application b
                        if b is "Safari" then
                            repeat with w in windows
                                repeat with t in tabs of w
                                    if (URL of t) contains matchKey then
                                        set current tab of w to t
                                        set index of w to 1
                                        activate
                                        return "focused"
                                    end if
                                end repeat
                            end repeat
                        else
                            repeat with w in windows
                                set tabList to tabs of w
                                repeat with i from 1 to count of tabList
                                    if (URL of (item i of tabList)) contains matchKey then
                                        set active tab index of w to i
                                        set index of w to 1
                                        activate
                                        return "focused"
                                    end if
                                end repeat
                            end repeat
                        end if
                    end tell
                end if
            end try
        end repeat
        return "notfound"
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=8
            )
            if (result.stdout or "").strip() == "focused":
                return
        except Exception:
            pass
    # 回退：正常打开（多数浏览器对相同 URL 会复用标签）
    webbrowser.open(target)


def _use_macos_dock_app() -> bool:
    # 仅 macOS 打包态用 Cocoa 壳（进 Dock + 点图标开页面）。
    # 开发态 / 其它平台保持简单主线程 uvicorn。
    return sys.platform == "darwin" and getattr(sys, "frozen", False)


def _serve(cfg: dict) -> None:
    import uvicorn

    uvicorn.run(cfg["app"], host=cfg["host"], port=cfg["port"], log_level="info")


def _run_macos_dock_app(cfg: dict, url: str) -> int:
    """macOS：NSApplication 主循环。进 Dock、点 Dock 图标重新打开浏览器、退出时停服务。"""
    import AppKit
    from PyObjCTools import AppHelper

    # uvicorn 在后台线程跑（主线程要留给 Cocoa 事件循环）
    server_thread = threading.Thread(target=_serve, args=(cfg,), daemon=True)
    server_thread.start()
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    class AppDelegate(AppKit.NSObject):
        def applicationDidFinishLaunching_(self, _notification):  # noqa: N802
            print(f"URStaff 启动中，就绪后将打开：{url}/chat/")

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):  # noqa: N802
            # 点 Dock 图标（app 已在运行、无窗口）→ 聚焦已有页签，没有才新开
            _focus_or_open_browser(url + "/chat/")
            return True

        def applicationShouldTerminate_(self, _app):  # noqa: N802
            return AppKit.NSTerminateNow

    app = AppKit.NSApplication.sharedApplication()
    # Regular：常规 GUI app，进 Dock、可激活
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.activateIgnoringOtherApps_(True)
    AppHelper.runEventLoop()
    return 0


def main(argv: list[str] | None = None) -> int:
    # 时序：先设 env（apply_runtime_env），再 import uvicorn / 触发 app.* import。
    apply_runtime_env()
    _redirect_logs_when_frozen()

    cfg = build_server_config()
    url = f"http://{cfg['host']}:{cfg['port']}"

    # 已在运行：直接开浏览器并退出（双击重复启动的兜底）
    if port_in_use(cfg["host"], cfg["port"]):
        try:
            import urllib.request
            with urllib.request.urlopen(url + "/api/health", timeout=1):
                print(f"URStaff 已在运行：{url}/chat/")
                _focus_or_open_browser(url + "/chat/")
                return 0
        except Exception:
            print(f"端口 {cfg['port']} 已被其它程序占用。请设置 ULTRARAG_PORT 换端口后重试。", file=sys.stderr)
            return 2

    if _use_macos_dock_app():
        return _run_macos_dock_app(cfg, url)

    # 其它平台 / 开发态：主线程跑 uvicorn，后台线程开浏览器
    print(f"URStaff 启动中，就绪后将打开：{url}/chat/")
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    _serve(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
