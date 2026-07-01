"""Local-first calibration helpers for Nan Sentinel.

The module deliberately avoids model training and external vector services. Reviewed
examples stay in the local SQLite database, are retrieved with deterministic text
features, and are composed into a versioned few-shot prompt. The same memory can
also correct the rule-engine fallback when a near-duplicate review exists.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


STRATEGY_VERSION = "local-calibration-v1"
DEFAULT_SETTINGS = {
    "strategy_version": STRATEGY_VERSION,
    "max_examples": 5,
    "min_similarity": 0.18,
    "override_similarity": 0.72,
    "default_threshold": 0.62,
    "retrieval_enabled": True,
}

_SPACE_RE = re.compile(r"\s+")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_]{2,}", re.I)


def make_source_key(source_type: str = "qq", group_id: str = "", group_name: str = "") -> str:
    """Build a stable, non-secret source key used for thresholds and ranking."""
    source = (source_type or "unknown").strip().lower()[:40]
    identity = (group_id or group_name or "default").strip()[:160]
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


def text_features(content: str) -> list[str]:
    """Extract compact character n-grams and Latin tokens without extra packages."""
    normalized = _SPACE_RE.sub("", (content or "").lower())[:4000]
    features: set[str] = set(_LATIN_TOKEN_RE.findall(normalized))
    chars = [char for char in normalized if not char.isspace()]
    for size in (2, 3):
        features.update("".join(chars[index:index + size]) for index in range(max(0, len(chars) - size + 1)))
    return sorted(features)[:800]


def features_json(content: str) -> str:
    return json.dumps(text_features(content), ensure_ascii=False, separators=(",", ":"))


def _feature_set(content: str, stored: str | None = None) -> set[str]:
    if stored:
        try:
            value = json.loads(stored)
            if isinstance(value, list):
                return {str(item) for item in value}
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return set(text_features(content))


def text_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    cosine = overlap / math.sqrt(len(a) * len(b))
    containment = overlap / min(len(a), len(b))
    return min(1.0, cosine * 0.72 + containment * 0.28)


def get_settings(db_path: Path | str) -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM calibration_settings WHERE id=1").fetchone()
        conn.close()
    except sqlite3.Error:
        return settings
    if row:
        settings.update({
            "strategy_version": row["strategy_version"] or STRATEGY_VERSION,
            "max_examples": max(3, min(5, int(row["max_examples"] or 5))),
            "min_similarity": max(0.0, min(1.0, float(row["min_similarity"] or 0.18))),
            "override_similarity": max(0.0, min(1.0, float(row["override_similarity"] or 0.72))),
            "default_threshold": max(0.0, min(1.0, float(row["default_threshold"] or 0.62))),
            "retrieval_enabled": bool(row["retrieval_enabled"]),
        })
    return settings


def get_source_threshold(db_path: Path | str, source_key: str, default: float | None = None) -> float:
    fallback = float(DEFAULT_SETTINGS["default_threshold"] if default is None else default)
    if not source_key:
        return fallback
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT confidence_threshold FROM calibration_source_settings WHERE source_key=?",
            (source_key,),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return fallback
    return max(0.0, min(1.0, float(row[0]))) if row else fallback


def retrieve_examples(
    db_path: Path | str,
    content: str,
    source_key: str = "",
    *,
    limit: int | None = None,
    exclude_msg_id: str = "",
) -> list[dict[str, Any]]:
    settings = get_settings(db_path)
    if not settings["retrieval_enabled"]:
        return []
    query_features = set(text_features(content))
    if not query_features:
        return []
    take = max(3, min(5, int(limit or settings["max_examples"])))
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT msg_id, raw_content, original_category, corrected_category,
                      note, source_key, features_json, prompt_version, created_at
                 FROM classification_feedback
                WHERE active=1 AND is_gold=0 AND raw_content<>'' AND msg_id<>?
                ORDER BY updated_at DESC, id DESC LIMIT 500""",
            (exclude_msg_id,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    ranked: list[dict[str, Any]] = []
    for row in rows:
        score = text_similarity(query_features, _feature_set(row["raw_content"], row["features_json"]))
        same_source = bool(source_key and row["source_key"] == source_key)
        if same_source:
            score = min(1.0, score + 0.08)
        if score < settings["min_similarity"]:
            continue
        ranked.append({
            "msg_id": row["msg_id"],
            "content": row["raw_content"],
            "predicted": row["original_category"],
            "corrected": row["corrected_category"],
            "note": row["note"] or "",
            "same_source": same_source,
            "score": round(score, 4),
            "prompt_version": row["prompt_version"] or "legacy",
        })
    ranked.sort(key=lambda item: (item["score"], item["same_source"]), reverse=True)
    return ranked[:take]


def prompt_version(base_prompt: str) -> str:
    digest = hashlib.sha256(base_prompt.encode("utf-8")).hexdigest()[:10]
    return f"{STRATEGY_VERSION}:{digest}"


def register_prompt_version(
    db_path: Path | str,
    base_prompt: str,
    *,
    scope: str = "single",
    label: str = "应用内置规则",
    release_note: str = "随应用版本发布",
) -> str:
    """Register an immutable prompt snapshot and activate the first version only."""
    version = prompt_version(base_prompt)
    digest = hashlib.sha256(base_prompt.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT OR IGNORE INTO calibration_prompt_versions
           (version, scope, prompt_hash, prompt_text, label, release_note, status)
           VALUES (?, ?, ?, ?, ?, ?, 'candidate')""",
        (version, scope, digest, base_prompt, label, release_note),
    )
    active = conn.execute(
        "SELECT version FROM calibration_prompt_versions WHERE scope=? AND status='active'",
        (scope,),
    ).fetchone()
    if not active:
        conn.execute(
            """UPDATE calibration_prompt_versions
                  SET status='active', activated_at=CURRENT_TIMESTAMP
                WHERE version=?""",
            (version,),
        )
    conn.commit()
    conn.close()
    return version


def resolve_prompt(db_path: Path | str, fallback_prompt: str, *, scope: str = "single") -> tuple[str, str]:
    """Resolve the active immutable prompt; fall back safely if storage is unavailable."""
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            """SELECT prompt_text, version FROM calibration_prompt_versions
                 WHERE scope=? AND status='active' ORDER BY activated_at DESC LIMIT 1""",
            (scope,),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        row = None
    if row and row[0]:
        return str(row[0]), str(row[1])
    return fallback_prompt, prompt_version(fallback_prompt)


def compose_prompt(base_prompt: str, examples: list[dict[str, Any]]) -> str:
    """Append controlled few-shot memory without allowing arbitrary prompt editing."""
    if not examples:
        return base_prompt
    lines = [
        base_prompt,
        "",
        "═══ 本机人工校准案例（只用于理解相似语境） ═══",
        "以下案例来自本机用户复核。优先学习真实意图，不要复制姓名或来源信息。",
    ]
    for index, example in enumerate(examples, start=1):
        content = _SPACE_RE.sub(" ", str(example["content"]).strip())[:260]
        corrected = example["corrected"]
        note = f"；说明：{str(example['note'])[:100]}" if example.get("note") else ""
        lines.append(
            f"案例 {index}：{content}\n原判断：{example['predicted']}；人工结论：{corrected}{note}"
        )
    lines.extend([
        "仅在语义确实相似时参考案例；若证据不足，降低 confidence，不要强行套用。",
        f"校准策略版本：{STRATEGY_VERSION}",
    ])
    return "\n".join(lines)


def choose_override(
    baseline_category: str,
    examples: list[dict[str, Any]],
    override_similarity: float,
) -> tuple[str, float]:
    """Conservatively override only when a strong, coherent local memory exists."""
    if not examples or examples[0]["score"] < override_similarity:
        return baseline_category, 0.0
    votes: dict[str, float] = {}
    for example in examples:
        if example["score"] < override_similarity:
            continue
        category = str(example["corrected"])
        votes[category] = votes.get(category, 0.0) + float(example["score"])
    if not votes:
        return baseline_category, 0.0
    winner, weight = max(votes.items(), key=lambda item: item[1])
    total = sum(votes.values())
    coherence = weight / total if total else 0.0
    if coherence < 0.65:
        return baseline_category, 0.0
    return winner, min(1.0, float(examples[0]["score"]))


def evaluate_gold_set(db_path: Path | str) -> dict[str, Any]:
    """Leave gold rows out of retrieval and replay their original predictions locally."""
    settings = get_settings(db_path)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT msg_id, raw_content, original_category, corrected_category, source_key
                 FROM classification_feedback
                WHERE active=1 AND is_gold=1 AND raw_content<>'' ORDER BY id"""
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        rows = []
    details: list[dict[str, Any]] = []
    baseline_correct = 0
    calibrated_correct = 0
    for row in rows:
        examples = retrieve_examples(
            db_path,
            row["raw_content"],
            row["source_key"] or "",
            exclude_msg_id=row["msg_id"],
        )
        calibrated, score = choose_override(
            row["original_category"], examples, settings["override_similarity"]
        )
        expected = row["corrected_category"]
        baseline_correct += int(row["original_category"] == expected)
        calibrated_correct += int(calibrated == expected)
        details.append({
            "msg_id": row["msg_id"],
            "expected": expected,
            "baseline": row["original_category"],
            "calibrated": calibrated,
            "similarity": round(score, 4),
        })
    total = len(rows)
    return {
        "sample_count": total,
        "baseline_correct": baseline_correct,
        "calibrated_correct": calibrated_correct,
        "baseline_accuracy": round(baseline_correct / total * 100, 1) if total else None,
        "calibrated_accuracy": round(calibrated_correct / total * 100, 1) if total else None,
        "strategy_version": settings["strategy_version"],
        "details": details,
    }
