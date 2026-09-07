"""Real R execution with a local persistence HTTP server; no model API calls."""
import asyncio
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
from engine import server
from engine.kernel import note_kernel


class ExecutionLatencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob3-latency-test-")
        self.patches = [patch.object(server, "PROJECTS_DIR", self.temp.name)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for path, handle in list(note_kernel._kernels.items()):
            if path.startswith(self.temp.name):
                note_kernel.kill_kernel(handle)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def test_agent_uses_two_persistence_calls_and_runs_only_once_on_save_failure(self):
        calls = []
        fail_finish = False

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, body))
                finish = "/finish/" in self.path
                self.send_response(503 if finish and fail_finish else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"cellId": "a" * 24, "executionId": "b" * 24}).encode())

            def log_message(self, *_):
                pass

        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, "LIBRECHAT_INTERNAL_URL", f"http://127.0.0.1:{http.server_port}"), patch.object(server, "INTERNAL_SECRET", "test"):
                result = await server.execute_r_cell('counter <- 1; print(counter)', 'agent')
                self.assertIn('status=completed', result)
                self.assertEqual(len(calls), 2)
                self.assertTrue(calls[0][0].endswith('/start'))
                self.assertIn('/finish/', calls[1][0])
                self.assertIn('1', calls[1][1]['stdout'])
                fail_finish = True
                result = await server.execute_r_cell('counter <- counter + 1; print(counter)', 'agent')
                self.assertIn('do not rerun automatically', result)
                self.assertEqual(len(calls), 4)
                check = await asyncio.to_thread(server._run_cell, 'print(counter)', 'agent', 10)
                self.assertIn('[1] 2', check['stdout'])
                self.assertNotIn('[1] 3', check['stdout'])
        finally:
            http.shutdown()
            http.server_close()
            worker.join()

    async def test_http_cancel_is_served_while_r_is_running(self):
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-Internal-Secret": server.INTERNAL_SECRET}) as client:
            execution_id = 'c' * 24
            running = asyncio.create_task(client.post('/api/execute', json={
                'thread_id': 'cancel', 'execution_id': execution_id,
                'code': 'Sys.sleep(20); print("should not finish")',
            }))
            await asyncio.sleep(1)
            start = time.monotonic()
            response = await client.post('/api/cancel', json={'thread_id': 'cancel', 'execution_id': execution_id})
            self.assertEqual(response.status_code, 200)
            self.assertLess(time.monotonic() - start, 1)
            result = (await asyncio.wait_for(running, timeout=10)).json()
            self.assertTrue(result['cancelled'])

    async def test_errors_and_queued_cancellation_are_preserved(self):
        result = await asyncio.to_thread(server._run_cell, 'stop("expected test failure")', 'errors', 10)
        self.assertFalse(result['success'])
        self.assertIn('expected test failure', result['error'])
        execution_id = 'd' * 24
        flag = Path(server._cancel_path('queued', execution_id))
        flag.parent.mkdir(parents=True)
        flag.write_text('1')
        result = await asyncio.to_thread(server._run_cell, 'stop("must not execute")', 'queued', 10, execution_id)
        self.assertTrue(result['cancelled'])
        self.assertFalse(flag.exists())


if __name__ == '__main__':
    unittest.main()
