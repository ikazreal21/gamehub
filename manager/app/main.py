"""GameHub manager API - FastAPI backend serving API + static frontend."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path

import psutil
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, backups, config, docker_service
from . import ip_gate
from . import mods as mods_svc
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


@app.middleware("http")
async def admin_ip_gate(request: Request, call_next):
    """Restrict /admin + management /api/* to ADMIN_ALLOWED_IPS.
    Public surface stays open: /, /share/*, /public/*, /static/*, /favicon*."""
    path = request.url.path
    if path == "/admin" or path.startswith("/admin/") or (
        path.startswith("/api/") and not path.startswith("/api/docs")
    ):
        ip = ip_gate.client_ip(request)
        if not ip_gate.is_allowed(ip):
            return JSONResponse(status_code=403, content={"detail": "Admin access restricted (IP not allowlisted)"})
    return await call_next(request)


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
            "mods_dir": t.mods_dir, "mod_catalog": t.mod_catalog,
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
            "public_address": inst.public_address,
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
        dockerfile = (body.dockerfile or "").strip()
        image = body.image
        if dockerfile and not image:
            image = "__build__"  # placeholder: container created after custom build
        inst = docker_service.create_instance(
            name=body.name, game=body.game, image=image,
            ports=ports, env=env, extra_args=body.extra_args or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e}")
    if dockerfile:
        from . import builds as builds_svc
        try:
            builds_svc.save_dockerfile(inst.id, dockerfile)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _start_build(inst.id, body.start)
        return {**inst.to_dict(), "started": False, "build": "started"}
    started = False
    if body.start:
        try:
            docker_service.start(inst.id)
            started = True
        except Exception:
            pass
    return {**inst.to_dict(), "started": started}


@app.get("/api/servers/{server_id}")
def get_server(server_id: str, _=Depends(auth.require_auth)):
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    st = docker_service.container_state(server_id)
    stats = docker_service.stats(server_id)
    tpl = get_template(inst.game)
    d = {**inst.to_dict(), "state": st, "stats": stats}
    d["share_url"] = f"/share/{server_id}"
    from . import builds as builds_svc
    d["has_dockerfile"] = bool(builds_svc.load_dockerfile(server_id).strip())
    d["game_name"] = tpl.name if tpl else inst.game
    d["config_files"] = [
        {"index": i, "path": c.path_in_volume, "description": c.description}
        for i, c in enumerate(tpl.config_files)
    ] if tpl else []
    return d


@app.put("/api/servers/{server_id}/public-address")
def api_set_public_address(server_id: str, body: dict, _=Depends(auth.require_auth)):
    """Set the public join address (e.g. playit address). Body: {"public_address": "..."}"""
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    addr = str(body.get("public_address", "")).strip()[:200]
    inst.public_address = addr
    docker_service._instances[server_id] = inst
    docker_service._save()
    return {"public_address": addr, "share_url": f"/share/{server_id}"}


def _public_status(server_id: str) -> dict:
    """Safe public fields only: status, address, stats, player names (never ids/passwords)."""
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise HTTPException(404, "Server not found")
    tpl = get_template(inst.game)
    state = docker_service.container_state(server_id)
    stats = docker_service.stats(server_id)
    players: list[dict] = []
    players_online: int | None = None
    try:
        if tpl and tpl.rcon_supported and tpl.player_list_command:
            raw = rcon_helpers.exec_rcon(server_id, tpl.player_list_command)
            parsed = rcon_helpers.parse_players(inst.game, raw)
            # only expose names publicly, never steam ids/uids
            players = [{"name": p.get("name", "?")} for p in parsed]
            players_online = len(players)
    except Exception:
        pass
    return {
        "id": inst.id,
        "name": inst.name,
        "game": inst.game,
        "game_name": tpl.name if tpl else inst.game,
        "state": state,
        "running": state == "running",
        "public_address": inst.public_address,
        "cpu_percent": stats.get("cpu_percent"),
        "mem_mb": stats.get("mem_mb"),
        "players_online": players_online if players_online is not None else stats.get("players_online"),
        "players": players,
    }


@app.get("/public/servers")
def public_server_list():
    """Public homepage feed - NO auth. One entry per server (names + status + addresses)."""
    return [_public_status(s.id) for s in docker_service.list_instances()]


@app.get("/public/servers/{server_id}")
def public_server_status(server_id: str):
    """Public share endpoint - NO auth. Only safe fields: status, address, stats, player names."""
    return _public_status(server_id)


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


import threading
import time as _time

_update_jobs: dict[str, dict] = {}


def _run_update_job(server_id: str):
    job = _update_jobs[server_id]
    job["log"].append("Pulling latest image… (this takes minutes on big SteamCMD images)")
    try:
        res = docker_service.update_image(server_id)
        job["log"].append(f"Recreated container (restarted={res.get('restarted')}). Waiting for boot…")
        # wait until container reports running (max ~120s) so UI "done" is truthful
        for _ in range(40):
            _time.sleep(3)
            if docker_service.container_state(server_id) == "running":
                break
            job["log"].append(f"… state={docker_service.container_state(server_id)}")
        job["log"].append(f"Done. state={docker_service.container_state(server_id)}")
        job["state"] = "done"
        job["result"] = res
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        job["log"].append(f"ERROR: {e}")


@app.post("/api/servers/{server_id}/update")
def api_update(server_id: str, _=Depends(auth.require_auth)):
    """Start image pull + recreate as background job (SteamCMD game update). Poll status endpoint."""
    if not docker_service.get_instance(server_id):
        raise HTTPException(404, "Server not found")
    cur = _update_jobs.get(server_id)
    if cur and cur["state"] == "running":
        raise HTTPException(409, "Update already in progress")
    _update_jobs[server_id] = {"state": "running", "log": ["Update started"], "error": "", "result": None}
    threading.Thread(target=_run_update_job, args=(server_id,), daemon=True).start()
    return {"status": "started"}


@app.get("/api/servers/{server_id}/update/status")
def api_update_status(server_id: str, _=Depends(auth.require_auth)):
    job = _update_jobs.get(server_id)
    if not job:
        return {"state": "idle", "log": []}
    return job


_build_jobs: dict[str, dict] = {}


def _run_build_job(server_id: str, start_after: bool):
    from . import builds as builds_svc
    job = _build_jobs[server_id]
    try:
        tag = builds_svc.build(server_id, job["log"])
        job["log"].append(f"Image ready: {tag}")
        if start_after:
            job["log"].append("Starting server…")
            docker_service.start(server_id)
            job["log"].append("Done. state=" + docker_service.container_state(server_id))
        else:
            job["log"].append("Done. Press Start when ready.")
        job["state"] = "done"
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        job["log"].append(f"ERROR: {e}")


def _start_build(server_id: str, start_after: bool):
    cur = _build_jobs.get(server_id)
    if cur and cur["state"] == "running":
        raise HTTPException(409, "Build already in progress")
    _build_jobs[server_id] = {"state": "running", "log": ["Build queued"], "error": ""}
    threading.Thread(target=_run_build_job, args=(server_id, start_after), daemon=True).start()
    return {"status": "started"}


@app.post("/api/servers/{server_id}/build", status_code=202)
def api_build(server_id: str, body: dict, _=Depends(auth.require_auth)):
    """Build/rebuild custom image from stored (or just-provided) Dockerfile. Body: {"dockerfile"?, "start_after"?}"""
    from . import builds as builds_svc
    if not docker_service.get_instance(server_id):
        raise HTTPException(404, "Server not found")
    if body.get("dockerfile"):
        try:
            builds_svc.save_dockerfile(server_id, body["dockerfile"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    elif not builds_svc.load_dockerfile(server_id).strip():
        raise HTTPException(400, "No Dockerfile stored - paste one first")
    return _start_build(server_id, bool(body.get("start_after", False)))


@app.get("/api/servers/{server_id}/build/status")
def api_build_status(server_id: str, _=Depends(auth.require_auth)):
    job = _build_jobs.get(server_id)
    if not job:
        return {"state": "idle", "log": []}
    return job


@app.get("/api/servers/{server_id}/build/dockerfile")
def api_get_dockerfile(server_id: str, _=Depends(auth.require_auth)):
    from . import builds as builds_svc
    if not docker_service.get_instance(server_id):
        raise HTTPException(404, "Server not found")
    return {"dockerfile": builds_svc.load_dockerfile(server_id)}


@app.post("/api/servers/{server_id}/build/context", status_code=201)
async def api_upload_context(server_id: str, file: UploadFile = File(...), _=Depends(auth.require_auth)):
    """Upload a .zip build context (files your Dockerfile COPYs). Extracted next to the Dockerfile."""
    from . import builds as builds_svc
    if not docker_service.get_instance(server_id):
        raise HTTPException(404, "Server not found")
    name = (file.filename or "")
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "Upload a .zip file")
    try:
        data = await file.read()
        if len(data) > 100 * 1024 * 1024:
            raise HTTPException(400, "Context zip too large (100MB max)")
        info = builds_svc.save_context_zip(server_id, data)
        return {"status": "uploaded", "detail": info}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Bad zip: {e}")


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
    """Legacy Palworld-only endpoint (kept for compat) - prefer GET .../settings."""
    from . import game_settings as gs
    try:
        s = gs.get_settings(server_id)
        if s["game"] != "palworld":
            raise HTTPException(400, "Not a Palworld server - use GET .../settings")
        return {"settings": s["values"]}
    except KeyError:
        raise HTTPException(404, "Server not found")


@app.put("/api/servers/{server_id}/palworld-settings")
def api_put_pal_settings(server_id: str, body: dict, _=Depends(auth.require_auth)):
    """Legacy Palworld-only endpoint (kept for compat) - prefer PUT .../settings."""
    from . import game_settings as gs
    try:
        return gs.save_settings(server_id, body.get("settings", {}))
    except KeyError:
        raise HTTPException(404, "Server not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/servers/{server_id}/settings")
def api_get_settings(server_id: str, _=Depends(auth.require_auth)):
    """Generic per-game Settings form (all templates) - sits alongside the raw Config tab."""
    from . import game_settings as gs
    try:
        return gs.get_settings(server_id)
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.put("/api/servers/{server_id}/settings")
def api_put_settings(server_id: str, body: dict, _=Depends(auth.require_auth)):
    from . import game_settings as gs
    try:
        return gs.save_settings(server_id, body.get("values", body.get("settings", {})))
    except KeyError:
        raise HTTPException(404, "Server not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


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


@app.get("/api/servers/{server_id}/mods")
def api_list_mods(server_id: str, _=Depends(auth.require_auth)):
    try:
        inst = docker_service.get_instance(server_id)
        tpl = get_template(inst.game) if inst else None
        return {
            "mods_dir": (tpl.mods_dir if tpl else "mods"),
            "catalog": (tpl.mod_catalog if tpl else []),
            "installed": mods_svc.list_mods(server_id),
        }
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/servers/{server_id}/mods/install", status_code=201)
def api_install_mod(server_id: str, body: dict, _=Depends(auth.require_auth)):
    """Install by catalog id, direct URL, or catalog entry with custom URL. Body: {"mod_id"|"url", "filename"?}"""
    try:
        url, filename = "", ""
        if body.get("url"):
            url = body["url"]
            filename = body.get("filename", "")
        elif body.get("mod_id"):
            inst = docker_service.get_instance(server_id)
            tpl = get_template(inst.game) if inst else None
            entry = next((m for m in (tpl.mod_catalog if tpl else []) if m["id"] == body["mod_id"]), None)
            if not entry:
                raise HTTPException(404, "Catalog mod not found")
            url = body.get("url_override") or entry.get("url", "")
            filename = entry.get("filename", "")
            if not url:
                raise HTTPException(400, "This catalog entry needs a download URL (paste release URL as 'url').")
        else:
            raise HTTPException(400, "Provide mod_id or url")
        name = mods_svc.download_mod(server_id, url, filename)
        return {"status": "installed", "name": name, "note": "Restart server to load mods."}
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(502, f"Download failed: {e}")


@app.post("/api/servers/{server_id}/mods/upload", status_code=201)
async def api_upload_mod(server_id: str, file: UploadFile = File(...), _=Depends(auth.require_auth)):
    try:
        d = mods_svc.mods_dir(server_id)
        name = (file.filename or "upload.bin").split("/")[-1]
        if ".." in name or not name:
            raise HTTPException(400, "Invalid filename")
        dest = d / name
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        # auto-extract zips for convenience
        if dest.suffix.lower() == ".zip":
            import zipfile
            with zipfile.ZipFile(dest) as z:
                z.extractall(d)
            dest.unlink()
            return {"status": "uploaded+extracted", "name": name}
        return {"status": "uploaded", "name": name}
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, "Server not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/servers/{server_id}/mods/{name}")
def api_delete_mod(server_id: str, name: str, _=Depends(auth.require_auth)):
    try:
        mods_svc.delete_mod(server_id, name)
        return {"status": "deleted"}
    except KeyError:
        raise HTTPException(404, "Mod not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


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

    @app.get("/admin", include_in_schema=False)
    def admin(request: Request):
        ip_gate.require_admin_ip(request)
        return FileResponse(str(STATIC_DIR / "admin.html"))

    @app.get("/share/{server_id}", include_in_schema=False)
    def share_page(server_id: str):
        return FileResponse(str(STATIC_DIR / "share.html"))
