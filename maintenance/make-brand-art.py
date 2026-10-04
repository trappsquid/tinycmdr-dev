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
PAGE_ICON = ROOT / "assets" / "page-icon.png"
PAGE_MARK = ROOT / "assets" / "page-mark.png"
# left, upper, right, lower: the designer's framing with the bottom EXTENDED. Their
# original box stopped at y=850 while the badge's content reaches y=939, so the emblem's
# bottom was cut off in the rail (operator report, 2026-10-03). Measured content box at
# max(rgb)>=45: x 58..912, y 5..939 - the sides and top of their framing are kept, because
# a box on the whole content box includes the plate's glow and shrinks the emblem.
CROP = (225, 80, 900, 940)
COLS, ROWS = 24, 9                  # the app rail's budget (RAIL_WIDTH is 26)
# The PAGE icon is square, so it takes a square box around the same measured content
# (x 58..912, y 5..939): 854 wide, centred vertically -> y 43..897.
PAGE_CROP = (58, 43, 912, 897)
PAGE_FLAT = 45                      # max(rgb) at/below this is plate, not emblem


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


def png_encode(w, h, rows, colortype=2):
    """A PNG (8-bit, filter-0 rows) from raw RGB or RGBA byte rows - stdlib only."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, colortype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def page_icon(size=512, bg=(0x0F, 0x11, 0x14), crop=None, alpha=False):
    """The PAGE icon: the emblem from the master, flattened and box-downscaled.

    Same doctrine as the rail art - the master is the source of truth, this is the
    dependency-free derivation - except this one ships as FILES (assets/page-icon.png and
    assets/page-mark.png): the page asks for them by URL, and a browser wants a real PNG.

    Two forms, because they are used in two places:
      * alpha=False (page-icon.png): the plate flattened to the theme background. This is
        what an OS composites for a home-screen icon or an apple-touch-icon, where a
        transparent PNG lands on white or black depending on the OS mood.
      * alpha=True (page-mark.png): the same emblem with the plate TRANSPARENT, so it sits
        on the page's own background (header, empty state) with no square edge.

    The master's near-black plate and its glow are dropped either way (the plate is
    grainy: keeping it is both noisy to look at and ~300KB to transfer), and a pixel's
    coverage becomes its alpha, which is what gives the transparent form clean edges.
    """
    w, h, bpp, rows = decode_png(MASTER)
    left, upper, right, lower = crop or PAGE_CROP
    right, lower = min(right, w), min(lower, h)
    span = min(right - left, lower - upper)
    step = span / float(size)
    out_rows = []
    for oy in range(size):
        y0 = int(upper + oy * step)
        y1 = min(max(int(upper + (oy + 1) * step), y0 + 1), lower)
        line = bytearray()
        for ox in range(size):
            x0 = int(left + ox * step)
            x1 = min(max(int(left + (ox + 1) * step), x0 + 1), right)
            r = g = b = n = 0.0
            total = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    total += 1
                    pr, pg, pb, pa = pixel(rows, bpp, x, y)
                    if pa == 0 or max(pr, pg, pb) <= PAGE_FLAT:
                        continue
                    f = pa / 255.0
                    r += pr * f
                    g += pg * f
                    b += pb * f
                    n += f
            if n == 0:
                line += bytes((0, 0, 0, 0)) if alpha else bytes(bg)
                continue
            cover = min(1.0, n / max(1, total))
            if alpha:
                line += bytes((int(r / n + 0.5), int(g / n + 0.5), int(b / n + 0.5),
                               int(cover * 255 + 0.5)))
            else:
                rr = bg[0] + (r / n - bg[0]) * cover
                gg = bg[1] + (g / n - bg[1]) * cover
                bb = bg[2] + (b / n - bg[2]) * cover
                line += bytes((int(rr + 0.5), int(gg + 0.5), int(bb + 0.5)))
        out_rows.append(line)
    return png_encode(size, size, out_rows, colortype=6 if alpha else 2)


CHIBI = ROOT / "assets" / "page-chibi.png"
CHIBI_MASTER = ROOT / "assets" / "branding" / "tinycmdr-chibi-master.png"


def _rgba_crop(master=None, margin=0.06):
    """(side, RGBA rows) - the master cut to its content square, alpha made real.

    A cut-out master (RGBA: the chibi, the helm) keeps its own alpha and is cropped to
    what is actually drawn - that is what makes it usable at any CSS size without a
    transparent margin doing the layout. An RGB master (the badge) has its plate turned
    into transparency, with the edge alpha derived from luminance so the rim stays soft.
    """
    w, h, bpp, rows = decode_png(master or MASTER)
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixel(rows, bpp, x, y)
            if bpp == 4:
                if a > 15:
                    xs.append(x)
                    ys.append(y)
            elif a > 15 and max(r, g, b) > PAGE_FLAT:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise SystemExit("no content found in %s" % (master or MASTER))
    cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
    side = max(max(xs) - min(xs), max(ys) - min(ys))
    side = min(w, h, int(side * (1.0 + 2 * margin)))
    left = max(0, min(w - side, cx - side // 2))
    top = max(0, min(h - side, cy - side // 2))
    out = []
    for y in range(top, top + side):
        line = bytearray()
        for x in range(left, left + side):
            r, g, b, a = pixel(rows, bpp, x, y)
            if bpp == 3:
                a = 0 if max(r, g, b) <= PAGE_FLAT else min(
                    255, int((max(r, g, b) - PAGE_FLAT) * 255 / 70.0))
            line += bytes((r, g, b, a))
        out.append(line)
    return side, out


def _rgba_resample(side, rows, size):
    """Box-resample RGBA rows, alpha-weighted: a pixel's coverage becomes its alpha, which
    is what keeps a cut-out's edge clean at a smaller size instead of fringed."""
    step = side / float(size)
    out = []
    for oy in range(size):
        y0 = int(oy * step)
        y1 = min(max(int((oy + 1) * step), y0 + 1), side)
        line = bytearray()
        for ox in range(size):
            x0 = int(ox * step)
            x1 = min(max(int((ox + 1) * step), x0 + 1), side)
            r = g = b = n = 0.0
            total = 0
            for y in range(y0, y1):
                row = rows[y]
                for x in range(x0, x1):
                    o = x * 4
                    a = row[o + 3]
                    total += 1
                    if a == 0:
                        continue
                    f = a / 255.0
                    r += row[o] * f
                    g += row[o + 1] * f
                    b += row[o + 2] * f
                    n += f
            if n == 0:
                line += bytes((0, 0, 0, 0))
            else:
                cover = min(1.0, n / max(1, total))
                line += bytes((int(r / n + 0.5), int(g / n + 0.5), int(b / n + 0.5),
                               int(cover * 255 + 0.5)))
        out.append(line)
    return out


def page_art(size=None, master=None, margin=0.06):
    """The cut-out pipeline for page art: content-cropped square, real alpha, and an
    optional resample (`size=None` keeps the designer's own pixels - the backdrop)."""
    side, rows = _rgba_crop(master, margin)
    if size and size != side:
        rows = _rgba_resample(side, rows, size)
        side = size
    return side, png_encode(side, side, rows, colortype=6)


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
    ap.add_argument("--page-icon", type=int, metavar="SIZE",
                    help="write assets/page-icon.png (the opaque favicon/home-screen icon)")
    ap.add_argument("--page-mark", type=int, metavar="SIZE",
                    help="write assets/page-mark.png (the transparent emblem for the page)")
    ap.add_argument("--page-chibi", type=int, metavar="SIZE",
                    help="write assets/page-chibi.png (the chibi, content-cropped and "
                         "resampled with alpha) - the page's empty-state figure")
    a = ap.parse_args()
    configure(a.master, a.crop, a.cols, a.rows)
    if a.page_chibi:
        side, data = page_art(a.page_chibi, master=CHIBI_MASTER)
        CHIBI.write_bytes(data)
        print("wrote %s (%dx%d, %d bytes, transparent)"
              % (CHIBI.relative_to(ROOT), side, side, len(data)))
        if not (a.write or a.ansi or a.page_icon or a.page_mark):
            return 0
    if a.page_icon or a.page_mark:
        size = a.page_icon or a.page_mark
        if a.page_icon:
            data = page_icon(size)
            PAGE_ICON.write_bytes(data)
            print("wrote %s (%dx%d, %d bytes, opaque)"
                  % (PAGE_ICON.relative_to(ROOT), size, size, len(data)))
        if a.page_mark:
            data = page_icon(size, alpha=True)
            PAGE_MARK.write_bytes(data)
            print("wrote %s (%dx%d, %d bytes, transparent)"
                  % (PAGE_MARK.relative_to(ROOT), size, size, len(data)))
        if not (a.write or a.ansi):
            return 0
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
