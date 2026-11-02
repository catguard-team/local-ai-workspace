"""Strict configuration and gateway attestation for HTTP inside WireGuard.

This is not a general trusted-proxy mechanism. Only the configured VPN listener
may attest a request, and the backend remains bound to loopback.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parent / ".runtime" / "vpn-access.json"
DEFAULT_PUBLIC_PATH = DEFAULT_PATH.with_name("vpn-public.json")
TOKEN_HEADER = "x-localai-vpn-token"
PEER_HEADER = "x-localai-vpn-peer"
INTERNAL_PREFIX = "x-localai-vpn-"
RELAY_HEADER = "x-localai-relay-token"
RELAY_PREFIX = "x-localai-relay-"
PUBLIC_TOKEN_HEADER = "x-localai-public-token"
PUBLIC_CLIENT_HEADER = "x-localai-public-client"
PUBLIC_PREFIX = "x-localai-public-"
_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate VPN configuration field")
        result[key] = value
    return result


def load_vpn_config(path: Path | None = None) -> dict | None:
    """Return the exact validated config; missing is disabled, malformed fails."""
    path = Path(path) if path is not None else DEFAULT_PATH
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("VPN configuration points to a missing file") from None
        return None
    except (OSError, UnicodeError):
        raise ValueError("VPN configuration cannot be read") from None
    try:
        config = json.loads(source, object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or set(config) != {"enabled", "bind_ip", "peer_cidr", "shared_token"}:
            raise ValueError()
        if type(config["enabled"]) is not bool:
            raise ValueError()
        address = ipaddress.IPv4Address(config["bind_ip"])
        network = ipaddress.IPv4Network(config["peer_cidr"], strict=True)
        if (str(address) != config["bind_ip"] or str(network) != config["peer_cidr"]
                or not any(network.subnet_of(allowed) for allowed in _PRIVATE)
                or network.prefixlen > 30 or address not in network
                or address in (network.network_address, network.broadcast_address)):
            raise ValueError()
        if not isinstance(config["shared_token"], str) or not re.fullmatch(r"[0-9a-f]{64}", config["shared_token"]):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        # Do not disclose config content, especially the shared token.
        raise ValueError("Invalid VPN configuration: expected enabled, private bind_ip/peer_cidr and a 64-hex shared_token") from None
    return config


def load_vpn_public_config(path: Path | None = None, vpn_config: dict | None = None) -> dict | None:
    """Validate the optional HTTPS gateway and its single authorized VPN relay."""
    path = Path(path) if path is not None else DEFAULT_PUBLIC_PATH
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("VPN public configuration points to a missing file") from None
        return None
    except (OSError, UnicodeError):
        raise ValueError("VPN public configuration cannot be read") from None
    if vpn_config is None:
        vpn_config = load_vpn_config()
    try:
        config = json.loads(source, object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or set(config) != {"public_url", "relay_peer", "relay_token"}:
            raise ValueError()
        if not vpn_config or not vpn_config["enabled"]:
            raise ValueError()
        url = urlsplit(config["public_url"])
        host = url.hostname or ""
        labels = host.split(".")
        if (url.scheme != "https" or config["public_url"] != "https://" + host
                or len(host) > 253 or len(labels) < 2
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        if not permitted_peer(config["relay_peer"], vpn_config) or config["relay_peer"] == vpn_config["bind_ip"]:
            raise ValueError()
        if not isinstance(config["relay_token"], str) or not re.fullmatch(r"[0-9a-f]{64}", config["relay_token"]):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Invalid VPN public configuration: expected HTTPS public_url, private relay_peer and a 64-hex relay_token") from None
    return config


def vpn_origin(config: dict | None) -> str | None:
    return f"http://{config['bind_ip']}:8787" if config and config["enabled"] else None


def vpn_authority(config: dict) -> str:
    return f"{config['bind_ip']}:8787"


def permitted_peer(peer: str | None, config: dict) -> bool:
    try:
        address = ipaddress.IPv4Address(peer)
        network = ipaddress.IPv4Network(config["peer_cidr"])
        return str(address) == peer and address in network and address not in (network.network_address, network.broadcast_address)
    except (ValueError, TypeError):
        return False


def canonical_client_ip(value: str | None) -> str:
    """Accept one canonical IPv4/IPv6 address, never a proxy list or zone ID."""
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError()
        address = ipaddress.ip_address(value)
        if str(address) != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise PermissionError("Public relay requires one canonical client IP") from None
    return value


def public_client_token(client_ip: str, config: dict, public_config: dict) -> str:
    """Domain-separated attestation bound to the public origin and client IP."""
    payload = "localai-public-client-v1\n" + public_config["public_url"] + "\n" + canonical_client_ip(client_ip)
    return hmac.new(bytes.fromhex(config["shared_token"]), payload.encode("ascii"), hashlib.sha256).hexdigest()


def verified_public_client(headers, client_host: str | None, scheme: str,
                           config: dict | None, public_config: dict | None) -> str | None:
    """Verify relay client identity without granting the HTTP VPN cookie exception."""
    values = {str(k).lower(): str(v) for k, v in headers.items()}
    internal = {k for k in values if k.startswith(PUBLIC_PREFIX)}
    active = bool(config and config["enabled"] and public_config)
    if not internal:
        if active and values.get("host") == urlsplit(public_config["public_url"]).netloc:
            raise PermissionError("Public relay host requires gateway attestation")
        return None
    if (not active or internal != {PUBLIC_TOKEN_HEADER, PUBLIC_CLIENT_HEADER}
            or any(k.startswith((INTERNAL_PREFIX, RELAY_PREFIX)) for k in values)):
        raise PermissionError("Invalid public gateway attestation")
    try:
        loopback = ipaddress.ip_address(client_host).is_loopback
    except (ValueError, TypeError):
        loopback = False
    token = values.get(PUBLIC_TOKEN_HEADER, "")
    client_ip = canonical_client_ip(values.get(PUBLIC_CLIENT_HEADER))
    if (not loopback or scheme != "http"
            or values.get("host") != urlsplit(public_config["public_url"]).netloc
            or values.get("origin") not in (None, public_config["public_url"])
            or not re.fullmatch(r"[0-9a-f]{64}", token)
            or not hmac.compare_digest(token, public_client_token(client_ip, config, public_config))):
        raise PermissionError("Invalid public gateway attestation")
    return client_ip


def verified_vpn_peer(headers, client_host: str | None, scheme: str, config: dict | None) -> str | None:
    """Validate an internal attestation; any claimed but invalid VPN is denied."""
    values = {str(k).lower(): str(v) for k, v in headers.items()}
    if any(k.startswith(RELAY_PREFIX) for k in values):
        raise PermissionError("Relay credentials must terminate at the gateway")
    internal = {k for k in values if k.startswith(INTERNAL_PREFIX)}
    active = bool(config and config["enabled"])
    if not internal:
        if active and values.get("host") == vpn_authority(config):
            raise PermissionError("VPN host requires gateway attestation")
        return None
    if not active or internal != {TOKEN_HEADER, PEER_HEADER}:
        raise PermissionError("Invalid VPN gateway attestation")
    try:
        loopback = ipaddress.ip_address(client_host).is_loopback
    except (ValueError, TypeError):
        loopback = False
    token = values.get(TOKEN_HEADER, "")
    if (not loopback or scheme != "http" or values.get("host") != vpn_authority(config)
            or values.get("origin") not in (None, vpn_origin(config))
            or not re.fullmatch(r"[0-9a-f]{64}", token)
            or not hmac.compare_digest(token, config["shared_token"])
            or not permitted_peer(values.get(PEER_HEADER), config)):
        raise PermissionError("Invalid VPN gateway attestation")
    return values[PEER_HEADER]
