"""Volume backups: tar.gz named volumes (or dev data dir) into BACKUP_DIR."""
from __future__ import annotations
import tarfile
from datetime import datetime
from pathlib import Path

from . import config, docker_service


def _backup_filename(server_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{server_id}-{ts}.tar.gz"


def create_backup(server_id: str) -> dict:
    inst = docker_service.get_instance(server_id)
    if inst is None:
        raise KeyError("Server not found")
    fname = _backup_filename(server_id)
    dest = Path(config.BACKUP_DIR) / fname
    dest.parent.mkdir(parents=True, exist_ok=True)

    src = docker_service.volume_path(server_id, "data")
    if src is None or not Path(src).exists():
        # nothing to back up yet - create empty marker archive
        with tarfile.open(dest, "w:gz") as tf:
            pass
        return {"filename": fname, "size_bytes": dest.stat().st_size}

    with tarfile.open(dest, "w:gz") as tf:
        tf.add(str(src), arcname="data")
    return {"filename": fname, "size_bytes": dest.stat().st_size}


def list_backups(server_id: str | None = None) -> list[dict]:
    out = []
    bdir = Path(config.BACKUP_DIR)
    if not bdir.exists():
        return out
    for f in sorted(bdir.glob("*.tar.gz"), reverse=True):
        if server_id and not f.name.startswith(server_id):
            continue
        st = f.stat()
        out.append({
            "filename": f.name,
            "size_bytes": st.st_size,
            "created": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return out


def delete_backup(filename: str):
    # prevent path traversal
    if "/" in filename or ".." in filename:
        raise ValueError("Invalid filename")
    p = Path(config.BACKUP_DIR) / filename
    if p.exists():
        p.unlink()
