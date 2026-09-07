import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine.knowledge.indexer import BOOKS
from engine.knowledge.search import search_bioc_knowledge
from scripts.setup_knowledge import install


class KnowledgeSetupTests(unittest.TestCase):
    def test_download_index_attribution_and_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'public-source'
            source.mkdir()
            (source / 'chapter.qmd').write_text('---\ntitle: Example\n---\n# Analysis\nPublic analysis reference.\n')
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
            manifest = {'version': 1, 'books': [dict(book, repository=str(source), commit=commit, attribution='Fixture authors', license_statements=[{'path': 'NOTICE', 'statement': 'Fixture terms'}]) for book in BOOKS]}
            output = root / 'knowledge.db'
            stats = install(manifest, root / 'cache', output)
            self.assertEqual(set(stats), {book['slug'] for book in BOOKS})
            result = search_bioc_knowledge('analysis', limit=8, db_path=output)
            self.assertEqual(result['count'], 5)
            self.assertTrue(all(m['source_commit'] == commit for m in result['matches']))
            self.assertIn('Fixture authors', result['markdown'])
            self.assertIn('Fixture terms', result['markdown'])
            self.assertEqual(install(manifest, root / 'cache', output), stats)
            with sqlite3.connect(output) as conn:
                self.assertEqual(conn.execute('select count(*) from knowledge_sources').fetchone()[0], 5)
            # A damaged cached source must fail without replacing the good index.
            before = output.read_bytes()
            (root / 'cache/sources/osca' / commit / 'chapter.qmd').write_text('tampered')
            with self.assertRaises(RuntimeError):
                install(manifest, root / 'cache', output)
            self.assertEqual(output.read_bytes(), before)

    def test_index_failure_preserves_previous_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'knowledge.db'
            output.write_bytes(b'previous index')
            manifest = {'books': [{'slug': 'osca'}]}
            with patch('scripts.setup_knowledge.fetch', return_value=('osca', root)), patch('engine.knowledge.indexer.build_index', side_effect=ValueError('parse failed')):
                with self.assertRaises(ValueError):
                    install(manifest, root / 'cache', output)
            self.assertEqual(output.read_bytes(), b'previous index')
            self.assertFalse(list(root.glob('.knowledge-*.db')))


if __name__ == '__main__':
    unittest.main()
