"""The health route says the engine is up, without the secret and without R."""
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from engine import server


class HealthTests(unittest.TestCase):
    def test_health_answers_ok_without_the_secret(self):
        client = TestClient(server.app)
        with patch.object(server, "INTERNAL_SECRET", "a-secret"):
            res = client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    def test_health_does_not_touch_r(self):
        client = TestClient(server.app)
        with patch.object(server, "_run_cell") as run_cell, \
                patch.object(server, "_run_r_helper_script") as helper:
            self.assertEqual(client.get("/health").status_code, 200)
        run_cell.assert_not_called()
        helper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
