import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mothership import app as mothership_app


class MothershipRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        mothership_app.DB_PATH = Path(self.temp_dir.name) / "mothership.db"
        self.previous_admin_token = os.environ.get("MOTHERSHIP_ADMIN_TOKEN")
        os.environ["MOTHERSHIP_ADMIN_TOKEN"] = "admin-token-for-tests-32-characters"
        mothership_app._init_db()
        self.client = TestClient(mothership_app.app)
        self.admin_headers = {"X-Admin-Token": os.environ["MOTHERSHIP_ADMIN_TOKEN"]}

    def tearDown(self):
        self.client.close()
        if self.previous_admin_token is None:
            os.environ.pop("MOTHERSHIP_ADMIN_TOKEN", None)
        else:
            os.environ["MOTHERSHIP_ADMIN_TOKEN"] = self.previous_admin_token
        self.temp_dir.cleanup()

    def create_node(self):
        response = self.client.post("/api/admin/nodes", headers=self.admin_headers, json={"name": "测试哨站"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_admin_and_node_tokens_are_separated(self):
        unauthorized = self.client.post("/api/admin/nodes", json={"name": "未授权"})
        self.assertEqual(unauthorized.status_code, 403)

        node = self.create_node()
        self.assertTrue(node["node_token"].startswith("nsn_"))
        listed = self.client.get("/api/admin/nodes", headers=self.admin_headers).json()["nodes"]
        self.assertEqual(len(listed), 1)
        self.assertNotIn("node_token", listed[0])
        self.assertNotIn("token_hash", listed[0])

    def test_structured_ingest_dedup_review_and_disposition(self):
        node = self.create_node()
        payload = {
            "items": [
                {
                    "external_id": "a" * 64,
                    "category": "A",
                    "summary": "明天课程调整，请及时确认",
                    "tags": ["课程", "调整"],
                    "confidence": 0.83,
                    "source_type": "qq",
                    "source_name": "课程群",
                    "source_ref": "b" * 24,
                    "occurred_at": "2026-06-28 10:00:00",
                }
            ]
        }
        headers = {"Authorization": f"Bearer {node['node_token']}"}
        first = self.client.post("/api/v1/ingest", headers=headers, json=payload)
        duplicate = self.client.post("/api/v1/ingest", headers=headers, json=payload)
        self.assertEqual(first.json()["accepted"], 1)
        self.assertEqual(duplicate.json()["duplicates"], 1)

        dashboard = self.client.get("/api/admin/dashboard", headers=self.admin_headers).json()
        self.assertEqual(dashboard["open_alerts"], 1)
        self.assertEqual(dashboard["nodes"]["active"], 1)
        item = self.client.get("/api/admin/intelligence", headers=self.admin_headers).json()["items"][0]
        self.assertNotIn("raw_content", item)
        self.assertNotIn("sender_id", item)

        reviewed = self.client.post(
            f"/api/admin/intelligence/{item['id']}/review",
            headers=self.admin_headers,
            json={"verdict": "false_positive"},
        )
        self.assertEqual(reviewed.status_code, 200)
        disposed = self.client.post(
            f"/api/admin/intelligence/{item['id']}/disposition",
            headers=self.admin_headers,
            json={"disposition": "resolved"},
        )
        self.assertEqual(disposed.status_code, 200)

        dashboard = self.client.get("/api/admin/dashboard", headers=self.admin_headers).json()
        self.assertEqual(dashboard["open_alerts"], 0)
        self.assertEqual(dashboard["review_accuracy"], 0.0)
        audits = self.client.get("/api/admin/audit", headers=self.admin_headers).json()["items"]
        self.assertGreaterEqual(len(audits), 3)

    def test_disabled_node_cannot_ingest(self):
        node = self.create_node()
        disabled = self.client.patch(
            f"/api/admin/nodes/{node['id']}", headers=self.admin_headers, json={"enabled": False}
        )
        self.assertEqual(disabled.status_code, 200)
        response = self.client.post(
            "/api/v1/ingest",
            headers={"Authorization": f"Bearer {node['node_token']}"},
            json={"items": [{"external_id": "c" * 64, "category": "B", "summary": "测试"}]},
        )
        self.assertEqual(response.status_code, 403)

    def test_space_invitation_scoped_consent_and_pause(self):
        created = self.client.post(
            "/api/admin/spaces",
            headers=self.admin_headers,
            json={"name": "计科通知协作", "owner_label": "计科二班班委", "invite_days": 30},
        )
        self.assertEqual(created.status_code, 200)
        invitation = created.json()["invitation"]
        self.assertTrue(invitation["invite_key"].startswith("nsi_"))
        self.assertTrue(invitation["join_url"].startswith("http"))
        self.assertTrue(invitation["qr_data_url"].startswith("data:image/png;base64,"))

        inspected = self.client.post(
            "/api/v1/invitations/inspect", json={"invite_key": invitation["invite_key"]}
        )
        self.assertEqual(inspected.json()["space_name"], "计科通知协作")

        joined = self.client.post(
            "/api/v1/invitations/join",
            json={
                "invite_key": invitation["invite_key"],
                "device_name": "张同学课程哨站",
                "categories": ["A"],
                "source_refs": ["source-course"],
                "share_evidence": False,
                "expires_days": 30,
            },
        )
        self.assertEqual(joined.status_code, 200)
        member_headers = {"Authorization": f"Bearer {joined.json()['member_token']}"}

        ingest = self.client.post(
            "/api/v1/ingest",
            headers=member_headers,
            json={"items": [
                {"external_id": "a" * 64, "category": "A", "summary": "考试地点调整", "source_ref": "source-course", "evidence_excerpt": "不应进入母舰"},
                {"external_id": "b" * 64, "category": "B", "summary": "校园活动", "source_ref": "source-course"},
                {"external_id": "c" * 64, "category": "A", "summary": "其他群通知", "source_ref": "source-other"},
            ]},
        ).json()
        self.assertEqual(ingest["accepted"], 1)
        self.assertEqual(ingest["rejected_scope"], 2)
        item = self.client.get("/api/admin/intelligence", headers=self.admin_headers).json()["items"][0]
        self.assertEqual(item["evidence_excerpt"], "")

        paused = self.client.post(
            "/api/v1/membership/state", headers=member_headers, json={"state": "paused"}
        )
        self.assertEqual(paused.status_code, 200)
        blocked = self.client.post(
            "/api/v1/ingest",
            headers=member_headers,
            json={"items": [{"external_id": "d" * 64, "category": "A", "summary": "暂停后消息", "source_ref": "source-course"}]},
        )
        self.assertEqual(blocked.status_code, 403)
        membership = self.client.get("/api/v1/membership", headers=member_headers).json()
        self.assertEqual(membership["status"], "paused")

    def test_original_evidence_requires_one_time_student_approval(self):
        space = self.client.post(
            "/api/admin/spaces", headers=self.admin_headers,
            json={"name": "社团活动协作", "owner_label": "社团负责人"},
        ).json()
        joined = self.client.post(
            "/api/v1/invitations/join",
            json={
                "invite_key": space["invitation"]["invite_key"], "device_name": "活动哨站",
                "categories": ["A"], "source_refs": ["activity-source"], "expires_days": 30,
            },
        ).json()
        member_headers = {"Authorization": f"Bearer {joined['member_token']}"}
        self.client.post(
            "/api/v1/ingest", headers=member_headers,
            json={"items": [{"external_id": "e" * 64, "category": "A", "summary": "场地时间冲突", "source_ref": "activity-source"}]},
        )
        item = self.client.get("/api/admin/intelligence", headers=self.admin_headers).json()["items"][0]
        requested = self.client.post(
            f"/api/admin/intelligence/{item['id']}/evidence-requests",
            headers=self.admin_headers,
            json={"reason": "需要核对场地使用的准确时段"},
        )
        self.assertEqual(requested.status_code, 200)
        request_id = requested.json()["request_id"]

        student_view = self.client.get("/api/v1/evidence-requests", headers=member_headers).json()["items"][0]
        self.assertEqual(student_view["reason"], "需要核对场地使用的准确时段")
        self.assertNotIn("evidence_content", student_view)
        approved = self.client.post(
            f"/api/v1/evidence-requests/{request_id}/respond",
            headers=member_headers,
            json={"decision": "approved", "content": "原通知：场地使用时间为周五 19:00。"},
        )
        self.assertEqual(approved.status_code, 200)
        admin_view = self.client.get("/api/admin/evidence-requests", headers=self.admin_headers).json()["items"][0]
        self.assertEqual(admin_view["status"], "approved")
        self.assertIn("周五 19:00", admin_view["evidence_content"])

        deleted = self.client.delete("/api/v1/membership/data", headers=member_headers)
        self.assertEqual(deleted.json()["deleted"], 1)
        self.assertEqual(self.client.get("/api/admin/intelligence", headers=self.admin_headers).json()["total"], 0)

    def test_calibration_stats_are_opt_in_and_examples_are_individually_consented(self):
        space = self.client.post(
            "/api/admin/spaces", headers=self.admin_headers,
            json={"name": "课程校准协作", "owner_label": "班委"},
        ).json()
        joined = self.client.post(
            "/api/v1/invitations/join",
            json={
                "invite_key": space["invitation"]["invite_key"],
                "device_name": "校准测试哨站", "categories": ["A", "B"],
                "source_refs": ["course"], "share_calibration_stats": False,
                "expires_days": 30,
            },
        ).json()
        member_headers = {"Authorization": f"Bearer {joined['member_token']}"}
        report = {
            "reviewed_count": 4, "correct_count": 3,
            "confusion": [{"predicted": "A", "corrected": "B", "count": 1}],
            "prompt_version": "prompt-test-v1",
        }
        denied = self.client.put("/api/v1/calibration/report", headers=member_headers, json=report)
        self.assertEqual(denied.status_code, 403)

        grant = self.client.patch(
            "/api/v1/membership/grant", headers=member_headers,
            json={
                "categories": ["A", "B"], "source_refs": ["course"],
                "share_evidence": False, "share_calibration_stats": True,
            },
        )
        self.assertEqual(grant.status_code, 200)
        saved = self.client.put("/api/v1/calibration/report", headers=member_headers, json=report)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["accuracy"], 75.0)

        example_item = {
            "external_id": "f" * 64,
            "predicted_category": "A", "corrected_category": "B", "confidence": 0.62,
            "source_type": "qq", "source_ref": "hash-only",
            "content_excerpt": "考试地点调整（已脱敏）", "prompt_version": "prompt-test-v1",
        }
        missing_consent = self.client.put(
            "/api/v1/calibration/examples", headers=member_headers,
            json={"item": example_item},
        )
        self.assertEqual(missing_consent.status_code, 422)
        consented = self.client.put(
            "/api/v1/calibration/examples", headers=member_headers,
            json={"consent_confirmed": True, "item": example_item},
        )
        self.assertEqual(consented.status_code, 200)

        admin = self.client.get("/api/admin/calibration", headers=self.admin_headers).json()
        self.assertEqual(admin["aggregate"]["reporting_nodes"], 1)
        self.assertEqual(admin["aggregate"]["reviewed"], 4)
        self.assertEqual(admin["aggregate"]["accuracy"], 75.0)
        self.assertEqual(admin["aggregate"]["consented_examples"], 1)
        self.assertNotIn("sender", str(admin).lower())

        deleted = self.client.delete("/api/v1/membership/data", headers=member_headers)
        self.assertEqual(deleted.json()["calibration_examples_deleted"], 1)
        cleared = self.client.get("/api/admin/calibration", headers=self.admin_headers).json()
        self.assertEqual(cleared["aggregate"]["reporting_nodes"], 0)
        self.assertEqual(cleared["aggregate"]["consented_examples"], 0)


if __name__ == "__main__":
    unittest.main()
