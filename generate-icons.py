#!/usr/bin/env python3
"""Generate simple placeholder PNG icons for the Chrome extension."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ICON_DIR = ROOT / "icons"
BACKGROUND = (239, 107, 63, 255)
FOREGROUND = (255, 244, 233, 255)
SHADOW = (198, 72, 31, 255)


def chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int, pixels: list[tuple[int, int, int, int]]) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            raw.extend((r, g, b, a))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    data = zlib.compress(bytes(raw), level=9)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", data) + chunk(b"IEND", b"")
    path.write_bytes(png)


def build_icon(size: int) -> list[tuple[int, int, int, int]]:
    pixels = [BACKGROUND] * (size * size)

    border = max(1, size // 16)
    inset = max(2, size // 8)
    stem = max(2, size // 7)
    top = inset
    bottom = size - inset
    left = inset
    mid = size // 2

    for y in range(size):
        for x in range(size):
            if x < border or y < border or x >= size - border or y >= size - border:
                pixels[y * size + x] = SHADOW

    def draw_rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int]) -> None:
        for y in range(max(0, y0), min(size, y1)):
            for x in range(max(0, x0), min(size, x1)):
                pixels[y * size + x] = color

    def draw_circle(cx: int, cy: int, radius: int, color: tuple[int, int, int, int]) -> None:
        rr = radius * radius
        for y in range(max(0, cy - radius), min(size, cy + radius + 1)):
            for x in range(max(0, cx - radius), min(size, cx + radius + 1)):
                dx = x - cx
                dy = y - cy
                if dx * dx + dy * dy <= rr:
                    pixels[y * size + x] = color

    draw_rect(left, top, left + stem, bottom, FOREGROUND)
    draw_rect(left, top, mid + stem // 2, top + stem, FOREGROUND)
    draw_circle(mid + inset // 2, size - inset - stem, stem + max(1, size // 18), FOREGROUND)
    draw_circle(mid + inset // 2, size - inset - stem, max(1, stem // 2), BACKGROUND)

    return pixels


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        pixels = build_icon(size)
        write_png(ICON_DIR / f"icon{size}.png", size, pixels)


if __name__ == "__main__":
    main()
