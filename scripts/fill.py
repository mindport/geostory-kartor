#!/usr/bin/env python3
"""Fill the blank paper of the newer Häradsekonomiska kartan edition (layer hek) with the older edition (hek2).

Jens 2026-11-01 (GeoStory debug log 2026-10-31 17_20, Gottröra): on a/b sheets the newer edition (e.g. Stockholms län
1901–1906) is blank paper where the area belonged to the neighbouring county, while the older edition in hek2 (e.g.
Uppsala län 1859–1863) has the map there. For every block where hek2 exists, the blank paper of the hek block (large,
smooth, light areas) gets the hek2 pixels under it. Blank paper with nothing under it stays as it is.
Blocks where hek is missing get the hek2 block. Changed blocks are sent to api/histupload.php with fill=1: the server
replaces the block and keeps the original once in api/_cache/histtiles/hek_fillbak/.

Environment: HEK_UPLOAD_KEY, SHEETS (JSON list of hek2 sheets {id, poly}), ZOOMS (default "8-16"), DRY ("1" = no upload),
OUTDIR (optional: also save the filled blocks as PNG there, for checking).
"""
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import zipfile
import urllib.request
import urllib.error

import numpy as np
from PIL import Image
from scipy import ndimage

TILE = "https://www.geostory.se/api/histmaptile.php?l=%s&z=%d&b=4&bx=%d&by=%d"
UPLOAD = "https://www.geostory.se/api/histupload.php"
PART = 60
key = os.environ.get("HEK_UPLOAD_KEY", "")
sheets = json.loads(os.environ["SHEETS"])
z0, z1 = [int(v) for v in os.environ.get("ZOOMS", "8-16").split("-")]
dry = os.environ.get("DRY", "") == "1"


def get(layer, z, bx, by):
    """A block as RGBA array, or None (204 = no block). one.com answers 429 to many requests: wait and try again."""
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(urllib.request.Request(TILE % (layer, z, bx, by), headers={"User-Agent": "geostory-kartor"}), timeout=60) as r:
                time.sleep(0.4)
                if r.status == 204:
                    return None
                return np.asarray(Image.open(io.BytesIO(r.read())).convert("RGBA")).astype(np.int32)
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return None
            time.sleep(5 * attempt)
        except Exception:
            time.sleep(5 * attempt)
    raise RuntimeError("block %s %d/%d_%d" % (layer, z, bx, by))


def blank_paper(img):
    """Large connected areas of light, warm, smooth paper (not a field or a gap between houses)."""
    rgb = img[..., :3]
    gray = rgb.mean(axis=2)
    mean = ndimage.uniform_filter(gray, 15)
    sq = ndimage.uniform_filter(gray * gray, 15)
    std = np.sqrt(np.maximum(sq - mean * mean, 0))
    paper = (gray > 200) & (rgb[..., 0] >= rgb[..., 2]) & (std < 6) & (img[..., 3] > 0)
    paper = ndimage.binary_opening(paper, structure=np.ones((9, 9)))
    lab, n = ndimage.label(paper)
    if n == 0:
        return np.zeros(paper.shape, bool)
    sizes = ndimage.sum(paper, lab, range(1, n + 1))
    big = np.zeros(n + 1, bool)
    big[1:] = sizes > paper.size * 0.03
    return ndimage.binary_dilation(big[lab], structure=np.ones((5, 5)))


def blocks(poly, z):
    """The 4x4 blocks (1024 px at zoom z) that touch the sheet's bounding box."""
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    n = 2 ** z

    def tx(lon):
        return int((lon + 180) / 360 * n)

    def ty(lat):
        return int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    for by in range(ty(max(lats)) // 4, ty(min(lats)) // 4 + 1):
        for bx in range(tx(min(lons)) // 4, tx(max(lons)) // 4 + 1):
            yield bx, by


def upload(files):
    for i in range(0, len(files), PART):
        part = files[i:i + PART]
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as t:
            path = t.name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            for name, data in part:
                zf.writestr(name, data)
        code, body = "", ""
        for attempt in range(1, 6):
            r = subprocess.run(["curl", "-s", "-m", "240", "-w", "\n%{http_code}", "-H", "X-GS-Key: " + key,
                                "-F", "fill=1", "-F", "zip=@" + path, UPLOAD], capture_output=True, text=True)
            body, _, code = r.stdout.rpartition("\n")
            if code == "200":
                break
            time.sleep(10 * attempt)
        os.unlink(path)
        if code != "200":
            raise RuntimeError("upload answered %s %s" % (code, body[:200]))
        print("  upload %s" % body.strip(), flush=True)


report = []
seen = set()
ok = True
for s in sheets:
    t = time.time()
    changed, checked = [], 0
    try:
        for z in range(z1, z0 - 1, -1):
            for bx, by in blocks(s["poly"], z):
                if (z, bx, by) in seen:
                    continue
                seen.add((z, bx, by))
                old = get("hek2", z, bx, by)
                if old is None or old[..., 3].max() == 0:
                    continue
                checked += 1
                new = get("hek", z, bx, by)
                if new is None:
                    out = old
                else:
                    fill = blank_paper(new) & (old[..., 3] > 0)
                    if fill.mean() < 0.005:
                        continue
                    out = new.copy()
                    out[fill] = old[fill]
                buf = io.BytesIO()
                Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA").save(buf, "WEBP", quality=75)
                changed.append(("%d/b4/%d_%d.webp" % (z, bx, by), buf.getvalue()))
                if os.environ.get("OUTDIR"):
                    Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA").save(os.path.join(os.environ["OUTDIR"], "%d_%d_%d.png" % (z, bx, by)))
        if changed and not dry:
            upload(changed)
        line = "%s: %d blocks with the older edition, %d filled%s [%ds]" % (s["id"], checked, len(changed), " (dry)" if dry else "", time.time() - t)
    except Exception as e:
        ok = False
        line = "%s: failed %s" % (s["id"], e)
    print(line, flush=True)
    report.append(line)

with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a") as f:
    f.write("\n".join("- " + l for l in report) + "\n")
sys.exit(0 if ok else 1)
