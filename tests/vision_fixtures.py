"""Tiny deterministic synthetic image fixtures for vision tests.

A 1x1 PNG and JPEG are generated from fixed bytes, so tests never depend on
network access, image libraries, or external fixtures. Only these tiny,
obviously-synthetic bytes are used; no real image content is ever committed or
logged.
"""

from __future__ import annotations

import base64

# 16x16 solid PNG (88 bytes). A real, decodable image (solid red) so the same
# deterministic fixture also works against a live vision model; it is still
# tiny and obviously synthetic.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000001000000010080200000090916836"
    "000000164944415478da63f8a0a040126218d530aa61f86a00006e5e30100ce312"
    "630000000049454e44ae426082"
)

# Minimal 1x1 JPEG (baseline, ~125 bytes).
JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605"
    "080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e"
    "2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b"
    "080001000101011100ffc4001f00000105010101010101000000000000000001"
    "02030405060708090a0bffc400b5100002010303020403050504040000017d01"
    "020300041105122131410613516107227114328191a1082342b1c11552d1f024"
    "33627282090a161718191a25262728292a3435363738393a434445464748494a"
    "535455565758595a636465666768696a737475767778797a838485868788898a"
    "92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5"
    "c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6"
    "f7f8f9faffda0008010100003f00d2cf20ffd9"
)

# 1x1 WebP (RIFF/WEBP VP8L).
WEBP_BYTES = bytes.fromhex(
    "5249464624000000574542505650384c170000002f000000100710111188"
    "8888fe07fc0000fc0000000000"
)


def data_url(media_type: str, payload: bytes) -> str:
    return f"data:{media_type};base64," + base64.b64encode(payload).decode("ascii")


PNG_DATA_URL = data_url("image/png", PNG_BYTES)
JPEG_DATA_URL = data_url("image/jpeg", JPEG_BYTES)
WEBP_DATA_URL = data_url("image/webp", WEBP_BYTES)


__all__ = [
    "PNG_BYTES",
    "JPEG_BYTES",
    "WEBP_BYTES",
    "PNG_DATA_URL",
    "JPEG_DATA_URL",
    "WEBP_DATA_URL",
    "data_url",
]
