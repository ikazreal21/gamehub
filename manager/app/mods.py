"""Mod management: files in per-game mods dir + URL installs + catalog."""
from __future__ import annotations
import shutil
import urllib.request
import zipfile
from pathlib import Path

from . import docker_service
from .games.templates import get_template


def mods_dir(server_id: str) -> Path:
    inst = docker_service.get_instance(server_id)
    if inst is None:
        raise KeyError("Server not found")
    tpl = get_template(inst.game)
    sub = (tpl.mods_dir if tpl and tpl.mods_dir else "mods").strip("/")
    base = docker_service.volume_path(server_id, "data")
    if base is None:
        raise ValueError("Volume not available (server never started?)")
    p = Path(base) / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_mods(server_id: str) -> list[dict]:
    d = mods_dir(server_id)
    out = []
    for f in sorted(d.iterdir()):
        st = f.stat()
        out.append({
            "name": f.name, "is_dir": f.is_dir(),
            "size_bytes": st.st_size, "modified": st.st_mtime,
        })
    return out


def delete_mod(server_id: str, name: str):
    if "/" in name or ".." in name or not name:
        raise ValueError("Invalid name")
    p = mods_dir(server_id) / name
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        raise KeyError("Mod not found")


def download_mod(server_id: str, url: str, filename: str = "", extract_zip: bool = True) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http(s)://")
    d = mods_dir(server_id)
    name = filename.strip() or url.split("?")[0].rstrip("/").split("/")[-1] or "mod.bin"
    if "/" in name or ".." in name:
        raise ValueError("Invalid filename")
    dest = d / name
    req = urllib.request.Request(url, headers={"User-Agent": "GameHub/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    if extract_zip and dest.suffix.lower() == ".zip":
        with zipfile.ZipFile(dest) as z:
            z.extractall(d)
        dest.unlink()  # remove archive, keep extracted content
        return f"extracted {name}"
    return name
