# 🎮 GameHub — self-hosted game server manager

Control **Palworld** (start/stop/restart, live console, RCON player admin, PalWorldSettings editor, backups, updates, metrics) + easily add **Minecraft, Valheim, Project Zomboid, or any Docker game**.

Stack: **Python FastAPI backend** + vanilla JS frontend, Docker Compose deploy. Manager controls game containers via the Docker socket.

## Quick deploy (Linux server + Docker)

```bash
# 1. copy project to server
scp -r gamehub user@server:/opt/gamehub
ssh user@server

cd /opt/gamehub
cp .env.example .env
# edit .env -> set ADMIN_PASS + SECRET_KEY (openssl rand -hex 32)
nano .env

docker compose up -d --build
# open http://<server-ip>:8000  login: admin / your ADMIN_PASS
```

Manager needs `/var/run/docker.sock` (already in compose) + ports open:
- **8000** manager UI
- **8211/udp + 27015/udp + 25575/tcp** Palworld defaults (adjust per game)
- Use UFW: `ufw allow 8000/tcp && ufw allow 8211/udp && ufw allow 27015/udp`

Put behind HTTPS in production (Caddy/Nginx reverse proxy).

## Usage

1. **+ New** → pick template (Palworld etc), name `palworld-1`, set RCON/admin password → Create + Start.
2. **Console tab**: live logs (websocket) + RCON command box. Quick buttons: `ShowPlayers`, `Save`, `Broadcast …`.
3. **Players tab**: list via `ShowPlayers`, Kick/Ban buttons (uses `KickPlayer <steamid>` / `BanPlayer <steamid>` on Palworld).
4. **Config tab**: raw `PalWorldSettings.ini` editor. **Palworld⚙ tab**: form editor for `OptionSettings=(...)`.
5. **Env tab**: JSON env (server name, passwords, ports). Save → **Update** button recreates container to apply.
6. **Backups tab**: one-click tar.gz of volume → `./backups/`. Restore: `tar -xzf backups/<file> -C /var/lib/docker/volumes/<vol>/_data --strip-components=1` (stop server first).
7. **⬆ Update**: pulls latest image + recreates (how SteamCMD games update).

## Adding other games

**No-code path (Generic template)**: + New → Generic/Custom → paste any Docker image + ports/env. Done.

**Template path (recommended)**: edit `manager/app/games/templates.py` — copy e.g. the `valheim` block:

```python
_register(GameTemplate(
    id="rust", name="Rust",
    image="didstopia/rust-server:latest",
    ports=[{"host": 28015, "container": 28015, "protocol": "udp"}],
    env_defaults={"RUST_SERVER_NAME": "GameHub Rust", ...},
    volumes={"data": "/steamcmd/rust"},
    rcon_supported=True, rcon_port=28016,
    player_list_command="status",
))
```

Restart manager → new template appears in UI. Fields:
| field | meaning |
|---|---|
| `image` | Docker image |
| `ports` | host→container defaults |
| `env_defaults` | default env vars (overridable per server) |
| `volumes` | named-volume key → container path |
| `rcon_*` | RCON support, port, password env key |
| `config_files` | editable files inside volume |
| `helpful_commands` | quick buttons in console |

Player parsing per game lives in `manager/app/rcon_helpers.py::parse_players` — extend for new formats.

## Project layout

```
gamehub/
  docker-compose.yml      # manager + (optional) pre-seeded game
  .env.example
  manager/
    Dockerfile
    requirements.txt
    app/
      main.py             # all REST + WS endpoints, static hosting
      config.py auth.py models.py
      docker_service.py   # container lifecycle + instances.json
      rcon.py             # Source RCON client
      rcon_helpers.py     # per-server RCON target + player parsing
      palworld_config.py  # OptionSettings parser/serializer
      backups.py
      games/base.py       # GameTemplate + ServerInstance dataclasses
      games/templates.py  # ★ add games here ★
    static/               # frontend (index.html, app.js, styles.css)
  data/ backups/          # runtime (volumes, instances.json, archives)
```

## API (all except POST /api/login need `Authorization: Bearer <token>`)

- `GET /api/templates`, `GET/POST /api/servers`, `GET/DELETE /api/servers/{id}`
- `POST /api/servers/{id}/start|stop|restart|update`
- `GET /api/servers/{id}/stats`, `GET /api/servers/{id}/logs`, `WS /api/servers/{id}/logs/ws?token=`
- `POST /api/servers/{id}/rcon {command}`, `GET /api/servers/{id}/players`
- `GET/PUT /api/servers/{id}/env`, `GET/PUT /api/servers/{id}/config`, `GET/PUT /api/servers/{id}/palworld-settings`
- `GET /api/backups`, `POST /api/servers/{id}/backup`, `DELETE /api/backups/{f}`, `GET /api/system`

## Security notes

- Single admin user, JWT in localStorage. Change defaults, use HTTPS, don't expose RCON ports publicly (bind to 127.0.0.1 or firewall them).
- Manager has docker-socket access = root-equivalent on host. Keep host patched, backups off-site.

## Dev (no docker)

```bash
cd manager
pip install -r requirements.txt
MOCK_DOCKER=true ADMIN_PASS=test SECRET_KEY=test uvicorn app.main:app --reload
# http://127.0.0.1:8000
```
