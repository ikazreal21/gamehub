"""Docker orchestration + instance persistence (instances.json)."""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config
from .games.base import ServerInstance
from .games.templates import get_template

_instances: dict[str, ServerInstance] = {}
_docker = None


def _load():
    global _instances
    p = Path(config.INSTANCES_FILE)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            _instances = {d["id"]: ServerInstance.from_dict(d) for d in data}
        except Exception:
            _instances = {}
    else:
        _instances = {}


def _save():
    p = Path(config.INSTANCES_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([s.to_dict() for s in _instances.values()], indent=2))


_load()


def docker_client():
    global _docker
    if config.MOCK_DOCKER:
        return None
    if _docker is None:
        import docker
        _docker = docker.from_env()
    return _docker


def list_instances() -> list[ServerInstance]:
    return list(_instances.values())


def get_instance(server_id: str) -> ServerInstance | None:
    return _instances.get(server_id)


def create_instance(name: str, game: str, image: str | None, ports: list[dict] | None,
                    env: dict[str, str] | None, extra_args: str = "") -> ServerInstance:
    tpl = get_template(game)
    if tpl is None:
        raise ValueError(f"Unknown game template: {game}")
    final_image = image or tpl.image
    if not final_image:
        raise ValueError("Docker image is required (no default for generic template)")
    final_ports = ports if ports is not None else [dict(p) for p in tpl.ports]
    merged_env = dict(tpl.env_defaults)
    merged_env.update(env or {})
    cid = f"gamehub-{name}"
    if cid in _instances:
        raise ValueError(f"Server '{name}' already exists")
    inst = ServerInstance(
        id=cid, name=name, game=game, image=final_image,
        ports=final_ports, env=merged_env, extra_args=extra_args,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _instances[cid] = inst
    _save()
    # create container now (don't auto-start? we DO start for UX)
    ensure_container(inst)
    return inst


def delete_instance(server_id: str, remove_volumes: bool = False):
    inst = _instances.get(server_id)
    if inst is None:
        raise KeyError("Server not found")
    c = docker_client()
    if c is not None:
        try:
            cont = c.containers.get(server_id)
            try:
                cont.stop(timeout=10)
            except Exception:
                pass
            cont.remove(v=remove_volumes, force=True)
        except Exception:
            pass
    del _instances[server_id]
    _save()


def ensure_container(inst: ServerInstance):
    """Create docker container if missing. Returns container or None in mock mode."""
    c = docker_client()
    if c is None:
        return None
    try:
        return c.containers.get(inst.id)
    except Exception:
        pass
    tpl = get_template(inst.game)
    volumes = {}
    if tpl:
        for key, container_path in tpl.volumes.items():
            vol_name = f"{inst.id}-{key}"
            volumes[vol_name] = {"bind": container_path, "mode": "rw"}
    port_bindings = {}
    for p in inst.ports:
        proto = p.get("protocol", "udp")
        cport = f"{p['container']}/{proto}" if proto != "both" else None
        if proto == "both":
            port_bindings[f"{p['container']}/tcp"] = p["host"]
            port_bindings[f"{p['container']}/udp"] = p["host"]
        else:
            port_bindings[cport] = p["host"]
    container = c.containers.create(
        inst.image, name=inst.id, environment=inst.env,
        ports=port_bindings, volumes=volumes, detach=True,
        restart_policy={"Name": "unless-stopped"},
    )
    return container


def get_container(server_id: str):
    c = docker_client()
    if c is None:
        return None
    try:
        return c.containers.get(server_id)
    except Exception:
        return None


def container_state(server_id: str) -> str:
    cont = get_container(server_id)
    if cont is None:
        return "mock" if config.MOCK_DOCKER else "missing"
    try:
        cont.reload()
        return cont.status  # running, exited, created...
    except Exception:
        return "unknown"


def start(server_id: str):
    inst = get_instance(server_id)
    if inst is None:
        raise KeyError("Server not found")
    cont = ensure_container(inst)
    if cont is None:
        return {"status": "mock-started"}
    cont.start()
    return {"status": "started"}


def stop(server_id: str, timeout: int = 30):
    cont = get_container(server_id)
    if cont is None:
        return {"status": "mock-stopped" if config.MOCK_DOCKER else "missing"}
    cont.stop(timeout=timeout)
    return {"status": "stopped"}


def restart(server_id: str, timeout: int = 30):
    cont = get_container(server_id)
    if cont is None:
        return {"status": "mock-restarted" if config.MOCK_DOCKER else "missing"}
    cont.restart(timeout=timeout)
    return {"status": "restarted"}


def logs_tail(server_id: str, tail: int = 200) -> str:
    cont = get_container(server_id)
    if cont is None:
        return "(mock mode - no docker logs. Set MOCK_DOCKER=false on server.)\n"
    try:
        return cont.logs(tail=tail).decode("utf-8", errors="replace")
    except Exception as e:
        return f"(log error: {e})\n"


def stream_logs(server_id: str, tail: int = 100):
    """Generator yielding log lines (blocking)."""
    cont = get_container(server_id)
    if cont is None:
        yield "(mock mode - no live logs)\n"
        return
    try:
        for chunk in cont.logs(stream=True, follow=True, tail=tail):
            yield chunk.decode("utf-8", errors="replace")
    except Exception as e:
        yield f"\n(log stream ended: {e})\n"


def stats(server_id: str) -> dict[str, Any]:
    cont = get_container(server_id)
    if cont is None:
        return {"cpu_percent": 0, "mem_mb": 0, "state": container_state(server_id)}
    try:
        s = cont.stats(stream=False)
        # cpu calc
        cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
        cpu = 0.0
        if sys_delta > 0:
            cpu = cpu_delta / sys_delta * len(s["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])) * 100
        mem = s["memory_stats"].get("usage", 0) / 1024 / 1024
        return {"cpu_percent": round(cpu, 1), "mem_mb": round(mem, 1), "state": cont.status}
    except Exception:
        return {"cpu_percent": 0, "mem_mb": 0, "state": "unknown"}


def update_image(server_id: str) -> dict[str, Any]:
    """Pull latest image and recreate container (for SteamCMD updates)."""
    inst = get_instance(server_id)
    if inst is None:
        raise KeyError("Server not found")
    c = docker_client()
    if c is None:
        return {"status": "mock-updated"}
    c.images.pull(inst.image)
    try:
        old = c.containers.get(server_id)
        was_running = old.status == "running"
        try:
            old.stop(timeout=20)
        except Exception:
            pass
        old.remove(force=True)
    except Exception:
        was_running = False
    ensure_container(inst)
    if was_running:
        c.containers.get(server_id).start()
    return {"status": "updated", "image": inst.image, "restarted": was_running}


def volume_path(server_id: str, volume_key: str = "data") -> Optional[Path]:
    """Resolve host path of a named volume by inspecting container mount."""
    c = docker_client()
    if c is None:
        # dev fallback: local ./data/<server_id>/<key>
        p = Path(config.DATA_DIR) / server_id / volume_key
        p.mkdir(parents=True, exist_ok=True)
        return p
    try:
        cont = c.containers.get(server_id)
        cont.reload()
        for m in cont.attrs.get("Mounts", []):
            name = m.get("Name", "")
            if name == f"{server_id}-{volume_key}":
                # named volume - find mountpoint via volume inspect
                vol = c.volumes.get(name)
                return Path(vol.attrs.get("Mountpoint", ""))
        return None
    except Exception:
        return None
