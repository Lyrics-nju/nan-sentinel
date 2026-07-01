"""Nan Sentinel 情报母舰。

只接收哨站生成的结构化情报。默认数据模型不包含发送者身份和完整聊天原文。
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import io
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlencode

import qrcode
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator


APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MOTHERSHIP_DB_PATH", APP_DIR / "mothership.db"))
RETENTION_DAYS = max(7, min(3650, int(os.environ.get("MOTHERSHIP_RETENTION_DAYS", "90"))))
MAX_BODY_BYTES = 1_000_000


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db() -> None:
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            token_hash      TEXT NOT NULL UNIQUE,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen       TIMESTAMP,
            last_error      TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS intelligence (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id             TEXT NOT NULL,
            external_id         TEXT NOT NULL,
            predicted_category  TEXT NOT NULL,
            corrected_category  TEXT,
            summary             TEXT NOT NULL,
            tags                TEXT DEFAULT '[]',
            confidence          REAL,
            source_type         TEXT DEFAULT '',
            source_name         TEXT DEFAULT '',
            source_ref          TEXT DEFAULT '',
            evidence_excerpt    TEXT DEFAULT '',
            occurred_at         TEXT DEFAULT '',
            ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            disposition         TEXT NOT NULL DEFAULT 'new',
            review_status       TEXT NOT NULL DEFAULT 'unreviewed',
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE,
            UNIQUE(node_id, external_id)
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            intelligence_id     INTEGER NOT NULL,
            verdict             TEXT NOT NULL,
            corrected_category  TEXT,
            note                TEXT DEFAULT '',
            reviewer            TEXT DEFAULT 'admin',
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(intelligence_id) REFERENCES intelligence(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            actor        TEXT NOT NULL,
            action       TEXT NOT NULL,
            target_type  TEXT NOT NULL,
            target_id    TEXT NOT NULL,
            detail       TEXT DEFAULT '{}',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS spaces (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            description  TEXT DEFAULT '',
            owner_label  TEXT DEFAULT '',
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS invitations (
            id          TEXT PRIMARY KEY,
            space_id    TEXT NOT NULL,
            token_hash  TEXT NOT NULL UNIQUE,
            token_hint  TEXT NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            max_uses    INTEGER NOT NULL DEFAULT 0,
            uses        INTEGER NOT NULL DEFAULT 0,
            expires_at  TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(space_id) REFERENCES spaces(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS evidence_requests (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            intelligence_id   INTEGER NOT NULL,
            node_id           TEXT NOT NULL,
            reason            TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending',
            evidence_content  TEXT DEFAULT '',
            requested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            responded_at      TIMESTAMP,
            expires_at        TEXT NOT NULL,
            FOREIGN KEY(intelligence_id) REFERENCES intelligence(id) ON DELETE CASCADE,
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS calibration_reports (
            node_id              TEXT PRIMARY KEY,
            reviewed_count       INTEGER NOT NULL DEFAULT 0,
            correct_count        INTEGER NOT NULL DEFAULT 0,
            accuracy             REAL,
            confusion            TEXT NOT NULL DEFAULT '[]',
            prompt_version       TEXT NOT NULL DEFAULT '',
            updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS calibration_examples (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id              TEXT NOT NULL,
            external_id          TEXT NOT NULL,
            predicted_category   TEXT NOT NULL,
            corrected_category   TEXT NOT NULL,
            confidence           REAL,
            source_type          TEXT NOT NULL DEFAULT '',
            source_ref           TEXT NOT NULL DEFAULT '',
            content_excerpt      TEXT NOT NULL DEFAULT '',
            prompt_version       TEXT NOT NULL DEFAULT '',
            consented_at         TEXT NOT NULL,
            ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE,
            UNIQUE(node_id, external_id)
        );

        CREATE INDEX IF NOT EXISTS idx_intelligence_time ON intelligence(ingested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_intelligence_category ON intelligence(predicted_category);
        CREATE INDEX IF NOT EXISTS idx_intelligence_disposition ON intelligence(disposition);
        CREATE INDEX IF NOT EXISTS idx_intelligence_node ON intelligence(node_id);
        CREATE INDEX IF NOT EXISTS idx_invitation_space ON invitations(space_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_node ON evidence_requests(node_id, status);
        CREATE INDEX IF NOT EXISTS idx_calibration_examples_node ON calibration_examples(node_id, ingested_at DESC);
        """
    )
    node_columns = {row[1] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
    node_migrations = {
        "space_id": "TEXT",
        "grant_status": "TEXT NOT NULL DEFAULT 'active'",
        "granted_categories": "TEXT NOT NULL DEFAULT '[\"A\",\"B\",\"C\"]'",
        "granted_sources": "TEXT NOT NULL DEFAULT '[]'",
        "share_evidence": "INTEGER NOT NULL DEFAULT 0",
        "share_calibration_stats": "INTEGER NOT NULL DEFAULT 0",
        "expires_at": "TEXT DEFAULT ''",
    }
    for name, definition in node_migrations.items():
        if name not in node_columns:
            conn.execute(f"ALTER TABLE nodes ADD COLUMN {name} {definition}")
    conn.commit()
    conn.close()


def _cleanup_retention(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "DELETE FROM intelligence WHERE ingested_at < datetime('now', ?)",
        (f"-{RETENTION_DAYS} days",),
    )
    conn.execute(
        """UPDATE evidence_requests SET evidence_content='', status='expired'
           WHERE expires_at!='' AND expires_at <= ? AND status IN ('pending','approved')""",
        (_utcnow().isoformat(timespec="seconds"),),
    )
    conn.execute(
        "DELETE FROM calibration_examples WHERE ingested_at < datetime('now', ?)",
        (f"-{RETENTION_DAYS} days",),
    )
    return max(0, cursor.rowcount)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _expires_in(days: int) -> str:
    return (_utcnow() + timedelta(days=days)).isoformat(timespec="seconds")


def _is_expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) <= _utcnow()
    except ValueError:
        return True


def _qr_data_url(value: str) -> str:
    image = qrcode.make(value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _audit(conn: sqlite3.Connection, actor: str, action: str, target_type: str, target_id: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, target_type, target_id, detail) VALUES (?, ?, ?, ?, ?)",
        (actor, action, target_type, target_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def _require_admin(x_admin_token: str = Header("")) -> str:
    expected = os.environ.get("MOTHERSHIP_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(503, "母舰尚未配置管理员令牌")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "管理员令牌无效")
    return "admin"


def _lookup_node(authorization: str, require_active: bool = True) -> sqlite3.Row:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少哨站令牌")
    token = authorization.removeprefix("Bearer ").strip()
    token_hash = _token_hash(token)
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM nodes WHERE token_hash=?",
        (token_hash,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(403, "成员密钥无效")
    if require_active:
        if not row["enabled"] or row["grant_status"] != "active":
            raise HTTPException(403, "该哨站已暂停、撤销或停用")
        if _is_expired(row["expires_at"]):
            raise HTTPException(403, "共享授权已到期")
    return row


def _require_node(authorization: str = Header("")) -> sqlite3.Row:
    return _lookup_node(authorization, require_active=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_db()
    conn = _connect()
    _cleanup_retention(conn)
    conn.commit()
    conn.close()
    yield


app = FastAPI(title="Nan Sentinel 情报母舰", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def harden_http(request: Request, call_next):
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "请求体过大"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Content-Length 无效"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class NodeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class NodeUpdate(BaseModel):
    enabled: bool


class SpaceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: str = Field("", max_length=300)
    owner_label: str = Field("", max_length=80)
    invite_days: int = Field(30, ge=1, le=365)


class InvitationRotate(BaseModel):
    expires_days: int = Field(30, ge=1, le=365)
    max_uses: int = Field(0, ge=0, le=10000)


class InvitationInspect(BaseModel):
    invite_key: str = Field(..., min_length=12, max_length=256)


class InvitationJoin(BaseModel):
    invite_key: str = Field(..., min_length=12, max_length=256)
    device_name: str = Field(..., min_length=1, max_length=60)
    categories: list[Literal["A", "B", "C"]] = Field(..., min_length=1, max_length=3)
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    share_evidence: bool = False
    share_calibration_stats: bool = False
    expires_days: int = Field(30, ge=1, le=365)

    @model_validator(mode="after")
    def normalize_grant(self):
        self.categories = list(dict.fromkeys(self.categories))
        self.source_refs = list(dict.fromkeys(ref.strip() for ref in self.source_refs if ref.strip()))
        if any(len(ref) > 128 for ref in self.source_refs):
            raise ValueError("来源标识过长")
        return self


class MembershipGrant(BaseModel):
    categories: list[Literal["A", "B", "C"]] = Field(..., min_length=1, max_length=3)
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    share_evidence: bool = False
    share_calibration_stats: bool = False

    @model_validator(mode="after")
    def normalize_grant(self):
        self.categories = list(dict.fromkeys(self.categories))
        self.source_refs = list(dict.fromkeys(ref.strip() for ref in self.source_refs if ref.strip()))
        if any(len(ref) > 128 for ref in self.source_refs):
            raise ValueError("来源标识过长")
        return self


class MembershipState(BaseModel):
    state: Literal["active", "paused"]


class MembershipRevoke(BaseModel):
    delete_data: bool = False


class EvidenceRequestCreate(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


class EvidenceResponse(BaseModel):
    decision: Literal["approved", "denied"]
    content: str = Field("", max_length=5000)

    @model_validator(mode="after")
    def require_content(self):
        if self.decision == "approved" and not self.content.strip():
            raise ValueError("批准原文申请时必须提供原文")
        return self


class IntelligenceItem(BaseModel):
    external_id: str = Field(..., min_length=12, max_length=128)
    category: Literal["A", "B", "C"]
    summary: str = Field(..., min_length=1, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    confidence: Optional[float] = Field(None, ge=0, le=1)
    source_type: str = Field("", max_length=40)
    source_name: str = Field("", max_length=120)
    source_ref: str = Field("", max_length=128)
    evidence_excerpt: str = Field("", max_length=300)
    occurred_at: str = Field("", max_length=40)


class IngestPayload(BaseModel):
    items: list[IntelligenceItem] = Field(..., min_length=1, max_length=200)


class CalibrationReportPayload(BaseModel):
    reviewed_count: int = Field(..., ge=0, le=10_000_000)
    correct_count: int = Field(..., ge=0, le=10_000_000)
    confusion: list[dict] = Field(default_factory=list, max_length=32)
    prompt_version: str = Field("", max_length=160)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.correct_count > self.reviewed_count:
            raise ValueError("正确数量不能超过复核数量")
        return self


class CalibrationExampleItem(BaseModel):
    external_id: str = Field(..., min_length=12, max_length=128)
    predicted_category: Literal["A", "B", "C", "None"]
    corrected_category: Literal["A", "B", "C", "None"]
    confidence: Optional[float] = Field(None, ge=0, le=1)
    source_type: str = Field("", max_length=40)
    source_ref: str = Field("", max_length=128)
    content_excerpt: str = Field("", max_length=300)
    prompt_version: str = Field("", max_length=160)


class CalibrationExamplePayload(BaseModel):
    consent_confirmed: Literal[True]
    item: CalibrationExampleItem


class ReviewPayload(BaseModel):
    verdict: Literal["confirmed", "false_positive", "corrected"]
    corrected_category: Optional[Literal["A", "B", "C"]] = None
    note: str = Field("", max_length=500)

    @model_validator(mode="after")
    def validate_correction(self):
        if self.verdict == "corrected" and not self.corrected_category:
            raise ValueError("纠正分类时必须提供 corrected_category")
        return self


class DispositionPayload(BaseModel):
    disposition: Literal["new", "acknowledged", "assigned", "resolved"]


@app.get("/api/health")
async def health():
    configured = bool(os.environ.get("MOTHERSHIP_ADMIN_TOKEN", ""))
    return {
        "status": "ok",
        "configured": configured,
        "privacy_mode": "structured_intelligence_only",
        "retention_days": RETENTION_DAYS,
        "ts": int(time.time()),
    }


def _invitation_row(conn: sqlite3.Connection, invite_key: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT i.*, s.name AS space_name, s.description, s.owner_label, s.enabled AS space_enabled
           FROM invitations i JOIN spaces s ON s.id=i.space_id WHERE i.token_hash=?""",
        (_token_hash(invite_key.strip()),),
    ).fetchone()
    if not row or not row["enabled"] or not row["space_enabled"]:
        raise HTTPException(404, "邀请密钥无效或已停用")
    if _is_expired(row["expires_at"]):
        raise HTTPException(410, "邀请密钥已过期")
    if row["max_uses"] and row["uses"] >= row["max_uses"]:
        raise HTTPException(410, "邀请密钥使用次数已达上限")
    return row


def _new_invitation(conn: sqlite3.Connection, space_id: str, days: int, max_uses: int = 0) -> dict:
    invite_key = "nsi_" + secrets.token_urlsafe(30)
    invitation_id = str(uuid.uuid4())
    expires_at = _expires_in(days)
    conn.execute(
        """INSERT INTO invitations
           (id, space_id, token_hash, token_hint, max_uses, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (invitation_id, space_id, _token_hash(invite_key), invite_key[-6:], max_uses, expires_at),
    )
    return {"id": invitation_id, "invite_key": invite_key, "expires_at": expires_at, "max_uses": max_uses}


def _invitation_delivery(invitation: dict, request: Request) -> dict:
    mother_url = str(request.base_url).rstrip("/")
    join_base = os.environ.get("MOTHERSHIP_JOIN_BASE_URL", "http://127.0.0.1:8000/settings").strip()
    join_url = f"{join_base}?{urlencode({'tab': 'mothership', 'mothership': mother_url, 'invite': invitation['invite_key']})}"
    return {**invitation, "mothership_url": mother_url, "join_url": join_url, "qr_data_url": _qr_data_url(join_url), "token_shown_once": True}


@app.post("/api/admin/spaces")
async def create_space(payload: SpaceCreate, request: Request, x_admin_token: str = Header("")):
    actor = _require_admin(x_admin_token)
    space_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO spaces (id, name, description, owner_label) VALUES (?, ?, ?, ?)",
            (space_id, payload.name.strip(), payload.description.strip(), payload.owner_label.strip()),
        )
        invitation = _new_invitation(conn, space_id, payload.invite_days)
        _audit(conn, actor, "space.created", "space", space_id, {"name": payload.name.strip()})
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, "协作空间名称已存在")
    conn.close()
    return {
        "space": {"id": space_id, "name": payload.name.strip(), "description": payload.description.strip(), "owner_label": payload.owner_label.strip()},
        "invitation": _invitation_delivery(invitation, request),
    }


@app.get("/api/admin/spaces")
async def list_spaces(x_admin_token: str = Header("")):
    _require_admin(x_admin_token)
    conn = _connect()
    rows = conn.execute(
        """SELECT s.*,
                  COUNT(DISTINCT n.id) AS member_count,
                  SUM(CASE WHEN n.grant_status='active' AND n.enabled=1 THEN 1 ELSE 0 END) AS active_members,
                  COUNT(DISTINCT i.id) AS intelligence_count
           FROM spaces s
           LEFT JOIN nodes n ON n.space_id=s.id
           LEFT JOIN intelligence i ON i.node_id=n.id
           GROUP BY s.id ORDER BY s.created_at DESC"""
    ).fetchall()
    conn.close()
    return {"spaces": [dict(row) for row in rows]}


@app.post("/api/admin/spaces/{space_id}/invitations")
async def rotate_invitation(space_id: str, payload: InvitationRotate, request: Request, x_admin_token: str = Header("")):
    actor = _require_admin(x_admin_token)
    conn = _connect()
    space = conn.execute("SELECT id FROM spaces WHERE id=? AND enabled=1", (space_id,)).fetchone()
    if not space:
        conn.close()
        raise HTTPException(404, "协作空间不存在")
    conn.execute("UPDATE invitations SET enabled=0 WHERE space_id=?", (space_id,))
    invitation = _new_invitation(conn, space_id, payload.expires_days, payload.max_uses)
    _audit(conn, actor, "invitation.rotated", "space", space_id, {"expires_at": invitation["expires_at"]})
    conn.commit()
    conn.close()
    return {"invitation": _invitation_delivery(invitation, request)}


@app.post("/api/v1/invitations/inspect")
async def inspect_invitation(payload: InvitationInspect):
    conn = _connect()
    row = _invitation_row(conn, payload.invite_key)
    result = {
        "space_id": row["space_id"],
        "space_name": row["space_name"],
        "description": row["description"],
        "owner_label": row["owner_label"],
        "invite_expires_at": row["expires_at"],
        "privacy_notice": "你选择的来源和分类之外不会同步；完整原文必须再次批准。",
    }
    conn.close()
    return result


@app.post("/api/v1/invitations/join")
async def join_space(payload: InvitationJoin):
    conn = _connect()
    invitation = _invitation_row(conn, payload.invite_key)
    node_id = str(uuid.uuid4())
    member_token = "nsm_" + secrets.token_urlsafe(32)
    expires_at = _expires_in(payload.expires_days)
    try:
        conn.execute(
            """INSERT INTO nodes
               (id, name, token_hash, space_id, grant_status, granted_categories,
                granted_sources, share_evidence, share_calibration_stats, expires_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
            (
                node_id, payload.device_name.strip(), _token_hash(member_token), invitation["space_id"],
                json.dumps(payload.categories), json.dumps(payload.source_refs),
                int(payload.share_evidence), int(payload.share_calibration_stats), expires_at,
            ),
        )
        conn.execute("UPDATE invitations SET uses=uses+1 WHERE id=?", (invitation["id"],))
        _audit(
            conn, f"node:{node_id}", "membership.joined", "space", invitation["space_id"],
            {"categories": payload.categories, "source_count": len(payload.source_refs), "share_evidence": payload.share_evidence, "share_calibration_stats": payload.share_calibration_stats, "expires_at": expires_at},
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, "哨站名称已存在，请换一个名称")
    conn.close()
    return {
        "status": "joined",
        "space": {"id": invitation["space_id"], "name": invitation["space_name"], "owner_label": invitation["owner_label"]},
        "node_id": node_id,
        "member_token": member_token,
        "token_shown_once": True,
        "grant": {"categories": payload.categories, "source_refs": payload.source_refs, "share_evidence": payload.share_evidence, "share_calibration_stats": payload.share_calibration_stats, "expires_at": expires_at},
    }


@app.post("/api/v1/ingest")
async def ingest(payload: IngestPayload, authorization: str = Header("")):
    authenticated = _require_node(authorization)
    conn = _connect()
    accepted = 0
    duplicates = 0
    rejected_scope = 0
    allowed_categories = set(json.loads(authenticated["granted_categories"] or "[]"))
    allowed_sources = set(json.loads(authenticated["granted_sources"] or "[]"))
    for item in payload.items:
        source_denied = (
            item.source_ref not in allowed_sources and "*" not in allowed_sources
            if authenticated["space_id"] else bool(allowed_sources and item.source_ref not in allowed_sources)
        )
        if item.category not in allowed_categories or source_denied:
            rejected_scope += 1
            continue
        evidence = item.evidence_excerpt if authenticated["share_evidence"] else ""
        cursor = conn.execute(
            """INSERT OR IGNORE INTO intelligence
               (node_id, external_id, predicted_category, summary, tags, confidence,
                source_type, source_name, source_ref, evidence_excerpt, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                authenticated["id"], item.external_id, item.category, item.summary,
                json.dumps([tag[:40] for tag in item.tags], ensure_ascii=False),
                item.confidence, item.source_type, item.source_name, item.source_ref,
                evidence, item.occurred_at,
            ),
        )
        if cursor.rowcount:
            accepted += 1
        else:
            duplicates += 1
    conn.execute("UPDATE nodes SET last_seen=CURRENT_TIMESTAMP, last_error='' WHERE id=?", (authenticated["id"],))
    _cleanup_retention(conn)
    conn.commit()
    conn.close()
    return {"status": "ok", "accepted": accepted, "duplicates": duplicates, "rejected_scope": rejected_scope}


@app.put("/api/v1/calibration/report")
async def ingest_calibration_report(payload: CalibrationReportPayload, authorization: str = Header("")):
    authenticated = _require_node(authorization)
    if not authenticated["share_calibration_stats"]:
        raise HTTPException(403, "学生未授权共享匿名校准统计")
    accuracy = round(payload.correct_count / payload.reviewed_count * 100, 1) if payload.reviewed_count else None
    conn = _connect()
    conn.execute(
        """INSERT INTO calibration_reports
           (node_id, reviewed_count, correct_count, accuracy, confusion, prompt_version, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(node_id) DO UPDATE SET
             reviewed_count=excluded.reviewed_count,
             correct_count=excluded.correct_count,
             accuracy=excluded.accuracy,
             confusion=excluded.confusion,
             prompt_version=excluded.prompt_version,
             updated_at=CURRENT_TIMESTAMP""",
        (
            authenticated["id"], payload.reviewed_count, payload.correct_count, accuracy,
            json.dumps(payload.confusion, ensure_ascii=False), payload.prompt_version,
        ),
    )
    _audit(
        conn, f"node:{authenticated['id']}", "calibration.report_synced", "node",
        authenticated["id"], {"reviewed_count": payload.reviewed_count, "accuracy": accuracy},
    )
    conn.commit()
    conn.close()
    return {"status": "saved", "accuracy": accuracy}


@app.put("/api/v1/calibration/examples")
async def ingest_calibration_example(payload: CalibrationExamplePayload, authorization: str = Header("")):
    authenticated = _require_node(authorization)
    item = payload.item
    conn = _connect()
    conn.execute(
        """INSERT INTO calibration_examples
           (node_id, external_id, predicted_category, corrected_category, confidence,
            source_type, source_ref, content_excerpt, prompt_version, consented_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(node_id, external_id) DO UPDATE SET
             predicted_category=excluded.predicted_category,
             corrected_category=excluded.corrected_category,
             confidence=excluded.confidence,
             source_type=excluded.source_type,
             source_ref=excluded.source_ref,
             content_excerpt=excluded.content_excerpt,
             prompt_version=excluded.prompt_version,
             consented_at=excluded.consented_at,
             ingested_at=CURRENT_TIMESTAMP""",
        (
            authenticated["id"], item.external_id, item.predicted_category,
            item.corrected_category, item.confidence, item.source_type, item.source_ref,
            item.content_excerpt, item.prompt_version, _utcnow().isoformat(timespec="seconds"),
        ),
    )
    _audit(
        conn, f"node:{authenticated['id']}", "calibration.example_consented",
        "calibration_example", item.external_id,
        {"predicted": item.predicted_category, "corrected": item.corrected_category},
    )
    conn.commit()
    conn.close()
    return {"status": "saved", "external_id": item.external_id}


@app.delete("/api/v1/calibration/examples/{external_id}")
async def delete_calibration_example(external_id: str, authorization: str = Header("")):
    authenticated = _require_node(authorization)
    conn = _connect()
    cursor = conn.execute(
        "DELETE FROM calibration_examples WHERE node_id=? AND external_id=?",
        (authenticated["id"], external_id),
    )
    _audit(
        conn, f"node:{authenticated['id']}", "calibration.example_deleted",
        "calibration_example", external_id, {},
    )
    conn.commit()
    conn.close()
    return {"status": "deleted", "deleted": max(0, cursor.rowcount)}


def _membership_payload(row: sqlite3.Row) -> dict:
    return {
        "node_id": row["id"],
        "device_name": row["name"],
        "space_id": row["space_id"],
        "space_name": row["space_name"] if "space_name" in row.keys() else "",
        "owner_label": row["owner_label"] if "owner_label" in row.keys() else "",
        "status": "expired" if _is_expired(row["expires_at"]) else row["grant_status"],
        "enabled": bool(row["enabled"]),
        "categories": json.loads(row["granted_categories"] or "[]"),
        "source_refs": json.loads(row["granted_sources"] or "[]"),
        "share_evidence": bool(row["share_evidence"]),
        "share_calibration_stats": bool(row["share_calibration_stats"]),
        "expires_at": row["expires_at"],
    }


def _membership_with_space(authorization: str) -> tuple[sqlite3.Row, sqlite3.Connection]:
    authenticated = _lookup_node(authorization, require_active=False)
    conn = _connect()
    row = conn.execute(
        """SELECT n.*, COALESCE(s.name, '') AS space_name, COALESCE(s.owner_label, '') AS owner_label
           FROM nodes n LEFT JOIN spaces s ON s.id=n.space_id WHERE n.id=?""",
        (authenticated["id"],),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "成员不存在")
    return row, conn


@app.get("/api/v1/membership")
async def membership(authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    result = _membership_payload(row)
    conn.close()
    return result


@app.patch("/api/v1/membership/grant")
async def update_membership_grant(payload: MembershipGrant, authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    if row["grant_status"] == "revoked":
        conn.close()
        raise HTTPException(403, "授权已经撤销")
    conn.execute(
        "UPDATE nodes SET granted_categories=?, granted_sources=?, share_evidence=?, share_calibration_stats=? WHERE id=?",
        (json.dumps(payload.categories), json.dumps(payload.source_refs), int(payload.share_evidence), int(payload.share_calibration_stats), row["id"]),
    )
    if not payload.share_calibration_stats:
        conn.execute("DELETE FROM calibration_reports WHERE node_id=?", (row["id"],))
    _audit(conn, f"node:{row['id']}", "membership.grant_updated", "node", row["id"], payload.model_dump())
    conn.commit()
    updated = conn.execute(
        """SELECT n.*, COALESCE(s.name, '') AS space_name, COALESCE(s.owner_label, '') AS owner_label
           FROM nodes n LEFT JOIN spaces s ON s.id=n.space_id WHERE n.id=?""",
        (row["id"],),
    ).fetchone()
    result = _membership_payload(updated)
    conn.close()
    return result


@app.post("/api/v1/membership/state")
async def set_membership_state(payload: MembershipState, authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    if row["grant_status"] == "revoked":
        conn.close()
        raise HTTPException(403, "授权已经撤销")
    if payload.state == "active" and not row["enabled"]:
        conn.close()
        raise HTTPException(403, "该哨站已被空间管理员停用")
    if payload.state == "active" and _is_expired(row["expires_at"]):
        conn.close()
        raise HTTPException(409, "授权已到期，无法恢复")
    conn.execute("UPDATE nodes SET grant_status=? WHERE id=?", (payload.state, row["id"]))
    _audit(conn, f"node:{row['id']}", f"membership.{payload.state}", "node", row["id"])
    conn.commit()
    conn.close()
    return {"status": payload.state}


@app.delete("/api/v1/membership/data")
async def delete_membership_data(authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    cursor = conn.execute("DELETE FROM intelligence WHERE node_id=?", (row["id"],))
    deleted = max(0, cursor.rowcount)
    conn.execute("DELETE FROM calibration_reports WHERE node_id=?", (row["id"],))
    example_cursor = conn.execute("DELETE FROM calibration_examples WHERE node_id=?", (row["id"],))
    calibration_deleted = max(0, example_cursor.rowcount)
    _audit(
        conn, f"node:{row['id']}", "membership.data_deleted", "node", row["id"],
        {"count": deleted, "calibration_examples": calibration_deleted},
    )
    conn.commit()
    conn.close()
    return {"status": "deleted", "deleted": deleted, "calibration_examples_deleted": calibration_deleted}


@app.post("/api/v1/membership/revoke")
async def revoke_membership(payload: MembershipRevoke, authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    deleted = 0
    if payload.delete_data:
        cursor = conn.execute("DELETE FROM intelligence WHERE node_id=?", (row["id"],))
        deleted = max(0, cursor.rowcount)
        conn.execute("DELETE FROM calibration_reports WHERE node_id=?", (row["id"],))
        conn.execute("DELETE FROM calibration_examples WHERE node_id=?", (row["id"],))
    conn.execute("UPDATE nodes SET grant_status='revoked', enabled=0 WHERE id=?", (row["id"],))
    _audit(conn, f"node:{row['id']}", "membership.revoked", "node", row["id"], {"deleted": deleted})
    conn.commit()
    conn.close()
    return {"status": "revoked", "deleted": deleted}


@app.get("/api/v1/evidence-requests")
async def node_evidence_requests(authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    requests = conn.execute(
        """SELECT e.id, e.reason, e.status, e.requested_at, e.responded_at, e.expires_at,
                  i.external_id, i.summary, i.source_name, i.occurred_at
           FROM evidence_requests e JOIN intelligence i ON i.id=e.intelligence_id
           WHERE e.node_id=? ORDER BY e.id DESC""",
        (row["id"],),
    ).fetchall()
    conn.close()
    return {"items": [dict(item) for item in requests]}


@app.post("/api/v1/evidence-requests/{request_id}/respond")
async def respond_evidence_request(request_id: int, payload: EvidenceResponse, authorization: str = Header("")):
    row, conn = _membership_with_space(authorization)
    evidence = conn.execute(
        "SELECT * FROM evidence_requests WHERE id=? AND node_id=?",
        (request_id, row["id"]),
    ).fetchone()
    if not evidence:
        conn.close()
        raise HTTPException(404, "原文申请不存在")
    if evidence["status"] != "pending" or _is_expired(evidence["expires_at"]):
        conn.close()
        raise HTTPException(409, "原文申请已处理或已过期")
    content = payload.content.strip() if payload.decision == "approved" else ""
    conn.execute(
        """UPDATE evidence_requests SET status=?, evidence_content=?, responded_at=CURRENT_TIMESTAMP, expires_at=?
           WHERE id=?""",
        (payload.decision, content, _expires_in(7), request_id),
    )
    _audit(conn, f"node:{row['id']}", f"evidence.{payload.decision}", "evidence_request", str(request_id))
    conn.commit()
    conn.close()
    return {"status": payload.decision}


@app.post("/api/admin/nodes")
async def create_node(payload: NodeCreate, x_admin_token: str = Header("")):
    actor = _require_admin(x_admin_token)
    node_id = str(uuid.uuid4())
    token = "nsn_" + secrets.token_urlsafe(32)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO nodes (id, name, token_hash) VALUES (?, ?, ?)",
            (node_id, payload.name.strip(), _token_hash(token)),
        )
        _audit(conn, actor, "node.created", "node", node_id, {"name": payload.name.strip()})
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, "哨站名称已存在")
    conn.close()
    return {"id": node_id, "name": payload.name.strip(), "node_token": token, "token_shown_once": True}


@app.patch("/api/admin/nodes/{node_id}")
async def update_node(node_id: str, payload: NodeUpdate, x_admin_token: str = Header("")):
    actor = _require_admin(x_admin_token)
    conn = _connect()
    cursor = conn.execute("UPDATE nodes SET enabled=? WHERE id=?", (int(payload.enabled), node_id))
    if not cursor.rowcount:
        conn.close()
        raise HTTPException(404, "哨站不存在")
    _audit(conn, actor, "node.enabled" if payload.enabled else "node.disabled", "node", node_id)
    conn.commit()
    conn.close()
    return {"status": "ok", "enabled": payload.enabled}


@app.get("/api/admin/nodes")
async def list_nodes(x_admin_token: str = Header("")):
    _require_admin(x_admin_token)
    conn = _connect()
    rows = conn.execute(
        """SELECT n.id, n.name, n.enabled, n.created_at, n.last_seen, n.last_error,
                  n.space_id, n.grant_status, n.granted_categories, n.granted_sources,
                  n.share_evidence, n.expires_at, COALESCE(s.name, '') AS space_name,
                  CASE WHEN n.enabled=1 AND n.grant_status='active'
                         AND (n.expires_at='' OR n.expires_at>strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))
                         AND n.last_seen >= datetime('now','-5 minutes') THEN 1 ELSE 0 END AS active,
                  COUNT(i.id) AS intelligence_count,
                  SUM(CASE WHEN i.review_status!='unreviewed' THEN 1 ELSE 0 END) AS reviewed,
                  SUM(CASE WHEN i.review_status='confirmed' THEN 1 ELSE 0 END) AS confirmed
           FROM nodes n LEFT JOIN spaces s ON s.id=n.space_id LEFT JOIN intelligence i ON i.node_id=n.id
           GROUP BY n.id ORDER BY n.created_at DESC"""
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        reviewed = item["reviewed"] or 0
        item["granted_categories"] = json.loads(item["granted_categories"] or "[]")
        item["granted_source_count"] = len(json.loads(item.pop("granted_sources") or "[]"))
        item["share_evidence"] = bool(item["share_evidence"])
        if _is_expired(item["expires_at"]):
            item["grant_status"] = "expired"
        item["review_accuracy"] = round((item["confirmed"] or 0) / reviewed * 100, 1) if reviewed else None
        result.append(item)
    return {"nodes": result}


def _serialize_intelligence(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["tags"] = json.loads(item.get("tags") or "[]")
    item["effective_category"] = item.get("corrected_category") or item.get("predicted_category")
    return item


@app.get("/api/admin/intelligence")
async def list_intelligence(
    category: Optional[Literal["A", "B", "C"]] = None,
    node_id: Optional[str] = None,
    disposition: Optional[Literal["new", "acknowledged", "assigned", "resolved"]] = None,
    review_status: Optional[Literal["unreviewed", "confirmed", "false_positive", "corrected"]] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_admin_token: str = Header(""),
):
    _require_admin(x_admin_token)
    conditions = []
    params: list = []
    if category:
        conditions.append("COALESCE(i.corrected_category, i.predicted_category)=?")
        params.append(category)
    if node_id:
        conditions.append("i.node_id=?")
        params.append(node_id)
    if disposition:
        conditions.append("i.disposition=?")
        params.append(disposition)
    if review_status:
        conditions.append("i.review_status=?")
        params.append(review_status)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    conn = _connect()
    total = conn.execute(f"SELECT COUNT(*) FROM intelligence i{where}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT i.*, n.name AS node_name
            FROM intelligence i JOIN nodes n ON n.id=i.node_id
            {where} ORDER BY i.id DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    conn.close()
    return {"total": total, "items": [_serialize_intelligence(row) for row in rows]}


@app.post("/api/admin/intelligence/{intelligence_id}/evidence-requests")
async def request_evidence(
    intelligence_id: int,
    payload: EvidenceRequestCreate,
    x_admin_token: str = Header(""),
):
    actor = _require_admin(x_admin_token)
    conn = _connect()
    intelligence = conn.execute(
        "SELECT id, node_id FROM intelligence WHERE id=?",
        (intelligence_id,),
    ).fetchone()
    if not intelligence:
        conn.close()
        raise HTTPException(404, "情报不存在")
    pending = conn.execute(
        "SELECT id FROM evidence_requests WHERE intelligence_id=? AND status='pending'",
        (intelligence_id,),
    ).fetchone()
    if pending:
        conn.close()
        raise HTTPException(409, "该情报已有待处理的原文申请")
    cursor = conn.execute(
        """INSERT INTO evidence_requests (intelligence_id, node_id, reason, expires_at)
           VALUES (?, ?, ?, ?)""",
        (intelligence_id, intelligence["node_id"], payload.reason.strip(), _expires_in(7)),
    )
    request_id = cursor.lastrowid
    _audit(conn, actor, "evidence.requested", "intelligence", str(intelligence_id), {"request_id": request_id, "reason": payload.reason.strip()})
    conn.commit()
    conn.close()
    return {"status": "pending", "request_id": request_id}


@app.get("/api/admin/evidence-requests")
async def list_evidence_requests(x_admin_token: str = Header("")):
    _require_admin(x_admin_token)
    conn = _connect()
    _cleanup_retention(conn)
    conn.commit()
    rows = conn.execute(
        """SELECT e.*, i.summary, i.external_id, n.name AS node_name,
                  COALESCE(s.name, '') AS space_name
           FROM evidence_requests e
           JOIN intelligence i ON i.id=e.intelligence_id
           JOIN nodes n ON n.id=e.node_id
           LEFT JOIN spaces s ON s.id=n.space_id
           ORDER BY e.id DESC LIMIT 200"""
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        item = dict(row)
        if item["status"] == "pending" and _is_expired(item["expires_at"]):
            item["status"] = "expired"
        items.append(item)
    return {"items": items}


@app.get("/api/admin/dashboard")
async def dashboard(x_admin_token: str = Header("")):
    _require_admin(x_admin_token)
    conn = _connect()
    total_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    active_nodes = conn.execute(
        """SELECT COUNT(*) FROM nodes
           WHERE enabled=1 AND grant_status='active'
             AND (expires_at='' OR expires_at>?)
             AND last_seen >= datetime('now','-5 minutes')""",
        (_utcnow().isoformat(timespec="seconds"),),
    ).fetchone()[0]
    intelligence_24h = conn.execute(
        "SELECT COUNT(*) FROM intelligence WHERE ingested_at >= datetime('now','-1 day')"
    ).fetchone()[0]
    open_alerts = conn.execute(
        """SELECT COUNT(*) FROM intelligence
           WHERE COALESCE(corrected_category,predicted_category)='A'
             AND disposition='new' AND review_status='unreviewed'"""
    ).fetchone()[0]
    reviewed = conn.execute("SELECT COUNT(*) FROM intelligence WHERE review_status!='unreviewed'").fetchone()[0]
    confirmed = conn.execute("SELECT COUNT(*) FROM intelligence WHERE review_status='confirmed'").fetchone()[0]
    category_rows = conn.execute(
        """SELECT COALESCE(corrected_category,predicted_category) AS category, COUNT(*) AS count
           FROM intelligence WHERE review_status!='false_positive' GROUP BY category"""
    ).fetchall()
    source_rows = conn.execute(
        """SELECT COALESCE(NULLIF(source_type,''),'unknown') AS source, COUNT(*) AS count
           FROM intelligence WHERE review_status!='false_positive' GROUP BY source ORDER BY count DESC"""
    ).fetchall()
    alert_rows = conn.execute(
        """SELECT i.*, n.name AS node_name FROM intelligence i JOIN nodes n ON n.id=i.node_id
           WHERE COALESCE(i.corrected_category,i.predicted_category)='A'
             AND i.disposition='new' AND i.review_status='unreviewed'
           ORDER BY i.id DESC LIMIT 20"""
    ).fetchall()
    calibration_row = conn.execute(
        """SELECT COUNT(*) AS reporting_nodes,
                  COALESCE(SUM(reviewed_count), 0) AS reviewed,
                  COALESCE(SUM(correct_count), 0) AS correct
             FROM calibration_reports"""
    ).fetchone()
    calibration_examples = conn.execute("SELECT COUNT(*) FROM calibration_examples").fetchone()[0]
    conn.close()
    return {
        "nodes": {"total": total_nodes, "active": active_nodes},
        "intelligence_24h": intelligence_24h,
        "open_alerts": open_alerts,
        "reviewed": reviewed,
        "review_accuracy": round(confirmed / reviewed * 100, 1) if reviewed else None,
        "categories": {row["category"]: row["count"] for row in category_rows},
        "sources": {row["source"]: row["count"] for row in source_rows},
        "alerts": [_serialize_intelligence(row) for row in alert_rows],
        "retention_days": RETENTION_DAYS,
        "privacy_mode": "structured_intelligence_only",
        "calibration": {
            "reporting_nodes": calibration_row["reporting_nodes"],
            "reviewed": calibration_row["reviewed"],
            "accuracy": round(calibration_row["correct"] / calibration_row["reviewed"] * 100, 1)
                if calibration_row["reviewed"] else None,
            "consented_examples": calibration_examples,
        },
    }


@app.get("/api/admin/calibration")
async def admin_calibration(x_admin_token: str = Header("")):
    _require_admin(x_admin_token)
    conn = _connect()
    _cleanup_retention(conn)
    conn.commit()
    aggregate = conn.execute(
        """SELECT COUNT(*) AS reporting_nodes,
                  COALESCE(SUM(reviewed_count), 0) AS reviewed,
                  COALESCE(SUM(correct_count), 0) AS correct
             FROM calibration_reports"""
    ).fetchone()
    report_rows = conn.execute(
        """SELECT r.reviewed_count, r.correct_count, r.accuracy, r.confusion,
                  r.prompt_version, r.updated_at, n.name AS node_name,
                  COALESCE(s.name, '') AS space_name
             FROM calibration_reports r
             JOIN nodes n ON n.id=r.node_id
             LEFT JOIN spaces s ON s.id=n.space_id
            ORDER BY r.updated_at DESC"""
    ).fetchall()
    example_rows = conn.execute(
        """SELECT e.external_id, e.predicted_category, e.corrected_category,
                  e.confidence, e.source_type, e.source_ref, e.content_excerpt,
                  e.prompt_version, e.consented_at, e.ingested_at,
                  n.name AS node_name, COALESCE(s.name, '') AS space_name
             FROM calibration_examples e
             JOIN nodes n ON n.id=e.node_id
             LEFT JOIN spaces s ON s.id=n.space_id
            ORDER BY e.ingested_at DESC LIMIT 200"""
    ).fetchall()
    conn.close()
    reports = []
    for row in report_rows:
        item = dict(row)
        item["confusion"] = json.loads(item.get("confusion") or "[]")
        reports.append(item)
    reviewed = int(aggregate["reviewed"] or 0)
    correct = int(aggregate["correct"] or 0)
    return {
        "aggregate": {
            "reporting_nodes": int(aggregate["reporting_nodes"] or 0),
            "reviewed": reviewed,
            "correct": correct,
            "accuracy": round(correct / reviewed * 100, 1) if reviewed else None,
            "consented_examples": len(example_rows),
        },
        "reports": reports,
        "examples": [dict(row) for row in example_rows],
        "privacy": {
            "statistics": "匿名聚合，不含消息正文、群名或发送者",
            "examples": "仅展示学生逐条批准的脱敏样本",
        },
    }


@app.post("/api/admin/intelligence/{intelligence_id}/review")
async def review_intelligence(
    intelligence_id: int,
    payload: ReviewPayload,
    x_admin_token: str = Header(""),
):
    actor = _require_admin(x_admin_token)
    conn = _connect()
    exists = conn.execute("SELECT 1 FROM intelligence WHERE id=?", (intelligence_id,)).fetchone()
    if not exists:
        conn.close()
        raise HTTPException(404, "情报不存在")
    corrected = payload.corrected_category if payload.verdict == "corrected" else None
    conn.execute(
        "UPDATE intelligence SET review_status=?, corrected_category=? WHERE id=?",
        (payload.verdict, corrected, intelligence_id),
    )
    conn.execute(
        """INSERT INTO reviews (intelligence_id, verdict, corrected_category, note, reviewer)
           VALUES (?, ?, ?, ?, ?)""",
        (intelligence_id, payload.verdict, corrected, payload.note.strip(), actor),
    )
    _audit(conn, actor, "intelligence.reviewed", "intelligence", str(intelligence_id), payload.model_dump())
    conn.commit()
    conn.close()
    return {"status": "ok", "review_status": payload.verdict, "corrected_category": corrected}


@app.post("/api/admin/intelligence/{intelligence_id}/disposition")
async def set_disposition(
    intelligence_id: int,
    payload: DispositionPayload,
    x_admin_token: str = Header(""),
):
    actor = _require_admin(x_admin_token)
    conn = _connect()
    cursor = conn.execute(
        "UPDATE intelligence SET disposition=? WHERE id=?",
        (payload.disposition, intelligence_id),
    )
    if not cursor.rowcount:
        conn.close()
        raise HTTPException(404, "情报不存在")
    _audit(conn, actor, "intelligence.disposition", "intelligence", str(intelligence_id), payload.model_dump())
    conn.commit()
    conn.close()
    return {"status": "ok", "disposition": payload.disposition}


@app.get("/api/admin/audit")
async def audit_log(limit: int = Query(100, ge=1, le=500), x_admin_token: str = Header("")):
    _require_admin(x_admin_token)
    conn = _connect()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"] or "{}")
        result.append(item)
    return {"items": result}


@app.get("/api/admin/export")
async def export_structured(x_admin_token: str = Header("")):
    actor = _require_admin(x_admin_token)
    conn = _connect()
    rows = conn.execute(
        """SELECT i.id, n.name AS node_name, i.predicted_category, i.corrected_category,
                  i.summary, i.tags, i.confidence, i.source_type, i.source_name,
                  i.source_ref, i.evidence_excerpt, i.occurred_at, i.ingested_at,
                  i.disposition, i.review_status
           FROM intelligence i JOIN nodes n ON n.id=i.node_id ORDER BY i.id"""
    ).fetchall()
    _audit(conn, actor, "intelligence.exported", "collection", "all", {"count": len(rows)})
    conn.commit()
    conn.close()
    return {
        "privacy_mode": "structured_intelligence_only",
        "count": len(rows),
        "items": [_serialize_intelligence(row) for row in rows],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
