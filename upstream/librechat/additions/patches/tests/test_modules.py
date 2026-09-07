import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from omicsbase_shared import assets, editor, reports
from omicsbase_shared.frontend import validate_frontend
from omicsbase_shared.reporting import _inspect_project_report
from omicsbase_shared.workspace import _resolve_safe_path


class IntegrationModulesTests(unittest.TestCase):
    def test_editor_report_and_assets_keep_their_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '_quarto.yml').write_text('book:\n  title: Study\n  chapters: [analysis.qmd]\n')
            (root / 'analysis.qmd').write_text('---\ntitle: Analysis\n---\n# Findings\n')
            (root / '_site').mkdir()
            (root / '_site/analysis.html').write_text('<p>Findings</p>')
            report = _inspect_project_report(root)
            self.assertEqual(report['project_title'], 'Study')
            self.assertEqual(report['chapters'][0]['status'], 'completed')
            app = FastAPI()
            for router in (editor.router, reports.router, assets.router): app.include_router(router)
            with patch.object(editor, '_get_project_dir', return_value=root), patch.object(reports, '_get_project_dir', return_value=root), TestClient(app) as client:
                self.assertEqual(client.get('/api/omicsbase/editor/chat').status_code, 200)
                self.assertEqual(client.post('/api/omicsbase/editor/chat/file?path=notes.txt', json={'content':'saved'}).status_code, 200)
                self.assertEqual(client.get('/api/omicsbase/editor/chat/file?path=notes.txt').json()['content'], 'saved')
                self.assertEqual(client.get('/api/omicsbase/report/chat/site/analysis.html').text, '<p>Findings</p>')
                self.assertEqual(client.get('/api/omicsbase/assets/editor.js').status_code, 200)
                self.assertEqual(client.get('/api/omicsbase/assets/app.py').status_code, 404)

    def test_path_boundary_and_missing_frontend_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'study'
            root.mkdir()
            self.assertEqual(_resolve_safe_path(root, 'file.txt'), root / 'file.txt')
            with self.assertRaises(Exception): _resolve_safe_path(root, '../study-private/file.txt')
            with self.assertRaisesRegex(RuntimeError, 'Missing OmicsBase frontend'):
                validate_frontend(root)
