"""RCON helpers: resolve host/port/password per instance, parse player lists."""
from __future__ import annotations
import re

from . import docker_service
from .games.templates import get_template
from .rcon import rcon_command


def rcon_target(server_id: str) -> tuple[str, int, str]:
    inst = docker_service.get_instance(server_id)
    if inst is None:
        raise KeyError("Server not found")
    tpl = get_template(inst.game)
    port = tpl.rcon_port if tpl and tpl.rcon_port else None
    pw_key = tpl.rcon_env_key if tpl else "RCON_PASSWORD"
    # allow override via env RCON_PORT
    if inst.env.get("RCON_PORT"):
        try:
            port = int(inst.env["RCON_PORT"])
        except ValueError:
            pass
    # Palworld uses ADMIN_PASSWORD env for RCON
    password = inst.env.get(pw_key, "") or inst.env.get("ADMIN_PASSWORD", "") or inst.env.get("RCON_PASSWORD", "")
    if not port:
        raise ValueError("This server has no RCON port configured")
    if not password:
        raise ValueError(f"RCON password empty (set {pw_key} in env)")
    # RCON is container-local; manager reaches it via container gateway.
    # Simplest robust approach: RCON to localhost mapped host port.
    host_port = port
    for p in inst.ports:
        if p.get("container") == port:
            host_port = p.get("host", port)
            break
    return "127.0.0.1", host_port, password


def exec_rcon(server_id: str, command: str) -> str:
    host, port, password = rcon_target(server_id)
    return rcon_command(host, port, password, command)


def parse_players(game: str, raw: str) -> list[dict]:
    """Best-effort parse of player list output per game."""
    players: list[dict] = []
    if game == "palworld":
        # format: "name,playeruid,steamid\nBob,12345,7656119...\n"
        for line in raw.strip().splitlines()[1:]:
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 3 and parts[0]:
                players.append({"name": parts[0], "uid": parts[1], "steam_id": parts[2]})
            elif len(parts) == 1 and parts[0] and parts[0].lower() not in ("name,playeruid,steamid",):
                players.append({"name": parts[0]})
    elif game == "minecraft":
        m = re.search(r"There are \d+ of a max of \d+ players online:(.*)", raw, re.DOTALL)
        if m:
            for n in m.group(1).replace("\n", "").split(","):
                n = n.strip()
                if n:
                    players.append({"name": n})
    else:
        for line in raw.strip().splitlines():
            line = line.strip()
            if line:
                players.append({"name": line})
    return players
