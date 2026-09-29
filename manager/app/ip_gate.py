"""IP allowlist gate for the admin UI + management API.

Set ADMIN_ALLOWED_IPS in .env to a comma-separated list of IPs/CIDRs, e.g.:
  ADMIN_ALLOWED_IPS=127.0.0.1, 192.168.50.0/24, 203.0.113.7

Empty = no restriction (anyone can reach /admin, login still required).

How the client IP is determined (in order):
  1. CF-Connecting-IP (Cloudflare Tunnel / proxy)
  2. First entry of X-Forwarded-For
  3. Direct connection address

WARNING with CGNAT / mobile data (common on PH ISPs): your public IP changes
often. If you lock yourself out, SSH into the server, clear ADMIN_ALLOWED_IPS
in .env and restart:  docker compose up -d --build

Localhost (127.0.0.1 / ::1) is always allowed so healthchecks and SSH tunnels work.
"""
from __future__ import annotations
import ipaddress

from fastapi import HTTPException, Request

from . import config


def _allowlist() -> list[str]:
    raw = getattr(config, "ADMIN_ALLOWED_IPS", "")
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def client_ip(req: Request) -> str:
    cf = req.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    xff = req.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return req.client.host if req.client else ""


def is_allowed(ip: str) -> bool:
    if ip in ("127.0.0.1", "::1"):
        return True
    nets = _allowlist()
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if "/" in n:
                if addr in ipaddress.ip_network(n, strict=False):
                    return True
            elif addr == ipaddress.ip_address(n):
                return True
        except ValueError:
            continue
    return False


def require_admin_ip(req: Request):
    ip = client_ip(req)
    if not is_allowed(ip):
        raise HTTPException(status_code=403, detail="Admin access restricted (IP not allowlisted)")
    return ip
