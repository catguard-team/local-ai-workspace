"""Resolve the active public or VPN address while retaining raw tunnel URLs."""
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

STATE = Path(__file__).resolve().parent / ".runtime"


def tunnel_url(prefix="tunnel"):
    candidates = []
    for name in (prefix + ".log", prefix + "-error.log"):
        path = STATE / name
        if path.is_file():
            matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", path.read_text(errors="ignore"))
            if matches:
                candidates.append((path.stat().st_mtime, matches[-1]))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("fixed-access.json contains a duplicate field")
        result[key] = value
    return result


def fixed_access():
    """Return validated named-tunnel settings, or None when not configured.

    This file contains only non-secret endpoint identity. An invalid existing
    configuration is an error: it must never silently change the published URL.
    """
    path = STATE / "fixed-access.json"
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("fixed-access.json points to a missing file") from None
        return None
    except (OSError, UnicodeError):
        raise ValueError("fixed-access.json could not be read") from None
    try:
        config = json.loads(source, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        raise ValueError("fixed-access.json is not valid JSON") from None
    expected = {"provider", "public_url", "tunnel_id"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("fixed-access.json requires exactly provider, public_url and tunnel_id")
    if config["provider"] != "cloudflare":
        raise ValueError("fixed-access.json provider must be cloudflare")
    address = config["public_url"]
    if not isinstance(address, str):
        raise ValueError("fixed-access.json public_url must be a canonical HTTPS hostname")
    try:
        parsed = urlsplit(address)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port is not None
                or parsed.path or parsed.query or parsed.fragment
                or address != "https://" + host):
            raise ValueError()
        if not host.isascii() or len(host) > 253:
            raise ValueError()
        labels = host.split(".")
        if len(labels) < 2 or labels[-1].isdigit():
            raise ValueError()
        for label in labels:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label):
                raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("fixed-access.json public_url must be a canonical HTTPS hostname") from None
    tunnel_id = config["tunnel_id"]
    try:
        if not isinstance(tunnel_id, str) or str(uuid.UUID(tunnel_id)) != tunnel_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("fixed-access.json tunnel_id must be a canonical UUID") from None
    return config


def public_url():
    """VPN-only takes priority; an invalid selected mode never falls back."""
    vpn = vpn_only_access()
    if vpn is not None:
        return vpn["public_url"]
    config = fixed_access()
    return config["public_url"] if config is not None else tunnel_url()


def vpn_only_access():
    """Return non-secret VPN details only after explicit VPN-only activation.

    A staged vpn-access.json can enable a listener while the existing public
    address still works. Only this separate marker selects VPN-only startup.
    The optional public relay exposes HTTPS through the WireGuard transport.
    Never serialize either shared token into startup logs or address files.
    """
    marker = STATE / "vpn-only"
    if not marker.exists() and not marker.is_symlink():
        return None
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("vpn-only must be a regular activation marker file")
    from vpn_access import load_vpn_config, load_vpn_public_config, vpn_origin
    config = load_vpn_config(path=STATE / "vpn-access.json")
    if config is None or not config["enabled"]:
        raise ValueError("VPN-only mode requires an enabled vpn-access.json")
    origin = vpn_origin(config)
    if not origin:
        raise ValueError("VPN-only mode requires a valid VPN origin")
    relay = load_vpn_public_config(path=STATE / "vpn-public.json", vpn_config=config)
    return {"provider": "wireguard", "public_url": relay["public_url"] if relay else origin,
            "public_gateway": relay is not None,
            "bind_ip": config["bind_ip"], "peer_cidr": config["peer_cidr"]}
