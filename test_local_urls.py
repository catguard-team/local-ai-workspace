"""URL-selection tests use only an isolated temporary STATE directory."""
import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import local_urls


class LocalUrlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local-ai-url-test-")
        self.state = Path(self.temp.name).resolve()
        self.original_parent = self.state.parent
        self.state_patch = patch.object(local_urls, "STATE", self.state)
        self.state_patch.start()
        self.config = {"provider": "cloudflare", "public_url": "https://ai.example.com",
                       "tunnel_id": "9fb893b1-7fab-4ebf-ae4d-2b144acb1cc2"}

    def tearDown(self):
        self.state_patch.stop()
        # Verify the exact test-owned directory before native recursive cleanup.
        assert Path(self.temp.name).resolve() == self.state
        assert self.state.parent == self.original_parent
        self.temp.cleanup()

    def write_config(self, value):
        (self.state / "fixed-access.json").write_text(json.dumps(value), encoding="utf-8")

    def quick_log(self):
        (self.state / "tunnel-error.log").write_text(
            "old https://old-example.trycloudflare.com\n"
            "new https://current-example.trycloudflare.com\n", encoding="utf-8")

    def write_vpn(self, *, enabled=True):
        value = {"enabled": enabled, "bind_ip": "10.77.0.2", "peer_cidr": "10.77.0.0/24",
                 "shared_token": "ab" * 32}
        (self.state / "vpn-access.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def write_vpn_public(self):
        value = {"public_url": "https://ai.example.com", "relay_peer": "10.77.0.1",
                 "relay_token": "cd" * 32}
        (self.state / "vpn-public.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def test_unconfigured_uses_last_quick_url_and_empty_returns_none(self):
        self.assertIsNone(local_urls.fixed_access())
        self.assertIsNone(local_urls.public_url())
        self.quick_log()
        self.assertEqual(local_urls.public_url(), "https://current-example.trycloudflare.com")

    def test_fixed_address_wins_without_hiding_raw_quick_urls(self):
        self.quick_log()
        self.write_config(self.config)
        (self.state / "computer-tunnel.log").write_text(
            "https://legacy-agent.trycloudflare.com", encoding="utf-8")
        self.assertEqual(local_urls.fixed_access(), self.config)
        self.assertEqual(local_urls.public_url(), "https://ai.example.com")
        self.assertEqual(local_urls.tunnel_url(), "https://current-example.trycloudflare.com")
        self.assertEqual(local_urls.tunnel_url("computer-tunnel"), "https://legacy-agent.trycloudflare.com")

    def test_exact_schema_and_provider_are_required(self):
        for value in (None, [], "cloudflare", {},
                      {**self.config, "provider": "ngrok"},
                      {**self.config, "credentials_file": "private.json"},
                      {key: val for key, val in self.config.items() if key != "tunnel_id"}):
            with self.subTest(value_type=type(value).__name__):
                self.write_config(value)
                with self.assertRaises(ValueError):
                    local_urls.fixed_access()

    def test_noncanonical_or_unsafe_urls_are_rejected(self):
        invalid = [None, 1, "http://ai.example.com", "HTTPS://ai.example.com",
                   "https://AI.example.com", "https://user@ai.example.com",
                   "https://user:password@ai.example.com", "https://ai.example.com:443",
                   "https://ai.example.com/", "https://ai.example.com/chat",
                   "https://ai.example.com?key=value", "https://ai.example.com#part",
                   "https://localhost", "https://127.0.0.1", "https://[::1]",
                   "https://bad_host.example.com", "https://-bad.example.com",
                   "https://bad-.example.com", "https://ai..example.com",
                   "https://ai.example.com.", "https://ai.example.com\\evil",
                   "https://ai.example.com\n", " https://ai.example.com",
                   "https://한글.example.com", "https://" + "a" * 64 + ".example.com"]
        for address in invalid:
            with self.subTest(address=address):
                self.write_config({**self.config, "public_url": address})
                with self.assertRaises(ValueError):
                    local_urls.fixed_access()

    def test_uuid_must_use_canonical_lowercase_hyphenated_form(self):
        for tunnel_id in (None, 12, "", "not-a-uuid", self.config["tunnel_id"].upper(),
                          self.config["tunnel_id"].replace("-", ""),
                          "{" + self.config["tunnel_id"] + "}"):
            with self.subTest(tunnel_id=tunnel_id):
                self.write_config({**self.config, "tunnel_id": tunnel_id})
                with self.assertRaises(ValueError):
                    local_urls.fixed_access()

    def test_existing_malformed_file_never_falls_back_to_quick_url(self):
        self.quick_log()
        for source in ("{", "{\"provider\":\"cloudflare\",\"provider\":\"cloudflare\"}"):
            (self.state / "fixed-access.json").write_text(source, encoding="utf-8")
            with self.assertRaises(ValueError):
                local_urls.public_url()
        (self.state / "fixed-access.json").write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            local_urls.public_url()
        (self.state / "fixed-access.json").unlink()
        (self.state / "fixed-access.json").mkdir()
        with self.assertRaises(ValueError):
            local_urls.public_url()

    def test_staged_vpn_does_not_change_existing_published_address(self):
        self.quick_log()
        self.write_vpn()
        self.assertIsNone(local_urls.vpn_only_access())
        self.assertEqual(local_urls.public_url(), "https://current-example.trycloudflare.com")
        self.write_config(self.config)
        self.assertEqual(local_urls.public_url(), "https://ai.example.com")

    def test_explicit_vpn_only_wins_without_exposing_shared_token(self):
        self.quick_log()
        self.write_config(self.config)
        vpn = self.write_vpn()
        (self.state / "vpn-only").touch()
        selected = local_urls.vpn_only_access()
        self.assertEqual(selected["provider"], "wireguard")
        self.assertFalse(selected["public_gateway"])
        self.assertEqual(local_urls.public_url(), "http://10.77.0.2:8787")
        self.assertNotIn("shared_token", selected)
        self.assertNotIn(vpn["shared_token"], json.dumps(selected))
        self.assertEqual(local_urls.tunnel_url(), "https://current-example.trycloudflare.com")
        # An unused historical provider cannot override explicitly selected VPN access.
        (self.state / "fixed-access.json").write_text("malformed", encoding="utf-8")
        self.assertEqual(local_urls.public_url(), "http://10.77.0.2:8787")

    def test_staged_public_relay_does_not_activate_fixed_address(self):
        self.quick_log()
        self.write_vpn()
        self.write_vpn_public()
        self.assertEqual(local_urls.public_url(), "https://current-example.trycloudflare.com")
        self.assertIsNone(local_urls.vpn_only_access())

    def test_vpn_public_gateway_prefers_https_without_exposing_relay_token(self):
        vpn = self.write_vpn()
        relay = self.write_vpn_public()
        (self.state / "vpn-only").touch()
        selected = local_urls.vpn_only_access()
        self.assertTrue(selected["public_gateway"])
        self.assertEqual(local_urls.public_url(), "https://ai.example.com")
        self.assertEqual(selected["bind_ip"], "10.77.0.2")
        for key in ("shared_token", "relay_token", "relay_peer"):
            self.assertNotIn(key, selected)
        self.assertNotIn(vpn["shared_token"], json.dumps(selected))
        self.assertNotIn(relay["relay_token"], json.dumps(selected))

    def test_invalid_selected_relay_does_not_fall_back_to_direct_or_public(self):
        self.quick_log()
        self.write_vpn()
        self.write_config(self.config)
        (self.state / "vpn-only").touch()
        relay = self.write_vpn_public()
        for field, value in (("public_url", "http://ai.example.com"),
                             ("relay_peer", "10.78.0.1"), ("relay_token", "invalid")):
            with self.subTest(field=field):
                (self.state / "vpn-public.json").write_text(json.dumps({**relay, field: value}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    local_urls.public_url()

    def test_vpn_only_requires_enabled_valid_config_and_never_falls_back(self):
        self.quick_log()
        self.write_config(self.config)
        (self.state / "vpn-only").touch()
        with self.assertRaises(ValueError):
            local_urls.public_url()
        self.write_vpn(enabled=False)
        with self.assertRaises(ValueError):
            local_urls.public_url()
        (self.state / "vpn-access.json").write_text("malformed", encoding="utf-8")
        with self.assertRaises(ValueError):
            local_urls.public_url()
        vpn = self.write_vpn()
        vpn["bind_ip"] = "0.0.0.0"
        (self.state / "vpn-access.json").write_text(json.dumps(vpn), encoding="utf-8")
        with self.assertRaises(ValueError):
            local_urls.public_url()

    def test_vpn_only_marker_must_be_a_file(self):
        self.quick_log()
        self.write_vpn()
        (self.state / "vpn-only").mkdir()
        with self.assertRaises(ValueError):
            local_urls.public_url()

    def test_address_text_labels_vpn_http_and_omits_credentials(self):
        script = Path(__file__).with_name("write-access.py")
        spec = importlib.util.spec_from_file_location("address_writer_test", script)
        writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(writer)
        text = writer.access_text(self.state, "http://10.77.0.2:8787", vpn_only=True)
        self.assertIn("WireGuard", text)
        self.assertIn("VPN 전용 고정 주소", text)
        self.assertIn("http://10.77.0.2:8787", text)
        self.assertNotIn("HTTPS", text)
        self.assertNotIn("shared_token", text)
        self.assertFalse((self.state / "접속주소.txt").exists())
        public_text = writer.access_text(self.state, "https://ai.example.com", vpn_only=True, public_gateway=True)
        self.assertIn("고정 HTTPS 주소", public_text)
        self.assertIn("브라우저로 접속", public_text)
        self.assertIn("VPN을 설치할 필요가 없습니다", public_text)
        self.assertNotIn("WireGuard를 먼저 연결", public_text)
        self.assertNotIn("relay_token", public_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
