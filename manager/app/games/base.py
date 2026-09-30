"""Game template abstraction - this is how you add new games easily.

To add a game:
  1. Copy one of the dicts in `templates.py` (or create a new GameTemplate)
  2. Set docker image, ports, env defaults, volume paths, rcon info
  3. Restart manager - it appears in UI automatically. No code changes needed
     for generic Docker games. Only add a subclass here if you need custom
     player parsing or config handling.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConfigFileDef:
    path_in_volume: str  # e.g. "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    volume: str = "data"  # which named volume key
    language: str = "ini"
    description: str = ""


@dataclass
class GameTemplate:
    id: str
    name: str
    image: str
    description: str = ""
    # default ports exposed on host -> container
    ports: List[Dict[str, Any]] = field(default_factory=list)
    # default env vars
    env_defaults: Dict[str, str] = field(default_factory=dict)
    # volumes: key -> container path
    volumes: Dict[str, str] = field(default_factory=dict)
    rcon_supported: bool = False
    rcon_port: Optional[int] = None  # container rcon port
    rcon_env_key: str = "RCON_PASSWORD"
    player_list_command: str = ""
    # files editable in UI
    config_files: List[ConfigFileDef] = field(default_factory=list)
    # helpful commands shown as buttons in UI
    helpful_commands: List[str] = field(default_factory=list)
    needs_steamcmd_update: bool = False
    # mods: folder inside "data" volume + installable catalog {id,name,url,description,filename}
    mods_dir: str = ""
    mod_catalog: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ServerInstance:
    """A deployed game server (persisted to instances.json)."""
    id: str  # docker container name, e.g. gamehub-palworld-1
    name: str
    game: str  # template id
    image: str
    ports: List[Dict[str, Any]] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    extra_args: str = ""
    created_at: str = ""
    public_address: str = ""  # e.g. playit address xxx.asia.playit.gg:1234
    hidden: bool = False  # True = hide from public homepage (share link still works)

    def container_name(self) -> str:
        return self.id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "game": self.game,
            "image": self.image, "ports": self.ports, "env": self.env,
            "extra_args": self.extra_args, "created_at": self.created_at,
            "public_address": self.public_address, "hidden": self.hidden,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ServerInstance":
        return ServerInstance(
            id=d["id"], name=d.get("name", d["id"]), game=d["game"],
            image=d["image"], ports=d.get("ports", []), env=d.get("env", {}),
            extra_args=d.get("extra_args", ""), created_at=d.get("created_at", ""),
            public_address=d.get("public_address", ""), hidden=d.get("hidden", False),
        )
