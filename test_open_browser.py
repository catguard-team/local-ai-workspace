import io
import subprocess
import unittest
from unittest.mock import patch

import httpx

import open_browser as browser


class BrowserStartupTests(unittest.TestCase):
    def response(self, request):
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status":"ok", "mode":"sandbox-files-only", "model":browser.MODEL})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data":[{"id":browser.MODEL}]})
        return httpx.Response(200, text='<form id="login-form"></form>', headers={"content-type":"text/html"})

    def loaded(self):
        return subprocess.CompletedProcess([], 0, '[{"identifier":"qwen3.8-27b"}]')

    def test_checks_model_and_selected_https_page(self):
        seen = []
        def respond(request):
            seen.append(str(request.url))
            return self.response(request)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client, \
             patch.object(browser.subprocess, "run", return_value=self.loaded()) as cli:
            self.assertTrue(browser.ready(client, "https://ai.example.com"))
        self.assertIn("http://127.0.0.1:8081/api/health", seen)
        self.assertIn("http://127.0.0.1:8787/api/health", seen)
        self.assertIn("https://ai.example.com/api/health", seen)
        self.assertIn("https://ai.example.com/", seen)
        cli.assert_called_once()
        self.assertEqual(cli.call_args.args[0], [str(browser.LMS), "ps", "--json"])

    def test_downloaded_but_unloaded_model_is_not_ready(self):
        with httpx.Client(transport=httpx.MockTransport(self.response)) as client, \
             patch.object(browser.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '[]')):
            self.assertFalse(browser.ready(client, browser.LOCAL_URL))

    def test_wrong_service_and_non_login_page_are_rejected(self):
        for bad_path in ("/api/health", "/"):
            def respond(request):
                if request.url.path == bad_path:
                    return httpx.Response(200, json={"status":"ok"})
                return self.response(request)
            with self.subTest(path=bad_path), httpx.Client(transport=httpx.MockTransport(respond)) as client, \
                 patch.object(browser.subprocess, "run", return_value=self.loaded()):
                self.assertFalse(browser.ready(client, browser.LOCAL_URL))

    def test_waits_for_temporary_outage_before_opening(self):
        with patch.object(browser, "public_url", return_value="https://ai.example.com"), \
             patch.object(browser, "ready", side_effect=[httpx.ConnectError("starting"), False, True]) as probe, \
             patch.object(browser.time, "sleep"), patch.object(browser.os, "startfile") as launch, \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(browser.main(), 0)
            self.assertEqual(probe.call_count, 3)
        launch.assert_called_once_with("https://ai.example.com/")

    def test_unconfigured_installation_uses_loopback(self):
        with patch.object(browser, "public_url", return_value=None), patch.object(browser, "ready", return_value=True):
            self.assertEqual(browser.wait_until_ready(), browser.LOCAL_URL + "/")

    def test_unready_server_does_not_open_browser(self):
        with patch.object(browser, "wait_until_ready", side_effect=RuntimeError("not ready")), \
             patch.object(browser.os, "startfile") as launch, \
             patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(browser.main(), 1)
        launch.assert_not_called()

    def test_tls_errors_timeout_without_opening_or_falling_back(self):
        with patch.object(browser, "public_url", return_value="https://ai.example.com"), \
             patch.object(browser, "ready", side_effect=httpx.ConnectError("certificate verify failed")), \
             patch.object(browser.os, "startfile") as launch:
            with self.assertRaisesRegex(RuntimeError, "https://ai.example.com"):
                browser.wait_until_ready(timeout=0)
        launch.assert_not_called()

    def test_browser_launch_error_returns_failure(self):
        with patch.object(browser, "wait_until_ready", return_value=browser.LOCAL_URL + "/"), \
             patch.object(browser.os, "startfile", side_effect=OSError("No browser")), \
             patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(browser.main(), 1)


if __name__ == "__main__":
    unittest.main()
