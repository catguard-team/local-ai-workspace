"""WireGuard listener and cookie boundary tests; only synthetic local state."""
import asyncio
from collections import defaultdict, deque
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bcrypt
from fastapi.testclient import TestClient

import gateway
import sandbox_agent as service
from sandbox_fs import SafeWorkspace
from vpn_access import (load_vpn_config, load_vpn_public_config, vpn_origin, verified_vpn_peer,
                        verified_public_client, canonical_client_ip, public_client_token,
                        TOKEN_HEADER, PEER_HEADER, RELAY_HEADER, PUBLIC_TOKEN_HEADER, PUBLIC_CLIENT_HEADER)

CONFIG = {"enabled": True, "bind_ip": "10.77.0.2", "peer_cidr": "10.77.0.0/24", "shared_token": "a" * 64}
VPN_HEADERS = {"host": "10.77.0.2:8787", "origin": "http://10.77.0.2:8787", TOKEN_HEADER: "a" * 64, PEER_HEADER: "10.77.0.3"}
PUBLIC_CONFIG = {"public_url": "https://ai.example.com", "relay_peer": "10.77.0.1", "relay_token": "b" * 64}
RELAY_HEADERS = {"Host": "ai.example.com", "Origin": "https://ai.example.com", RELAY_HEADER: "b" * 64,
                 "X-Real-IP": "203.0.113.42"}


class ConfigAndProxyTests(unittest.TestCase):
    def test_strict_private_configuration(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent / ".runtime", prefix="vpn-config-test-") as tmp:
            path = Path(tmp) / "config.json"
            self.assertIsNone(load_vpn_config(path))
            path.write_text(json.dumps(CONFIG), encoding="utf-8")
            self.assertEqual(vpn_origin(load_vpn_config(path)), "http://10.77.0.2:8787")
            for change in ({"enabled": 1}, {"bind_ip": "127.0.0.1"}, {"bind_ip": "0.0.0.0"}, {"bind_ip": "8.8.8.8"}, {"peer_cidr": "0.0.0.0/0"}, {"peer_cidr": "10.77.0.2/24"}, {"bind_ip": "10.77.0.0"}, {"bind_ip": "10.77.0.255"}, {"shared_token": "bad"}, {"extra": "no"}):
                path.write_text(json.dumps({**CONFIG, **change}), encoding="utf-8")
                with self.assertRaises(ValueError, msg=str(list(change))):
                    load_vpn_config(path)

    def test_public_proxy_strips_internal_and_forwarded_ip_spoofing(self):
        malicious = {"Host": "example.trycloudflare.com", "CF-Connecting-IP": "10.77.0.3", "X-Forwarded-For": "10.77.0.3", "X-Real-IP": "10.77.0.3", "Forwarded": "for=10.77.0.3", TOKEN_HEADER: CONFIG["shared_token"], PEER_HEADER: "10.77.0.3", "x-localai-vpn-extra": "bad", RELAY_HEADER: PUBLIC_CONFIG["relay_token"], "X-LocalAI-Relay-Extra": "spoof"}
        malicious.update({PUBLIC_TOKEN_HEADER: "c" * 64, PUBLIC_CLIENT_HEADER: "1.1.1.1", "X-LocalAI-Public-Extra": "spoof"})
        result = gateway.proxy_headers(malicious, "127.0.0.1", "127.0.0.1", CONFIG)
        self.assertEqual(result, {"Host": "example.trycloudflare.com"})

    def test_public_relay_configuration_is_exact_and_private(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent / ".runtime", prefix="vpn-config-test-") as tmp:
            path = Path(tmp) / "public.json"
            self.assertIsNone(load_vpn_public_config(path, CONFIG))
            path.write_text(json.dumps(PUBLIC_CONFIG), encoding="utf-8")
            self.assertEqual(load_vpn_public_config(path, CONFIG), PUBLIC_CONFIG)
            with self.assertRaises(ValueError):
                load_vpn_public_config(path, {**CONFIG, "enabled": False})
            for change in ({"public_url": "http://ai.example.com"}, {"public_url": "https://ai.example.com/"}, {"public_url": "https://ai.example.com:443"}, {"public_url": "https://user@ai.example.com"}, {"public_url": "https://127.0.0.1"}, {"public_url": "https://localhost"}, {"public_url": "https://ai.example.com?q=1"}, {"public_url": "https://ai.example.com/#part"}, {"relay_peer": "192.168.1.1"}, {"relay_peer": CONFIG["bind_ip"]}, {"relay_peer": "10.77.0.0"}, {"relay_peer": "10.77.0.255"}, {"relay_token": "invalid"}, {"extra": "no"}):
                path.write_text(json.dumps({**PUBLIC_CONFIG, **change}), encoding="utf-8")
                with self.assertRaises(ValueError, msg=str(list(change))):
                    load_vpn_public_config(path, CONFIG)

    def test_public_relay_requires_its_peer_token_host_and_https_origin(self):
        spoofed = {**RELAY_HEADERS, TOKEN_HEADER: CONFIG["shared_token"], PEER_HEADER: "10.77.0.99", "CF-Connecting-IP": "10.77.0.3", PUBLIC_TOKEN_HEADER: "c" * 64, PUBLIC_CLIENT_HEADER: "1.1.1.1"}
        result = gateway.proxy_headers(spoofed, CONFIG["bind_ip"], PUBLIC_CONFIG["relay_peer"], CONFIG, vpn_only=True, public_config=PUBLIC_CONFIG)
        self.assertEqual(result, {"Host": "ai.example.com", "Origin": "https://ai.example.com",
                                 PUBLIC_CLIENT_HEADER: RELAY_HEADERS["X-Real-IP"],
                                 PUBLIC_TOKEN_HEADER: public_client_token(RELAY_HEADERS["X-Real-IP"], CONFIG, PUBLIC_CONFIG)})
        self.assertNotEqual(result[PUBLIC_TOKEN_HEADER], CONFIG["shared_token"])
        self.assertIsNone(verified_vpn_peer(result, "127.0.0.1", "http", CONFIG))
        self.assertEqual(verified_public_client(result, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG), RELAY_HEADERS["X-Real-IP"])
        for peer, headers in [("10.77.0.3", RELAY_HEADERS), ("8.8.8.8", RELAY_HEADERS), (PUBLIC_CONFIG["relay_peer"], {**RELAY_HEADERS, RELAY_HEADER: "a" * 64}), (PUBLIC_CONFIG["relay_peer"], {**RELAY_HEADERS, RELAY_HEADER: "한" * 64}), (PUBLIC_CONFIG["relay_peer"], {"Host": "ai.example.com"}), (PUBLIC_CONFIG["relay_peer"], {**RELAY_HEADERS, "Host": "ai.example.com:443"}), (PUBLIC_CONFIG["relay_peer"], {**RELAY_HEADERS, "Origin": "http://ai.example.com"})]:
            with self.assertRaises(PermissionError):
                gateway.proxy_headers(headers, CONFIG["bind_ip"], peer, CONFIG, public_config=PUBLIC_CONFIG)
        with self.assertRaises(PermissionError):
            gateway.proxy_headers(RELAY_HEADERS, "127.0.0.1", "127.0.0.1", CONFIG, vpn_only=True, public_config=PUBLIC_CONFIG)

    def test_public_client_ip_requires_one_canonical_ipv4_or_ipv6(self):
        for address in ("203.0.113.42", "2001:db8::42"):
            self.assertEqual(canonical_client_ip(address), address)
            result = gateway.proxy_headers({**RELAY_HEADERS, "X-Real-IP": address}, CONFIG["bind_ip"],
                                           PUBLIC_CONFIG["relay_peer"], CONFIG, public_config=PUBLIC_CONFIG)
            self.assertEqual(verified_public_client(result, "::1", "http", CONFIG, PUBLIC_CONFIG), address)
        for address in (None, "", " 203.0.113.42", "203.0.113.42 ", "203.0.113.42, 1.1.1.1", "203.0.113.42:1234",
                        "203.000.113.42", "[2001:db8::42]", "2001:DB8::42", "2001:db8:0:0:0:0:0:42", "fe80::1%eth0"):
            headers = {k: v for k, v in RELAY_HEADERS.items() if k != "X-Real-IP"}
            if address is not None:
                headers["X-Real-IP"] = address
            with self.assertRaises(PermissionError, msg=repr(address)):
                gateway.proxy_headers(headers, CONFIG["bind_ip"], PUBLIC_CONFIG["relay_peer"], CONFIG, public_config=PUBLIC_CONFIG)

    def test_backend_public_assertion_is_bound_to_client_origin_and_transport(self):
        headers = gateway.proxy_headers(RELAY_HEADERS, CONFIG["bind_ip"], PUBLIC_CONFIG["relay_peer"], CONFIG, public_config=PUBLIC_CONFIG)
        self.assertIsNone(verified_public_client({"Host": "other.example.com"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG))
        self.assertEqual(verified_public_client({k: v for k, v in headers.items() if k != "Origin"},
                                               "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG), RELAY_HEADERS["X-Real-IP"])
        for changed, client, scheme, config, public_config in [
            ({"Host": "ai.example.com"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, PUBLIC_TOKEN_HEADER: CONFIG["shared_token"]}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, PUBLIC_TOKEN_HEADER: "한" * 64}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, PUBLIC_CLIENT_HEADER: "203.0.113.43"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, PUBLIC_CLIENT_HEADER: "invalid"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, "Host": "other.example.com"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, "Origin": "http://ai.example.com"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, "x-localai-public-extra": "x"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, TOKEN_HEADER: CONFIG["shared_token"], PEER_HEADER: "10.77.0.3"}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            ({**headers, RELAY_HEADER: PUBLIC_CONFIG["relay_token"]}, "127.0.0.1", "http", CONFIG, PUBLIC_CONFIG),
            (headers, "10.77.0.1", "http", CONFIG, PUBLIC_CONFIG),
            (headers, "127.0.0.1", "https", CONFIG, PUBLIC_CONFIG),
            (headers, "127.0.0.1", "http", None, PUBLIC_CONFIG),
            (headers, "127.0.0.1", "http", {**CONFIG, "enabled": False}, PUBLIC_CONFIG),
            (headers, "127.0.0.1", "http", CONFIG, None),
        ]:
            with self.assertRaises(PermissionError):
                verified_public_client(changed, client, scheme, config, public_config)

    def test_vpn_proxy_uses_real_socket_peer_and_exact_origin(self):
        headers = {"Host": "10.77.0.2:8787", "Origin": "http://10.77.0.2:8787", TOKEN_HEADER: "b" * 64, PEER_HEADER: "10.77.0.99", "CF-Connecting-IP": "8.8.8.8", PUBLIC_TOKEN_HEADER: "c" * 64, PUBLIC_CLIENT_HEADER: "1.1.1.1"}
        result = gateway.proxy_headers(headers, "10.77.0.2", "10.77.0.3", CONFIG)
        self.assertEqual(result[TOKEN_HEADER], CONFIG["shared_token"])
        self.assertEqual(result[PEER_HEADER], "10.77.0.3")
        self.assertNotIn("CF-Connecting-IP", result)
        self.assertNotIn(PUBLIC_TOKEN_HEADER, result)
        self.assertNotIn(PUBLIC_CLIENT_HEADER, result)
        for local, peer, bad_headers in [("0.0.0.0", "10.77.0.3", headers), ("192.168.1.2", "10.77.0.3", headers), ("10.77.0.2", "192.168.1.10", headers), ("10.77.0.2", "10.77.0.3", {**headers, "Host": "evil.example"}), ("10.77.0.2", "10.77.0.3", {**headers, "Origin": "https://10.77.0.2:8787"})]:
            with self.assertRaises(PermissionError):
                gateway.proxy_headers(bad_headers, local, peer, CONFIG)

    def test_vpn_only_mode_blocks_old_tunnel_but_keeps_local_health(self):
        with self.assertRaises(PermissionError):
            gateway.proxy_headers({"Host": "old.trycloudflare.com"}, "127.0.0.1", "127.0.0.1", CONFIG, vpn_only=True)
        self.assertEqual(gateway.proxy_headers({"Host": "127.0.0.1:8787"}, "127.0.0.1", "127.0.0.1", CONFIG, vpn_only=True), {"Host": "127.0.0.1:8787"})

    def test_backend_requires_attestation_and_exact_transport(self):
        self.assertEqual(verified_vpn_peer(VPN_HEADERS, "127.0.0.1", "http", CONFIG), "10.77.0.3")
        self.assertIsNone(verified_vpn_peer({"host": "public.example"}, "127.0.0.1", "http", CONFIG))
        for headers, client, scheme, config in [
            ({"host": "10.77.0.2:8787"}, "127.0.0.1", "http", CONFIG),
            ({**VPN_HEADERS, TOKEN_HEADER: "b" * 64}, "127.0.0.1", "http", CONFIG),
            ({**VPN_HEADERS, TOKEN_HEADER: "한" * 64}, "127.0.0.1", "http", CONFIG),
            ({**VPN_HEADERS, "host": "public.example"}, "127.0.0.1", "http", CONFIG),
            ({**VPN_HEADERS, "origin": "https://10.77.0.2:8787"}, "127.0.0.1", "http", CONFIG),
            ({**VPN_HEADERS, PEER_HEADER: "1.1.1.1"}, "127.0.0.1", "http", CONFIG),
            ({**VPN_HEADERS, "x-localai-vpn-extra": "x"}, "127.0.0.1", "http", CONFIG),
            ({"host": "ai.example.com", RELAY_HEADER: PUBLIC_CONFIG["relay_token"]}, "127.0.0.1", "http", CONFIG),
            (VPN_HEADERS, "10.77.0.3", "http", CONFIG),
            (VPN_HEADERS, "127.0.0.1", "https", CONFIG),
            (VPN_HEADERS, "127.0.0.1", "http", None),
            (VPN_HEADERS, "127.0.0.1", "http", {**CONFIG, "enabled": False}),
        ]:
            with self.assertRaises(PermissionError):
                verified_vpn_peer(headers, client, scheme, config)

    def test_delayed_wireguard_binding_retries_only_exact_ip(self):
        calls = []
        class Site:
            def __init__(self, runner, host, port):
                calls.append((host, port))
            async def start(self):
                if len(calls) == 1:
                    raise OSError("interface unavailable")
            async def stop(self):
                pass
        with patch.object(gateway.web, "TCPSite", Site):
            result = asyncio.run(gateway.bind_vpn_when_ready(object(), CONFIG, retry_interval=0))
        self.assertIsInstance(result, Site)
        self.assertEqual(calls, [("10.77.0.2", 8787), ("10.77.0.2", 8787)])


class VpnLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / ".runtime", prefix="vpn-api-test-")
        self.root = Path(self.temp.name)
        state = self.root / "state"
        state.mkdir()
        root = self.root / "agent"
        root.mkdir()
        database = state / "app.db"
        with sqlite3.connect(database) as con:
            con.executescript("CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); INSERT INTO metadata VALUES('cptr_imported','fixture'); INSERT INTO metadata VALUES('webui_imported','fixture');")
            con.execute("INSERT INTO users VALUES(?,?,?)", ("fixture", "vpn_test", bcrypt.hashpw(b"vpn-fixture-password", bcrypt.gensalt(rounds=4)).decode()))
        con.close()
        self.patchers = [patch.object(service, "STATE", state), patch.object(service, "DB", database), patch.object(service, "UPLOADS", state / "uploads"), patch.object(service, "ROOT", root), patch.object(service, "workspace", SafeWorkspace(root)), patch.object(service, "jobs", {}), patch.object(service, "login_attempts", defaultdict(deque)), patch.object(service, "load_vpn_config", return_value=CONFIG), patch.object(service, "load_vpn_public_config", return_value=PUBLIC_CONFIG)]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(service.app, base_url="http://10.77.0.2:8787", client=("127.0.0.1", 12345))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_http_vpn_login_cookie_and_persistent_session(self):
        headers = {**VPN_HEADERS, "x-sandbox-request": "1"}
        response = self.client.post("/api/login", json={"username": "vpn_test", "password": "vpn-fixture-password"}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertNotIn("; secure", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(self.client.get("/api/me", headers=VPN_HEADERS).json()["username"], "vpn_test")
        self.assertIn("10.77.0.3", service.login_attempts)
        forged = {**headers, "origin": "http://attacker.example"}
        self.assertEqual(self.client.post("/api/logout", headers=forged).status_code, 403)

    def test_unattested_private_host_and_bad_token_are_rejected(self):
        self.assertEqual(self.client.get("/api/health").status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={**VPN_HEADERS, TOKEN_HEADER: "b" * 64}).status_code, 403)

    def test_public_https_cookie_remains_secure(self):
        with TestClient(service.app, base_url="https://example.trycloudflare.com", client=("127.0.0.1", 12345)) as public:
            response = public.post("/api/login", json={"username": "vpn_test", "password": "vpn-fixture-password"}, headers={"x-sandbox-request": "1", "origin": "https://example.trycloudflare.com"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("; secure", response.headers["set-cookie"].lower())
            self.assertEqual(public.get("/api/me").status_code, 200)

    def test_https_relay_over_private_http_keeps_secure_cookie(self):
        headers = gateway.proxy_headers({**RELAY_HEADERS, "x-sandbox-request": "1"}, CONFIG["bind_ip"], PUBLIC_CONFIG["relay_peer"], CONFIG, vpn_only=True, public_config=PUBLIC_CONFIG)
        with TestClient(service.app, base_url="http://ai.example.com", client=("127.0.0.1", 12345)) as public:
            response = public.post("/api/login", json={"username": "vpn_test", "password": "vpn-fixture-password"}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            cookie = response.headers["set-cookie"]
            self.assertIn("; secure", cookie.lower())
            # The real browser sends the Secure cookie over HTTPS to the AWS relay.
            browser_headers = {**headers, "cookie": cookie.split(";", 1)[0]}
            self.assertEqual(public.get("/api/me", headers=browser_headers).json()["username"], "vpn_test")
            self.assertEqual(public.post("/api/logout", headers={**browser_headers, "Origin": "https://attacker.example"}).status_code, 403)
            self.assertIn(RELAY_HEADERS["X-Real-IP"], service.login_attempts)
            self.assertNotIn("127.0.0.1", service.login_attempts)

    def test_public_relay_login_rate_limit_is_per_authenticated_client(self):
        first = gateway.proxy_headers({**RELAY_HEADERS, "x-sandbox-request": "1"}, CONFIG["bind_ip"], PUBLIC_CONFIG["relay_peer"], CONFIG, public_config=PUBLIC_CONFIG)
        second = gateway.proxy_headers({**RELAY_HEADERS, "x-sandbox-request": "1", "X-Real-IP": "2001:db8::42"}, CONFIG["bind_ip"], PUBLIC_CONFIG["relay_peer"], CONFIG, public_config=PUBLIC_CONFIG)
        with TestClient(service.app, base_url="http://ai.example.com", client=("127.0.0.1", 12345)) as public:
            bad = {"username": "vpn_test", "password": "wrong-password"}
            for _ in range(15):
                self.assertEqual(public.post("/api/login", json=bad, headers=first).status_code, 401)
            self.assertEqual(public.post("/api/login", json=bad, headers=first).status_code, 429)
            # Client-supplied proxy headers cannot escape the authenticated rate key.
            self.assertEqual(public.post("/api/login", json=bad, headers={**first, "x-forwarded-for": "1.1.1.1"}).status_code, 429)
            response = public.post("/api/login", json={"username": "vpn_test", "password": "vpn-fixture-password"}, headers=second)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("; secure", response.headers["set-cookie"].lower())
            self.assertEqual(set(service.login_attempts), {"203.0.113.42", "2001:db8::42"})
            self.assertEqual(public.get("/api/health").status_code, 403)
            self.assertEqual(public.get("/api/health", headers={**first, PUBLIC_CLIENT_HEADER: "203.0.113.43"}).status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
