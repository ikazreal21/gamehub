"""GameHub manager API - FastAPI backend serving API + static frontend."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path

import psutil
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, backups, config, docker_service
from . import palworld_config as pal_cfg
from . import rcon_helpers
from .games.templates import get_template, list_templates
from .models import CreateServerRequest, LoginRequest, RconRequest, TokenResponse, UpdateEnvRequest

app = FastAPI(title="GameHub Manager", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.post("/api/login", response_model=TokenResponse)
def login(body: LoginRequest):
    if not auth.verify_login(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=auth.create_token())


@app.get("/api/templates")
def templates(_=Depends(auth.require_auth)):
    out = []
    for t in list_templates():
        out.append({
            "id": t.id, "name": t.name, "image": t.image,
            "description": t.description, "ports": t.ports,
            "env_defaults": t.env_defaults, "volumes": t.volumes,
            "rcon_supported": t.rcon_supported, "rcon_port": t.rcon_port,
            "config_files": [c.__dict__ for c in t.config_files],
            "helpful_commands": t.helpful_commands,
        })
    return out


@app.get("/api/servers")
def list_servers(_=Depends(auth.require_auth)):
    result = []
    for inst in docker_service.list_instances():
        st = docker_service.container_state(inst.id)
        tpl = get_template(inst.game)
        result.append({
            "id": inst.id, "name": inst.name, "game": inst.game,
            "game_name": tpl.name if tpl else inst.game,
            "image": inst.image, "state": st,
            "ports": inst.ports, "env_keys": sorted(inst.env.keys()),
            "created_at": inst.created_at,
        })
    return result


@app.post("/api/servers", status_code=201)
def create_server(body: CreateServerRequest, _=Depends(auth.require_auth)):
    try:
        ports = [p.model_dump() for p in body.ports] if body.ports else None
        env = dict(body.env or {})
        if body.rcon_password:
            tpl = get_template(body.game)
            key = tpl.rcon_env_key if tpl else "RCON_PASSWORD"
            env[key] = body.rcon_password
            # palworld image also reads ADMIN_PASSWORD; keep both in sync
            if body.game == "palworld":
                env["ADMIN_PASSWORD"] = body.rcon_password
        inst = docker_service.create_instance(
            name=body.name, game=body.game, image=body.image,
            ports=ports, env=env, extra_args=body.extra_args or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e}")
    # auto-start
    try:
        docker_service.start(inst.id)
    except Exception:
        pass
    return inst.to_dict()


@app.get("/api/servers/{server_id}")
def get_server(server_id: str, _=Depends(auth.require_auth)):
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    st = docker_service.container_state(server_id)
    stats = docker_service.stats(server_id)
    return {**inst.to_dict(), "state": st, "stats": stats}


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str, remove_volumes: bool = False, _=Depends(auth.require_auth)):
    try:
        docker_service.delete_instance(server_id, remove_volumes)
    except KeyError:
        raise HTTPException(404, "Server not found")
    return {"status": "deleted"}


@app.post("/api/servers/{server_id}/start")
def api_start(server_id: str, _=Depends(auth.require_auth)):
    try:
        return docker_service.start(server_id)
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/servers/{server_id}/stop")
def api_stop(server_id: str, _=Depends(auth.require_auth)):
    return docker_service.stop(server_id)


@app.post("/api/servers/{server_id}/restart")
def api_restart(server_id: str, _=Depends(auth.require_auth)):
    return docker_service.restart(server_id)


@app.post("/api/servers/{server_id}/update")
def api_update(server_id: str, _=Depends(auth.require_auth)):
    """Pull latest image + recreate (SteamCMD game update)."""
    try:
        return docker_service.update_image(server_id)
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/servers/{server_id}/stats")
def api_stats(server_id: str, _=Depends(auth.require_auth)):
    if not docker_service.get_instance(server_id):
        raise HTTPException(404, "Server not found")
    s = docker_service.stats(server_id)
    # try player count via rcon (non-fatal)
    players = None
    try:
        inst = docker_service.get_instance(server_id)
        tpl = get_template(inst.game) if inst else None
        if tpl and tpl.rcon_supported and tpl.player_list_command:
            raw = rcon_helpers.exec_rcon(server_id, tpl.player_list_command)
            players = len(rcon_helpers.parse_players(inst.game, raw))
    except Exception:
        pass
    s["players_online"] = players
    return s


@app.get("/api/servers/{server_id}/logs")
def api_logs(server_id: str, tail: int = 200, _=Depends(auth.require_auth)):
    return {"logs": docker_service.logs_tail(server_id, tail)}


@app.websocket("/api/servers/{server_id}/logs/ws")
async def ws_logs(ws: WebSocket, server_id: str):
    # token via query ?token=
    token = ws.query_params.get("token", "")
    try:
        import jwt as _jwt
        _jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        # send history then stream
        await ws.send_text(docker_service.logs_tail(server_id, 200))
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def _produce():
            try:
                for chunk in docker_service.stream_logs(server_id):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, f"\n(stream error {e})\n")

        import threading
        t = threading.Thread(target=_produce, daemon=True)
        t.start()
        while True:
            chunk = await queue.get()
            await ws.send_text(chunk)
    except WebSocketDisconnect:
        pass


@app.post("/api/servers/{server_id}/rcon")
def api_rcon(server_id: str, body: RconRequest, _=Depends(auth.require_auth)):
    try:
        out = rcon_helpers.exec_rcon(server_id, body.command)
        return {"output": out}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"RCON failed: {e}")


@app.get("/api/servers/{server_id}/players")
def api_players(server_id: str, _=Depends(auth.require_auth)):
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    tpl = get_template(inst.game)
    if not tpl or not tpl.rcon_supported:
        return {"players": [], "raw": "RCON not supported for this game"}
    try:
        raw = rcon_helpers.exec_rcon(server_id, tpl.player_list_command or "")
        return {"players": rcon_helpers.parse_players(inst.game, raw), "raw": raw}
    except Exception as e:
        raise HTTPException(502, f"RCON failed: {e}")


@app.get("/api/servers/{server_id}/env")
def api_get_env(server_id: str, _=Depends(auth.require_auth)):
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    return {"env": inst.env, "ports": inst.ports, "image": inst.image}


@app.put("/api/servers/{server_id}/env")
def api_put_env(server_id: str, body: UpdateEnvRequest, _=Depends(auth.require_auth)):
    from .games.base import ServerInstance
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    inst.env.update(body.env)
    # persist
    docker_service._instances[server_id] = inst
    docker_service._save()
    return {"env": inst.env, "note": "Recreate/restart container to apply (update button recreates)."}


def _resolve_config_path(server_id: str, file_idx: int = 0) -> Path | None:
    inst = docker_service.get_instance(server_id)
    if not inst:
        return None
    tpl = get_template(inst.game)
    if not tpl or not tpl.config_files:
        return None
    if file_idx >= len(tpl.config_files):
        return None
    cdef = tpl.config_files[file_idx]
    base = docker_service.volume_path(server_id, cdef.volume)
    if base is None:
        return None
    return Path(base) / cdef.path_in_volume


@app.get("/api/servers/{server_id}/config")
def api_get_config(server_id: str, file_idx: int = 0, _=Depends(auth.require_auth)):
    p = _resolve_config_path(server_id, file_idx)
    if p is None:
        raise HTTPException(400, "No editable config for this game")
    if not p.exists():
        # palworld: return defaults so UI isn't empty
        inst = docker_service.get_instance(server_id)
        if inst and inst.game == "palworld":
            return {"content": pal_cfg.DEFAULT_PALWORLD_SETTINGS, "path": str(p), "exists": False}
        return {"content": "", "path": str(p), "exists": False}
    return {"content": p.read_text(errors="replace"), "path": str(p), "exists": True}


@app.put("/api/servers/{server_id}/config")
def api_put_config(server_id: str, body: dict, file_idx: int = 0, _=Depends(auth.require_auth)):
    p = _resolve_config_path(server_id, file_idx)
    if p is None:
        raise HTTPException(400, "No editable config for this game")
    content = body.get("content", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"status": "saved", "path": str(p)}


@app.get("/api/servers/{server_id}/palworld-settings")
def api_get_pal_settings(server_id: str, _=Depends(auth.require_auth)):
    p = _resolve_config_path(server_id, 0)
    content = p.read_text(errors="replace") if p and p.exists() else pal_cfg.DEFAULT_PALWORLD_SETTINGS
    return {"settings": pal_cfg.parse_option_settings(content)}


@app.put("/api/servers/{server_id}/palworld-settings")
def api_put_pal_settings(server_id: str, body: dict, _=Depends(auth.require_auth)):
    settings = body.get("settings", {})
    content = pal_cfg.dump_option_settings({k: str(v) for k, v in settings.items()})
    p = _resolve_config_path(server_id, 0)
    if p is None:
        raise HTTPException(400, "No palworld config path")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"status": "saved"}


@app.get("/api/backups")
def api_list_backups(server: str | None = None, _=Depends(auth.require_auth)):
    return backups.list_backups(server)


@app.post("/api/servers/{server_id}/backup", status_code=201)
def api_backup(server_id: str, _=Depends(auth.require_auth)):
    try:
        return backups.create_backup(server_id)
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/backups/{filename}")
def api_del_backup(filename: str, _=Depends(auth.require_auth)):
    try:
        backups.delete_backup(filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "deleted"}


@app.get("/api/system")
def api_system(_=Depends(auth.require_auth)):
    vm = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "mem_total_mb": round(vm.total / 1024 / 1024),
        "mem_used_mb": round(vm.used / 1024 / 1024),
        "mem_percent": vm.percent,
        "disk": [
            {"mount": p.mountpoint, "percent": psutil.disk_usage(p.mountpoint).percent}
            for p in psutil.disk_partitions(all=False)[:4]
        ],
    }


# ---- static frontend ----
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))
