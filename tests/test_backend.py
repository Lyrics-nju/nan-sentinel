import sqlite3
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import api  # noqa: E402


class BackendRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        api.DATA_DIR = data_dir
        api.BASE_DIR = data_dir
        api.DB_PATH = data_dir / "market.db"
        api.CONFIG_PATH = data_dir / "config.yaml"
        api.CONFIG_PATH.write_text(
            yaml.safe_dump(
                {
                    "profile": {"nickname": "测试用户"},
                    "llm": {
                        "api_key": "secret-test-key",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-chat",
                    },
                    "napcat": {"include_private": False, "group_ids": []},
                    "mothership": {
                        "enabled": False,
                        "url": "http://127.0.0.1:8010",
                        "node_token": "secret-node-token",
                        "admin_token": "secret-admin-token",
                        "share_evidence": False,
                    },
                    "scraper": {"mode": "realtime"},
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        api._init_db()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def ingest(self, msg_id="m-1", category="A", content="【教务处】明天考试"):
        return self.client.post(
            "/api/ingest",
            json={
                "msg_id": msg_id,
                "sender_id": "u-1",
                "sender_name": "同学",
                "group_id": "g-1",
                "group_name": "班级群",
                "raw_content": content,
                "category": category,
                "summary": content,
                "tags": ["测试"],
                "source_type": "qq",
                "source_name": "QQ",
                "confidence": 0.8,
                "classification_method": "test",
            },
        )

    def test_config_never_returns_api_key_and_blank_update_preserves_it(self):
        config = self.client.get("/api/config")
        self.assertEqual(config.status_code, 200)
        body = config.json()
        self.assertNotIn("llm_api_key", body)
        self.assertNotIn("mothership_node_token", body)
        self.assertNotIn("mothership_admin_token", body)
        self.assertTrue(body["mothership_node_token_set"])
        self.assertTrue(body["mothership_admin_token_set"])
        self.assertTrue(body["llm_api_key_set"])

        response = self.client.post("/api/config", json={"llm_api_key": "", "llm_model": "deepseek-chat"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.load_config()["llm"]["api_key"], "secret-test-key")

    def test_mothership_card_is_structured_and_outbox_survives_failure(self):
        config = api.load_config()
        message = {
            "msg_id": "private-local-id",
            "category": "A",
            "summary": "课程安排有变化",
            "tags": ["课程"],
            "raw_content": "联系 QQ:12345678，手机 13800138000",
            "source_type": "qq",
            "source_name": "课程群",
            "created_at": "2026-06-28 10:00:00",
        }
        card = api._build_mothership_card(message, config)
        self.assertNotIn("raw_content", card)
        self.assertNotIn("sender_id", card)
        self.assertEqual(card["evidence_excerpt"], "")

        config["mothership"]["share_evidence"] = True
        card_with_evidence = api._build_mothership_card(message, config)
        self.assertNotIn("13800138000", card_with_evidence["evidence_excerpt"])
        self.assertNotIn("12345678", card_with_evidence["evidence_excerpt"])

        config["mothership"].update({"enabled": True, "url": "http://127.0.0.1:9"})
        api.save_config(config)
        connection = sqlite3.connect(api.DB_PATH)
        self.assertTrue(api._enqueue_mothership(connection, message, config))
        connection.commit()
        connection.close()
        result = asyncio.run(api._flush_mothership_outbox())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["pending"], 1)

    def test_consent_preview_filters_categories_and_sources(self):
        self.assertEqual(self.ingest(msg_id="scope-a", category="A", content="考试地点调整").status_code, 200)
        second = self.client.post(
            "/api/ingest",
            json={
                "msg_id": "scope-b", "sender_id": "u-2", "sender_name": "同学",
                "group_id": "g-2", "group_name": "社团群", "raw_content": "社团招新",
                "category": "B", "summary": "社团招新", "tags": ["社团"],
                "source_type": "qq", "source_name": "QQ", "confidence": 0.7,
                "classification_method": "test",
            },
        )
        self.assertEqual(second.status_code, 200)
        options = self.client.get("/api/mothership/share-options").json()["items"]
        class_source = next(item for item in options if item["label"] == "班级群")
        preview = self.client.post(
            "/api/mothership/share-preview",
            json={"categories": ["A"], "source_refs": [class_source["ref"]], "share_evidence": False},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["count"], 1)
        self.assertEqual(preview.json()["items"][0]["summary"], "考试地点调整")
        self.assertIn("完整原文", preview.json()["never_shared"])

        no_source = self.client.post(
            "/api/mothership/share-preview",
            json={"categories": ["A", "B"], "source_refs": [], "share_evidence": False},
        )
        self.assertEqual(no_source.json()["count"], 0)

        local_message = api._find_local_message_by_external_id(preview.json()["items"][0]["external_id"])
        self.assertIsNotNone(local_message)
        self.assertEqual(local_message["msg_id"], "scope-a")

    def test_cross_site_and_dns_rebinding_requests_are_rejected(self):
        cross_site = self.client.post(
            "/api/batch_process",
            headers={"Origin": "https://malicious.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(cross_site.status_code, 403)

        bad_host = self.client.get("/api/health", headers={"Host": "malicious.example"})
        self.assertEqual(bad_host.status_code, 403)

        local = self.client.get("/api/health")
        self.assertEqual(local.status_code, 200)
        self.assertEqual(local.headers["x-content-type-options"], "nosniff")
        self.assertEqual(local.headers["cache-control"], "no-store")

    def test_duplicate_ingest_does_not_inflate_counts(self):
        self.assertEqual(self.ingest().json()["status"], "ok")
        self.assertEqual(self.ingest().json()["status"], "duplicate")
        stats = self.client.get("/api/stats").json()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["A"], 1)

    def test_legacy_node_rows_remain_visible_after_migration(self):
        connection = sqlite3.connect(api.DB_PATH)
        connection.execute("ALTER TABLE messages ADD COLUMN node_name TEXT DEFAULT ''")
        connection.execute(
            """INSERT INTO messages
               (msg_id, sender_id, sender_name, raw_content, category, summary, tags, node_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("legacy-1", "u-old", "旧用户", "历史通知", "A", "历史通知", "[]", "旧测试节点"),
        )
        connection.commit()
        connection.close()

        api._init_db()
        messages = self.client.get("/api/messages").json()
        self.assertTrue(any(message["msg_id"] == "legacy-1" for message in messages))

    def test_feedback_corrects_category_and_handles_false_positive(self):
        self.ingest()
        corrected = self.client.post("/api/messages/m-1/feedback", json={"corrected_category": "B"})
        self.assertEqual(corrected.status_code, 200)
        messages = self.client.get("/api/messages?category=B").json()
        self.assertEqual(len(messages), 1)

        removed = self.client.post("/api/messages/m-1/feedback", json={"corrected_category": "None"})
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/stats").json()["total"], 0)
        feedback = self.client.get("/api/feedback/stats").json()
        self.assertEqual(feedback["reviewed"], 1)
        self.assertEqual(feedback["incorrect"], 1)

    def test_local_calibration_retrieves_reviews_gates_low_confidence_and_evaluates_gold(self):
        config = api.load_config()
        config["llm"]["api_key"] = ""
        api.save_config(config)

        self.assertEqual(self.ingest(msg_id="train-1", category="A", content="【教务处】明天考试地点调整").status_code, 200)
        reviewed = self.client.post(
            "/api/messages/train-1/feedback",
            json={"corrected_category": "B", "note": "这是同学转述，不是官方通知"},
        )
        self.assertEqual(reviewed.status_code, 200)

        from scraper import classify_message
        source_context = {"source_type": "qq", "group_id": "g-1", "group_name": "班级群"}
        calibrated = asyncio.run(classify_message(
            "【教务处】明天考试地点调整", api.load_config(), source_context, api.DB_PATH,
        ))
        self.assertEqual(calibrated["base_category"], "A")
        self.assertEqual(calibrated["category"], "B")
        self.assertGreaterEqual(calibrated["calibration_examples"], 1)
        self.assertIn("local_memory", calibrated["method"])

        source_key = api.calibration.make_source_key("qq", "g-1", "班级群")
        threshold = self.client.put(
            "/api/calibration/source-threshold",
            json={"source_key": source_key, "label": "班级群", "confidence_threshold": 0.9},
        )
        self.assertEqual(threshold.status_code, 200)
        uncertain = asyncio.run(classify_message(
            "【学院】下周课程调整，请准时参加", api.load_config(), source_context, api.DB_PATH,
        ))
        self.assertTrue(uncertain["review_required"])

        self.assertEqual(self.ingest(msg_id="gold-1", category="A", content="【教务处】明天考试地点调整，请相互转告").status_code, 200)
        self.assertEqual(self.client.post(
            "/api/messages/gold-1/feedback", json={"corrected_category": "B"},
        ).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/calibration/examples/gold-1/gold", json={"enabled": True},
        ).status_code, 200)
        evaluation = self.client.post("/api/calibration/evaluate")
        self.assertEqual(evaluation.status_code, 200)
        result = evaluation.json()
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["baseline_accuracy"], 0.0)
        self.assertEqual(result["calibrated_accuracy"], 100.0)
        summary = self.client.get("/api/calibration").json()
        self.assertEqual(summary["counts"]["gold"], 1)
        self.assertEqual(summary["strategy"]["max_examples"], 5)

    def test_prompt_versions_can_activate_and_rollback(self):
        from scraper import CLASSIFY_PROMPT

        initial = self.client.get("/api/calibration").json()
        active = initial["strategy"]["active_prompt_version"]
        candidate = api.calibration.register_prompt_version(
            api.DB_PATH,
            CLASSIFY_PROMPT + "\n测试版：保留可回退记录。",
            label="测试候选规则",
            release_note="回归测试",
        )
        switched = self.client.post(f"/api/calibration/prompt-versions/{candidate}/activate")
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["strategy"]["active_prompt_version"], candidate)
        prompt, version = api.calibration.resolve_prompt(api.DB_PATH, CLASSIFY_PROMPT)
        self.assertEqual(version, candidate)
        self.assertIn("测试版", prompt)

        rolled_back = self.client.post(f"/api/calibration/prompt-versions/{active}/activate")
        self.assertEqual(rolled_back.status_code, 200)
        self.assertEqual(rolled_back.json()["strategy"]["active_prompt_version"], active)

    def test_calibration_sample_requires_explicit_share_and_stays_persistent(self):
        self.assertEqual(self.ingest(
            msg_id="share-1", category="A",
            content="考试调整，联系 QQ:12345678，手机 13800138000",
        ).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/messages/share-1/feedback", json={"corrected_category": "B"},
        ).status_code, 200)
        message = self.client.get("/api/messages").json()[0]
        self.assertEqual(message["calibration_status"], "active")
        self.assertFalse(message["feedback_shared_with_mothership"])

        remote = AsyncMock(return_value={"status": "saved"})
        with patch.object(api, "_mothership_node_request", new=remote):
            shared = self.client.post(
                "/api/calibration/examples/share-1/share", json={"enabled": True},
            )
            self.assertEqual(shared.status_code, 200)
            payload = remote.await_args.kwargs["payload"]
            self.assertTrue(payload["consent_confirmed"])
            serialized = str(payload)
            self.assertNotIn("13800138000", serialized)
            self.assertNotIn("12345678", serialized)
            self.assertNotIn("班级群", serialized)

        summary = self.client.get("/api/calibration").json()
        sample = next(item for item in summary["examples"] if item["msg_id"] == "share-1")
        self.assertTrue(sample["shared_with_mothership"])

        remote = AsyncMock(return_value={"status": "deleted", "deleted": 1})
        with patch.object(api, "_mothership_node_request", new=remote):
            withdrawn = self.client.post(
                "/api/calibration/examples/share-1/share", json={"enabled": False},
            )
            self.assertEqual(withdrawn.status_code, 200)
        self.assertFalse(self.client.get("/api/messages").json()[0]["feedback_shared_with_mothership"])

    def test_bookmark_folder_creation_and_joined_message_data(self):
        self.ingest()
        created = self.client.post("/api/folders", json={"name": "考试"})
        self.assertEqual(created.status_code, 200)
        added = self.client.post("/api/bookmarks", json={"msg_id": "m-1", "folder": "考试"})
        self.assertEqual(added.status_code, 200)

        bookmarks = self.client.get("/api/bookmarks?folder=考试").json()
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0]["summary"], "【教务处】明天考试")
        folders = {item["name"]: item["count"] for item in self.client.get("/api/folders").json()}
        self.assertEqual(folders["考试"], 1)

    def test_failed_batch_keeps_original_buffer(self):
        config = api.load_config()
        config["llm"]["api_key"] = ""
        api.save_config(config)
        large_message = "课程讨论" * 4000
        for index in range(4):
            buffered = self.client.post(
                "/api/buffer",
                json={
                    "msg_id": f"buffer-{index}",
                    "chat_id": "group-1",
                    "chat_name": "测试群",
                    "sender_id": "u-1",
                    "sender_name": "同学",
                    "raw_content": large_message,
                },
            )
            self.assertEqual(buffered.status_code, 200)
        processed = self.client.post("/api/batch_process")
        self.assertEqual(processed.status_code, 200)
        self.assertEqual(processed.json()["status"], "partial")
        self.assertEqual(self.client.get("/api/buffer_stats").json()["buffered"], 4)

    def test_csv_source_import_uses_common_pipeline(self):
        config = api.load_config()
        config["llm"]["api_key"] = ""
        api.save_config(config)
        response = self.client.post(
            "/api/sources/import",
            json={
                "source_type": "csv",
                "source_name": "课程表导出",
                "messages": [
                    {
                        "id": "csv-1",
                        "channel_name": "课程通知",
                        "sender_name": "教务处",
                        "content": "【教务处】重要通知：明天考试安排调整",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 1)
        message = self.client.get("/api/messages").json()[0]
        self.assertEqual(message["source_type"], "csv")
        self.assertEqual(message["source_name"], "课程表导出")


if __name__ == "__main__":
    unittest.main()
