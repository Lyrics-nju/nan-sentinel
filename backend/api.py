"""
Nan Sentinel（南哨）— FastAPI 后端
配置管理 · NapCat 登录 · 消息查询 · SSE 实时推送
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import calibration

# ── 路径 ──────────────────────────────────────────────────
# APP_DIR: 应用程序文件（EXE、前端、NapCat）— 不动
# DATA_DIR: 用户数据（数据库、配置）— 存到 %APPDATA%，更新不丢失
if os.environ.get("AI_CONSOLE_BASE"):
    APP_DIR = Path(os.environ["AI_CONSOLE_BASE"])
elif getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

# 用户数据目录：开发模式在 APP_DIR，生产模式在 %APPDATA%/AIConsole
if getattr(sys, 'frozen', False):
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AIConsole"
else:
    DATA_DIR = APP_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR = DATA_DIR
CONFIG_PATH = DATA_DIR / "config.yaml"
DB_PATH = DATA_DIR / "market.db"

# NapCat 路径：开发模式在上级目录，分发包在同级目录
NAPCAT_DIR = APP_DIR.parent / "NapCat_Portable"
if not NAPCAT_DIR.exists():
    NAPCAT_DIR = APP_DIR / "NapCat_Portable"

# ── SSE 广播队列 ──────────────────────────────────────────
_subscribers: list[asyncio.Queue] = []


def _broadcast(msg: dict):
    for q in _subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(msg)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


async def _event_stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(q)
    try:
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=15)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                # keepalive 每 15s 发一次，防止连接断开
                yield ": keepalive\n\n"
    finally:
        _subscribers.remove(q)


# ── 配置读写 ──────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # 首次运行：从应用目录复制默认配置到数据目录
    default_cfg = APP_DIR / "config.yaml"
    if default_cfg.exists():
        import shutil
        shutil.copy2(str(default_cfg), str(CONFIG_PATH))
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)


# ── NapCat WebUI 鉴权 ────────────────────────────────────

def _password_hash(token: str) -> str:
    return hashlib.sha256((token + ".napcat").encode()).hexdigest()


def _napcat_webui_url(cfg: dict) -> str:
    """NapCat 管理接口只允许回环地址，避免把本机凭据发往外部主机。"""
    value = cfg.get("napcat", {}).get("webui_url", "http://127.0.0.1:6099")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return "http://127.0.0.1:6099"
    return value.rstrip("/")


def _napcat_webui_token(cfg: dict) -> str:
    token = cfg.get("napcat", {}).get("webui_token", "")
    if token:
        return token
    token_path = NAPCAT_DIR / "napcat" / "config" / "webui.json"
    try:
        with open(token_path, "r", encoding="utf-8") as file:
            return str(json.load(file).get("token", ""))
    except (OSError, json.JSONDecodeError):
        return ""


# WebUI credential 缓存
_napcat_cred_cache = {"cred": "", "expires": 0.0}


async def _napcat_login(client: httpx.AsyncClient) -> str:
    """获取 WebUI 凭证，返回 Credential 字符串（带 5 分钟缓存）。"""
    import time
    now = time.time()
    if _napcat_cred_cache["cred"] and now < _napcat_cred_cache["expires"]:
        return _napcat_cred_cache["cred"]

    cfg = load_config()
    webui_url = _napcat_webui_url(cfg)
    token = _napcat_webui_token(cfg)
    h = _password_hash(token)
    r = await client.post(f"{webui_url}/api/auth/login", json={"hash": h}, timeout=5)
    data = r.json()
    if data.get("code") != 0:
        if _napcat_cred_cache["cred"]:
            return _napcat_cred_cache["cred"]
        raise HTTPException(502, f"NapCat WebUI login failed: {data}")
    cred = data["data"]["Credential"]
    _napcat_cred_cache["cred"] = cred
    _napcat_cred_cache["expires"] = now + 300
    return cred


# ── 数据库 ────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id      TEXT UNIQUE,
            chat_type   TEXT DEFAULT 'group',
            group_id    TEXT,
            group_name  TEXT,
            sender_id   TEXT,
            sender_name TEXT,
            raw_content TEXT,
            category    TEXT,
            summary     TEXT,
            tags        TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 为旧表补齐字段。迁移不得改变历史数据的可见性。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    message_columns = {
        "chat_type": "TEXT DEFAULT 'group'",
        "source_type": "TEXT DEFAULT 'qq'",
        "source_name": "TEXT DEFAULT ''",
        "source_url": "TEXT DEFAULT ''",
        "confidence": "REAL",
        "classification_method": "TEXT DEFAULT 'llm'",
        "predicted_category": "TEXT DEFAULT ''",
        "review_required": "INTEGER NOT NULL DEFAULT 0",
        "prompt_version": "TEXT DEFAULT ''",
        "calibration_examples": "INTEGER NOT NULL DEFAULT 0",
        "calibration_similarity": "REAL NOT NULL DEFAULT 0",
    }
    for name, definition in message_columns.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
    # 批量处理缓冲池
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_buffer (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id      TEXT UNIQUE,
            chat_type   TEXT,
            chat_id     TEXT,
            chat_name   TEXT,
            sender_id   TEXT,
            sender_name TEXT,
            raw_content TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 收藏表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id      TEXT UNIQUE,
            folder      TEXT DEFAULT '默认收藏',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookmark_folders (
            name       TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT OR IGNORE INTO bookmark_folders (name) VALUES ('默认收藏')")
    # 周报缓存表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS summary_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start  TEXT,
            week_end    TEXT,
            category    TEXT,
            summary     TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classification_feedback (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id             TEXT NOT NULL,
            original_category  TEXT NOT NULL,
            corrected_category TEXT NOT NULL,
            note               TEXT DEFAULT '',
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_msg_id ON classification_feedback(msg_id)")
    feedback_cols = [r[1] for r in conn.execute("PRAGMA table_info(classification_feedback)").fetchall()]
    feedback_columns = {
        "raw_content": "TEXT DEFAULT ''",
        "source_type": "TEXT DEFAULT ''",
        "source_name": "TEXT DEFAULT ''",
        "source_key": "TEXT DEFAULT ''",
        "confidence": "REAL",
        "model_name": "TEXT DEFAULT ''",
        "prompt_version": "TEXT DEFAULT ''",
        "features_json": "TEXT DEFAULT '[]'",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "is_gold": "INTEGER NOT NULL DEFAULT 0",
        "shared_with_mothership": "INTEGER NOT NULL DEFAULT 0",
        "shared_at": "TEXT DEFAULT ''",
        "updated_at": "TEXT DEFAULT ''",
    }
    for name, definition in feedback_columns.items():
        if name not in feedback_cols:
            conn.execute(f"ALTER TABLE classification_feedback ADD COLUMN {name} {definition}")
    # 旧版本反馈只有分类结果；在原消息仍存在时补齐本地校准所需字段。
    legacy_feedback = conn.execute(
        """SELECT f.msg_id, m.raw_content, m.source_type, m.source_name,
                  m.group_id, m.group_name, m.confidence, m.prompt_version
             FROM classification_feedback f
             JOIN messages m ON m.msg_id=f.msg_id
            WHERE COALESCE(f.raw_content,'')='' OR COALESCE(f.source_key,'')=''"""
    ).fetchall()
    for row in legacy_feedback:
        content = row[1] or ""
        source_type = row[2] or "qq"
        source_name = row[3] or row[5] or ""
        source_key = calibration.make_source_key(source_type, row[4] or "", row[5] or source_name)
        conn.execute(
            """UPDATE classification_feedback
                  SET raw_content=?, source_type=?, source_name=?, source_key=?,
                      confidence=?, prompt_version=?, features_json=?,
                      updated_at=COALESCE(NULLIF(updated_at,''), created_at)
                WHERE msg_id=?""",
            (
                content, source_type, source_name, source_key, row[6], row[7] or "legacy",
                calibration.features_json(content), row[0],
            ),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_source ON classification_feedback(source_key, active, is_gold)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_settings (
            id                    INTEGER PRIMARY KEY CHECK (id=1),
            strategy_version      TEXT NOT NULL,
            max_examples          INTEGER NOT NULL DEFAULT 5,
            min_similarity        REAL NOT NULL DEFAULT 0.18,
            override_similarity   REAL NOT NULL DEFAULT 0.72,
            default_threshold     REAL NOT NULL DEFAULT 0.62,
            retrieval_enabled     INTEGER NOT NULL DEFAULT 1,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        """INSERT OR IGNORE INTO calibration_settings
           (id, strategy_version, max_examples, min_similarity, override_similarity,
            default_threshold, retrieval_enabled)
           VALUES (1, ?, 5, 0.18, 0.72, 0.62, 1)""",
        (calibration.STRATEGY_VERSION,),
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_source_settings (
            source_key            TEXT PRIMARY KEY,
            label                 TEXT NOT NULL DEFAULT '',
            confidence_threshold  REAL NOT NULL DEFAULT 0.62,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_evaluations (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_version      TEXT NOT NULL,
            sample_count          INTEGER NOT NULL,
            baseline_accuracy     REAL,
            calibrated_accuracy   REAL,
            details_json          TEXT NOT NULL DEFAULT '[]',
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_prompt_versions (
            version       TEXT PRIMARY KEY,
            scope         TEXT NOT NULL DEFAULT 'single',
            prompt_hash   TEXT NOT NULL,
            prompt_text   TEXT NOT NULL,
            label         TEXT NOT NULL DEFAULT '',
            release_note  TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'candidate',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            activated_at  TIMESTAMP
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_scope_hash ON calibration_prompt_versions(scope, prompt_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_scope_status ON calibration_prompt_versions(scope, status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mothership_outbox (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id      TEXT NOT NULL UNIQUE,
            payload     TEXT NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            last_error  TEXT DEFAULT '',
            synced_at   TIMESTAMP,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    from scraper import CLASSIFY_PROMPT
    calibration.register_prompt_version(
        DB_PATH, CLASSIFY_PROMPT, scope="single", label="单条消息分类规则",
        release_note="应用内置单条消息语义分类规则",
    )
    calibration.register_prompt_version(
        DB_PATH, BATCH_PROMPT, scope="batch", label="批量话题聚类规则",
        release_note="应用内置批量语义聚类规则",
    )


def _query_messages(category: Optional[str], limit: int, offset: int,
                     search: Optional[str] = None, days: Optional[int] = None,
                     folder: Optional[str] = None,
                     review_required: Optional[bool] = None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    conditions = []
    params = []

    if category:
        conditions.append("category=?")
        params.append(category)
    if search:
        conditions.append("(summary LIKE ? OR raw_content LIKE ? OR sender_name LIKE ? OR group_name LIKE ?)")
        kw = f"%{search}%"
        params.extend([kw, kw, kw, kw])
    if days:
        conditions.append("created_at >= datetime('now', ?)")
        params.append(f"-{days} days")
    if folder:
        conditions.append("msg_id IN (SELECT msg_id FROM bookmarks WHERE folder=?)")
        params.append(folder)
    if review_required is not None:
        conditions.append("review_required=?")
        params.append(1 if review_required else 0)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM messages{where} ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()

    # 附加收藏信息
    bookmarks = {r[0] for r in conn.execute("SELECT msg_id FROM bookmarks").fetchall()}
    feedback_by_msg: dict[str, dict] = {}
    message_ids = [row["msg_id"] for row in rows]
    if message_ids:
        placeholders = ",".join("?" * len(message_ids))
        feedback_rows = conn.execute(
            f"""SELECT msg_id, corrected_category, active, is_gold,
                       shared_with_mothership,
                       COALESCE(NULLIF(updated_at,''), created_at) AS reviewed_at
                  FROM classification_feedback WHERE msg_id IN ({placeholders})""",
            message_ids,
        ).fetchall()
        feedback_by_msg = {row["msg_id"]: dict(row) for row in feedback_rows}
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        d["bookmarked"] = d["msg_id"] in bookmarks
        d["review_required"] = bool(d.get("review_required"))
        feedback = feedback_by_msg.get(d["msg_id"])
        d["calibration_status"] = "active" if feedback and feedback["active"] else "inactive" if feedback else "none"
        d["feedback_corrected_category"] = feedback["corrected_category"] if feedback else None
        d["feedback_reviewed_at"] = feedback["reviewed_at"] if feedback else None
        d["feedback_is_gold"] = bool(feedback["is_gold"]) if feedback else False
        d["feedback_shared_with_mothership"] = bool(feedback["shared_with_mothership"]) if feedback else False
        result.append(d)
    return result


def _query_stats() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    a = conn.execute("SELECT COUNT(*) FROM messages WHERE category='A'").fetchone()[0]
    b = conn.execute("SELECT COUNT(*) FROM messages WHERE category='B'").fetchone()[0]
    c = conn.execute("SELECT COUNT(*) FROM messages WHERE category='C'").fetchone()[0]
    reviewed = conn.execute("SELECT COUNT(*) FROM classification_feedback").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM classification_feedback WHERE original_category=corrected_category").fetchone()[0]
    pending_review = conn.execute("SELECT COUNT(*) FROM messages WHERE review_required=1").fetchone()[0]
    conn.close()
    return {
        "total": total,
        "A": a,
        "B": b,
        "C": c,
        "reviewed": reviewed,
        "review_accuracy": round(correct / reviewed * 100, 1) if reviewed else None,
        "pending_review": pending_review,
    }


# ── 启动/关闭 ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    yield


# ── FastAPI ───────────────────────────────────────────────

app = FastAPI(title="Nan Sentinel Local API", version="0.4.0", lifespan=lifespan)


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    """拒绝 DNS rebinding 与跨站表单请求；桌面 API 只服务本机页面。"""
    host = urlparse(f"//{request.headers.get('host', '')}").hostname
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    if host not in allowed_hosts:
        return JSONResponse(status_code=403, content={"detail": "仅允许本机访问"})

    origin = request.headers.get("origin")
    if origin and urlparse(origin).hostname not in allowed_hosts:
        return JSONResponse(status_code=403, content={"detail": "拒绝跨站请求"})
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return JSONResponse(status_code=403, content={"detail": "拒绝跨站请求"})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self' data:; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常捕获 — 防止未处理异常导致进程退出。"""
    import traceback
    tb = traceback.format_exc()
    print(f"[ERR] Unhandled exception on {request.url.path}:\n{tb}", flush=True)
    return JSONResponse(status_code=500, content={"error": "internal_server_error"})


# ── 数据模型 ──────────────────────────────────────────────

class ConfigPayload(BaseModel):
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    napcat_group_ids: Optional[list[int]] = None
    include_private: Optional[bool] = None
    scraper_mode: Optional[str] = None
    mothership_enabled: Optional[bool] = None
    mothership_url: Optional[str] = None
    mothership_node_name: Optional[str] = None
    mothership_node_token: Optional[str] = None
    mothership_admin_token: Optional[str] = None
    mothership_share_evidence: Optional[bool] = None
    mothership_share_calibration_stats: Optional[bool] = None
    mothership_categories: Optional[list[Literal["A", "B", "C"]]] = None
    mothership_source_refs: Optional[list[str]] = None
    mothership_clear_node_token: bool = False
    mothership_clear_admin_token: bool = False


def _validate_http_url(value: str, field_name: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, f"{field_name} 必须是有效的 http/https 地址")
    return value.rstrip("/")


def _mothership_settings(cfg: dict) -> dict:
    return cfg.get("mothership", {}) if isinstance(cfg.get("mothership"), dict) else {}


def _redact_evidence(value: str) -> str:
    """母舰证据片段去除常见手机号、邮箱和 QQ/微信联系方式。"""
    value = re.sub(r"\b1[3-9]\d{9}\b", "[手机号已脱敏]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已脱敏]", value)
    value = re.sub(r"(?i)(QQ|微信|vx|wechat)\s*[:：]?\s*[A-Za-z0-9_-]{5,}", r"\1:[联系方式已脱敏]", value)
    return value[:300]


def _message_source_ref(message: dict) -> str:
    source_type = str(message.get("source_type", "qq") or "qq")
    source_name = str(message.get("source_name", "") or "")
    group_id = str(message.get("group_id", "") or "")
    group_name = str(message.get("group_name", "") or "")
    identity = f"{source_type}|{source_name}|{group_id}|{group_name}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _message_matches_grant(message: dict, cfg: dict) -> bool:
    mothership = _mothership_settings(cfg)
    categories = mothership.get("categories") or ["A", "B", "C"]
    source_refs = mothership.get("source_refs") or []
    if str(message.get("category", "B")) not in categories:
        return False
    if mothership.get("space_id") or mothership.get("strict_source_grant"):
        return "*" in source_refs or _message_source_ref(message) in source_refs
    return not source_refs or _message_source_ref(message) in source_refs


def _build_mothership_card(message: dict, cfg: dict) -> dict:
    tags = message.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []
    if not isinstance(tags, list):
        tags = []
    msg_id = str(message.get("msg_id", ""))
    source_type = str(message.get("source_type", "qq") or "qq")
    source_label = str(message.get("group_name", "") or message.get("source_name", "") or source_type)
    fingerprint = hashlib.sha256(f"{source_type}|{msg_id}".encode("utf-8")).hexdigest()
    mothership = _mothership_settings(cfg)
    raw_content = str(message.get("raw_content", ""))
    evidence = _redact_evidence(raw_content) if mothership.get("share_evidence", False) else ""
    return {
        "external_id": fingerprint,
        "category": message.get("category", "B"),
        "summary": str(message.get("summary", ""))[:1000],
        "tags": [str(tag)[:40] for tag in tags[:12]],
        "confidence": message.get("confidence"),
        "source_type": source_type[:40],
        "source_name": source_label[:120],
        "source_ref": _message_source_ref(message),
        "evidence_excerpt": evidence,
        "occurred_at": str(message.get("created_at", ""))[:40],
    }


def _enqueue_mothership(conn: sqlite3.Connection, message: dict, cfg: Optional[dict] = None) -> bool:
    cfg = cfg or load_config()
    mothership = _mothership_settings(cfg)
    if not mothership.get("enabled") or not mothership.get("url") or not mothership.get("node_token"):
        return False
    if not _message_matches_grant(message, cfg):
        return False
    payload = _build_mothership_card(message, cfg)
    conn.execute(
        "INSERT OR IGNORE INTO mothership_outbox (msg_id, payload) VALUES (?, ?)",
        (str(message.get("msg_id", "")), json.dumps(payload, ensure_ascii=False)),
    )
    return True


async def _flush_mothership_outbox(limit: int = 100) -> dict:
    cfg = load_config()
    mothership = _mothership_settings(cfg)
    if not mothership.get("enabled"):
        return {"status": "disabled", "synced": 0}
    url = mothership.get("url", "").strip()
    token = mothership.get("node_token", "").strip()
    if not url or not token:
        return {"status": "not_configured", "synced": 0}
    base_url = _validate_http_url(url, "母舰地址")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, payload FROM mothership_outbox WHERE synced_at IS NULL ORDER BY id LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    conn.close()
    if not rows:
        return {"status": "ok", "synced": 0, "pending": 0}
    try:
        items = [json.loads(row["payload"]) for row in rows]
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(connect=5, read=20, write=10, pool=5)) as client:
            response = await client.post(
                f"{base_url}/api/v1/ingest",
                headers={"Authorization": f"Bearer {token}"},
                json={"items": items},
            )
            response.raise_for_status()
            data = response.json()
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            f"UPDATE mothership_outbox SET synced_at=CURRENT_TIMESTAMP, last_error='' WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        pending = conn.execute("SELECT COUNT(*) FROM mothership_outbox WHERE synced_at IS NULL").fetchone()[0]
        conn.close()
        return {"status": "ok", "synced": len(ids), "pending": pending, "remote": data}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            f"UPDATE mothership_outbox SET attempts=attempts+1, last_error=? WHERE id IN ({placeholders})",
            [error, *ids],
        )
        conn.commit()
        pending = conn.execute("SELECT COUNT(*) FROM mothership_outbox WHERE synced_at IS NULL").fetchone()[0]
        conn.close()
        return {"status": "error", "synced": 0, "pending": pending, "error": error}


def _schedule_mothership_flush() -> None:
    try:
        asyncio.get_running_loop().create_task(_flush_mothership_outbox())
    except RuntimeError:
        pass


# ── 接口：健康检查 ────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}


@app.get("/api/services")
async def services_status():
    """返回各服务的运行状态。"""
    napcat_webui = _is_port_open(6099)
    napcat_ws = _is_port_open(3001)
    cfg = load_config()
    llm_key = cfg.get("llm", {}).get("api_key", "")
    llm_model = cfg.get("llm", {}).get("model", "")
    return {
        "napcat_webui": napcat_webui,
        "napcat_ws": napcat_ws,
        "api_server": True,
        "ws_config_ok": napcat_ws,
        "llm_configured": bool(llm_key),
        "llm_model": llm_model or "未配置",
    }


# ── 接口：配置管理 ────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    llm = cfg.get("llm", {})
    key = llm.get("api_key", "")
    mothership = _mothership_settings(cfg)
    return {
        "llm_base_url": llm.get("base_url", ""),
        "llm_model": llm.get("model", ""),
        "llm_api_key_set": bool(key),
        "configured": bool(key),
        "scraper_mode": cfg.get("scraper", {}).get("mode", "realtime"),
        "include_private": bool(cfg.get("napcat", {}).get("include_private", False)),
        "napcat_group_ids": cfg.get("napcat", {}).get("group_ids", []),
        "mothership_enabled": bool(mothership.get("enabled", False)),
        "mothership_url": mothership.get("url", ""),
        "mothership_node_name": mothership.get("node_name", ""),
        "mothership_node_token_set": bool(mothership.get("node_token", "")),
        "mothership_admin_token_set": bool(mothership.get("admin_token", "")),
        "mothership_share_evidence": bool(mothership.get("share_evidence", False)),
        "mothership_share_calibration_stats": bool(mothership.get("share_calibration_stats", False)),
        "mothership_categories": mothership.get("categories") or ["A", "B", "C"],
        "mothership_source_refs": mothership.get("source_refs") or [],
        "mothership_space_id": mothership.get("space_id", ""),
        "mothership_space_name": mothership.get("space_name", ""),
        "mothership_owner_label": mothership.get("owner_label", ""),
        "mothership_membership_status": mothership.get("membership_status", ""),
        "mothership_expires_at": mothership.get("expires_at", ""),
    }


@app.post("/api/config")
async def post_config(payload: ConfigPayload):
    cfg = load_config()
    if "llm" not in cfg:
        cfg["llm"] = {}
    if payload.llm_api_key is not None and payload.llm_api_key.strip() and "*" not in payload.llm_api_key:
        cfg["llm"]["api_key"] = payload.llm_api_key
    if payload.llm_base_url is not None:
        cfg["llm"]["base_url"] = _validate_http_url(payload.llm_base_url, "LLM Base URL")
    if payload.llm_model is not None:
        model = payload.llm_model.strip()
        if not model:
            raise HTTPException(400, "模型名称不能为空")
        cfg["llm"]["model"] = model
    if payload.napcat_group_ids is not None:
        if "napcat" not in cfg:
            cfg["napcat"] = {}
        cfg["napcat"]["group_ids"] = payload.napcat_group_ids
    if payload.include_private is not None:
        if "napcat" not in cfg:
            cfg["napcat"] = {}
        cfg["napcat"]["include_private"] = payload.include_private
    if payload.scraper_mode is not None:
        if payload.scraper_mode not in ("realtime", "batch"):
            raise HTTPException(400, "scraper_mode must be 'realtime' or 'batch'")
        if "scraper" not in cfg:
            cfg["scraper"] = {}
        cfg["scraper"]["mode"] = payload.scraper_mode
    mothership = cfg.setdefault("mothership", {})
    if payload.mothership_enabled is not None:
        mothership["enabled"] = payload.mothership_enabled
    if payload.mothership_url is not None:
        url = payload.mothership_url.strip()
        mothership["url"] = _validate_http_url(url, "母舰地址") if url else ""
    if payload.mothership_node_name is not None:
        node_name = payload.mothership_node_name.strip()
        if len(node_name) > 60:
            raise HTTPException(400, "哨站名称不能超过 60 个字符")
        mothership["node_name"] = node_name
    if payload.mothership_node_token is not None and payload.mothership_node_token.strip() and "*" not in payload.mothership_node_token:
        mothership["node_token"] = payload.mothership_node_token.strip()
    if payload.mothership_admin_token is not None and payload.mothership_admin_token.strip() and "*" not in payload.mothership_admin_token:
        mothership["admin_token"] = payload.mothership_admin_token.strip()
    if payload.mothership_clear_node_token:
        mothership["node_token"] = ""
    if payload.mothership_clear_admin_token:
        mothership["admin_token"] = ""
    if payload.mothership_share_evidence is not None:
        mothership["share_evidence"] = payload.mothership_share_evidence
    if payload.mothership_share_calibration_stats is not None:
        mothership["share_calibration_stats"] = payload.mothership_share_calibration_stats
    if payload.mothership_categories is not None:
        categories = list(dict.fromkeys(payload.mothership_categories))
        if not categories:
            raise HTTPException(400, "至少选择一个共享分类")
        mothership["categories"] = categories
    if payload.mothership_source_refs is not None:
        refs = list(dict.fromkeys(ref.strip() for ref in payload.mothership_source_refs if ref.strip()))
        if any(len(ref) > 128 for ref in refs):
            raise HTTPException(400, "来源标识过长")
        mothership["source_refs"] = refs
    if mothership.get("enabled") and not all((mothership.get("url"), mothership.get("node_token"))):
        raise HTTPException(400, "启用母舰同步前必须填写母舰地址和哨站令牌")
    save_config(cfg)
    return {"status": "saved"}


# ── 接口：用户注册/身份 ──────────────────────────────────

class RegisterPayload(BaseModel):
    nickname: str


@app.post("/api/register")
async def register_user(payload: RegisterPayload):
    """保存仅在本机显示的昵称，不参与数据隔离或网络同步。"""
    nickname = payload.nickname.strip()
    if not nickname or len(nickname) > 20:
        raise HTTPException(400, "昵称需要 1-20 个字符")
    cfg = load_config()
    if "profile" not in cfg:
        cfg["profile"] = {}
    cfg["profile"]["nickname"] = nickname
    save_config(cfg)
    return {"status": "ok", "nickname": nickname}


@app.get("/api/user")
async def get_user():
    """获取当前用户信息。"""
    cfg = load_config()
    nickname = cfg.get("profile", {}).get("nickname", "")
    return {"nickname": nickname, "registered": bool(nickname)}


@app.get("/api/user/stats")
async def get_user_stats():
    """获取当前用户的统计数据。"""
    conn = sqlite3.connect(str(DB_PATH))
    today_count = conn.execute("SELECT COUNT(*) FROM messages WHERE DATE(created_at)=DATE('now','localtime')").fetchone()[0]
    total_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    bookmark_count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    conn.close()
    return {"today": today_count, "total": total_count, "bookmarks": bookmark_count}


# ── 接口：NapCat 登录 ────────────────────────────────────

@app.get("/api/login/qrcode")
async def get_qrcode():
    cfg = load_config()
    webui_url = _napcat_webui_url(cfg)
    platform = cfg.get("napcat", {}).get("login_platform", "iPad")
    # 优先：通过 NapCat WebUI API 获取（POST 方法）
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            cred = await _napcat_login(client)
            headers = {"Authorization": f"Bearer {cred}"}
            # 先设置平台
            try:
                await client.post(f"{webui_url}/api/QQLogin/SetPlatform", headers=headers, json={"platform": platform}, timeout=3)
            except Exception:
                pass  # 如果 NapCat 不支持此接口，忽略
            r = await client.post(f"{webui_url}/api/QQLogin/GetQQLoginQrcode", headers=headers, timeout=5)
            data = r.json()
            if data.get("code") == 0 and data.get("data"):
                qr_data = data["data"]
                # NapCat v4 返回 {"qrcode": "https://..."} 格式
                qr_url = qr_data.get("qrcode", "") if isinstance(qr_data, dict) else ""
                if qr_url:
                    # 用 qrcode 库生成 base64 PNG
                    try:
                        import qrcode, io, base64
                        img = qrcode.make(qr_url)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode()
                        return {"status": "ok", "qrcode": b64, "format": "png"}
                    except ImportError:
                        return {"status": "ok", "qrcode": qr_url, "format": "url"}
    except Exception:
        pass
    # 降级：读取本地缓存
    qr_paths = [
        NAPCAT_DIR / "napcat" / "cache" / "qrcode.png",
        NAPCAT_DIR / "NapCat" / "cache" / "qrcode.png",
    ]
    for qr_path in qr_paths:
        if qr_path.exists() and qr_path.stat().st_size > 100:
            b64 = base64.b64encode(qr_path.read_bytes()).decode()
            return {"status": "ok", "qrcode": b64, "format": "png"}
    # 最终降级：返回占位 SVG
    return {"status": "mock", "qrcode": _mock_qr_svg(), "format": "svg"}


@app.get("/api/qrcode")
async def get_qrcode_image():
    """直接返回 QR 码 PNG 二进制流，供前端 <img src> 直接引用。"""
    import io
    # 1) 通过 NapCat WebUI API 获取 QR URL，再生成 PNG
    try:
        cfg = load_config()
        webui_url = _napcat_webui_url(cfg)
        platform = cfg.get("napcat", {}).get("login_platform", "iPad")
        async with httpx.AsyncClient(trust_env=False) as client:
            cred = await _napcat_login(client)
            headers = {"Authorization": f"Bearer {cred}"}
            # 先设置平台
            try:
                await client.post(f"{webui_url}/api/QQLogin/SetPlatform", headers=headers, json={"platform": platform}, timeout=3)
            except Exception:
                pass
            r = await client.post(f"{webui_url}/api/QQLogin/GetQQLoginQrcode", headers=headers, timeout=5)
            data = r.json()
            if data.get("code") == 0 and data.get("data"):
                qr_url = data["data"].get("qrcode", "") if isinstance(data["data"], dict) else ""
                if qr_url:
                    try:
                        import qrcode
                        img = qrcode.make(qr_url)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        from fastapi.responses import Response
                        return Response(content=buf.getvalue(), media_type="image/png")
                    except ImportError:
                        pass
    except Exception:
        pass

    # 2) 降级：读取磁盘缓存
    qr_paths = [
        NAPCAT_DIR / "napcat" / "cache" / "qrcode.png",
        NAPCAT_DIR / "NapCat" / "cache" / "qrcode.png",
    ]
    for qr_path in qr_paths:
        if qr_path.exists() and qr_path.stat().st_size > 100:
            from fastapi.responses import Response
            return Response(content=qr_path.read_bytes(), media_type="image/png")
    raise HTTPException(404, "QR code not ready")


def _hide_args() -> dict:
    """返回 subprocess 隐藏窗口的参数（与 launcher.py 保持一致）。"""
    import subprocess
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": si,
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


def _restart_napcat_sync():
    """静默重启 NapCat — 杀掉 NapCat 进程后在后台重新启动，不弹窗。"""
    import subprocess
    import time as _time

    # 1) 只杀 NapCat 相关的 node 进程（通过 cmdline 包含 napcat 判断）
    napcat_killed = False
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info["name"] or "").lower()
                cmdline = " ".join(p.info["cmdline"] or []).lower()
                if name == "node.exe" and "napcat" in cmdline:
                    p.kill()
                    napcat_killed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        # psutil 不可用时用 wmic 精确查找 napcat 进程
        try:
            result = subprocess.run(
                ['wmic', 'process', 'where', "name='node.exe' and commandline like '%napcat%'", 'get', 'processid'],
                capture_output=True, text=True, timeout=10,
                **_hide_args(),
            )
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if line.isdigit():
                    subprocess.run(['taskkill', '/F', '/PID', line], capture_output=True, timeout=5, **_hide_args())
                    napcat_killed = True
        except Exception:
            pass
    except Exception:
        pass

    if not napcat_killed:
        return

    _time.sleep(3)

    # 2) 静默启动 NapCat（使用 STARTUPINFO 隐藏窗口，与 launcher.py 一致）
    napcat_dir = NAPCAT_DIR
    node_exe = napcat_dir / "node.exe"
    if not node_exe.exists():
        node_exe = "node.exe"  # fallback to PATH

    entry = None
    if (napcat_dir / "index.js").exists():
        entry = "index.js"
    elif (napcat_dir / "napcat" / "napcat.mjs").exists():
        entry = "napcat\\napcat.mjs"
    if entry:
        subprocess.Popen(
            [str(node_exe), entry],
            cwd=str(napcat_dir),
            **_hide_args(),
        )


@app.post("/api/login/reset")
async def login_reset():
    """刷新 NapCat 二维码缓存；绝不修改系统 QQ 客户端的账号与会话数据。"""
    cleaned = []

    qr_paths = [
        NAPCAT_DIR / "napcat" / "cache" / "qrcode.png",
        NAPCAT_DIR / "NapCat" / "cache" / "qrcode.png",
    ]
    for p in qr_paths:
        if p.exists():
            p.unlink()
            cleaned.append(str(p))

    import asyncio as _aio
    loop = _aio.get_event_loop()
    loop.run_in_executor(None, _restart_napcat_sync)

    return {"status": "ok", "cleaned": cleaned, "message": "二维码已刷新，NapCat 正在重启"}


class PlatformPayload(BaseModel):
    platform: str  # "Windows", "iPad", "Android"


@app.post("/api/login/platform")
async def set_login_platform(payload: PlatformPayload):
    """设置登录平台（Windows/iPad/Android）。
    iPad 和 Android 协议可与电脑端 QQ 同时在线。"""
    platform = payload.platform
    if platform not in ("Windows", "iPad", "Android"):
        raise HTTPException(400, "platform 必须是 Windows、iPad 或 Android")

    cfg = load_config()
    if "napcat" not in cfg:
        cfg["napcat"] = {}
    cfg["napcat"]["login_platform"] = platform
    save_config(cfg)
    return {"status": "ok", "platform": platform}


@app.get("/api/login/platform")
async def get_login_platform():
    """获取当前登录平台设置。"""
    cfg = load_config()
    platform = cfg.get("napcat", {}).get("login_platform", "iPad")
    return {"platform": platform}


WS_SERVER_CONFIG = [
    {
        "name": "websocket-server",
        "enable": True,
        "host": "127.0.0.1",
        "port": 3001,
        "messagePostFormat": "array",
        "reportSelfMessage": True,
        "token": "",
        "enableForcePushEvent": True,
        "debug": False,
        "heartInterval": 30000,
    }
]


def _ensure_ws_config(uin: str) -> bool:
    """检查并修复 NapCat 账号专属配置，确保 WebSocket 服务器已启用。
    同时修复 onebot11_{uin}.json 和 napcat_protocol_{uin}.json。
    返回 True 表示配置被修改（需要重启 NapCat）。"""
    napcat_config_dir = NAPCAT_DIR / "napcat" / "config"
    napcat_config_dir.mkdir(parents=True, exist_ok=True)
    modified = False

    def secure_servers(data: dict) -> bool:
        network = data.setdefault("network", {})
        servers = network.get("websocketServers")
        if not servers:
            network["websocketServers"] = [dict(WS_SERVER_CONFIG[0])]
            return True
        changed = False
        for server in servers:
            if server.get("host") != "127.0.0.1":
                server["host"] = "127.0.0.1"
                changed = True
        return changed

    # 修复 napcat_protocol_{uin}.json（这个文件会覆盖 onebot11 配置）
    protocol_cfg = napcat_config_dir / f"napcat_protocol_{uin}.json"
    try:
        if protocol_cfg.exists():
            with open(protocol_cfg, "r", encoding="utf-8") as f:
                pdata = json.load(f)
        else:
            pdata = {}
    except (json.JSONDecodeError, OSError):
        pdata = {}

    protocol_changed = pdata.get("enable") is not True
    pdata["enable"] = True
    protocol_changed = secure_servers(pdata) or protocol_changed
    if protocol_changed:
        with open(protocol_cfg, "w", encoding="utf-8") as f:
            json.dump(pdata, f, indent=2, ensure_ascii=False)
        modified = True

    # 修复 onebot11_{uin}.json
    account_cfg = napcat_config_dir / f"onebot11_{uin}.json"
    if account_cfg.exists():
        try:
            with open(account_cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

        if secure_servers(data):
            with open(account_cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            modified = True

    return modified


def _is_port_open(port: int) -> bool:
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result == 0
    except Exception:
        return False


_last_napcat_restart = 0.0  # 上次自动重启 NapCat 的时间戳


def _log_login_debug(msg: str):
    """输出不包含账号、昵称、消息正文或凭据的诊断信息。"""
    print(msg, flush=True)


def _detect_login_by_ws_raw() -> dict | None:
    """用原始 socket 连接 WebSocket 服务器检测登录状态（无外部依赖）。
    NapCat 登录后，WS 连接建立时会立即推送 meta_event，包含 self_id。"""
    ws_port = 3001
    if not _is_port_open(ws_port):
        return None

    try:
        import socket
        import struct
        import random

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", ws_port))

        # WebSocket 握手
        key_bytes = bytes([random.randint(0, 255) for _ in range(16)])
        import base64 as _b64
        ws_key = _b64.b64encode(key_bytes).decode()

        handshake = (
            f"GET / HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{ws_port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(handshake.encode())

        # 读取握手响应
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        if b"101" not in response:
            _log_login_debug("[WS-RAW] Handshake failed")
            sock.close()
            return None

        _log_login_debug("[WS-RAW] Handshake OK, waiting for data...")

        # 读取 WebSocket 帧（等待最多 5 秒）
        sock.settimeout(5)
        try:
            frame_data = sock.recv(65536)
        except socket.timeout:
            _log_login_debug("[WS-RAW] No data received within 5s")
            sock.close()
            return None

        if len(frame_data) < 2:
            _log_login_debug("[WS-RAW] Frame too short")
            sock.close()
            return None

        # 解析 WebSocket 帧
        opcode = frame_data[0] & 0x0F
        masked = bool(frame_data[1] & 0x80)
        payload_len = frame_data[1] & 0x7F
        offset = 2

        if payload_len == 126:
            payload_len = struct.unpack(">H", frame_data[2:4])[0]
            offset = 4
        elif payload_len == 127:
            payload_len = struct.unpack(">Q", frame_data[2:10])[0]
            offset = 10

        if masked:
            mask_key = frame_data[offset:offset + 4]
            offset += 4
            payload = bytearray(frame_data[offset:offset + payload_len])
            for i in range(len(payload)):
                payload[i] ^= mask_key[i % 4]
            payload = bytes(payload)
        else:
            payload = frame_data[offset:offset + payload_len]

        if opcode == 1:  # TEXT frame
            text = payload.decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
                _log_login_debug(f"[WS-RAW] Received event type={data.get('post_type', 'unknown')}")
                if data.get("post_type") == "meta_event":
                    self_id = str(data.get("self_id", ""))
                    if self_id and self_id != "0":
                        sock.close()
                        return {"status": "logged_in", "uin": self_id, "nickname": ""}
            except json.JSONDecodeError:
                pass

        sock.close()
    except Exception as e:
        _log_login_debug(f"[WS-RAW] Error: {type(e).__name__}")

    return None


def _detect_login_by_config_files() -> dict | None:
    """通过 NapCat 配置文件 + WS 端口验证检测已登录的 QQ 账号。
    配置文件在登出后也会存在，所以必须同时验证 WS 服务器（端口 3001）是否在运行。
    WS 端口打开 = NapCat 已登录且正在推送消息。"""
    napcat_config_dir = NAPCAT_DIR / "napcat" / "config"
    if not napcat_config_dir.exists():
        return None

    # 配置文件只是用来获取 UIN，真正的登录确认要看 WS 端口
    if not _is_port_open(3001):
        return None

    for f in napcat_config_dir.glob("onebot11_*.json"):
        uin = f.stem.replace("onebot11_", "")
        if uin and uin != "0":
            nickname = ""
            napcat_cfg = napcat_config_dir / f"napcat_{uin}.json"
            if napcat_cfg.exists():
                try:
                    with open(napcat_cfg, "r", encoding="utf-8") as fh:
                        nc = json.load(fh)
                    nickname = nc.get("nickname", "")
                except Exception:
                    pass
            _log_login_debug("[FILE-DETECT] Local account config and WS port are ready")
            return {"status": "logged_in", "uin": uin, "nickname": nickname}
    return None


@app.get("/api/login/status")
async def get_login_status():
    cfg = load_config()
    webui_url = _napcat_webui_url(cfg)
    # NapCat 未启动时不要发起多轮 HTTP 超时；先使用本地轻量兜底检查。
    if not _is_port_open(6099):
        file_result = _detect_login_by_config_files()
        if file_result:
            return file_result
        ws_result = _detect_login_by_ws_raw()
        if ws_result:
            return ws_result
        return {"status": "offline"}

    # 1) 尝试通过 WebUI API 获取登录信息
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            cred = await _napcat_login(client)
            headers = {"Authorization": f"Bearer {cred}"}

            # 尝试 GetQQLoginInfo
            try:
                r = await client.post(f"{webui_url}/api/QQLogin/GetQQLoginInfo", headers=headers, timeout=5)
                data = r.json()
                _log_login_debug(f"[LOGIN] GetQQLoginInfo: code={data.get('code')}")

                if data.get("code") == 0 and data.get("data"):
                    info = data["data"]
                    uin = str(info.get("uin", ""))
                    nickname = info.get("nick", "") or info.get("nickname", "") or info.get("nickName", "")

                    # 多种方式判断在线状态
                    online = False
                    if info.get("online") is True:
                        online = True
                    status_val = str(info.get("status", "")).lower()
                    if status_val in ("online", "1", "true"):
                        online = True
                    login_state = info.get("loginState", info.get("login_state", ""))
                    if str(login_state).lower() in ("1", "true", "online", "loggedin"):
                        online = True
                    if info.get("isLoggedIn") is True:
                        online = True
                    # 有有效 uin 就认为已登录
                    if not online and uin and uin != "0":
                        online = True

                    _log_login_debug(f"[LOGIN] Account online={online}")

                    if uin and uin != "0" and online:
                        ws_ok = _is_port_open(3001)
                        ws_config_fixed = False
                        if not ws_ok:
                            _ensure_ws_config(uin)
                            # 冷却机制：60 秒内只重启一次，防止循环重启
                            global _last_napcat_restart
                            now = time.time()
                            if now - _last_napcat_restart > 60:
                                _last_napcat_restart = now
                                _log_login_debug(f"[LOGIN] WS port 3001 not open, auto-restarting NapCat...")
                                import asyncio as _aio
                                loop = _aio.get_event_loop()
                                loop.run_in_executor(None, _restart_napcat_sync)
                            else:
                                _log_login_debug(f"[LOGIN] WS port 3001 not open, restart cooldown active")
                            ws_config_fixed = True
                        return {"status": "logged_in", "uin": uin, "nickname": nickname, "ws_config_fixed": ws_config_fixed}
            except Exception as e:
                _log_login_debug(f"[LOGIN] GetQQLoginInfo failed: {type(e).__name__}")

            # 备用：GetLoginList
            try:
                r2 = await client.post(f"{webui_url}/api/QQLogin/GetLoginList", headers=headers, timeout=5)
                data2 = r2.json()
                _log_login_debug(f"[LOGIN] GetLoginList: code={data2.get('code')}")
                if data2.get("code") == 0 and data2.get("data"):
                    login_list = data2["data"]
                    items = login_list if isinstance(login_list, list) else [login_list]
                    for account in items:
                        acc_uin = str(account.get("uin", ""))
                        if acc_uin and acc_uin != "0":
                            nickname = account.get("nick", "") or account.get("nickname", "")
                            return {"status": "logged_in", "uin": acc_uin, "nickname": nickname, "ws_config_fixed": False}
            except Exception as e:
                _log_login_debug(f"[LOGIN] GetLoginList failed: {type(e).__name__}")

    except Exception as e:
        _log_login_debug(f"[LOGIN] WebUI auth failed: {type(e).__name__}")

    # 2) 降级：原始 WebSocket 连接检测（无外部依赖）
    ws_result = _detect_login_by_ws_raw()
    if ws_result:
        return ws_result

    # 3) 降级：通过 NapCat 配置文件检测（最终兜底）
    file_result = _detect_login_by_config_files()
    if file_result:
        return file_result

    # 4) 检测 NapCat 是否正在运行
    if _is_port_open(6099):
        return {"status": "waiting_scan"}

    return {"status": "offline"}


@app.post("/api/restart-napcat")
async def restart_napcat():
    """静默重启 NapCat 进程（不弹窗）。"""
    import asyncio as _aio
    loop = _aio.get_event_loop()
    loop.run_in_executor(None, _restart_napcat_sync)
    return {"status": "restarted", "message": "NapCat 正在重启，请稍后重新扫码登录"}


# ── 接口：消息查询 ────────────────────────────────────────

@app.get("/api/messages")
async def get_messages(
    category: Optional[str] = Query(None, pattern="^[ABC]$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1, le=365),
    folder: Optional[str] = Query(None),
    review_required: Optional[bool] = Query(None),
):
    return _query_messages(
        category, limit, offset, search=search, days=days,
        folder=folder, review_required=review_required,
    )


# ── 接口：统计 ────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    return _query_stats()


# ── 接口：删除消息 ────────────────────────────────────────

@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: int):
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT msg_id FROM messages WHERE id=?", (message_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "消息不存在")
    msg_id = row[0]
    conn.execute("DELETE FROM bookmarks WHERE msg_id=?", (msg_id,))
    conn.execute("DELETE FROM classification_feedback WHERE msg_id=?", (msg_id,))
    conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


class BatchDeletePayload(BaseModel):
    ids: list[int]


@app.post("/api/messages/delete_batch")
async def delete_batch(payload: BatchDeletePayload):
    if not payload.ids:
        return {"status": "ok", "deleted": 0}
    if len(payload.ids) > 200:
        raise HTTPException(400, "单次最多删除 200 条消息")
    conn = sqlite3.connect(str(DB_PATH))
    placeholders = ",".join("?" * len(payload.ids))
    # 先删除关联的收藏
    conn.execute(f"DELETE FROM bookmarks WHERE msg_id IN (SELECT msg_id FROM messages WHERE id IN ({placeholders}))", payload.ids)
    conn.execute(f"DELETE FROM classification_feedback WHERE msg_id IN (SELECT msg_id FROM messages WHERE id IN ({placeholders}))", payload.ids)
    cur = conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", payload.ids)
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return {"status": "ok", "deleted": deleted}


# ── 接口：收藏 ────────────────────────────────────────────

class BookmarkPayload(BaseModel):
    msg_id: str
    folder: str = "默认收藏"


@app.post("/api/bookmarks")
async def add_bookmark(payload: BookmarkPayload):
    folder = payload.folder.strip() or "默认收藏"
    if len(folder) > 40:
        raise HTTPException(400, "收藏夹名称不能超过 40 个字符")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        exists = conn.execute("SELECT 1 FROM messages WHERE msg_id=?", (payload.msg_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "消息不存在")
        conn.execute("INSERT OR IGNORE INTO bookmark_folders (name) VALUES (?)", (folder,))
        conn.execute(
            "INSERT OR REPLACE INTO bookmarks (msg_id, folder) VALUES (?, ?)",
            (payload.msg_id, folder),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "bookmarked"}


@app.delete("/api/bookmarks/{msg_id}")
async def remove_bookmark(msg_id: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM bookmarks WHERE msg_id=?", (msg_id,))
    conn.commit()
    conn.close()
    return {"status": "unbookmarked"}


@app.get("/api/bookmarks")
async def list_bookmarks(folder: Optional[str] = Query(None), search: Optional[str] = Query(None)):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conditions = []
    params: list = []
    if folder:
        conditions.append("b.folder=?")
        params.append(folder)
    if search:
        conditions.append("(m.summary LIKE ? OR m.raw_content LIKE ? OR m.sender_name LIKE ? OR m.group_name LIKE ?)")
        keyword = f"%{search}%"
        params.extend([keyword, keyword, keyword, keyword])
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"""SELECT m.*, b.folder, b.created_at AS bookmarked_at
            FROM bookmarks b
            JOIN messages m ON m.msg_id=b.msg_id
            {where}
            ORDER BY b.id DESC""",
        params,
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item["tags"]) if item.get("tags") else []
        item["bookmarked"] = True
        result.append(item)
    return result


# ── 接口：收藏夹管理 ──────────────────────────────────────

@app.get("/api/folders")
async def list_folders():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT f.name, COUNT(b.id) AS count
        FROM bookmark_folders f
        LEFT JOIN bookmarks b ON b.folder=f.name
        GROUP BY f.name
        ORDER BY CASE WHEN f.name='默认收藏' THEN 0 ELSE 1 END, f.name
    """).fetchall()
    conn.close()
    return [{"name": r[0], "count": r[1]} for r in rows]


class FolderCreatePayload(BaseModel):
    name: str


@app.post("/api/folders")
async def create_folder(payload: FolderCreatePayload):
    name = payload.name.strip()
    if not name or len(name) > 40:
        raise HTTPException(400, "收藏夹名称需要 1-40 个字符")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT OR IGNORE INTO bookmark_folders (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return {"status": "created", "name": name}


class FolderRenamePayload(BaseModel):
    old_name: str
    new_name: str


@app.post("/api/folders/rename")
async def rename_folder(payload: FolderRenamePayload):
    old_name = payload.old_name.strip()
    new_name = payload.new_name.strip()
    if old_name == "默认收藏":
        raise HTTPException(400, "默认收藏夹不能重命名")
    if not new_name or len(new_name) > 40:
        raise HTTPException(400, "收藏夹名称需要 1-40 个字符")
    conn = sqlite3.connect(str(DB_PATH))
    exists = conn.execute("SELECT 1 FROM bookmark_folders WHERE name=?", (old_name,)).fetchone()
    if not exists:
        conn.close()
        raise HTTPException(404, "收藏夹不存在")
    conn.execute("INSERT OR IGNORE INTO bookmark_folders (name) VALUES (?)", (new_name,))
    conn.execute("UPDATE bookmarks SET folder=? WHERE folder=?", (new_name, old_name))
    conn.execute("DELETE FROM bookmark_folders WHERE name=?", (old_name,))
    conn.commit()
    conn.close()
    return {"status": "renamed"}


@app.delete("/api/folders/{name}")
async def delete_folder(name: str):
    if name == "默认收藏":
        raise HTTPException(400, "默认收藏夹不能删除")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE bookmarks SET folder='默认收藏' WHERE folder=?", (name,))
    conn.execute("DELETE FROM bookmark_folders WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return {"status": "folder_deleted"}


# ── 接口：分类反馈 ───────────────────────────────────────

class FeedbackPayload(BaseModel):
    corrected_category: Literal["A", "B", "C", "None"]
    note: str = ""


@app.post("/api/messages/{msg_id}/feedback")
async def submit_feedback(msg_id: str, payload: FeedbackPayload):
    note = payload.note.strip()
    if len(note) > 500:
        raise HTTPException(400, "反馈说明不能超过 500 个字符")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM messages WHERE msg_id=?", (msg_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "消息不存在")
    item = dict(row)
    original = item.get("predicted_category") or item["category"]
    source_key = calibration.make_source_key(
        item.get("source_type") or "qq",
        item.get("group_id") or "",
        item.get("group_name") or item.get("source_name") or "",
    )
    model_name = load_config().get("llm", {}).get("model", "")
    conn.execute(
        """INSERT INTO classification_feedback
           (msg_id, original_category, corrected_category, note, raw_content,
            source_type, source_name, source_key, confidence, model_name,
            prompt_version, features_json, active, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
           ON CONFLICT(msg_id) DO UPDATE SET
             corrected_category=excluded.corrected_category,
             note=excluded.note,
             raw_content=excluded.raw_content,
             source_type=excluded.source_type,
             source_name=excluded.source_name,
             source_key=excluded.source_key,
             confidence=excluded.confidence,
             model_name=excluded.model_name,
             prompt_version=excluded.prompt_version,
             features_json=excluded.features_json,
             active=1,
             updated_at=CURRENT_TIMESTAMP""",
        (
            msg_id, original, payload.corrected_category, note,
            item.get("raw_content") or "", item.get("source_type") or "",
            item.get("source_name") or item.get("group_name") or "", source_key,
            item.get("confidence"), model_name, item.get("prompt_version") or "legacy",
            calibration.features_json(item.get("raw_content") or ""),
        ),
    )
    queued_for_mothership = False
    if payload.corrected_category == "None":
        conn.execute("DELETE FROM bookmarks WHERE msg_id=?", (msg_id,))
        conn.execute("DELETE FROM messages WHERE msg_id=?", (msg_id,))
    else:
        conn.execute(
            "UPDATE messages SET category=?, review_required=0 WHERE msg_id=?",
            (payload.corrected_category, msg_id),
        )
        item["category"] = payload.corrected_category
        item["review_required"] = 0
        queued_for_mothership = _enqueue_mothership(conn, item)
    conn.commit()
    conn.close()
    if queued_for_mothership:
        _schedule_mothership_flush()
    calibration_report_sync = await _sync_calibration_report_if_allowed()
    return {
        "status": "saved",
        "original_category": original,
        "corrected_category": payload.corrected_category,
        "calibration_effect": "added",
        "strategy_version": calibration.STRATEGY_VERSION,
        "mothership_stats_sync": calibration_report_sync,
    }


@app.get("/api/feedback/stats")
async def feedback_stats():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        """SELECT original_category, corrected_category, COUNT(*)
             FROM classification_feedback WHERE active=1
            GROUP BY original_category, corrected_category"""
    ).fetchall()
    total = sum(r[2] for r in rows)
    correct = sum(r[2] for r in rows if r[0] == r[1])
    conn.close()
    return {
        "reviewed": total,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy": round(correct / total * 100, 1) if total else None,
        "confusion": [
            {"predicted": r[0], "corrected": r[1], "count": r[2]} for r in rows
        ],
    }


def _calibration_report_payload() -> dict:
    """Build an aggregate-only report. No content, source names or sender data leave the device."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        """SELECT original_category, corrected_category, COUNT(*)
             FROM classification_feedback WHERE active=1
            GROUP BY original_category, corrected_category"""
    ).fetchall()
    conn.close()
    reviewed = sum(int(row[2]) for row in rows)
    correct = sum(int(row[2]) for row in rows if row[0] == row[1])
    from scraper import CLASSIFY_PROMPT
    _prompt, prompt_version = calibration.resolve_prompt(DB_PATH, CLASSIFY_PROMPT, scope="single")
    return {
        "reviewed_count": reviewed,
        "correct_count": correct,
        "confusion": [
            {"predicted": row[0], "corrected": row[1], "count": int(row[2])}
            for row in rows
        ],
        "prompt_version": prompt_version,
    }


async def _sync_calibration_report_if_allowed() -> dict:
    cfg = load_config()
    mothership = _mothership_settings(cfg)
    if not mothership.get("share_calibration_stats"):
        return {"status": "not_authorized"}
    if (
        not mothership.get("enabled")
        or mothership.get("membership_status") in ("paused", "expired", "revoked")
        or not mothership.get("url")
        or not mothership.get("node_token")
    ):
        return {"status": "not_connected"}
    try:
        return await _mothership_node_request(
            "/api/v1/calibration/report",
            method="PUT",
            payload=_calibration_report_payload(),
        )
    except HTTPException as exc:
        return {"status": "error", "detail": str(exc.detail)[:200]}


def _calibration_external_id(msg_id: str) -> str:
    return hashlib.sha256(f"calibration|{msg_id}".encode("utf-8")).hexdigest()


class CalibrationSettingsPayload(BaseModel):
    max_examples: Optional[int] = Field(None, ge=3, le=5)
    min_similarity: Optional[float] = Field(None, ge=0, le=1)
    override_similarity: Optional[float] = Field(None, ge=0, le=1)
    default_threshold: Optional[float] = Field(None, ge=0, le=1)
    retrieval_enabled: Optional[bool] = None


class SourceThresholdPayload(BaseModel):
    source_key: str = Field(..., min_length=3, max_length=220)
    label: str = Field("", max_length=200)
    confidence_threshold: float = Field(..., ge=0, le=1)


class CalibrationFlagPayload(BaseModel):
    enabled: bool


def _calibration_summary() -> dict:
    settings = calibration.get_settings(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    counts = conn.execute(
        """SELECT COUNT(*) AS reviewed,
                  SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) AS active,
                  SUM(CASE WHEN active=1 AND is_gold=1 THEN 1 ELSE 0 END) AS gold,
                  SUM(CASE WHEN active=1 AND original_category<>corrected_category THEN 1 ELSE 0 END) AS corrected
             FROM classification_feedback"""
    ).fetchone()
    examples = conn.execute(
        """SELECT msg_id, raw_content, original_category, corrected_category, note,
                  source_name, source_key, confidence, prompt_version, is_gold,
                  active, shared_with_mothership, shared_at,
                  COALESCE(NULLIF(updated_at,''), created_at) AS reviewed_at
             FROM classification_feedback
            ORDER BY active DESC, reviewed_at DESC, id DESC LIMIT 50"""
    ).fetchall()
    source_rows = conn.execute(
        """SELECT source_key, label, confidence_threshold, updated_at
             FROM calibration_source_settings ORDER BY label, source_key"""
    ).fetchall()
    latest = conn.execute(
        "SELECT * FROM calibration_evaluations ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prompt_versions = conn.execute(
        """SELECT version, scope, label, release_note, status, created_at, activated_at
             FROM calibration_prompt_versions WHERE scope='single'
            ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
                     created_at DESC"""
    ).fetchall()
    conn.close()
    from scraper import CLASSIFY_PROMPT
    _active_prompt, active_prompt_version = calibration.resolve_prompt(
        DB_PATH, CLASSIFY_PROMPT, scope="single"
    )
    mothership = _mothership_settings(load_config())
    source_map = {row["source_key"]: dict(row) for row in source_rows}
    for row in examples:
        if row["source_key"] and row["source_key"] not in source_map:
            source_map[row["source_key"]] = {
                "source_key": row["source_key"],
                "label": row["source_name"] or "未命名来源",
                "confidence_threshold": settings["default_threshold"],
                "updated_at": "",
            }
    return {
        "strategy": {
            **settings,
            "active_prompt_version": active_prompt_version,
            "layers": [
                {"key": "base", "label": "稳定分类规则", "managed_by": "应用版本"},
                {"key": "memory", "label": "本地复核案例", "managed_by": "用户反馈"},
                {"key": "source", "label": "来源置信阈值", "managed_by": "校准策略"},
                {"key": "gate", "label": "低置信度待确认", "managed_by": "安全门"},
            ],
        },
        "counts": {key: int(counts[key] or 0) for key in ("reviewed", "active", "gold", "corrected")},
        "examples": [
            {
                **dict(row),
                "content_preview": (row["raw_content"] or "")[:140],
                "raw_content": None,
                "is_gold": bool(row["is_gold"]),
                "active": bool(row["active"]),
                "shared_with_mothership": bool(row["shared_with_mothership"]),
            }
            for row in examples
        ],
        "sources": sorted(source_map.values(), key=lambda item: (item["label"], item["source_key"])),
        "prompt_versions": [dict(row) for row in prompt_versions],
        "latest_evaluation": dict(latest) if latest else None,
        "mothership": {
            "connected": bool(mothership.get("url") and mothership.get("node_token")),
            "membership_status": mothership.get("membership_status", ""),
            "space_name": mothership.get("space_name", ""),
            "share_calibration_stats": bool(mothership.get("share_calibration_stats", False)),
        },
    }


@app.get("/api/calibration")
async def calibration_summary():
    return _calibration_summary()


@app.patch("/api/calibration/settings")
async def update_calibration_settings(payload: CalibrationSettingsPayload):
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return _calibration_summary()
    allowed = {
        "max_examples", "min_similarity", "override_similarity",
        "default_threshold", "retrieval_enabled",
    }
    conn = sqlite3.connect(str(DB_PATH))
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "retrieval_enabled":
            value = 1 if value else 0
        conn.execute(
            f"UPDATE calibration_settings SET {key}=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (value,),
        )
    conn.commit()
    conn.close()
    return _calibration_summary()


@app.post("/api/calibration/prompt-versions/{version}/activate")
async def activate_calibration_prompt(version: str):
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT scope FROM calibration_prompt_versions WHERE version=?",
        (version,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "提示词版本不存在")
    scope = row[0]
    conn.execute(
        "UPDATE calibration_prompt_versions SET status='archived' WHERE scope=? AND status='active'",
        (scope,),
    )
    conn.execute(
        """UPDATE calibration_prompt_versions
              SET status='active', activated_at=CURRENT_TIMESTAMP
            WHERE version=?""",
        (version,),
    )
    conn.commit()
    conn.close()
    return _calibration_summary()


@app.put("/api/calibration/source-threshold")
async def update_source_threshold(payload: SourceThresholdPayload):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """INSERT INTO calibration_source_settings
           (source_key, label, confidence_threshold, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(source_key) DO UPDATE SET
             label=excluded.label,
             confidence_threshold=excluded.confidence_threshold,
             updated_at=CURRENT_TIMESTAMP""",
        (payload.source_key, payload.label.strip(), payload.confidence_threshold),
    )
    conn.commit()
    conn.close()
    return {"status": "saved"}


@app.post("/api/calibration/examples/{msg_id}/gold")
async def set_calibration_gold(msg_id: str, payload: CalibrationFlagPayload):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "UPDATE classification_feedback SET is_gold=?, updated_at=CURRENT_TIMESTAMP WHERE msg_id=?",
        (1 if payload.enabled else 0, msg_id),
    )
    conn.commit()
    conn.close()
    if not cursor.rowcount:
        raise HTTPException(404, "校准案例不存在")
    return {"status": "saved", "is_gold": payload.enabled}


@app.post("/api/calibration/examples/{msg_id}/active")
async def set_calibration_active(msg_id: str, payload: CalibrationFlagPayload):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "UPDATE classification_feedback SET active=?, updated_at=CURRENT_TIMESTAMP WHERE msg_id=?",
        (1 if payload.enabled else 0, msg_id),
    )
    conn.commit()
    conn.close()
    if not cursor.rowcount:
        raise HTTPException(404, "校准案例不存在")
    return {"status": "saved", "active": payload.enabled}


@app.post("/api/calibration/examples/{msg_id}/share")
async def share_calibration_example(msg_id: str, payload: CalibrationFlagPayload):
    external_id = _calibration_external_id(msg_id)
    if not payload.enabled:
        result = await _mothership_node_request(
            f"/api/v1/calibration/examples/{external_id}", method="DELETE"
        )
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute(
            """UPDATE classification_feedback
                  SET shared_with_mothership=0, shared_at='', updated_at=CURRENT_TIMESTAMP
                WHERE msg_id=?""",
            (msg_id,),
        )
        conn.commit()
        conn.close()
        if not cursor.rowcount:
            raise HTTPException(404, "校准案例不存在")
        return {**result, "shared_with_mothership": False}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT msg_id, original_category, corrected_category, confidence,
                  source_type, source_key, raw_content, prompt_version
             FROM classification_feedback WHERE msg_id=? AND active=1""",
        (msg_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "校准案例不存在或已停用")
    item = {
        "external_id": external_id,
        "predicted_category": row["original_category"],
        "corrected_category": row["corrected_category"],
        "confidence": row["confidence"],
        "source_type": (row["source_type"] or "unknown")[:40],
        "source_ref": hashlib.sha256((row["source_key"] or "unknown").encode("utf-8")).hexdigest()[:24],
        "content_excerpt": _redact_evidence(row["raw_content"] or ""),
        "prompt_version": (row["prompt_version"] or "legacy")[:160],
    }
    result = await _mothership_node_request(
        "/api/v1/calibration/examples",
        method="PUT",
        payload={"consent_confirmed": True, "item": item},
    )
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """UPDATE classification_feedback
              SET shared_with_mothership=1, shared_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE msg_id=?""",
        (msg_id,),
    )
    conn.commit()
    conn.close()
    return {**result, "shared_with_mothership": True}


@app.post("/api/calibration/evaluate")
async def run_calibration_evaluation():
    result = calibration.evaluate_gold_set(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """INSERT INTO calibration_evaluations
           (strategy_version, sample_count, baseline_accuracy, calibrated_accuracy, details_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            result["strategy_version"], result["sample_count"],
            result["baseline_accuracy"], result["calibrated_accuracy"],
            json.dumps(result["details"], ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return result


# ── 接口：情报母舰 ────────────────────────────────────────

async def _mothership_remote_request(
    path: str,
    *,
    method: str = "GET",
    admin: bool = False,
    payload: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    cfg = load_config()
    mothership = _mothership_settings(cfg)
    url = mothership.get("url", "").strip()
    if not url:
        raise HTTPException(400, "尚未配置母舰地址")
    base_url = _validate_http_url(url, "母舰地址")
    headers = {}
    if admin:
        token = mothership.get("admin_token", "").strip()
        if not token:
            raise HTTPException(400, "尚未配置母舰管理员令牌")
        headers["X-Admin-Token"] = token
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(connect=5, read=20, write=10, pool=5)) as client:
            response = await client.request(
                method,
                f"{base_url}{path}",
                headers=headers,
                json=payload,
                params=params,
            )
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = data.get("detail", f"母舰返回 HTTP {response.status_code}") if isinstance(data, dict) else f"母舰返回 HTTP {response.status_code}"
            raise HTTPException(response.status_code if response.status_code in (400, 401, 403, 404, 409, 410) else 502, detail)
        if not isinstance(data, dict):
            raise HTTPException(502, "母舰返回格式无效")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"无法连接情报母舰：{type(exc).__name__}")


async def _mothership_direct_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Optional[dict] = None,
    headers: Optional[dict[str, str]] = None,
) -> dict:
    base_url = _validate_http_url(base_url, "母舰地址")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(connect=5, read=20, write=10, pool=5)) as client:
            response = await client.request(method, f"{base_url}{path}", headers=headers or {}, json=payload)
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = data.get("detail", f"母舰返回 HTTP {response.status_code}") if isinstance(data, dict) else f"母舰返回 HTTP {response.status_code}"
            raise HTTPException(response.status_code if response.status_code in (400, 401, 403, 404, 409, 410) else 502, detail)
        if not isinstance(data, dict):
            raise HTTPException(502, "母舰返回格式无效")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"无法连接情报母舰：{type(exc).__name__}")


async def _mothership_node_request(path: str, *, method: str = "GET", payload: Optional[dict] = None) -> dict:
    cfg = load_config()
    mothership = _mothership_settings(cfg)
    url = mothership.get("url", "").strip()
    token = mothership.get("node_token", "").strip()
    if not url or not token:
        raise HTTPException(400, "尚未加入协作空间")
    return await _mothership_direct_request(
        url, path, method=method, payload=payload, headers={"Authorization": f"Bearer {token}"}
    )


class InvitationInspectPayload(BaseModel):
    mothership_url: str
    invite_key: str = Field(..., min_length=12, max_length=256)


class MembershipJoinPayload(InvitationInspectPayload):
    device_name: str = Field(..., min_length=1, max_length=60)
    categories: list[Literal["A", "B", "C"]] = Field(..., min_length=1, max_length=3)
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    share_evidence: bool = False
    share_calibration_stats: bool = False
    expires_days: int = Field(30, ge=1, le=365)


class SharePreviewPayload(BaseModel):
    categories: list[Literal["A", "B", "C"]] = Field(..., min_length=1, max_length=3)
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    share_evidence: bool = False
    share_calibration_stats: bool = False


class MembershipGrantPayload(BaseModel):
    categories: list[Literal["A", "B", "C"]] = Field(..., min_length=1, max_length=3)
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    share_evidence: bool = False
    share_calibration_stats: bool = False


class MembershipStatePayload(BaseModel):
    state: Literal["active", "paused"]


class MembershipRevokePayload(BaseModel):
    delete_data: bool = False


class EvidenceRespondPayload(BaseModel):
    decision: Literal["approved", "denied"]


@app.get("/api/mothership/share-options")
async def mothership_share_options():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT source_type, source_name, group_id, group_name, COUNT(*) AS message_count
           FROM messages GROUP BY source_type, source_name, group_id, group_name
           ORDER BY message_count DESC, group_name"""
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        item = dict(row)
        items.append({
            "ref": _message_source_ref(item),
            "label": item.get("group_name") or item.get("source_name") or item.get("source_type") or "未知来源",
            "source_type": item.get("source_type") or "qq",
            "source_name": item.get("source_name") or "",
            "message_count": item["message_count"],
        })
    return {"items": items}


@app.post("/api/mothership/share-preview")
async def mothership_share_preview(payload: SharePreviewPayload):
    cfg = load_config()
    preview_cfg = json.loads(json.dumps(cfg))
    preview_cfg.setdefault("mothership", {}).update({
        "categories": list(dict.fromkeys(payload.categories)),
        "source_refs": list(dict.fromkeys(payload.source_refs)),
        "share_evidence": payload.share_evidence,
        "share_calibration_stats": payload.share_calibration_stats,
        "strict_source_grant": True,
    })
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 2000").fetchall()
    conn.close()
    matched = [dict(row) for row in rows if _message_matches_grant(dict(row), preview_cfg)]
    cards = [_build_mothership_card(item, preview_cfg) for item in matched[:5]]
    return {
        "count": len(matched),
        "items": cards,
        "shared_fields": ["分类", "摘要", "标签", "置信度", "来源名称", "发生时间"]
            + (["脱敏证据片段"] if payload.share_evidence else [])
            + (["匿名校准统计（数量、准确率、混淆矩阵、提示词版本）"] if payload.share_calibration_stats else []),
        "never_shared": ["发送者身份", "完整原文", "私聊内容", "未逐条批准的纠错样本"],
    }


@app.post("/api/mothership/invitations/inspect")
async def local_inspect_invitation(payload: InvitationInspectPayload):
    return await _mothership_direct_request(
        payload.mothership_url, "/api/v1/invitations/inspect", method="POST", payload={"invite_key": payload.invite_key.strip()}
    )


@app.post("/api/mothership/membership/join")
async def local_join_membership(payload: MembershipJoinPayload):
    categories = list(dict.fromkeys(payload.categories))
    refs = list(dict.fromkeys(ref.strip() for ref in payload.source_refs if ref.strip()))
    result = await _mothership_direct_request(
        payload.mothership_url,
        "/api/v1/invitations/join",
        method="POST",
        payload={
            "invite_key": payload.invite_key.strip(),
            "device_name": payload.device_name.strip(),
            "categories": categories,
            "source_refs": refs,
            "share_evidence": payload.share_evidence,
            "share_calibration_stats": payload.share_calibration_stats,
            "expires_days": payload.expires_days,
        },
    )
    cfg = load_config()
    mothership = cfg.setdefault("mothership", {})
    mothership.update({
        "enabled": True,
        "url": _validate_http_url(payload.mothership_url, "母舰地址"),
        "node_name": payload.device_name.strip(),
        "node_token": result["member_token"],
        "space_id": result["space"]["id"],
        "space_name": result["space"]["name"],
        "owner_label": result["space"].get("owner_label", ""),
        "membership_status": "active",
        "categories": categories,
        "source_refs": refs,
        "share_evidence": payload.share_evidence,
        "share_calibration_stats": payload.share_calibration_stats,
        "expires_at": result["grant"]["expires_at"],
    })
    save_config(cfg)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM mothership_outbox")
    conn.commit()
    conn.close()
    safe_result = {key: value for key, value in result.items() if key != "member_token"}
    safe_result["calibration_report_sync"] = await _sync_calibration_report_if_allowed()
    return safe_result


@app.get("/api/mothership/membership")
async def local_membership():
    result = await _mothership_node_request("/api/v1/membership")
    cfg = load_config()
    mothership = cfg.setdefault("mothership", {})
    mothership.update({
        "membership_status": result.get("status", ""),
        "space_id": result.get("space_id", ""),
        "space_name": result.get("space_name", ""),
        "owner_label": result.get("owner_label", ""),
        "categories": result.get("categories", []),
        "source_refs": result.get("source_refs", []),
        "share_evidence": bool(result.get("share_evidence")),
        "share_calibration_stats": bool(result.get("share_calibration_stats")),
        "expires_at": result.get("expires_at", ""),
    })
    save_config(cfg)
    return result


@app.patch("/api/mothership/membership/grant")
async def local_update_membership_grant(payload: MembershipGrantPayload):
    categories = list(dict.fromkeys(payload.categories))
    refs = list(dict.fromkeys(ref.strip() for ref in payload.source_refs if ref.strip()))
    result = await _mothership_node_request(
        "/api/v1/membership/grant", method="PATCH",
        payload={
            "categories": categories,
            "source_refs": refs,
            "share_evidence": payload.share_evidence,
            "share_calibration_stats": payload.share_calibration_stats,
        },
    )
    cfg = load_config()
    mothership = cfg.setdefault("mothership", {})
    mothership.update({
        "categories": categories,
        "source_refs": refs,
        "share_evidence": payload.share_evidence,
        "share_calibration_stats": payload.share_calibration_stats,
    })
    save_config(cfg)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM mothership_outbox WHERE synced_at IS NULL")
    conn.commit()
    conn.close()
    result["calibration_report_sync"] = await _sync_calibration_report_if_allowed()
    return result


@app.post("/api/mothership/membership/state")
async def local_set_membership_state(payload: MembershipStatePayload):
    result = await _mothership_node_request("/api/v1/membership/state", method="POST", payload=payload.model_dump())
    cfg = load_config()
    mothership = cfg.setdefault("mothership", {})
    mothership["membership_status"] = payload.state
    mothership["enabled"] = payload.state == "active"
    save_config(cfg)
    return result


@app.delete("/api/mothership/membership/data")
async def local_delete_membership_data():
    result = await _mothership_node_request("/api/v1/membership/data", method="DELETE")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM mothership_outbox")
    conn.execute("UPDATE classification_feedback SET shared_with_mothership=0, shared_at=''")
    conn.commit()
    conn.close()
    return result


@app.post("/api/mothership/membership/revoke")
async def local_revoke_membership(payload: MembershipRevokePayload):
    result = await _mothership_node_request("/api/v1/membership/revoke", method="POST", payload=payload.model_dump())
    cfg = load_config()
    mothership = cfg.setdefault("mothership", {})
    mothership.update({
        "enabled": False, "node_token": "", "space_id": "", "space_name": "",
        "owner_label": "", "membership_status": "revoked", "expires_at": "",
        "categories": ["A", "B", "C"], "source_refs": [], "share_evidence": False,
        "share_calibration_stats": False,
    })
    save_config(cfg)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM mothership_outbox")
    conn.commit()
    conn.close()
    return result


def _find_local_message_by_external_id(external_id: str) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM messages ORDER BY id DESC").fetchall()
    conn.close()
    for row in rows:
        item = dict(row)
        source_type = str(item.get("source_type", "qq") or "qq")
        fingerprint = hashlib.sha256(f"{source_type}|{item.get('msg_id', '')}".encode("utf-8")).hexdigest()
        if hmac.compare_digest(fingerprint, external_id):
            return item
    return None


@app.get("/api/mothership/evidence-requests")
async def local_evidence_requests():
    result = await _mothership_node_request("/api/v1/evidence-requests")
    items = []
    for request_item in result.get("items", []):
        item = dict(request_item)
        local_message = _find_local_message_by_external_id(str(item.get("external_id", "")))
        item["local_available"] = bool(local_message)
        item["local_content"] = str(local_message.get("raw_content", ""))[:5000] if local_message else ""
        items.append(item)
    return {"items": items}


@app.post("/api/mothership/evidence-requests/{request_id}/respond")
async def local_respond_evidence_request(request_id: int, payload: EvidenceRespondPayload):
    requests = await _mothership_node_request("/api/v1/evidence-requests")
    request_item = next((item for item in requests.get("items", []) if item.get("id") == request_id), None)
    if not request_item:
        raise HTTPException(404, "原文申请不存在")
    content = ""
    if payload.decision == "approved":
        local_message = _find_local_message_by_external_id(str(request_item.get("external_id", "")))
        if not local_message:
            raise HTTPException(404, "本机已找不到对应原文")
        content = str(local_message.get("raw_content", ""))[:5000]
    return await _mothership_node_request(
        f"/api/v1/evidence-requests/{request_id}/respond",
        method="POST",
        payload={"decision": payload.decision, "content": content},
    )


@app.get("/api/mothership/status")
async def mothership_status():
    cfg = load_config()
    mothership = _mothership_settings(cfg)
    conn = sqlite3.connect(str(DB_PATH))
    pending = conn.execute("SELECT COUNT(*) FROM mothership_outbox WHERE synced_at IS NULL").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM mothership_outbox WHERE synced_at IS NULL AND attempts>0").fetchone()[0]
    last_error_row = conn.execute(
        "SELECT last_error FROM mothership_outbox WHERE last_error!='' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    remote = None
    membership = None
    if mothership.get("url"):
        try:
            remote = await _mothership_remote_request("/api/health")
        except HTTPException as exc:
            remote = {"status": "offline", "detail": exc.detail}
    if mothership.get("url") and mothership.get("node_token"):
        try:
            membership = await _mothership_node_request("/api/v1/membership")
        except HTTPException as exc:
            membership = {"status": "unavailable", "detail": exc.detail}
    return {
        "enabled": bool(mothership.get("enabled")),
        "configured": bool(mothership.get("url") and mothership.get("node_token")),
        "admin_configured": bool(mothership.get("url") and mothership.get("admin_token")),
        "share_evidence": bool(mothership.get("share_evidence", False)),
        "share_calibration_stats": bool(mothership.get("share_calibration_stats", False)),
        "privacy_mode": "redacted_evidence" if mothership.get("share_evidence") else "structured_intelligence_only",
        "pending": pending,
        "failed": failed,
        "last_error": last_error_row[0] if last_error_row else "",
        "remote": remote,
        "membership": membership,
    }


@app.post("/api/mothership/sync")
async def mothership_sync():
    cfg = load_config()
    mothership = _mothership_settings(cfg)
    if not mothership.get("enabled"):
        raise HTTPException(400, "请先启用母舰同步")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1000").fetchall()
    queued = 0
    for row in rows:
        if _enqueue_mothership(conn, dict(row), cfg):
            queued += 1
    conn.commit()
    conn.close()
    result = await _flush_mothership_outbox(200)
    result["scanned"] = len(rows)
    result["queued"] = queued
    return result


class MothershipNodeCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class MothershipNodeUpdatePayload(BaseModel):
    enabled: bool


class MothershipSpaceCreatePayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: str = Field("", max_length=300)
    owner_label: str = Field("", max_length=80)
    invite_days: int = Field(30, ge=1, le=365)


class MothershipInvitationRotatePayload(BaseModel):
    expires_days: int = Field(30, ge=1, le=365)
    max_uses: int = Field(0, ge=0, le=10000)


class MothershipEvidenceRequestPayload(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


class MothershipReviewPayload(BaseModel):
    verdict: Literal["confirmed", "false_positive", "corrected"]
    corrected_category: Optional[Literal["A", "B", "C"]] = None
    note: str = Field("", max_length=500)


class MothershipDispositionPayload(BaseModel):
    disposition: Literal["new", "acknowledged", "assigned", "resolved"]


@app.get("/api/mothership/admin/dashboard")
async def mothership_admin_dashboard():
    return await _mothership_remote_request("/api/admin/dashboard", admin=True)


@app.get("/api/mothership/admin/nodes")
async def mothership_admin_nodes():
    return await _mothership_remote_request("/api/admin/nodes", admin=True)


@app.get("/api/mothership/admin/spaces")
async def mothership_admin_spaces():
    return await _mothership_remote_request("/api/admin/spaces", admin=True)


@app.get("/api/mothership/admin/calibration")
async def mothership_admin_calibration():
    return await _mothership_remote_request("/api/admin/calibration", admin=True)


@app.post("/api/mothership/admin/spaces")
async def mothership_admin_create_space(payload: MothershipSpaceCreatePayload):
    return await _mothership_remote_request(
        "/api/admin/spaces", method="POST", admin=True, payload=payload.model_dump()
    )


@app.post("/api/mothership/admin/spaces/{space_id}/invitations")
async def mothership_admin_rotate_invitation(space_id: str, payload: MothershipInvitationRotatePayload):
    return await _mothership_remote_request(
        f"/api/admin/spaces/{space_id}/invitations", method="POST", admin=True, payload=payload.model_dump()
    )


@app.post("/api/mothership/admin/nodes")
async def mothership_admin_create_node(payload: MothershipNodeCreatePayload):
    return await _mothership_remote_request(
        "/api/admin/nodes", method="POST", admin=True, payload=payload.model_dump()
    )


@app.patch("/api/mothership/admin/nodes/{node_id}")
async def mothership_admin_update_node(node_id: str, payload: MothershipNodeUpdatePayload):
    return await _mothership_remote_request(
        f"/api/admin/nodes/{node_id}", method="PATCH", admin=True, payload=payload.model_dump()
    )


@app.get("/api/mothership/admin/intelligence")
async def mothership_admin_intelligence(
    category: Optional[Literal["A", "B", "C"]] = None,
    node_id: Optional[str] = None,
    disposition: Optional[Literal["new", "acknowledged", "assigned", "resolved"]] = None,
    review_status: Optional[Literal["unreviewed", "confirmed", "false_positive", "corrected"]] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    params = {
        "category": category,
        "node_id": node_id,
        "disposition": disposition,
        "review_status": review_status,
        "limit": limit,
        "offset": offset,
    }
    return await _mothership_remote_request(
        "/api/admin/intelligence", admin=True, params={key: value for key, value in params.items() if value is not None}
    )


@app.post("/api/mothership/admin/intelligence/{intelligence_id}/review")
async def mothership_admin_review(intelligence_id: int, payload: MothershipReviewPayload):
    return await _mothership_remote_request(
        f"/api/admin/intelligence/{intelligence_id}/review",
        method="POST",
        admin=True,
        payload=payload.model_dump(),
    )


@app.post("/api/mothership/admin/intelligence/{intelligence_id}/disposition")
async def mothership_admin_disposition(intelligence_id: int, payload: MothershipDispositionPayload):
    return await _mothership_remote_request(
        f"/api/admin/intelligence/{intelligence_id}/disposition",
        method="POST",
        admin=True,
        payload=payload.model_dump(),
    )


@app.get("/api/mothership/admin/evidence-requests")
async def mothership_admin_evidence_requests():
    return await _mothership_remote_request("/api/admin/evidence-requests", admin=True)


@app.post("/api/mothership/admin/intelligence/{intelligence_id}/evidence-requests")
async def mothership_admin_request_evidence(intelligence_id: int, payload: MothershipEvidenceRequestPayload):
    return await _mothership_remote_request(
        f"/api/admin/intelligence/{intelligence_id}/evidence-requests",
        method="POST",
        admin=True,
        payload=payload.model_dump(),
    )


# ── 接口：周报 ────────────────────────────────────────────

def _summary_sources(conn: sqlite3.Connection, summary: str) -> list[dict]:
    ids = sorted({int(value) for value in re.findall(r"\[M(\d+)\]", summary)})
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT id, category, summary, group_name, sender_name, created_at, source_type, source_name
            FROM messages WHERE id IN ({placeholders})""",
        ids,
    ).fetchall()
    return [dict(row) for row in rows]

@app.get("/api/weekly_summaries")
async def list_weekly_summaries():
    """返回所有已缓存的历史周报列表。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT week_start, week_end, category, summary, created_at FROM summary_log ORDER BY id DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["sources"] = _summary_sources(conn, item["summary"])
        result.append(item)
    conn.close()
    return result


@app.get("/api/weekly_summary")
async def weekly_summary(
    category: Optional[str] = Query(None, pattern="^[ABC]$"),
    refresh: bool = Query(False),
):
    import datetime as _dt
    today = _dt.date.today()
    week_start = today - _dt.timedelta(days=today.weekday())
    week_end = week_start + _dt.timedelta(days=6)
    ws, we = week_start.isoformat(), week_end.isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 检查缓存
    cached = conn.execute(
        "SELECT summary FROM summary_log WHERE week_start=? AND category=? ORDER BY id DESC LIMIT 1",
        (ws, category or "ALL"),
    ).fetchone()
    if cached and not refresh:
        sources = _summary_sources(conn, cached["summary"])
        conn.close()
        return {"week_start": ws, "week_end": we, "category": category, "summary": cached["summary"], "sources": sources, "cached": True}

    # 查询本周消息
    conditions = ["created_at >= ?", "created_at <= ?"]
    params: list = [ws + " 00:00:00", we + " 23:59:59"]
    if category:
        conditions.append("category=?")
        params.append(category)
    where = " WHERE " + " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT id, category, summary, tags, sender_name, group_name, created_at FROM messages{where} ORDER BY created_at",
        params,
    ).fetchall()
    conn.close()

    if not rows:
        return {"week_start": ws, "week_end": we, "category": category, "summary": "本周暂无消息。", "cached": False}

    # 拼 prompt
    lines = []
    prompt_rows = rows[:200]
    for r in prompt_rows:
        tags = json.loads(r["tags"]) if r["tags"] else []
        lines.append(f"[M{r['id']}][{r['category']}] {r['created_at']} {r['group_name']}/{r['sender_name']}: {r['summary']} {' '.join('#'+t for t in tags)}")

    cfg = load_config()
    llm = cfg.get("llm", {})
    api_key = llm.get("api_key", "")
    base_url = _validate_http_url(llm.get("base_url", "https://api.deepseek.com/v1"), "LLM Base URL")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    model = llm.get("model", "deepseek-chat")

    if not api_key:
        raise HTTPException(400, "未配置 LLM API Key，无法生成周报")

    prompt = f"""以下是本周（{ws} ~ {we}）的社群情报消息，共 {len(lines)} 条。
请按以下格式生成周报摘要，并在每个事实后引用对应的消息编号，例如 [M12]：

1. **A 类（重要信息）**：本周有哪些重要事项？列出关键点。
2. **B 类（校园轶事）**：本周有哪些有趣的校园故事？
3. **C 类（二手资讯）**：本周有哪些二手交易信息？

如果某个类别没有消息，简短说明即可。使用简洁的中文。

消息列表：
{chr(10).join(lines[:200])}"""

    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1500, "temperature": 0.3},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            summary = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if not summary:
                raise ValueError("模型返回了空周报")
            referenced_ids = {int(value) for value in re.findall(r"\[M(\d+)\]", summary)}
            allowed_ids = {int(row["id"]) for row in prompt_rows}
            if not referenced_ids:
                raise ValueError("模型未提供任何消息引用，请重试")
            unknown_ids = referenced_ids - allowed_ids
            if unknown_ids:
                raise ValueError(f"模型引用了不存在的消息：{sorted(unknown_ids)[:5]}")
    except Exception as e:
        raise HTTPException(502, f"周报生成失败：{e}")

    # 缓存
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if refresh:
        conn.execute(
            "DELETE FROM summary_log WHERE week_start=? AND category=?",
            (ws, category or "ALL"),
        )
    conn.execute(
        "INSERT INTO summary_log (week_start, week_end, category, summary) VALUES (?, ?, ?, ?)",
        (ws, we, category or "ALL", summary),
    )
    conn.commit()
    sources = _summary_sources(conn, summary)
    conn.close()

    return {"week_start": ws, "week_end": we, "category": category, "summary": summary, "sources": sources, "cached": False}


# ── 接口：SSE 实时推送 ────────────────────────────────────

@app.get("/api/stream")
async def stream():
    return StreamingResponse(_event_stream(), media_type="text/event-stream")


# ── 接口：供 scraper 写入已分类消息 ────────────────────────

class ClassifiedMessage(BaseModel):
    msg_id: str
    chat_type: str = "group"
    group_id: str = ""
    group_name: str = ""
    sender_id: str
    sender_name: str
    raw_content: str
    category: Literal["A", "B", "C"]
    summary: str
    tags: list[str]
    created_at: str = ""
    source_type: str = "qq"
    source_name: str = "QQ"
    source_url: str = ""
    confidence: Optional[float] = None
    classification_method: str = "llm"
    predicted_category: Optional[Literal["A", "B", "C"]] = None
    review_required: bool = False
    prompt_version: str = ""
    calibration_examples: int = Field(0, ge=0, le=5)
    calibration_similarity: float = Field(0, ge=0, le=1)


@app.post("/api/ingest")
async def ingest(msg: ClassifiedMessage):
    if not msg.msg_id.strip() or not msg.raw_content.strip():
        raise HTTPException(400, "消息 ID 和正文不能为空")
    if len(msg.raw_content) > 20000:
        raise HTTPException(400, "单条消息不能超过 20000 个字符")
    if msg.source_url:
        parsed_source = urlparse(msg.source_url)
        if parsed_source.scheme not in ("http", "https") or not parsed_source.netloc or len(msg.source_url) > 2000:
            raise HTTPException(400, "来源链接必须是有效的 http/https 地址")
    ts = msg.created_at or time.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(DB_PATH))
    queued_for_mothership = False
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO messages
               (msg_id, chat_type, group_id, group_name, sender_id, sender_name,
                raw_content, category, summary, tags, created_at, source_type,
                source_name, source_url, confidence, classification_method,
                predicted_category, review_required, prompt_version,
                calibration_examples, calibration_similarity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg.msg_id, msg.chat_type, msg.group_id, msg.group_name,
             msg.sender_id, msg.sender_name, msg.raw_content,
             msg.category, msg.summary, json.dumps(msg.tags, ensure_ascii=False), ts,
             msg.source_type, msg.source_name, msg.source_url,
             msg.confidence, msg.classification_method,
             msg.predicted_category or msg.category, 1 if msg.review_required else 0,
             msg.prompt_version, msg.calibration_examples, msg.calibration_similarity),
        )
        if cursor.rowcount:
            card_source = msg.model_dump()
            card_source["created_at"] = ts
            if not msg.review_required:
                queued_for_mothership = _enqueue_mothership(conn, card_source)
        conn.commit()
    finally:
        conn.close()
    if cursor.rowcount == 0:
        return {"status": "duplicate"}
    _broadcast({
        "type": "new_message",
        "data": {
            "msg_id": msg.msg_id,
            "chat_type": msg.chat_type,
            "group_id": msg.group_id,
            "group_name": msg.group_name,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "raw_content": msg.raw_content,
            "category": msg.category,
            "summary": msg.summary,
            "tags": msg.tags,
            "created_at": ts,
            "source_type": msg.source_type,
            "source_name": msg.source_name,
            "source_url": msg.source_url,
            "confidence": msg.confidence,
            "classification_method": msg.classification_method,
            "predicted_category": msg.predicted_category or msg.category,
            "review_required": msg.review_required,
            "prompt_version": msg.prompt_version,
            "calibration_examples": msg.calibration_examples,
            "calibration_similarity": msg.calibration_similarity,
        },
    })
    if queued_for_mothership:
        _schedule_mothership_flush()
    return {"status": "ok"}


class SourceMessage(BaseModel):
    id: str = Field("", max_length=200)
    channel_id: str = Field("", max_length=200)
    channel_name: str = Field("", max_length=200)
    sender_id: str = Field("", max_length=200)
    sender_name: str = Field("", max_length=200)
    content: str = Field(..., max_length=20000)
    created_at: str = Field("", max_length=40)
    source_url: str = Field("", max_length=2000)


class SourceImportPayload(BaseModel):
    source_type: Literal["csv", "webhook", "feishu", "dingtalk", "email", "rss"]
    source_name: str = Field("", max_length=200)
    messages: list[SourceMessage] = Field(..., min_length=1, max_length=500)


@app.post("/api/sources/import")
async def import_source_messages(payload: SourceImportPayload):
    """导入用户主动提供的数据。CSV 由前端解析，Webhook 可直接提交同一结构。"""
    from scraper import classify_message

    cfg = load_config()
    imported = 0
    filtered = 0
    duplicates = 0
    for index, item in enumerate(payload.messages):
        content = item.content.strip()
        if len(content) < 2:
            filtered += 1
            continue
        if len(content) > 20000:
            raise HTTPException(400, f"第 {index + 1} 条消息超过 20000 个字符")
        result = await classify_message(
            content,
            cfg,
            {
                "source_type": payload.source_type,
                "group_id": item.channel_id,
                "group_name": item.channel_name or payload.source_name,
            },
            DB_PATH,
        )
        category = result.get("category", "None")
        if category not in ("A", "B", "C"):
            filtered += 1
            continue
        external_id = item.id.strip() or hashlib.sha256(
            f"{payload.source_type}|{item.channel_id}|{item.sender_id}|{item.created_at}|{content}".encode("utf-8")
        ).hexdigest()[:24]
        tags = result.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(tag)[:40] for tag in tags[:8]]
        summary = result.get("summary", content[:80])
        if not isinstance(summary, str) or not summary.strip():
            summary = content[:80]
        response = await ingest(ClassifiedMessage(
            msg_id=f"{payload.source_type}:{external_id}",
            chat_type="group",
            group_id=item.channel_id,
            group_name=item.channel_name,
            sender_id=item.sender_id,
            sender_name=item.sender_name,
            raw_content=content,
            category=category,
            summary=summary[:500],
            tags=tags,
            created_at=item.created_at,
            source_type=payload.source_type,
            source_name=payload.source_name.strip() or payload.source_type.upper(),
            source_url=item.source_url,
            confidence=result.get("confidence"),
            classification_method=result.get("method", "llm"),
            predicted_category=result.get("base_category") or category,
            review_required=bool(result.get("review_required")),
            prompt_version=result.get("prompt_version", ""),
            calibration_examples=int(result.get("calibration_examples", 0)),
            calibration_similarity=float(result.get("calibration_similarity", 0)),
        ))
        if response.get("status") == "duplicate":
            duplicates += 1
        else:
            imported += 1
    return {"status": "ok", "imported": imported, "filtered": filtered, "duplicates": duplicates}


# ── 接口：缓冲池（batch 模式） ───────────────────────────

class BufferMessage(BaseModel):
    msg_id: str
    chat_type: str = "group"
    chat_id: str = ""
    chat_name: str = ""
    sender_id: str
    sender_name: str
    raw_content: str
    created_at: str = ""


@app.post("/api/buffer")
async def buffer_message(msg: BufferMessage):
    if not msg.msg_id.strip() or not msg.raw_content.strip():
        raise HTTPException(400, "消息 ID 和正文不能为空")
    if len(msg.raw_content) > 20000:
        raise HTTPException(400, "单条消息不能超过 20000 个字符")
    ts = msg.created_at or time.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """INSERT OR IGNORE INTO raw_buffer
               (msg_id, chat_type, chat_id, chat_name, sender_id, sender_name, raw_content, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg.msg_id, msg.chat_type, msg.chat_id, msg.chat_name,
             msg.sender_id, msg.sender_name, msg.raw_content, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "buffered"}


@app.get("/api/buffer_stats")
async def buffer_stats():
    conn = sqlite3.connect(str(DB_PATH))
    total = conn.execute("SELECT COUNT(*) FROM raw_buffer").fetchone()[0]
    conn.close()
    return {"buffered": total}


BATCH_PROMPT = """你是一个语义话题聚类引擎。以下是一段群聊/私聊的完整对话记录。

你的任务：阅读所有消息，按【讨论的语义话题】进行聚类，识别出独立的讨论线程。

═══ 最高原则：按语义聚类，严禁按发言人聚类 ═══

错误示例：把 User A 的所有消息归为一个话题 — 这是完全错误的！
正确示例：User A 提问 → User B 回答 → User C 反驳 → User A 追问，这四条消息属于同一个话题。
判断依据：「这些消息是否在讨论同一件事」，而不是「这些消息是否来自同一个人」。

═══ 强制规则 ═══

1. 按【语义话题】聚类，绝对不按【发言人】聚类。
   - 同一话题可以包含多个不同用户的发言。
   - 同一用户的不同发言可以分属不同话题。
2. 多人对话必须完整保留：提问、回答、补充、追问、确认、反驳 — 只要围绕同一话题，全部归入同一个 Topic。
3. 每条消息只能归属于一个话题。
4. 如果所有消息都是废话，返回空数组 []。

═══ 高召回率约束 ═══

- 每个话题的 original_messages 必须 100% 包含所有相关原始消息，一条不准遗漏。
- content 字段必须是原始消息的逐字拷贝，严禁篡改、缩写、改写。
- original_messages 必须按时间顺序排列。
- 宁可多收录，不可遗漏。

═══ A 类：重要信息（绝对白名单，极度严格）═══

仅且只有以下内容归为 A：
- 学校/学院/老师/教务处发布的官方通知、公告
- 考试安排、课程调整、放假通知
- 作业/论文/项目的截止日期（DDL）
- 奖学金、评优、保研等官方政策变动
- 紧急安全事项、突发事件通知

🔥 A 类铁律 — 以下内容严禁归为 A：
- 政治讨论、历史争论、社会热点辩论（如阶级斗争、地主、战争讨论）→ 必须归为 B
- 学术争论、课程内容讨论 → 必须归为 B
- 个人观点、意见表达、情绪吐槽 → 必须归为 B
- 非官方来源的任何信息 → 不得归为 A
- 只有「学校官方」发出的「正式通知」才能是 A，学生之间的讨论永远不是 A

═══ B 类：校园轶事 ═══

- 校园趣闻、吐槽、日常分享、情感表达、段子
- 政治/历史/社会话题的讨论和争论
- 学术讨论、课程内容辩论、学习心得
- 课程评价、社团活动
- 任何形式的观点表达和辩论
- 网络梗、玩梗、meme 讨论

═══ C 类：二手资讯（含虚拟服务）═══

实体物品交易（必须是真实的交易意图）：
- 买卖、转让、求购、拼单、代购、闲置交易

虚拟服务交易：
- 代课、代跑、跑腿、代取快递
- 带饭、帮取外卖
- 拼车、拼房、合租
- 技能交换、有偿帮忙

⚠️ 极度重要 — 识别中文修辞手法，防止误判：
- "砸锅卖铁也要买" 是成语/夸张修辞，意思是"无论如何都要"，绝对不是真的卖废铁！严禁判为 C！
- "卖肾买iPhone" 是网络梗，不是真实的器官交易！严禁判为 C！
- "穷得叮当响" 是夸张，不是在卖东西！
- 判断是否为 C 类的唯一标准：是否存在真实的、具体的交易意图（有人要买/卖某个具体的东西或服务）
- 仅字面提及"卖""买""出"等词汇，但上下文是吐槽、玩梗、夸张修辞的，一律不是 C

═══ None 类：垃圾信息（毫不留情，宁缺毋滥）═══

以下内容必须归为 None，不得生成 Topic：
- 纯表情回复、单字回复（嗯、哦、好、6、啊、哈、草、牛）
- 纯 meme/段子转发（无实质信息）
- emoji 堆砌、无意义的表情包
- 阴阳怪气的讽刺、纯玩梗（无实质内容）
- 无意义的吐槽、情绪宣泄（无信息增量）
- 灌水、刷屏、复读机
- "哈哈哈哈哈"、"笑死"、"绝了" 等纯情绪表达
- 任何不含实质性信息交流的废话

原则：宁可漏掉一条有价值的消息，也绝对不把垃圾信息归入 ABC。如果一条消息删掉后不影响任何人的信息获取，它就是 None。

═══ 输出格式 ═══

严格返回 JSON 数组，不要返回其他内容：
[
  {
    "topic_title": "具体话题标题",
    "category": "A/B/C",
    "summary": "该话题的 1-2 句话总结",
    "tags": ["标签1", "标签2"],
    "confidence": 0.0,
    "original_messages": [
      {"time": "消息时间", "sender": "发送人", "content": "原始消息逐字内容"},
      ...
    ]
  },
  ...
]"""


@app.post("/api/batch_process")
async def batch_process():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM raw_buffer ORDER BY chat_id, created_at").fetchall()
    if not rows:
        conn.close()
        return {"status": "empty", "processed": 0}

    # 按 chat_id 分组
    groups: dict[str, list] = {}
    for r in rows:
        cid = r["chat_id"] or "unknown"
        groups.setdefault(cid, []).append(dict(r))

    cfg = load_config()
    llm_cfg = cfg.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    if api_key:
        base_url = _validate_http_url(base_url, "LLM Base URL")
    if api_key and not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    model = llm_cfg.get("model", "gpt-4o-mini")

    _log_login_debug(f"[BATCH] Processing {len(rows)} messages in {len(groups)} groups, LLM={'on' if api_key else 'off'}")

    all_topics = []
    successful_ids: list[int] = []
    failures: list[dict] = []
    for cid, msgs in groups.items():
        chat_type = msgs[0]["chat_type"]
        chat_name = msgs[0]["chat_name"]
        source_context = {"source_type": "qq", "group_id": cid, "group_name": chat_name}
        source_key = calibration.make_source_key("qq", cid, chat_name)
        calibration_settings = calibration.get_settings(DB_PATH)
        active_batch_prompt, active_batch_version = calibration.resolve_prompt(
            DB_PATH, BATCH_PROMPT, scope="batch"
        )

        # 拼上下文文本给 LLM
        lines = []
        for m in msgs:
            prefix = "群聊" if m["chat_type"] == "group" else "私聊"
            lines.append(f"[{prefix}: {m['chat_name']}] {m['created_at']} | {m['sender_name']}: {m['raw_content']}")
        context_text = "\n".join(lines)
        if len(context_text) > 60000:
            failures.append({"chat_id": cid, "chat_name": chat_name, "error": "消息过多，请缩小批次后重试"})
            continue

        if not api_key:
            # 降级：规则引擎逐条分类，按类别聚合话题
            from scraper import classify_message
            buckets: dict[str, list] = {"A": [], "B": [], "C": []}
            bucket_results: dict[str, list] = {"A": [], "B": [], "C": []}
            for m in msgs:
                r = await classify_message(m["raw_content"], cfg, source_context, DB_PATH)
                c = r["category"]
                if c in buckets:
                    buckets[c].append(m)
                    bucket_results[c].append(r)
            for cat, cat_msgs in buckets.items():
                if not cat_msgs:
                    continue
                orig = [{"time": m["created_at"] or "", "sender": m["sender_name"] or "", "content": m["raw_content"] or ""} for m in cat_msgs]
                all_topics.append({
                    "topic_title": f"【批量】{chat_name} — {cat}类话题",
                    "category": cat,
                    "summary": f"共 {len(cat_msgs)} 条相关消息。",
                    "tags": ["规则聚合"],
                    "chat_type": chat_type,
                    "chat_name": chat_name,
                    "original_messages": orig,
                    "confidence": min((float(item.get("confidence") or 0) for item in bucket_results[cat]), default=0),
                    "review_required": any(bool(item.get("review_required")) for item in bucket_results[cat]),
                    "prompt_version": active_batch_version,
                    "calibration_examples": max((int(item.get("calibration_examples", 0)) for item in bucket_results[cat]), default=0),
                    "calibration_similarity": max((float(item.get("calibration_similarity", 0)) for item in bucket_results[cat]), default=0),
                })
            successful_ids.extend(m["id"] for m in msgs)
            continue

        try:
            examples = calibration.retrieve_examples(DB_PATH, context_text, source_key)
            async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5)) as client:
                r = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": calibration.compose_prompt(active_batch_prompt, examples)},
                            {"role": "user", "content": context_text},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 4000,
                    },
                )
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"].strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                topics = json.loads(text)
                if isinstance(topics, dict):
                    topics = [topics]
                if not isinstance(topics, list):
                    raise ValueError("模型返回格式不是话题数组")
                normalized_topics = []
                for t in topics[:100]:
                    if not isinstance(t, dict):
                        raise ValueError("模型返回了无效话题")
                    if t.get("category") not in ("A", "B", "C", "None"):
                        raise ValueError("模型返回了未知分类")
                    summary = t.get("summary", "")
                    t["summary"] = summary[:1000] if isinstance(summary, str) else ""
                    title = t.get("topic_title", "话题")
                    t["topic_title"] = title[:200] if isinstance(title, str) else "话题"
                    tags = t.get("tags", [])
                    t["tags"] = [str(tag)[:40] for tag in tags[:8]] if isinstance(tags, list) else []
                    try:
                        confidence = max(0.0, min(1.0, float(t.get("confidence", 0.65))))
                    except (TypeError, ValueError):
                        confidence = 0.65
                    threshold = calibration.get_source_threshold(
                        DB_PATH, source_key, calibration_settings["default_threshold"]
                    )
                    t["confidence"] = confidence
                    t["review_required"] = confidence < threshold
                    t["prompt_version"] = active_batch_version
                    t["calibration_examples"] = len(examples)
                    t["calibration_similarity"] = examples[0]["score"] if examples else 0.0
                    originals = t.get("original_messages", [])
                    if not isinstance(originals, list):
                        originals = []
                    t["original_messages"] = [
                        {
                            "time": str(item.get("time", ""))[:40],
                            "sender": str(item.get("sender", ""))[:200],
                            "content": str(item.get("content", ""))[:20000],
                        }
                        for item in originals[:500]
                        if isinstance(item, dict)
                    ]
                    t["chat_type"] = chat_type
                    t["chat_name"] = chat_name
                    normalized_topics.append(t)
                all_topics.extend(normalized_topics)
                successful_ids.extend(m["id"] for m in msgs)
        except Exception as e:
            print(f"  [BATCH LLM ERROR] {type(e).__name__}: {e}", flush=True)
            failures.append({"chat_id": cid, "chat_name": chat_name, "error": str(e)[:160]})

    # 写入 messages 表 — 每个语义话题独立一张卡片
    inserted = 0
    queued_for_mothership = False
    for topic in all_topics:
        cat = topic.get("category", "None")
        if cat == "None":
            continue
        summary = topic.get("summary", "")
        tags = topic.get("tags", [])
        topic_title = topic.get("topic_title", "话题")
        chat_type = topic.get("chat_type", "group")
        chat_name = topic.get("chat_name", "")
        orig_msgs = topic.get("original_messages", [])

        fake_msg_id = "batch_" + hashlib.sha256(
            json.dumps(topic, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        # raw_content 存 JSON：标题 + 该话题专属的原始消息
        raw_payload = json.dumps({
            "title": topic_title,
            "messages": orig_msgs,
        }, ensure_ascii=False)

        cursor = conn.execute(
            """INSERT OR IGNORE INTO messages
               (msg_id, chat_type, group_id, group_name, sender_id, sender_name,
                 raw_content, category, summary, tags, source_type, source_name,
                classification_method, confidence, predicted_category,
                review_required, prompt_version, calibration_examples,
                calibration_similarity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fake_msg_id, chat_type, "", chat_name, "", "",
             f"[批量] {raw_payload}",
             cat, summary, json.dumps(tags, ensure_ascii=False), "qq", "QQ",
             "batch_llm" if api_key else "batch_rule",
             topic.get("confidence"), cat, 1 if topic.get("review_required") else 0,
             topic.get("prompt_version", ""), int(topic.get("calibration_examples", 0)),
             float(topic.get("calibration_similarity", 0))),
        )
        if cursor.rowcount == 0:
            continue
        inserted += 1
        if not topic.get("review_required"):
            queued_for_mothership = _enqueue_mothership(conn, {
                "msg_id": fake_msg_id,
                "raw_content": f"[批量] {raw_payload}",
                "category": cat,
                "summary": summary,
                "tags": tags,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source_type": "qq",
                "source_name": "QQ",
                "confidence": topic.get("confidence"),
            }, cfg) or queued_for_mothership
        _broadcast({
            "type": "new_message",
            "data": {
                "msg_id": fake_msg_id,
                "chat_type": chat_type,
                "group_id": "",
                "group_name": chat_name,
                "sender_id": "",
                "sender_name": "",
                "raw_content": f"[批量] {topic_title}",
                "category": cat,
                "summary": summary,
                "tags": tags,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "confidence": topic.get("confidence"),
                "classification_method": "batch_llm" if api_key else "batch_rule",
                "predicted_category": cat,
                "review_required": bool(topic.get("review_required")),
                "prompt_version": topic.get("prompt_version", ""),
                "calibration_examples": int(topic.get("calibration_examples", 0)),
                "calibration_similarity": float(topic.get("calibration_similarity", 0)),
            },
        })

    # 只删除成功处理的原始消息；失败批次必须留在缓冲区以便重试。
    if successful_ids:
        placeholders = ",".join("?" * len(successful_ids))
        conn.execute(f"DELETE FROM raw_buffer WHERE id IN ({placeholders})", successful_ids)
    conn.commit()
    conn.close()
    if queued_for_mothership:
        _schedule_mothership_flush()
    return {
        "status": "partial" if failures else "ok",
        "processed": len(successful_ids),
        "topics": inserted,
        "failed_groups": failures,
        "remaining": len(rows) - len(successful_ids),
    }


# ── 前端静态文件托管（必须在所有 API 路由之后）────────────

DIST_DIR = APP_DIR.parent / "frontend" / "dist"
if not DIST_DIR.exists():
    DIST_DIR = APP_DIR / "dist"

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA 兜底：非 API 路由全部返回 index.html。"""
        file_path = DIST_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(DIST_DIR / "index.html"))


# ── 工具 ──────────────────────────────────────────────────

def _mock_qr_svg() -> str:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="260" height="260" viewBox="0 0 260 260">'
    svg += '<rect width="260" height="260" fill="#111"/>'
    svg += '<text x="130" y="125" text-anchor="middle" font-family="monospace" font-size="13" fill="#555">NapCat</text>'
    svg += '<text x="130" y="145" text-anchor="middle" font-family="monospace" font-size="13" fill="#555">未检测到</text>'
    svg += '</svg>'
    return base64.b64encode(svg.encode()).decode()


# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import logging
    from logging.handlers import RotatingFileHandler
    # 写错误日志到文件，自动轮转（单文件 5MB，保留 3 个备份）
    log_file = BASE_DIR / "api.log"
    file_handler = RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            file_handler,
        ],
    )
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
