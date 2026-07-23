import json
import os
import tempfile
import unittest
from unittest.mock import patch

from webui import server


class WebUIEnvReloadTests(unittest.TestCase):
    def _env_file(self, value):
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        tmp.write(f"DYNAMIC_TEST_KEY={value}\n")
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        return tmp.name

    def test_child_env_uses_latest_dotenv_value_without_restart(self):
        path = self._env_file("new-value")
        with patch.object(server, "ENV_PATH", path):
            with patch.object(server, "BOOT_ENV", {}):
                with patch.dict(os.environ, {"DYNAMIC_TEST_KEY": "stale-value"}):
                    child = server._child_env()
        self.assertEqual(child["DYNAMIC_TEST_KEY"], "new-value")

    def test_explicit_startup_environment_keeps_precedence(self):
        path = self._env_file("dotenv-value")
        with patch.object(server, "ENV_PATH", path):
            with patch.object(server, "BOOT_ENV", {"DYNAMIC_TEST_KEY": "system-value"}):
                with patch.dict(os.environ, {"DYNAMIC_TEST_KEY": "system-value"}):
                    child = server._child_env()
        self.assertEqual(child["DYNAMIC_TEST_KEY"], "system-value")

    def test_status_exposes_loaded_version_and_process_id(self):
        with patch.object(server, "_fingerprint_provider", return_value="bitbrowser"):
            with patch.object(server, "_read_config_val", side_effect=lambda _key, default="": default):
                with patch.object(server, "_http_alive", return_value=True):
                    with patch.object(server, "_k12_alive", return_value=False):
                        with patch("common.proxy_switch.current_node", return_value="test-node"):
                            status = server.api_status()
        self.assertEqual(status["pid"], os.getpid())
        self.assertEqual(status["version"], server.WEBUI_VERSION)
        self.assertEqual(status["root"], server.ROOT)


class WebUIRunStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_event_exposes_exit_code_and_stop_state(self):
        run_id = "test-result-event"
        server.RUNS[run_id] = {
            "lines": ["finished"],
            "done": True,
            "returncode": 7,
            "stopped": False,
        }
        self.addCleanup(server.RUNS.pop, run_id, None)

        response = await server.api_logs(run_id)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        body = "".join(chunks)

        self.assertIn("event: done", body)
        payload = body.split("event: done\ndata: ", 1)[1].split("\n", 1)[0]
        self.assertEqual(
            json.loads(payload),
            {"returncode": 7, "stopped": False},
        )



if __name__ == "__main__":
    unittest.main()
