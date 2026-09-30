"""Per-game Settings forms (alongside the raw Config tab).

Each game gets a friendly key/value form instead of hand-editing files:
  palworld  -> PalWorldSettings.ini OptionSettings=(...)  (file)
  minecraft -> server.properties key=value                (file)
  zomboid   -> Server/*.ini key=value                     (file, name follows SERVER_NAME)
  valheim   -> env vars (no config file in this image)
  generic   -> env vars (dynamic - whatever the image uses)

API shape:
  GET /api/servers/{id}/settings -> {game, game_name, source, path, fields, values}
  PUT /api/servers/{id}/settings {values} -> saves to file or env
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from . import docker_service, palworld_config as pal_cfg
from .games.templates import get_template


# ---------------------------------------------------------------- schemas
# field: {key, label, type, default, options, description}
# type: text | number | integer | boolean | select | password

MINECRAFT_FIELDS: list[dict[str, Any]] = [
    {"key": "motd", "label": "MOTD", "type": "text", "default": "GameHub Minecraft", "description": "Message shown in the server list."},
    {"key": "max-players", "label": "Max players", "type": "integer", "default": "20", "description": "Max simultaneous players."},
    {"key": "difficulty", "label": "Difficulty", "type": "select", "options": ["peaceful", "easy", "normal", "hard"], "default": "normal"},
    {"key": "gamemode", "label": "Default gamemode", "type": "select", "options": ["survival", "creative", "adventure", "spectator"], "default": "survival"},
    {"key": "pvp", "label": "PvP", "type": "boolean", "default": "true"},
    {"key": "online-mode", "label": "Online mode", "type": "boolean", "default": "true", "description": "Verify players with Mojang. Turn off only for offline/cracked (not recommended)."},
    {"key": "white-list", "label": "Whitelist enabled", "type": "boolean", "default": "false"},
    {"key": "enforce-whitelist", "label": "Enforce whitelist", "type": "boolean", "default": "false"},
    {"key": "spawn-monsters", "label": "Spawn monsters", "type": "boolean", "default": "true"},
    {"key": "spawn-animals", "label": "Spawn animals", "type": "boolean", "default": "true"},
    {"key": "spawn-npcs", "label": "Spawn villagers", "type": "boolean", "default": "true"},
    {"key": "allow-flight", "label": "Allow flight", "type": "boolean", "default": "false"},
    {"key": "hardcore", "label": "Hardcore", "type": "boolean", "default": "false"},
    {"key": "enable-command-block", "label": "Command blocks", "type": "boolean", "default": "false"},
    {"key": "view-distance", "label": "View distance", "type": "integer", "default": "10"},
    {"key": "simulation-distance", "label": "Simulation distance", "type": "integer", "default": "10"},
    {"key": "max-world-size", "label": "Max world size", "type": "integer", "default": "29999984"},
    {"key": "level-name", "label": "Level name", "type": "text", "default": "world"},
    {"key": "level-seed", "label": "Level seed", "type": "text", "default": "", "description": "Blank = random."},
    {"key": "spawn-protection", "label": "Spawn protection radius", "type": "integer", "default": "16"},
]

ZOMBOID_KNOWN: dict[str, dict[str, Any]] = {
    # friendly metadata for common Project Zomboid Server.ini keys; unknown keys still editable as text
    "Public": {"label": "Public server", "type": "boolean", "default": "false"},
    "PublicName": {"label": "Public name", "type": "text", "default": "GameHub Zomboid"},
    "PublicDescription": {"label": "Public description", "type": "text", "default": ""},
    "MaxPlayers": {"label": "Max players", "type": "integer", "default": "16"},
    "Password": {"label": "Join password", "type": "password", "default": ""},
    "AdminPassword": {"label": "Admin password", "type": "password", "default": ""},
    "PauseEmpty": {"label": "Pause when empty", "type": "boolean", "default": "true"},
    "GlobalChat": {"label": "Global chat", "type": "boolean", "default": "true"},
    "Open": {"label": "Open (no whitelist)", "type": "boolean", "default": "true"},
    "PVP": {"label": "PvP", "type": "boolean", "default": "true"},
    "PauseWhenEmpty": {"label": "Pause when empty (alt key)", "type": "boolean", "default": "true"},
}

VALHEIM_FIELDS: list[dict[str, Any]] = [
    {"key": "SERVER_NAME", "label": "Server name", "type": "text", "default": "GameHub Valheim", "description": "Shown in the server browser."},
    {"key": "WORLD_NAME", "label": "World name", "type": "text", "default": "GameHub"},
    {"key": "SERVER_PASS", "label": "Join password", "type": "password", "default": "", "description": "Min 5 chars. Blank = no password (not recommended for public)."},
    {"key": "SERVER_PUBLIC", "label": "Public listing", "type": "select", "options": ["0", "1"], "default": "0", "description": "1 = show in community list, 0 = join by IP only."},
    {"key": "TZ", "label": "Timezone", "type": "text", "default": "UTC"},
]


# ---------------------------------------------------------------- properties/ini helpers (preserve comments + order)

def parse_kv_lines(content: str) -> tuple[dict[str, str], list[str]]:
    """Parse key=value lines (server.properties / Zomboid ini). Returns (values, order)."""
    values: dict[str, str] = {}
    order: list[str] = []
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";") or s.startswith("["):
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            k = k.strip()
            if k and k not in values:
                order.append(k)
            if k:
                values[k] = v.strip()
    return values, order


def dump_kv_lines(values: dict[str, str], order: list[str], original: str) -> str:
    """Rebuild key=value content, preserving comments/blank lines/order of the original."""
    seen: set[str] = set()
    out: list[str] = []
    for line in original.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";") or s.startswith("["):
            out.append(line)
            continue
        if "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in values:
                out.append(f"{k}={values[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k in order:
        if k not in seen and k in values:
            out.append(f"{k}={values[k]}")
            seen.add(k)
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    return "\n".join(out) + "\n"


DEFAULT_MINECRAFT_PROPERTIES = (
    "# Minecraft server properties (managed by GameHub - also editable via Settings tab)\n"
    "motd=GameHub Minecraft\n"
    "max-players=20\n"
    "difficulty=normal\n"
    "gamemode=survival\n"
    "pvp=true\n"
    "online-mode=true\n"
    "white-list=false\n"
    "enforce-whitelist=false\n"
    "spawn-monsters=true\n"
    "spawn-animals=true\n"
    "spawn-npcs=true\n"
    "allow-flight=false\n"
    "hardcore=false\n"
    "enable-command-block=false\n"
    "view-distance=10\n"
    "simulation-distance=10\n"
    "max-world-size=29999984\n"
    "level-name=world\n"
    "level-seed=\n"
    "spawn-protection=16\n"
)

DEFAULT_ZOMBOID_INI = (
    "; Project Zomboid server settings (managed by GameHub)\n"
    "Public=false\n"
    "PublicName=GameHub Zomboid\n"
    "PublicDescription=Managed by GameHub\n"
    "MaxPlayers=16\n"
    "Password=\n"
    "AdminPassword=changeme-admin\n"
    "PauseEmpty=true\n"
    "GlobalChat=true\n"
    "Open=true\n"
    "PVP=true\n"
)


# ---------------------------------------------------------------- path resolution

def settings_source(game: str) -> str:
    return {
        "palworld": "palworld",
        "minecraft": "properties",
        "zomboid": "ini",
        "valheim": "env",
        "generic": "env",
    }.get(game, "none")


def config_path(server_id: str, file_idx: int = 0) -> Path | None:
    """Same resolution as main._resolve_config_path + Zomboid SERVER_NAME glob fallback."""
    from .games.templates import get_template as _get_tpl
    inst = docker_service.get_instance(server_id)
    if not inst:
        return None
    tpl = _get_tpl(inst.game)
    if not tpl or not tpl.config_files:
        return None
    if file_idx >= len(tpl.config_files):
        return None
    cdef = tpl.config_files[file_idx]
    base = docker_service.volume_path(server_id, cdef.volume)
    if base is None:
        return None
    p = Path(base) / cdef.path_in_volume
    if p.exists():
        return p
    # Zomboid: file name follows SERVER_NAME (default GameHub.ini) - pick any Server/*.ini
    if inst.game == "zomboid":
        candidates = sorted((Path(base) / "Server").glob("*.ini"))
        if candidates:
            # prefer one matching SERVER_NAME
            want = (inst.env.get("SERVER_NAME", "") or "").strip()
            for c in candidates:
                if want and c.stem.lower() == want.lower():
                    return c
            return candidates[0]
    return p


# ---------------------------------------------------------------- field builders

def _infer_type(value: str) -> str:
    v = value.strip().lower()
    if v in ("true", "false"):
        return "boolean"
    try:
        int(value.strip())
        return "integer"
    except ValueError:
        pass
    try:
        float(value.strip())
        return "number"
    except ValueError:
        pass
    return "text"


def _field(key: str, value: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = meta or {}
    ftype = meta.get("type") or _infer_type(value)
    if "password" in key.lower() and ftype == "text":
        ftype = "password"
    return {
        "key": key,
        "label": meta.get("label", key),
        "type": ftype,
        "options": meta.get("options", []),
        "default": meta.get("default", value),
        "description": meta.get("description", ""),
    }


def get_settings(server_id: str) -> dict:
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise KeyError("Server not found")
    tpl = get_template(inst.game)
    game = inst.game
    src = settings_source(game)

    if src == "palworld":
        p = config_path(server_id, 0)
        content = p.read_text(errors="replace") if p and p.exists() else pal_cfg.DEFAULT_PALWORLD_SETTINGS
        values = pal_cfg.parse_option_settings(content)
        if not values:  # corrupt/empty file - start from defaults
            values = pal_cfg.parse_option_settings(pal_cfg.DEFAULT_PALWORLD_SETTINGS)
        fields = [_field(k, v) for k, v in values.items()]
        return {"game": game, "game_name": tpl.name if tpl else game, "source": src,
                "path": str(p) if p else "", "exists": bool(p and p.exists()),
                "fields": fields, "values": values}

    if src in ("properties", "ini"):
        p = config_path(server_id, 0)
        if p and p.exists():
            content = p.read_text(errors="replace")
        else:
            content = DEFAULT_MINECRAFT_PROPERTIES if game == "minecraft" else DEFAULT_ZOMBOID_INI
        values, order = parse_kv_lines(content)
        if game == "minecraft":
            by_key = {f["key"]: f for f in MINECRAFT_FIELDS}
            fields = [_field(f["key"], values.get(f["key"], f.get("default", "")),
                             {**f, "default": f.get("default", "")}) for f in MINECRAFT_FIELDS]
            # surface any extra keys present in the file (e.g. added by the image)
            known = {f["key"] for f in MINECRAFT_FIELDS}
            for k in order:
                if k not in known:
                    fields.append(_field(k, values.get(k, "")))
            for k in values:
                if k not in known and k not in order:
                    fields.append(_field(k, values[k]))
            merged = {f["key"]: values.get(f["key"], f.get("default", "")) for f in MINECRAFT_FIELDS}
            merged.update({k: v for k, v in values.items() if k not in merged})
            values = merged
        else:  # zomboid - dynamic + known metadata
            fields = [_field(k, values.get(k, ZOMBOID_KNOWN.get(k, {}).get("default", "")),
                             {"label": ZOMBOID_KNOWN.get(k, {}).get("label", k),
                              "type": ZOMBOID_KNOWN.get(k, {}).get("type"),
                              "default": ZOMBOID_KNOWN.get(k, {}).get("default", values.get(k, "")),
                              "description": ZOMBOID_KNOWN.get(k, {}).get("description", "")})
                      for k in (order + [k for k in values if k not in order])]
        return {"game": game, "game_name": tpl.name if tpl else game, "source": src,
                "path": str(p) if p else "", "exists": bool(p and p.exists()),
                "fields": fields, "values": values}

    if src == "env":
        schema = VALHEIM_FIELDS if game == "valheim" else []
        by_key = {f["key"]: f for f in schema}
        keys = list(dict.fromkeys([f["key"] for f in schema] + sorted(inst.env.keys())))
        fields = []
        values: dict[str, str] = {}
        for k in keys:
            v = inst.env.get(k, by_key.get(k, {}).get("default", ""))
            values[k] = v
            if k in by_key:
                fields.append({**by_key[k]})
            else:
                fields.append(_field(k, v))
        return {"game": game, "game_name": tpl.name if tpl else game, "source": src,
                "path": "", "exists": True, "fields": fields, "values": values,
                "note": "Saved to environment. Restart (most images) or Update-recreate to apply."}

    return {"game": game, "game_name": tpl.name if tpl else game, "source": "none",
            "path": "", "exists": False, "fields": [], "values": {}}


def save_settings(server_id: str, values: dict[str, str]) -> dict:
    inst = docker_service.get_instance(server_id)
    if not inst:
        raise KeyError("Server not found")
    game = inst.game
    src = settings_source(game)
    clean = {str(k): str(v) for k, v in (values or {}).items()}

    if src == "palworld":
        p = config_path(server_id, 0)
        if p is None:
            raise ValueError("No palworld config path")
        # merge over existing so a partial form submit can't wipe keys
        current = pal_cfg.parse_option_settings(
            p.read_text(errors="replace") if p.exists() else pal_cfg.DEFAULT_PALWORLD_SETTINGS)
        current.update(clean)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(pal_cfg.dump_option_settings(current))
        return {"status": "saved", "note": "Restart server to apply."}

    if src in ("properties", "ini"):
        p = config_path(server_id, 0)
        if p is None:
            raise ValueError("No config file for this game")
        if p.exists():
            original = p.read_text(errors="replace")
            cur, order = parse_kv_lines(original)
        else:
            original = DEFAULT_MINECRAFT_PROPERTIES if game == "minecraft" else DEFAULT_ZOMBOID_INI
            cur, order = parse_kv_lines(original)
        cur.update(clean)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump_kv_lines(cur, order, original))
        return {"status": "saved", "note": "Restart server to apply."}

    if src == "env":
        inst.env.update(clean)
        docker_service._instances[server_id] = inst
        docker_service._save()
        return {"status": "saved", "note": "Restart server to apply (Update recreates the container)."}

    raise ValueError("This game has no form settings - use the Config/Env tabs.")
