import unittest
import json
import hmac
import hashlib

from fastapi.testclient import TestClient

from autopilot.api.main import app
from autopilot.config import settings


class TestGitHubWebhookSignature(unittest.TestCase):
    def tearDown(self) -> None:
        # reset any secret after each test
        settings.GITHUB_WEBHOOK_SECRET = None

    def test_valid_signature(self):
        settings.GITHUB_WEBHOOK_SECRET = "supersecret"
        body = {"action": "ping", "repository": {"full_name": "org/repo"}}
        body_bytes = json.dumps(body).encode("utf-8")
        sig = hmac.new(
            settings.GITHUB_WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        headers = {
            "x-hub-signature-256": f"sha256={sig}",
            "content-type": "application/json",
        }

        with TestClient(app) as client:
            resp = client.post("/webhooks/github", data=body_bytes, headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("accepted", data)
        self.assertTrue(data["accepted"])  # accepted should be True

    def test_invalid_signature(self):
        settings.GITHUB_WEBHOOK_SECRET = "supersecret"
        body = {"some": "payload"}
        body_bytes = json.dumps(body).encode("utf-8")
        # wrong signature
        headers = {
            "x-hub-signature-256": "sha256=deadbeef",
            "content-type": "application/json",
        }

        with TestClient(app) as client:
            resp = client.post("/webhooks/github", data=body_bytes, headers=headers)
        self.assertEqual(resp.status_code, 401)

    def test_missing_signature_header(self):
        settings.GITHUB_WEBHOOK_SECRET = "supersecret"
        body = {"hello": "world"}
        body_bytes = json.dumps(body).encode("utf-8")

        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/github",
                data=body_bytes,
                headers={"content-type": "application/json"},
            )
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
