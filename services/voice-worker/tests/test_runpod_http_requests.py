from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from http_headers import AUREN_USER_AGENT, build_http_headers


SCRIPTS = Path(__file__).resolve().parents[3] / "infra" / "runpod" / "scripts"


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health_server = load_script("health_server")
bootstrap_models = load_script("bootstrap_models")


class FakeResponse:
    status = 200

    def __init__(self, body: bytes = b"{}") -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class RunPodHttpRequestTests(unittest.TestCase):
    def assert_standard_headers(self, request, *, api_key: str | None = None) -> None:
        self.assertEqual(request.get_header("User-agent"), AUREN_USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")
        expected_auth = f"Bearer {api_key}" if api_key else None
        self.assertEqual(request.get_header("Authorization"), expected_auth)

    def test_header_builder_omits_local_placeholder_credentials(self) -> None:
        headers = build_http_headers(api_key="local")

        self.assertEqual(headers["User-Agent"], AUREN_USER_AGENT)
        self.assertEqual(headers["Accept"], "application/json")
        self.assertNotIn("Authorization", headers)

    def test_readiness_probe_identifies_itself_to_remote_proxy(self) -> None:
        with patch.object(
            health_server,
            "urlopen",
            return_value=FakeResponse(),
        ) as mocked_urlopen:
            ok, detail = health_server.http_ok(
                "https://example.test/health",
                api_key="provider-secret",
            )

        self.assertTrue(ok)
        self.assertEqual(detail, "http_200")
        self.assert_standard_headers(
            mocked_urlopen.call_args.args[0],
            api_key="provider-secret",
        )

    def test_readiness_probe_reports_upstream_status_code(self) -> None:
        error = HTTPError(
            "https://example.test/health",
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )
        with patch.object(health_server, "urlopen", side_effect=error):
            ok, detail = health_server.http_ok("https://example.test/health")

        self.assertFalse(ok)
        self.assertEqual(detail, "http_403")

    def test_bootstrap_probe_identifies_itself_to_remote_proxy(self) -> None:
        with patch.object(
            bootstrap_models,
            "urlopen",
            return_value=FakeResponse(),
        ) as mocked_urlopen:
            bootstrap_models.wait_for_json(
                "https://example.test/health",
                timeout=1,
                api_key="provider-secret",
            )

        self.assert_standard_headers(
            mocked_urlopen.call_args.args[0],
            api_key="provider-secret",
        )

    def test_bootstrap_warmup_uses_standard_headers(self) -> None:
        config = SimpleNamespace(
            provider="qwen",
            model="Qwen/Qwen3-ASR-1.7B",
            language="",
            api_key="provider-secret",
            base_url="https://example.test/v1",
        )
        with (
            patch.object(bootstrap_models, "STT_CONFIG", config),
            patch.object(
                bootstrap_models,
                "urlopen",
                return_value=FakeResponse(b'{"text":"warm"}'),
            ) as mocked_urlopen,
        ):
            bootstrap_models.warmup_stt()

        request = mocked_urlopen.call_args.args[0]
        self.assert_standard_headers(request, api_key="provider-secret")
        self.assertTrue(
            request.get_header("Content-type").startswith("multipart/form-data;")
        )


if __name__ == "__main__":
    unittest.main()
