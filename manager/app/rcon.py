"""Minimal Source RCON client (Palworld, Minecraft Java, Zomboid, etc)."""
import socket
import struct


class RconError(Exception):
    pass


class RconClient:
    """Sync RCON client implementing Valve Source RCON protocol."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._req_id = 1

    def _packet(self, req_id: int, ptype: int, body: str) -> bytes:
        data = body.encode("utf-8") + b"\x00\x00"
        size = 4 + 4 + len(data)
        return struct.pack("<iii", size, req_id, ptype) + data

    def _read_packet(self):
        header = self._recvall(12)
        if len(header) < 12:
            raise RconError("Short RCON response")
        size, req_id, ptype = struct.unpack("<iii", header)
        body = self._recvall(max(0, size - 8))
        # strip 2 nulls
        text = body[:-2].decode("utf-8", errors="replace") if len(body) >= 2 else ""
        return req_id, ptype, text

    def _recvall(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))  # type: ignore
            if not chunk:
                break
            buf += chunk
        return buf

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # auth: type 3
        assert self._sock is not None
        self._sock.sendall(self._packet(self._req_id, 3, self.password))
        req_id, _, _ = self._read_packet()
        if req_id == -1:
            raise RconError("RCON auth failed (bad password?)")
        self._req_id += 1

    def exec(self, command: str) -> str:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        rid = self._req_id
        self._req_id += 1
        self._sock.sendall(self._packet(rid, 2, command))
        # read response(s); Palworld sends one packet, sometimes + empty terminator
        _, _, body = self._read_packet()
        # drain any trailing empty packet without blocking long
        self._sock.settimeout(0.3)
        try:
            while True:
                _, _, extra = self._read_packet()
                if not extra:
                    break
                body += extra
        except Exception:
            pass
        finally:
            self._sock.settimeout(self.timeout)
        return body

    def close(self):
        try:
            if self._sock:
                self._sock.close()
        finally:
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


def rcon_command(host: str, port: int, password: str, command: str, timeout: float = 5.0) -> str:
    with RconClient(host, port, password, timeout) as c:
        return c.exec(command)
