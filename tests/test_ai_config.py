import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_config import (
    clear_llm_config,
    config_status,
    parse_config_payload,
    save_llm_config,
    validate_llm_config,
)


class AIConfigTests(unittest.TestCase):
    def test_parses_deepseek_env(self):
        config = parse_config_payload({
            "provider": "auto",
            "configText": "DEEPSEEK_API_KEY=sk-deep\nDEEPSEEK_MODEL=deepseek-v4-flash",
        })
        self.assertEqual(config["provider"], "deepseek")
        self.assertEqual(config["apiKey"], "sk-deep")
        self.assertEqual(config["model"], "deepseek-v4-flash")

    def test_parses_qwen_json(self):
        config = parse_config_payload({
            "provider": "auto",
            "configText": json.dumps({
                "api_key": "sk-qwen",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3.7-plus",
            }),
        })
        self.assertEqual(config["provider"], "qwen")
        self.assertEqual(config["apiKey"], "sk-qwen")

    def test_parses_bearer_header(self):
        config = parse_config_payload({
            "provider": "deepseek",
            "configText": "Authorization: Bearer sk-header",
        })
        self.assertEqual(config["apiKey"], "sk-header")

    def test_rejects_remote_plain_http(self):
        with self.assertRaises(ValueError):
            parse_config_payload({
                "provider": "custom",
                "configText": "sk-test",
                "baseUrl": "http://example.com/v1",
                "model": "example",
            })

    def test_save_status_and_clear_do_not_expose_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env.local"
            path.write_text("UNRELATED=value\n", encoding="utf-8")
            config = parse_config_payload({"provider": "qwen", "configText": "sk-secret"})
            save_llm_config(config, path)
            status = config_status(path)
            self.assertTrue(status["configured"])
            self.assertNotIn("apiKey", status)
            self.assertNotIn("sk-secret", json.dumps(status))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            clear_llm_config(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "UNRELATED=value\n")

    def test_validation_uses_minimal_chat_request(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"OK"}}]}'

        config = parse_config_payload({"provider": "deepseek", "configText": "sk-test-key"})
        with patch("ai_config.urllib.request.urlopen", return_value=Response()) as mocked:
            ok, _ = validate_llm_config(config)
        self.assertTrue(ok)
        request = mocked.call_args.args[0]
        self.assertIn("/chat/completions", request.full_url)
        self.assertNotIn("sk-test-key", request.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
