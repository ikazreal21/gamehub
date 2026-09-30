"""Custom Dockerfile builds: paste/upload a Dockerfile (+ optional context zip),
built server-side into a per-server image tag. Admin-only, root-equivalent -
same trust level as pulling arbitrary images. Raw compose upload is NOT
supported (privileged/host mounts would escape the manager's control)."""
from __future__ import annotations
import io
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from . import config, docker_service

MAX_DOCKERFILE_BYTES = 200_000
MAX_LOG_LINES = 500


def builds_dir(server_id: str) -> Path:
    if "/" in server_id or ".." in server_id:
        raise ValueError("Invalid server id")
    p = Path(config.DATA_DIR) / "builds" / server_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_dockerfile(server_id: str, content: str) -> Path:
    if len(content.encode()) > MAX_DOCKERFILE_BYTES:
        raise ValueError("Dockerfile too large (200KB max)")
    p = builds_dir(server_id) / "Dockerfile"
    p.write_text(content)
    return p


def load_dockerfile(server_id: str) -> str:
    p = builds_dir(server_id) / "Dockerfile"
    return p.read_text() if p.exists() else ""


def save_context_zip(server_id: str, data: bytes) -> str:
    """Extract an uploaded context zip next to the Dockerfile. Returns member count."""
    d = builds_dir(server_id) / "context"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    zf = zipfile.ZipFile(io.BytesIO(data))
    # guard against zip-slip
    for m in zf.namelist():
        if m.startswith("/") or ".." in m:
            raise ValueError(f"Unsafe path in zip: {m}")
    zf.extractall(d)
    return f"{len(zf.namelist())} files"


def custom_tag(server_id: str) -> str:
    inst = docker_service.get_instance(server_id)
    base = (inst.name if inst else server_id).lower()
    return f"gamehub-custom-{base}:latest"


def build(server_id: str, log: list[str]):
    """Run docker build for the stored Dockerfile (+ context dir). Appends to log."""
    c = docker_service.docker_client()
    if c is None:
        raise RuntimeError("Docker not available (MOCK_DOCKER?)")
    d = builds_dir(server_id)
    dockerfile = (d / "Dockerfile").read_text() if (d / "Dockerfile").exists() else ""
    if not dockerfile.strip():
        raise ValueError("No Dockerfile stored for this server")
    tag = custom_tag(server_id)
    ctx = d / "context"
    log.append(f"Building {tag} …")
    if ctx.exists():
        # tar the context dir with Dockerfile injected at root
        tmp = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
        try:
            with tarfile.open(fileobj=tmp, mode="w") as tar:
                tar.add(str(ctx), arcname=".")
                df = io.BytesIO(dockerfile.encode())
                ti = tarfile.TarInfo("Dockerfile")
                ti.size = len(df.getvalue())
                tar.addfile(ti, df)
            tmp.close()
            with open(tmp.name, "rb") as f:
                stream = c.api.build(fileobj=f, tag=tag, rm=True, decode=True, custom_context=True)
                _drain(stream, log)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    else:
        stream = c.api.build(fileobj=io.BytesIO(dockerfile.encode()), tag=tag, rm=True, decode=True)
        _drain(stream, log)
    log.append(f"Built {tag}")
    docker_service.set_image(server_id, tag)
    return tag


def _drain(stream, log: list[str]):
    for chunk in stream:
        if not isinstance(chunk, dict):
            continue
        if "stream" in chunk:
            line = str(chunk["stream"]).rstrip()
            if line:
                log.append(line[-500:])
        elif "error" in chunk:
            raise RuntimeError(str(chunk["error"])[:500])
        if len(log) > MAX_LOG_LINES:
            del log[: len(log) - MAX_LOG_LINES]
