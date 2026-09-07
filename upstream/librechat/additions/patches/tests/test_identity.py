import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from omicsbase_shared.identity import runtime_name, verify_ticket
from omicsbase_shared.registry import bind_project, conversation_project


def ticket(**claims):
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    header = encode({'alg': 'HS256'})
    payload = encode({'sub': 'user-a', 'aud': 'omicsbase-openhands', 'iss': 'librechat', 'exp': time.time() + 60, **claims})
    signature = base64.urlsafe_b64encode(hmac.new(b'test', f'{header}.{payload}'.encode(), hashlib.sha256).digest()).decode().rstrip('=')
    return f'{header}.{payload}.{signature}'


class IdentityTests(unittest.TestCase):
    def test_user_not_conversation_controls_runtime_identity(self):
        self.assertEqual(runtime_name('user-a'), runtime_name('user-a'))
        self.assertNotEqual(runtime_name('user-a'), runtime_name('user-b'))
        with self.assertRaises(ValueError):
            runtime_name('../other-user')

    @patch.dict(os.environ, {'OMICSBASE_AUTH_SECRET': 'test'})
    def test_signed_identity_and_expiry(self):
        self.assertEqual(verify_ticket(ticket())['sub'], 'user-a')
        for invalid in [ticket(exp=0), ticket(aud='other'), ticket(sub='../user'), ticket() + 'x']:
            with self.assertRaises(ValueError):
                verify_ticket(invalid)

    def test_project_migration_preserves_files_and_checks_owner(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'OMICSBASE_PROJECTS_ROOT': tmp, 'OMICSBASE_STATE_ROOT': tmp + '/state'}):
            original = Path(tmp) / 'project-a'
            original.mkdir()
            (original / 'index.qmd').write_text('Real study')
            self.assertEqual(bind_project('user-a', 'project-a', 'chat-a'), '/workspace/project-a')
            bind_project('user-a', 'project-b', 'chat-b')
            self.assertTrue(original.is_symlink())
            self.assertEqual((original / 'index.qmd').read_text(), 'Real study')
            self.assertEqual((Path(tmp) / 'users/user-a/project-a/index.qmd').read_text(), 'Real study')
            self.assertEqual(conversation_project('user-a', 'chat-a'), '/workspace/project-a')
            with self.assertRaises(ValueError):
                conversation_project('user-b', 'chat-a')
            with self.assertRaises(ValueError):
                bind_project('user-b', 'project-a', 'chat-c')


if __name__ == '__main__':
    unittest.main()
