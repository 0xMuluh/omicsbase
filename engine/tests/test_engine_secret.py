"""The engine trusts only the secret it shares with LibreChat, never a missing one."""
import os
import time
import unittest
from unittest.mock import patch

import jwt
from starlette.testclient import TestClient

from engine import access, server

ENGINE = "engine-secret-that-is-long-enough-for-hs256"
REPORTS = "reports-secret-that-is-long-enough-for-hs256"
PROJECT = "507f1f77bcf86cd799439012"


def project_token(secret):
    claims = {"aud": "omicsbase-openhands", "iss": "librechat", "purpose": "project",
              "project_id": PROJECT, "sub": "u1", "exp": int(time.time()) + 60}
    return jwt.encode(claims, secret, algorithm="HS256")


class EngineSecretTests(unittest.TestCase):
    def test_engine_secret_takes_precedence_over_the_reports_secret(self):
        env = {"OMICSBASE_ENGINE_SECRET": ENGINE, "OMICSBASE_AUTH_SECRET": REPORTS}
        with patch.dict(os.environ, env):
            self.assertTrue(access.verify_project(project_token(ENGINE), PROJECT))
            self.assertFalse(access.verify_project(project_token(REPORTS), PROJECT))

    def test_reports_secret_is_used_when_no_engine_secret_is_set(self):
        with patch.dict(os.environ, {"OMICSBASE_AUTH_SECRET": REPORTS}):
            os.environ.pop("OMICSBASE_ENGINE_SECRET", None)
            self.assertTrue(access.verify_project(project_token(REPORTS), PROJECT))

    def test_execute_and_cancel_refuse_when_no_secret_is_configured(self):
        client = TestClient(server.app)
        with patch.object(server, "INTERNAL_SECRET", ""):
            for path in ("/api/execute", "/api/cancel"):
                self.assertEqual(client.post(path, json={"code": "1"}).status_code, 401)

    def test_execute_refuses_a_wrong_secret(self):
        client = TestClient(server.app)
        with patch.object(server, "INTERNAL_SECRET", ENGINE):
            res = client.post("/api/execute", json={"code": "1"}, headers={"X-Internal-Secret": REPORTS})
            self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
