"""Built-in game templates. Add new games here or via custom-templates.yaml."""
from .base import ConfigFileDef, GameTemplate

TEMPLATES: dict[str, GameTemplate] = {}


def _register(t: GameTemplate):
    TEMPLATES[t.id] = t
    return t


_register(GameTemplate(
    id="palworld",
    name="Palworld",
    image="thijsvanloef/palworld-server-docker:latest",
    description="Palworld dedicated server (SteamCMD, RCON admin). Default game + RCON ports.",
    ports=[
        {"host": 8211, "container": 8211, "protocol": "udp"},
        {"host": 27015, "container": 27015, "protocol": "udp"},
        {"host": 25575, "container": 25575, "protocol": "tcp"},  # RCON
    ],
    env_defaults={
        "PUID": "1000",
        "PGID": "1000",
        "PORT": "8211",
        "PLAYERS": "32",
        "MULTITHREADING": "true",
        "RCON_ENABLED": "true",
        "RCON_PORT": "25575",
        "TZ": "UTC",
        "SERVER_NAME": "GameHub Palworld",
        "SERVER_DESCRIPTION": "Managed by GameHub",
        "ADMIN_PASSWORD": "changeme-admin",
        "SERVER_PASSWORD": "",
        "COMMUNITY": "false",
    },
    volumes={"data": "/palworld"},
    rcon_supported=True,
    rcon_port=25575,
    rcon_env_key="ADMIN_PASSWORD",
    player_list_command="ShowPlayers",
    config_files=[
        ConfigFileDef(
            path_in_volume="Pal/Saved/Config/LinuxServer/PalWorldSettings.ini",
            description="Main Palworld settings (OptionSettings=...). Edit via form or raw.",
        ),
    ],
    helpful_commands=["ShowPlayers", "Info", "Save", "Broadcast Hello players!", "KickPlayer <steamid>", "BanPlayer <steamid>"],
    needs_steamcmd_update=True,
    mods_dir="Pal/Binaries/Linux/Mods",
    mod_catalog=[
        {"id": "ue4ss", "name": "UE4SS (mod loader, required first)", "url": "https://github.com/UE4SS-RE/RE-UE4SS/releases/latest/download/UE4SS_v3_Linux.zip", "filename": "UE4SS_v3_Linux.zip", "description": "Script mod loader. Install this before gameplay mods. Restart after install."},
        {"id": "adminengine", "name": "AdminEngine (!give, !spawn)", "url": "", "filename": "", "description": "Adds !give / !spawn chat commands. Requires UE4SS. Paste its download URL below (manual URL install) — pick the Linux build from its releases page."},
    ],
))

_register(GameTemplate(
    id="minecraft",
    name="Minecraft Java",
    image="itzg/minecraft-server:latest",
    description="Minecraft Java server (itzg image). RCON + query enabled by default.",
    ports=[
        {"host": 25565, "container": 25565, "protocol": "tcp"},
        {"host": 25575, "container": 25575, "protocol": "tcp"},
    ],
    env_defaults={
        "EULA": "TRUE",
        "MEMORY": "2G",
        "RCON_PASSWORD": "changeme-rcon",
        "ENABLE_RCON": "true",
        "RCON_PORT": "25575",
        "DIFFICULTY": "normal",
        "MOTD": "GameHub Minecraft",
        "MAX_PLAYERS": "20",
        "TZ": "UTC",
    },
    volumes={"data": "/data"},
    rcon_supported=True,
    rcon_port=25575,
    rcon_env_key="RCON_PASSWORD",
    player_list_command="list",
    config_files=[
        ConfigFileDef(path_in_volume="server.properties", language="properties", description="Vanilla server.properties"),
    ],
    helpful_commands=["list", "say Hello from GameHub!", "save-all", "whitelist list", "op <player>"],
    mods_dir="mods",
    mod_catalog=[],
))

_register(GameTemplate(
    id="valheim",
    name="Valheim",
    image="lloesche/valheim-server:latest",
    description="Valheim dedicated server (SteamCMD). No RCON - use logs.",
    ports=[
        {"host": 2456, "container": 2456, "protocol": "udp"},
        {"host": 2457, "container": 2457, "protocol": "udp"},
    ],
    env_defaults={
        "SERVER_NAME": "GameHub Valheim",
        "WORLD_NAME": "GameHub",
        "SERVER_PASS": "changeme123",
        "SERVER_PUBLIC": "0",
        "TZ": "UTC",
    },
    volumes={"data": "/config"},
    rcon_supported=False,
    config_files=[],
    helpful_commands=[],
    needs_steamcmd_update=True,
    mods_dir="plugins",
    mod_catalog=[],
))

_register(GameTemplate(
    id="zomboid",
    name="Project Zomboid",
    image="crownengine/project-zomboid:latest",
    description="Project Zomboid dedicated server with RCON.",
    ports=[
        {"host": 16261, "container": 16261, "protocol": "udp"},
        {"host": 16262, "container": 16262, "protocol": "tcp"},
        {"host": 27015, "container": 27015, "protocol": "tcp"},  # RCON-ish
    ],
    env_defaults={
        "ADMIN_PASSWORD": "changeme-admin",
        "RCON_PASSWORD": "changeme-rcon",
        "SERVER_NAME": "GameHub Zomboid",
        "MAX_PLAYERS": "16",
        "TZ": "UTC",
    },
    volumes={"data": "/data"},
    rcon_supported=True,
    rcon_port=27015,
    rcon_env_key="RCON_PASSWORD",
    player_list_command="players",
    config_files=[
        ConfigFileDef(path_in_volume="Server/GameHub.ini", language="ini", description="Server INI (name depends on SERVER_NAME)"),
    ],
    helpful_commands=["players", "save", "quit"],
    needs_steamcmd_update=True,
    mods_dir="Server/mods",
    mod_catalog=[],
))

_register(GameTemplate(
    id="generic",
    name="Generic / Custom",
    image="",
    description="Any Docker image. You supply image + ports + env. RCON optional.",
    ports=[],
    env_defaults={},
    volumes={"data": "/data"},
    rcon_supported=True,
    rcon_port=None,
    helpful_commands=[],
    mods_dir="mods",
    mod_catalog=[],
))


def get_template(game_id: str) -> GameTemplate | None:
    return TEMPLATES.get(game_id)


def list_templates() -> list[GameTemplate]:
    return list(TEMPLATES.values())
