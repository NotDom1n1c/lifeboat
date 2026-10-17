"""Streaming AES-256-GCM file encryption.

File format (all integers big-endian):

    magic "LFB1" | nonce prefix (8 bytes) | chunk size (4 bytes)
    then per chunk:  ciphertext length (4 bytes) | ciphertext + 16-byte GCM tag

Each chunk uses nonce = prefix + chunk index, and authenticates the header, its index and a
"last chunk" flag. So a backup cannot be modified, reordered or cut short without decryption
failing: a damaged backup is detected, never restored half-way.
"""

from __future__ import annotations

import base64
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"LFB1"
CHUNK = 1 << 20  # 1 MiB


class IntegrityError(Exception):
    """The encrypted file is damaged, truncated or was encrypted with another key."""


def load_key(value: str | None = None) -> bytes:
    raw = value if value is not None else os.environ.get("LIFEBOAT_KEY", "")
    if not raw:
        raise SystemExit("LIFEBOAT_KEY is not set (32 random bytes, base64)")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise SystemExit("LIFEBOAT_KEY must decode to exactly 32 bytes")
    return key


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _aad(header: bytes, index: int, last: bool) -> bytes:
    return header + struct.pack(">I?", index, last)


def encrypt_file(src: str, dst: str, key: bytes, chunk: int = CHUNK) -> None:
    aes = AESGCM(key)
    prefix = os.urandom(8)
    header = MAGIC + prefix + struct.pack(">I", chunk)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(header)
        index = 0
        block = fin.read(chunk)
        while True:
            nxt = fin.read(chunk)
            last = not nxt
            ct = aes.encrypt(prefix + struct.pack(">I", index), block, _aad(header, index, last))
            fout.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            block, index = nxt, index + 1


def decrypt_file(src: str, dst: str, key: bytes) -> None:
    aes = AESGCM(key)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        header = fin.read(16)
        if len(header) != 16 or header[:4] != MAGIC:
            raise IntegrityError("not a LIFEBOAT backup (bad header)")
        prefix = header[4:12]
        index = 0
        while True:
            size_raw = fin.read(4)
            if len(size_raw) != 4:
                raise IntegrityError("backup is truncated (no final chunk)")
            (size,) = struct.unpack(">I", size_raw)
            ct = fin.read(size)
            if len(ct) != size:
                raise IntegrityError(f"backup is truncated in chunk {index}")
            last = fin.peek(1)[:1] == b"" if hasattr(fin, "peek") else _at_end(fin)
            try:
                fout.write(aes.decrypt(prefix + struct.pack(">I", index), ct, _aad(header, index, last)))
            except Exception as err:  # InvalidTag
                raise IntegrityError(f"chunk {index} failed authentication: wrong key or modified data") from err
            if last:
                return
            index += 1


def _at_end(f) -> bool:
    pos = f.tell()
    end = f.read(1) == b""
    f.seek(pos)
    return end
