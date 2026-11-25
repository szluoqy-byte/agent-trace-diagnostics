"""Integrity checks shared by the bundled diagnosis engines."""

import hashlib
import json


def verify_source_tree(directory, engine):
    manifest = json.loads((directory / "UPSTREAM.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        try:
            content = (directory / relative).read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f"{engine} 官方源码完整性检查失败：{relative}") from exc
        if hashlib.sha256(content).hexdigest() == expected:
            continue
        # Older Windows checkouts may still contain CRLF.
        if b"\r\n" in content and hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() == expected:
            continue
        raise ValueError(f"{engine} 官方源码完整性检查失败：{relative}")
    return manifest
