# -*- coding: utf-8 -*-
"""
AI 学生消息助手 — 桌面客户端
pywebview 原生窗口 + 内嵌 React 前端，无浏览器依赖。
"""
import os
import sys
import json
import subprocess
import threading
import time
import socket
import webview
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

BACKEND_DIR = BASE_DIR / "backend" if (BASE_DIR / "backend").exists() else BASE_DIR
NAPCAT_DIR = BASE_DIR / "NapCat_Portable"
DIST_DIR = BASE_DIR / "dist"
DATA_DIR = BACKEND_DIR
API_PORT = 8000
API_BASE = f"http://127.0.0.1:{API_PORT}"

# ── 工具函数 ──────────────────────────────────────────────

def is_port_open(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def wait_for_port(port: int, timeout: int = 30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return False


def make_env():
    env = os.environ.copy()
    env["AI_CONSOLE_BASE"] = str(DATA_DIR)
    return env


def hide_args():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {
        "startupinfo": si,
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


# ── 服务管理 ──────────────────────────────────────────────

processes: dict[str, subprocess.Popen] = {}


def start_napcat():
    if not NAPCAT_DIR.exists():
        print("[WARN] NapCat_Portable 目录不存在", flush=True)
        return False
    if is_port_open(6099):
        print("[OK] NapCat 已在运行 (port 6099)", flush=True)
        return True

    entry = None
    if (NAPCAT_DIR / "index.js").exists():
        entry = "index.js"
    elif (NAPCAT_DIR / "napcat" / "napcat.mjs").exists():
        entry = "napcat\\napcat.mjs"
    if not entry:
        print("[WARN] NapCat 入口文件不存在", flush=True)
        return False

    node_exe = NAPCAT_DIR / "node.exe"
    if not node_exe.exists():
        node_exe = "node"

    print(f"  启动 NapCat: {node_exe} {entry}", flush=True)
    p = subprocess.Popen(
        [str(node_exe), entry], cwd=str(NAPCAT_DIR), **hide_args()
    )
    processes["napcat"] = p

    # 等待 NapCat 启动（最多 15 秒）
    if wait_for_port(6099, 15):
        print("[OK] NapCat WebUI 就绪 (port 6099)", flush=True)
        return True
    else:
        print("[WARN] NapCat 启动超时，WebUI 未就绪", flush=True)
        return False


def start_api():
    if is_port_open(API_PORT):
        return True

    env = make_env()
    hide = hide_args()

    if getattr(sys, 'frozen', False):
        exe = BASE_DIR / "api_server.exe"
        if exe.exists():
            p = subprocess.Popen([str(exe)], cwd=str(DATA_DIR), env=env, **hide)
        else:
            py = BASE_DIR / "python" / "python.exe"
            p = subprocess.Popen(
                [str(py), "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
                cwd=str(DATA_DIR), env=env, **hide
            )
    else:
        p = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
            cwd=str(BACKEND_DIR), env=env, **hide
        )

    processes["api"] = p
    return True


def start_scraper():
    env = make_env()
    hide = hide_args()

    if getattr(sys, 'frozen', False):
        exe = BASE_DIR / "scraper_agent.exe"
        if exe.exists():
            p = subprocess.Popen([str(exe)], cwd=str(DATA_DIR), env=env, **hide)
        else:
            py = BASE_DIR / "python" / "python.exe"
            p = subprocess.Popen([str(py), "scraper.py"], cwd=str(DATA_DIR), env=env, **hide)
    else:
        p = subprocess.Popen(
            [sys.executable, "scraper.py"], cwd=str(BACKEND_DIR), env=env, **hide
        )

    processes["scraper"] = p
    return True


def stop_all():
    for p in processes.values():
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    processes.clear()


# ── 主流程 ────────────────────────────────────────────────

def main():
    # 1. 启动 NapCat
    print("[1/3] 启动 NapCat...", flush=True)
    napcat_ok = start_napcat()

    # 2. 启动 API 服务器
    print("[2/3] 启动 API 服务器...", flush=True)
    start_api()
    if not wait_for_port(API_PORT, 20):
        print("[ERROR] API 服务器启动失败", flush=True)
        stop_all()
        return

    # 3. 启动 Scraper（NapCat 启动后再启动）
    if napcat_ok:
        print("[3/3] 启动 Scraper...", flush=True)
        start_scraper()
    else:
        print("[3/3] 跳过 Scraper（NapCat 未就绪）", flush=True)

    time.sleep(1)
    print(f"[OK] 服务已就绪，打开窗口...", flush=True)

    # 4. 打开 pywebview 原生窗口
    try:
        window = webview.create_window(
            title="AI 学生消息助手",
            url=API_BASE,
            width=1200,
            height=800,
            min_size=(900, 600),
            resizable=True,
            text_select=True,
        )

        # 窗口关闭时清理
        def on_closed():
            stop_all()

        window.events.closed += on_closed
        webview.start(debug=False)
    except Exception as e:
        print(f"[ERROR] 窗口启动失败: {e}", flush=True)
        print(f"[INFO] 请手动打开浏览器访问: {API_BASE}", flush=True)
        # 降级：打开浏览器
        import webbrowser
        webbrowser.open(API_BASE)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        stop_all()


if __name__ == "__main__":
    main()
