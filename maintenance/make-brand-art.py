#!/usr/bin/env python3
"""Re-derive the app-rail brand art from the badge master, with no third-party deps.

The SHIPPED artifact (assets/tui-rail-badge.json) is the designer's own render: PIL
LANCZOS downscaling keeps faint edge pixels that a box filter drops, and their version
carries the badge's lower detail (measured: 19 lit dots in the last row against 3 for the
box filter). This script is the *fallback*: the same recipe - crop, opaque near-black
becomes background, downscale into a 2x4-dot Braille grid, colour each cell by the mean of
its lit dots with saturation restored - implemented on the stdlib alone, so the art can be
regenerated on a host that has neither PIL nor numpy (this runtime's own rule: three
dependencies, and neither of those is one).

    python maintenance/make-brand-art.py --write    # rewrite the artifact (box filter)
    python maintenance/make-brand-art.py --ansi     # print the colour version to look at
    python maintenance/make-brand-art.py --master assets/branding/tinycmdr-helm-master.png \
        --crop 134,37,830,945 --cols 20 --rows 9    # the helm alternative, if ever wanted
"""
import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "branding" / "tinycmdr-badge-master.png"
ART = ROOT / "assets" / "tui-rail-badge.json"
# left, upper, right, lower: the designer's framing with the bottom EXTENDED. Their
# original box stopped at y=850 while the badge's content reaches y=939, so the emblem's
# bottom was cut off in the rail (operator report, 2026-10-03). Measured content box at
# max(rgb)>=45: x 58..912, y 5..939 - the sides and top of their framing are kept, because
# a box on the whole content box includes the plate's glow and shrinks the emblem.
CROP = (225, 80, 900, 940)
COLS, ROWS = 24, 9                  # the app rail's budget (RAIL_WIDTH is 26)


def configure(master=None, crop=None, cols=None, rows=None):
    """The designer's pick is the badge at 24x9; the helm is one flag away."""
    global MASTER, CROP, COLS, ROWS
    if master:
        MASTER = (ROOT / master) if not str(master).startswith("/") else Path(master)
    if crop:
        CROP = tuple(int(v) for v in crop)
    if cols:
        COLS = int(cols)
    if rows:
        ROWS = int(rows)
NEAR_BLACK = 25                     # max(rgb) below this, with any alpha, is background
MASK_ALPHA, MASK_RGB = 0.35, 35     # a dot is lit past both thresholds
SATURATION = 1.35
DOT_BITS = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
            (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80}


def decode_png(path):
    d = Path(path).read_bytes()
    pos, w, h, ct, idat = 8, None, None, None, b""
    while pos < len(d):
        ln = struct.unpack(">I", d[pos:pos + 4])[0]
        typ = d[pos + 4:pos + 8]
        data = d[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bitd, ct, comp, filt, inter = struct.unpack(">IIBBBBB", data[:13])
            if bitd != 8 or inter:
                raise SystemExit("expected an 8-bit non-interlaced PNG")
        elif typ == b"IDAT":
            idat += data
        pos += 12 + ln
    bpp = {2: 3, 6: 4}[ct]
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, bpp, rows


def pixel(rows, bpp, x, y):
    o = x * bpp
    r, g, b = rows[y][o], rows[y][o + 1], rows[y][o + 2]
    a = rows[y][o + 3] if bpp == 4 else 255
    return r, g, b, a


def load_cropped():
    w, h, bpp, rows = decode_png(MASTER)
    left, upper, right, lower = CROP
    right, lower = min(right, w), min(lower, h)
    out = []
    for y in range(upper, lower):
        line = []
        for x in range(left, right):
            r, g, b, a = pixel(rows, bpp, x, y)
            if a > 0 and max(r, g, b) < NEAR_BLACK:
                a = 0
            line.append((r, g, b, a))
        out.append(line)
    return out


def box_resample(src, cols, rows_n):
    """Area-average downscale, alpha-weighted so edges do not fringe to black."""
    sh, sw = len(src), len(src[0])
    out = []
    for ty in range(rows_n):
        y0, y1 = ty * sh // rows_n, max(ty * sh // rows_n + 1, (ty + 1) * sh // rows_n)
        line = []
        for tx in range(cols):
            x0, x1 = tx * sw // cols, max(tx * sw // cols + 1, (tx + 1) * sw // cols)
            rs = gs = bs = as_ = 0.0
            n = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    r, g, b, a = src[y][x]
                    wgt = a / 255.0
                    rs += r * wgt; gs += g * wgt; bs += b * wgt; as_ += a
                    n += 1
            if not n:
                line.append((0, 0, 0, 0))
                continue
            mean_a = as_ / n
            wsum = as_ / 255.0
            if wsum <= 0:
                line.append((0, 0, 0, 0))
            else:
                line.append((int(rs / wsum), int(gs / wsum), int(bs / wsum), int(mean_a)))
        out.append(line)
    return out


def to_cells(small):
    """Braille cell grid + per-cell hex colour, exactly as the rail draws it."""
    cells = []
    for cy in range(ROWS):
        row = []
        for cx in range(COLS):
            bits, sel = 0, []
            for (dx, dy), bit in DOT_BITS.items():
                r, g, b, a = small[cy * 4 + dy][cx * 2 + dx]
                if a / 255.0 > MASK_ALPHA and max(r, g, b) > MASK_RGB:
                    bits |= bit
                    sel.append((r, g, b))
            if not bits:
                row.append(None)
                continue
            mr = sum(s[0] for s in sel) / len(sel)
            mg = sum(s[1] for s in sel) / len(sel)
            mb = sum(s[2] for s in sel) / len(sel)
            gray = (mr + mg + mb) / 3
            mr = max(0, min(255, gray + (mr - gray) * SATURATION))
            mg = max(0, min(255, gray + (mg - gray) * SATURATION))
            mb = max(0, min(255, gray + (mb - gray) * SATURATION))
            row.append(["#%02x%02x%02x" % (int(mr), int(mg), int(mb)), chr(0x2800 + bits)])
        cells.append(row)
    return cells


def artifact():
    src = load_cropped()
    small = box_resample(src, COLS * 2, ROWS * 4)
    return {"cols": COLS, "rows": ROWS,
            "source": "assets/branding/tinycmdr-badge-master.png",
            "recipe": "crop %s, near-black transparent, box downscale to %dx%d dots, "
                      "braille + mean dot colour" % (list(CROP), COLS * 2, ROWS * 4),
            "cells": to_cells(small)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="rewrite the artifact (refused while one ships: needs --force)")
    ap.add_argument("--force", action="store_true",
                    help="really replace the shipped artifact with this fallback render")
    ap.add_argument("--ansi", action="store_true", help="print the colour version")
    ap.add_argument("--master", help="another master under assets/branding/ (or a path)")
    ap.add_argument("--crop", help="left,upper,right,lower (default: the badge's)")
    ap.add_argument("--cols", type=int, help="cells wide (default 24)")
    ap.add_argument("--rows", type=int, help="cells tall (default 9)")
    a = ap.parse_args()
    configure(a.master, a.crop, a.cols, a.rows)
    art = artifact()
    if a.ansi:
        for row in art["cells"]:
            line, last = "", None
            for cell in row:
                if cell is None:
                    line += " "
                    last = None
                    continue
                if cell[0] != last:
                    r, g, b = (int(cell[0][i:i + 2], 16) for i in (1, 3, 5))
                    line += "\x1b[38;2;%d;%d;%dm" % (r, g, b)
                    last = cell[0]
                line += cell[1]
            print(line + "\x1b[0m")
        dots = [sum(bin(ord(c[1]) - 0x2800).count("1") for c in row if c) for row in art["cells"]]
        print("dots per row: %s" % dots, file=sys.stderr)
    if a.write:
        if ART.exists() and not a.force:
            # The shipped artifact is the designer's render: LANCZOS keeps faint lower-edge
            # detail this box filter drops. Overwriting it must be deliberate.
            print("refusing to overwrite %s (the designer's render) - pass --force to "
                  "replace it with this dependency-free fallback" % ART.relative_to(ROOT),
                  file=sys.stderr)
            return 1
        ART.write_text(json.dumps(art, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("wrote %s" % ART.relative_to(ROOT))
    if not a.write and not a.ansi:
        current = json.loads(ART.read_text(encoding="utf-8")) if ART.exists() else None
        if current == art:
            print("the rail art matches the master")
            return 0
        print("STALE: assets/tui-rail-badge.json does not match the master - "
              "run --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
