"""Real R execution; no model API calls."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from engine import server
from engine.kernel import note_kernel


class ExecutionLatencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob3-latency-test-")
        self.patches = [patch.object(server, "PROJECTS_DIR", self.temp.name),
                        patch.object(server, "INTERNAL_SECRET", "test")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for path, handle in list(note_kernel._kernels.items()):
            if path.startswith(self.temp.name):
                note_kernel.kill_kernel(handle)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def test_the_engines_own_tool_runs_r_once(self):
        result = await server.execute_r_cell('counter <- 1; print(counter)', 'agent')
        self.assertIn('[1] 1', result)
        self.assertNotIn('noteCell', result)
        check = await asyncio.to_thread(server._run_cell, 'counter <- counter + 1; print(counter)', 'agent', 10)
        self.assertIn('[1] 2', check['stdout'])

    async def test_http_cancel_is_served_while_r_is_running(self):
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-Internal-Secret": "test"}) as client:
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

    async def test_output_and_phase_can_be_read_while_r_is_running(self):
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-Internal-Secret": "test"}) as client:
            execution_id = 'e' * 24
            response = await client.post('/api/execute', json={
                'thread_id': 'live', 'execution_id': execution_id, 'wait': False,
                'code': 'cat("one\\n"); Sys.sleep(4); cat("two\\n")',
            })
            self.assertEqual(response.status_code, 202)
            url = f'/api/executions/{execution_id}?thread_id=live'
            phases, status = [], {}
            for _ in range(300):
                status = (await client.get(url)).json()
                if not phases or phases[-1] != status['phase']:
                    phases.append(status['phase'])
                if status['output'] or status['done']:
                    break
                await asyncio.sleep(0.1)
            self.assertEqual(status['phase'], 'running')
            self.assertEqual(status['output'], 'one\n')
            self.assertIn('starting_r', phases)
            for _ in range(100):
                status = (await client.get(f"{url}&offset={status['next_offset']}")).json()
                if status['done']:
                    break
                await asyncio.sleep(0.1)
            self.assertEqual(status['phase'], 'done')
            self.assertEqual(status['result']['stdout'], 'one\ntwo\n')

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
