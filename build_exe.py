# -*- coding: utf-8 -*-
"""
AI 学生消息助手 — EXE 打包脚本
使用 PyInstaller 将核心组件打包为 Windows 可执行文件。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
NAPCAT_DIR = BASE_DIR / "NapCat_Portable"
OUTPUT_DIR = BASE_DIR / "dist_output_exe"
BUILD_DIR = BASE_DIR / "_build_pyinstaller"
CACHE_DIR = BASE_DIR / "_cache"

# venv Python（用于打包 api_server 和 scraper，它们依赖 backend 的包）
VENV_PYTHON = BACKEND_DIR / "venv" / "Scripts" / "python.exe"


def run(cmd, cwd=None, env=None):
    """执行命令并检查返回码。"""
    print(f"  > {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    _env = os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    if env:
        _env.update(env)
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        shell=isinstance(cmd, str), encoding="utf-8", errors="replace", env=_env
    )
    if result.returncode != 0:
        print(f"  [STDOUT] {result.stdout[-500:]}")
        print(f"  [STDERR] {result.stderr[-500:]}")
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def ensure_pyinstaller():
    """确保 PyInstaller 及依赖已安装。"""
    try:
        import PyInstaller
        print(f"  PyInstaller {PyInstaller.__version__} already installed")
    except ImportError:
        print("  Installing PyInstaller...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])

    # pywebview（launcher 用）
    try:
        import webview
    except ImportError:
        print("  Installing pywebview...")
        run([sys.executable, "-m", "pip", "install", "pywebview", "-q"])

    # 确保 venv 中也有 PyInstaller（api_server/scraper 打包用）
    if VENV_PYTHON.exists():
        print(f"  Ensuring PyInstaller in venv ({VENV_PYTHON})...")
        run([str(VENV_PYTHON), "-m", "pip", "install", "pyinstaller", "-q"])


def build_launcher_exe():
    """Step 1: 用 PyInstaller 打包 launcher.py 为单文件 EXE。"""
    print("\n[Step 1] Building launcher.exe...")

    launcher_py = BASE_DIR / "launcher.py"
    if not launcher_py.exists():
        raise FileNotFoundError(f"launcher.py not found at {launcher_py}")

    spec_dir = BUILD_DIR / "launcher"
    work_dir = BUILD_DIR / "launcher_work"
    dist_dir = BUILD_DIR / "launcher_dist"

    # 清理旧构建
    for d in [spec_dir, work_dir, dist_dir]:
        if d.exists():
            shutil.rmtree(d)

    run([
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "AI_Console_Launcher",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--noconsole",  # 无控制台窗口，真正的 GUI 程序
        "--icon", "NONE",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
        str(launcher_py),
    ], cwd=BASE_DIR)

    exe_path = dist_dir / "AI_Console_Launcher.exe"
    if not exe_path.exists():
        raise RuntimeError("launcher.exe build failed")
    print(f"  launcher.exe: {exe_path.stat().st_size // 1024}KB")
    return exe_path


def build_api_server_exe():
    """Step 2: 打包 API 服务器为 EXE。"""
    print("\n[Step 2] Building api_server.exe...")

    # 创建一个入口脚本，导入 api.py 的 app 并启动 uvicorn
    entry_py = BUILD_DIR / "api_entry.py"
    entry_py.write_text('''# -*- coding: utf-8 -*-
import sys
import os
from pathlib import Path

if getattr(sys, 'frozen', False):
    EXE_DIR = Path(sys.executable).parent
    os.chdir(str(EXE_DIR))
    os.environ["AI_CONSOLE_BASE"] = str(EXE_DIR)
else:
    BASE_DIR = Path(__file__).parent.parent
    sys.path.insert(0, str(BASE_DIR / "backend"))

import api

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(api.app, host="127.0.0.1", port=8000, log_level="info")
''', encoding="utf-8")

    # 使用 --paths 让 PyInstaller 能找到 backend 下的模块
    # 不使用 --add-data，因为 --add-data 只是复制数据文件，不会注册为可导入模块
    spec_dir = BUILD_DIR / "api"
    work_dir = BUILD_DIR / "api_work"
    dist_dir = BUILD_DIR / "api_dist"

    for d in [spec_dir, work_dir, dist_dir]:
        if d.exists():
            shutil.rmtree(d)

    # 使用 venv Python 打包（它有 uvicorn/fastapi 等依赖）
    py_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cmd = [
        py_exe, "-m", "PyInstaller",
        "--onefile",
        "--name", "api_server",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--console",
        "--icon", "NONE",
        # 添加 backend 目录到模块搜索路径
        "--paths", str(BACKEND_DIR),
        # 隐式导入
        "--hidden-import", "api",
        "--hidden-import", "scraper",
        "--hidden-import", "uvicorn",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "fastapi",
        "--hidden-import", "pydantic",
        "--hidden-import", "httpx",
        "--hidden-import", "aiohttp",
        "--hidden-import", "sqlite3",
        "--hidden-import", "_sqlite3",
        "--hidden-import", "aiosqlite",
        "--hidden-import", "yaml",
        "--hidden-import", "qrcode",
        "--hidden-import", "PIL",
    ]
    cmd.append(str(entry_py))

    run(cmd, cwd=BASE_DIR)

    exe_path = dist_dir / "api_server.exe"
    if not exe_path.exists():
        raise RuntimeError("api_server.exe build failed")
    print(f"  api_server.exe: {exe_path.stat().st_size // 1024}KB")
    return exe_path


def build_scraper_exe():
    """Step 3: 打包 Scraper Agent 为 EXE。"""
    print("\n[Step 3] Building scraper_agent.exe...")

    entry_py = BUILD_DIR / "scraper_entry.py"
    entry_py.write_text('''# -*- coding: utf-8 -*-
import sys
import os
from pathlib import Path

if getattr(sys, 'frozen', False):
    EXE_DIR = Path(sys.executable).parent
    os.chdir(str(EXE_DIR))
    os.environ["AI_CONSOLE_BASE"] = str(EXE_DIR)
else:
    BASE_DIR = Path(__file__).parent.parent
    sys.path.insert(0, str(BASE_DIR / "backend"))

import asyncio
import scraper

if __name__ == "__main__":
    asyncio.run(scraper.main())
''', encoding="utf-8")

    spec_dir = BUILD_DIR / "scraper"
    work_dir = BUILD_DIR / "scraper_work"
    dist_dir = BUILD_DIR / "scraper_dist"

    for d in [spec_dir, work_dir, dist_dir]:
        if d.exists():
            shutil.rmtree(d)

    py_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cmd = [
        py_exe, "-m", "PyInstaller",
        "--onefile",
        "--name", "scraper_agent",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--console",
        "--icon", "NONE",
        "--paths", str(BACKEND_DIR),
        "--hidden-import", "scraper",
        "--hidden-import", "aiohttp",
        "--hidden-import", "httpx",
        "--hidden-import", "yaml",
        "--hidden-import", "pydantic",
        str(entry_py),
    ]

    run(cmd, cwd=BASE_DIR)

    exe_path = dist_dir / "scraper_agent.exe"
    if not exe_path.exists():
        raise RuntimeError("scraper_agent.exe build failed")
    print(f"  scraper_agent.exe: {exe_path.stat().st_size // 1024}KB")
    return exe_path


def create_exe_package(launcher_exe, api_exe, scraper_exe):
    """Step 4: 组装完整的 EXE 分发包。"""
    print("\n[Step 4] Creating EXE distribution package...")

    pkg_dir = OUTPUT_DIR / "AI_Console_EXE"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    # 复制 EXE 文件
    shutil.copy2(launcher_exe, pkg_dir / "AI_Console_Launcher.exe")
    print("  Copied AI_Console_Launcher.exe")

    shutil.copy2(api_exe, pkg_dir / "api_server.exe")
    print("  Copied api_server.exe")

    shutil.copy2(scraper_exe, pkg_dir / "scraper_agent.exe")
    print("  Copied scraper_agent.exe")

    # 复制 requirements.txt 到包根目录（供参考）
    for fname in ["requirements.txt"]:
        src = BACKEND_DIR / fname
        if src.exists():
            shutil.copy2(src, pkg_dir / fname)

    # 复制 config.yaml（仅保留产品实际使用的字段，清空所有敏感信息）
    import yaml
    cfg_src = BACKEND_DIR / "config.yaml"
    if cfg_src.exists():
        with open(cfg_src, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        llm = cfg.get("llm", {})
        napcat = cfg.get("napcat", {})
        mothership = cfg.get("mothership", {})
        cfg = {
            "profile": {"nickname": ""},
            "llm": {
                "api_key": "",
                "base_url": llm.get("base_url", "https://api.deepseek.com"),
                "model": llm.get("model", "deepseek-chat"),
            },
            "napcat": {
                "group_ids": [],
                "include_private": False,
                "login_platform": napcat.get("login_platform", "iPad"),
                "webui_token": "",
                "webui_url": "http://127.0.0.1:6099",
                "ws_url": "ws://127.0.0.1:3001",
            },
            "mothership": {
                "enabled": False,
                "url": mothership.get("url", "http://127.0.0.1:8010"),
                "node_name": "",
                "node_token": "",
                "admin_token": "",
                "share_evidence": False,
                "space_id": "",
                "space_name": "",
                "owner_label": "",
                "membership_status": "",
                "categories": ["A"],
                "source_refs": [],
                "expires_at": "",
            },
            "scraper": {"mode": "realtime", "poll_interval": 2},
        }
        with open(pkg_dir / "config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
    print("  Copied config.yaml (all credentials cleared)")
    print("  (DB will be auto-created in %APPDATA%/AIConsole on first run)")

    # 复制前端 dist
    if DIST_DIR.exists():
        shutil.copytree(DIST_DIR, pkg_dir / "dist")
        print("  Copied frontend dist")

    # 复制 NapCat
    if NAPCAT_DIR.exists():
        shutil.copytree(NAPCAT_DIR, pkg_dir / "NapCat_Portable")
        napcat_size = sum(f.stat().st_size for f in (pkg_dir / "NapCat_Portable").rglob("*") if f.is_file())
        print(f"  Copied NapCat_Portable ({napcat_size // 1024 // 1024}MB)")
    else:
        print("  [WARN] NapCat_Portable not found")

    # 创建 README
    readme = pkg_dir / "使用说明.txt"
    readme.write_text("""AI 学生消息助手 — 桌面客户端

使用方法：
1. 双击 AI_Console_Launcher.exe 启动
2. 程序自动启动所有服务（NapCat / API / Scraper）
3. 主界面显示消息分类看板
4. 在 config.yaml 中填写你的 LLM API Key 和 QQ 配置
5. 扫码登录 QQ（NapCat WebUI: http://127.0.0.1:6099）
6. 消息自动采集、分类、显示

界面说明：
- 顶部：状态指示灯
- 中间：消息列表（支持 A/B/C 分类筛选）
- 右侧：消息详情面板
- 底部：服务状态栏

停止服务：
关闭程序窗口即可

如遇问题：
- 确保 8000 端口未被占用
- 确保 NapCat_Portable 目录存在
""", encoding="utf-8")
    print("  Created 使用说明.txt")

    return pkg_dir


def create_exe_zip(pkg_dir):
    """Step 5: 打包为 ZIP。"""
    print("\n[Step 5] Creating ZIP archive...")
    zip_path = OUTPUT_DIR / "AI_Console_EXE.zip"
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", OUTPUT_DIR, pkg_dir.name)
    size_mb = zip_path.stat().st_size // 1024 // 1024
    print(f"  AI_Console_EXE.zip ({size_mb}MB)")
    return zip_path


def cleanup_build():
    """清理构建临时文件。"""
    print("\n[Cleanup] Removing build artifacts...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"  Removed {BUILD_DIR}")


def main():
    print("=" * 50)
    print("  AI Console — EXE Build Script")
    print("=" * 50)

    # 前置检查
    if not DIST_DIR.exists():
        print("[ERROR] Frontend dist not found. Run 'npm run build' in frontend/ first.")
        sys.exit(1)

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    BUILD_DIR.mkdir(exist_ok=True)

    ensure_pyinstaller()

    # 构建三个 EXE
    launcher_exe = build_launcher_exe()
    api_exe = build_api_server_exe()
    scraper_exe = build_scraper_exe()

    # 组装分发包
    pkg_dir = create_exe_package(launcher_exe, api_exe, scraper_exe)

    # 创建 ZIP
    zip_path = create_exe_zip(pkg_dir)

    # 清理
    cleanup_build()

    print("\n" + "=" * 50)
    print("  EXE Build complete!")
    print(f"  Output: {OUTPUT_DIR.resolve()}")
    print(f"  Package: {pkg_dir.name}/")
    print(f"  ZIP: {zip_path.name}")
    print("=" * 50)


if __name__ == "__main__":
    main()
