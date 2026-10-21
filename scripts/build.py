#!/usr/bin/env python3
"""Build: one job's sheets of Häradsekonomiska kartan, uploaded to geostory.se.

For each sheet (from plan.py):
 1. download the scanned sheet (TIFF) from Lantmäteriet's open FTP (CC0)
 2. check that the image has the same proportions as the sheet in the index (the scans are cut to the map frame;
    a sheet with margins or a different shape is skipped and reported)
 3. place it with the four corners from the index (GCPs, EPSG:3006) and warp it to Web Mercator (EPSG:3857)
 4. cut it in blocks of 1024 px, WebP, transparent outside the sheet: a 1024 px tile at zoom z-2 is the block of 4x4
    tiles at zoom z that the app fetches (api/histmaptile.php?l=hek&b=4); zoom 8-16 (17 is made by the server)
 5. zip and POST to api/histupload.php with the sheet's name, years and corners (header X-GS-Key = HEK_UPLOAD_KEY)

Usage: build.py   (env SHEETS = json list from plan.py, HEK_UPLOAD_KEY)
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import zipfile

FTP = "ftp://download-opendata.lantmateriet.se/Haradsekonomiska_kartan/"
HALF = {"sodra": "Häradsekonomiska_Kartan_Södra", "norra": "Häradsekonomiska_Kartan_Norra"}
UPLOAD = "https://www.geostory.se/api/histupload.php"
MAX_SHAPE_DIFF = 0.03
PART = 120

key = os.environ["HEK_UPLOAD_KEY"]
sheets = json.loads(os.environ["SHEETS"])
report = []


# gdal2tiles is "gdal2tiles.py" in Ubuntu's GDAL package and "gdal2tiles" in Homebrew's.
GDAL2TILES = shutil.which("gdal2tiles") or shutil.which("gdal2tiles.py") or "gdal2tiles.py"


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def ftp_url(half, sid, name=""):
    return FTP + urllib.parse.quote(HALF[half]) + "/" + urllib.parse.quote(sid) + "/" + urllib.parse.quote(name)


def corners(ring):
    """Upper left, upper right, lower right, lower left (EPSG:3006 easting/northing)."""
    pts = sorted(ring, key=lambda p: -p[1])
    top = sorted(pts[:2], key=lambda p: p[0])
    bottom = sorted(pts[2:4], key=lambda p: p[0])
    return top[0], top[1], bottom[1], bottom[0]


def find_frame(path, white=238, frac=0.6, step=8):
    """The map frame inside white scan margins: the first/last row and column where at least `frac` of the pixels
    (read at 1/step size) are darker than `white`. Returns (x, y, width, height) in pixels or None."""
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Open(path)
    w, h = ds.RasterXSize, ds.RasterYSize
    bw, bh = max(1, w // step), max(1, h // step)
    n = min(3, ds.RasterCount)
    lum = sum(ds.GetRasterBand(i + 1).ReadAsArray(buf_xsize=bw, buf_ysize=bh).astype(np.float32) for i in range(n)) / n
    dark = lum < white
    rows = np.where(dark.mean(axis=1) > frac)[0]
    cols = np.where(dark.mean(axis=0) > frac)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    x0, x1 = int(cols[0]) * step, min(w, (int(cols[-1]) + 1) * step)
    y0, y1 = int(rows[0]) * step, min(h, (int(rows[-1]) + 1) * step)
    return x0, y0, x1 - x0, y1 - y0


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def build(s):
    sid = s["id"]
    if len(s["ring3006"]) != 4:
        return "skipped: the sheet has %d corners in the index" % len(s["ring3006"])
    listing = subprocess.run(["curl", "-s", "-m", "60", "--list-only", ftp_url(s["half"], sid)], capture_output=True, text=True).stdout.split()
    tifs = [f for f in listing if f.lower().endswith(".tif")]
    if not tifs:
        return "skipped: no TIFF on the FTP"
    work = "work_" + sid
    os.makedirs(work, exist_ok=True)
    try:
        src = os.path.join(work, "sheet.tif")
        run("curl", "-s", "-m", "900", "--retry", "3", "-o", src, ftp_url(s["half"], sid, tifs[0]))
        info = json.loads(subprocess.run(["gdalinfo", "-json", src], capture_output=True, text=True, check=True).stdout)
        w, h = info["size"]
        ul, ur, lr, ll = corners(s["ring3006"])
        frame = (dist(ul, ur) + dist(ll, lr)) / (dist(ul, ll) + dist(ur, lr))
        diff = abs(w / h - frame) / frame
        cropped = ""
        if diff > MAX_SHAPE_DIFF:
            # 2026-10-21: many scans have a white margin around the map paper (e.g. J112-95-16 Transtrand, 6100x4804,
            # 4.7 %). Find the map frame (rows and columns that are mostly not white) and cut the margin away.
            f = find_frame(src)
            if f is not None:
                x0, y0, cw, ch = f
                cdiff = abs(cw / ch - frame) / frame
                if cdiff <= MAX_SHAPE_DIFF:
                    crop = os.path.join(work, "crop.tif")
                    run("gdal_translate", "-q", "-srcwin", str(x0), str(y0), str(cw), str(ch), src, crop)
                    os.remove(src)
                    src, w, h = crop, cw, ch
                    cropped = " (margin cut: %dx%d at %d,%d, %.1f%% -> %.1f%%)" % (cw, ch, x0, y0, diff * 100, cdiff * 100)
                    diff = cdiff
        if diff > MAX_SHAPE_DIFF:
            return "skipped: the image is %dx%d, its shape differs %.1f%% from the sheet (margins?)" % (w, h, diff * 100)
        gcp = os.path.join(work, "gcp.tif")
        run("gdal_translate", "-q", "-of", "GTiff", "-a_srs", "EPSG:3006",
            "-gcp", "0", "0", str(ul[0]), str(ul[1]), "-gcp", str(w), "0", str(ur[0]), str(ur[1]),
            "-gcp", str(w), str(h), str(lr[0]), str(lr[1]), "-gcp", "0", str(h), str(ll[0]), str(ll[1]), src, gcp)
        os.remove(src)
        warped = os.path.join(work, "warped.tif")
        run("gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:3857", "-r", "bilinear", "-order", "1", "-dstalpha", "-co", "COMPRESS=DEFLATE", gcp, warped)
        os.remove(gcp)
        blocks = os.path.join(work, "blocks")
        run(GDAL2TILES, "--xyz", "-z", "6-14", "--tilesize=1024", "-r", "bilinear", "-w", "none", "--tiledriver=WEBP",
            "--webp-quality=75", "--processes=2", "-q", warped, blocks)
        files = []
        for root, _, names in os.walk(blocks):
            for f in names:
                if f.endswith(".webp"):
                    zt, x = root.split(os.sep)[-2:]
                    files.append((os.path.join(root, f), "%d/b4/%s_%s" % (int(zt) + 2, x, f)))
        # High zoom first, low zoom last: the low levels are shared with the neighbouring sheets and laid on top of
        # them on the server, which takes longest. Small parts, so each request stays well inside the server's time.
        files.sort(key=lambda t: -int(t[1].split("/")[0]))
        meta = json.dumps({"l": "hek", "id": sid, "name": s["name"], "years": s["years"], "poly": s["ring4326"]}, ensure_ascii=False)
        total = 0
        parts = [files[i:i + PART] for i in range(0, len(files), PART)]
        for k, part in enumerate(parts):
            zpath = os.path.join(work, "part%d.zip" % k)
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
                for src_file, name in part:
                    zf.write(src_file, name)
            total += os.path.getsize(zpath)
            code, body = "", ""
            for attempt in range(1, 6):
                r = subprocess.run(["curl", "-s", "-m", "240", "-w", "\n%{http_code}", "-H", "X-GS-Key: " + key,
                                    "-F", "zip=@" + zpath, "-F", "sheet=" + meta, UPLOAD], capture_output=True, text=True)
                body, _, code = r.stdout.rpartition("\n")
                if code == "200":
                    break
                # one.com answers 429 to many requests in a row from one address: wait and try again.
                time.sleep(10 * attempt)
            if code != "200":
                return "failed: upload part %d/%d answered %s %s" % (k + 1, len(parts), code, body[:200])
        return "ok: %d blocks in %d parts, %.1f MB%s, %s" % (len(files), len(parts), total / 1e6, cropped, body.strip())
    except subprocess.CalledProcessError as e:
        return "failed: %s" % " ".join(str(a) for a in e.cmd[:2])
    except Exception as e:
        return "failed: %s" % e
    finally:
        shutil.rmtree(work, ignore_errors=True)


ok = True
for s in sheets:
    t = time.time()
    result = build(s)
    line = "%s %s (%s): %s [%ds]" % (s["id"], s["name"], s["years"], result, time.time() - t)
    print(line, flush=True)
    report.append(line)
    ok = ok and not result.startswith("failed")

with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a") as f:
    f.write("\n".join("- " + l for l in report) + "\n")
sys.exit(0 if ok else 1)
