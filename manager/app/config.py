"""Central configuration via environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / ".." / "data")))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(BASE_DIR / ".." / "backups")))
INSTANCES_FILE = Path(os.getenv("INSTANCES_FILE", str(DATA_DIR / "instances.json")))

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
# Set ADMIN_PASS in production! Default is changeme.
ADMIN_PASS = os.getenv("ADMIN_PASS", "changeme")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-in-production")
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "720"))

# Comma-separated IPs/CIDRs allowed to reach /admin + management API.
# Empty = no IP restriction (login still required). Localhost always allowed.
ADMIN_ALLOWED_IPS = os.getenv("ADMIN_ALLOWED_IPS", "")

# Docker host - defaults to socket. Manager container mounts /var/run/docker.sock
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")

# When True (dev without docker), API runs in mock mode.
MOCK_DOCKER = os.getenv("MOCK_DOCKER", "false").lower() == "true"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
