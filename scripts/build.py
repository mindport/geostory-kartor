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

key = os.environ["HEK_UPLOAD_KEY"]
sheets = json.loads(os.environ["SHEETS"])
report = []


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
        run("gdal2tiles", "--xyz", "-z", "6-14", "--tilesize=1024", "-r", "bilinear", "-w", "none", "--tiledriver=WEBP",
            "--webp-quality=75", "--processes=2", "-q", warped, blocks)
        zpath = os.path.join(work, "blocks.zip")
        n = 0
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
            for root, _, files in os.walk(blocks):
                for f in files:
                    if f.endswith(".webp"):
                        zt, x = root.split(os.sep)[-2:]
                        zf.write(os.path.join(root, f), "%d/b4/%s_%s" % (int(zt) + 2, x, f))
                        n += 1
        meta = json.dumps({"l": "hek", "id": sid, "name": s["name"], "years": s["years"], "poly": s["ring4326"]}, ensure_ascii=False)
        size = os.path.getsize(zpath)
        for attempt in range(1, 6):
            r = subprocess.run(["curl", "-s", "-m", "300", "-w", "\n%{http_code}", "-H", "X-GS-Key: " + key,
                                "-F", "zip=@" + zpath, "-F", "sheet=" + meta, UPLOAD], capture_output=True, text=True)
            body, _, code = r.stdout.rpartition("\n")
            if code == "200":
                return "ok: %d blocks, %.1f MB, %s" % (n, size / 1e6, body.strip())
            # one.com answers 429 to many requests in a row from one address: wait and try again.
            time.sleep(10 * attempt)
        return "failed: upload answered %s %s" % (code, body[:200])
    except subprocess.CalledProcessError as e:
        return "failed: %s" % " ".join(str(a) for a in e.cmd[:2])
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
