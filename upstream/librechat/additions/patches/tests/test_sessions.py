import os
import tempfile
import time
import unittest
from unittest.mock import patch

from omicsbase_shared.identity import issue_session_ticket, verify_ticket
from omicsbase_shared.sessions import revoke


class SessionTests(unittest.TestCase):
    def test_session_revocation_invalidates_existing_ticket(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {'OMICSBASE_AUTH_SECRET': 'test', 'OMICSBASE_STATE_ROOT': root},
        ):
            token = issue_session_ticket('user-a', expires_in=60)
            self.assertEqual(verify_ticket(token)['purpose'], 'session')
            revoke('user-a')
            with self.assertRaises(ValueError):
                verify_ticket(token)

    def test_expired_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {'OMICSBASE_AUTH_SECRET': 'test', 'OMICSBASE_STATE_ROOT': root},
        ):
            token = issue_session_ticket('user-a', expires_in=0)
            with self.assertRaises(ValueError):
                verify_ticket(token)


if __name__ == '__main__':
    unittest.main()
