#!/usr/bin/env python3
"""Då och nu, steg 2: matchar varje gammalt foto mot gatubilder längs samma gata.

DISK-punkter och LightGlue-matchning (kornia), geometrisk kontroll med MAGSAC. Ett par godkänns när många punkter
håller ihop i en rimlig geometri; då får det gamla fotot Mapillary-bildens position och riktning (ståplats) och en
inpassning (homografi) för överlägget.

  python3 rephoto/match.py --work rephoto/work [--per-photo 60] [--min-inliers 60]

Läser <work>/candidates.json (fetch.py), skriver <work>/matches.json och jämförelsebilder i <work>/pairs/.
"""
import argparse
import hashlib
import json
import math
import os

import cv2
import kornia.feature as KF
import numpy as np
import requests
import torch

UA = {"User-Agent": "GeoStory-rephoto/0.1 (https://www.geostory.se)"}
DEV = torch.device("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
SIZE = 1024


def load(url, cache):
    f = os.path.join(cache, hashlib.sha1(url.split("?")[0].encode()).hexdigest() + ".jpg")
    if not os.path.exists(f):
        try:
            r = requests.get(url, headers=UA, timeout=60)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        open(f, "wb").write(r.content)
    img = cv2.imread(f)
    if img is None:
        return None
    s = SIZE / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img


def mapillary_urls(ids, token):
    out = {}
    for i in range(0, len(ids), 50):
        r = requests.get("https://graph.mapillary.com/", headers={"Authorization": "OAuth " + token}, timeout=120,
                         params={"ids": ",".join(ids[i:i + 50]), "fields": "thumb_1024_url"})
        if r.status_code == 200:
            out.update({k: v.get("thumb_1024_url") for k, v in r.json().items()})
    return out


def spread(images, n):
    """At most n images spread along the street (every k-th by position), both directions kept."""
    if len(images) <= n:
        return images
    images = sorted(images, key=lambda m: (m["computed_geometry"]["coordinates"][0], m["computed_geometry"]["coordinates"][1]))
    step = len(images) / n
    return [images[int(i * step)] for i in range(n)]


class Features:
    def __init__(self):
        self.disk = KF.DISK.from_pretrained("depth").to(DEV).eval()
        self.lg = KF.LightGlueMatcher("disk").to(DEV).eval()

    @torch.inference_mode()
    def extract(self, img):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = cv2.createCLAHE(2.0, (8, 8)).apply(g)  # old photos: low contrast, grey
        rgb = cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)
        h, w = rgb.shape[:2]
        h8, w8 = h - h % 16, w - w % 16
        t = torch.from_numpy(rgb[:h8, :w8]).permute(2, 0, 1).float().div(255)[None].to(DEV)
        f = self.disk(t, n=2048, pad_if_not_divisible=True)[0]
        return {"kp": f.keypoints, "desc": f.descriptors, "hw": torch.tensor([h8, w8], device=DEV)}

    @torch.inference_mode()
    def match(self, a, b):
        lafs_a = KF.laf_from_center_scale_ori(a["kp"][None], torch.full((1, len(a["kp"]), 1, 1), 10.0, device=DEV))
        lafs_b = KF.laf_from_center_scale_ori(b["kp"][None], torch.full((1, len(b["kp"]), 1, 1), 10.0, device=DEV))
        _, idx = self.lg(a["desc"], b["desc"], lafs_a, lafs_b, hw1=a["hw"], hw2=b["hw"])
        if len(idx) < 8:
            return None, None
        ia, ib = idx[:, 0].cpu().numpy(), idx[:, 1].cpu().numpy()
        return a["kp"].cpu().numpy()[ia], b["kp"].cpu().numpy()[ib]


def verify(pa, pb):
    """Inliers of a fundamental matrix (3D scene) and a sane homography for the overlay."""
    if pa is None or len(pa) < 15:
        return 0, 0.0, None
    F, fm = cv2.findFundamentalMat(pa, pb, cv2.USAC_MAGSAC, 1.5, 0.999, 10000)
    fin = int(fm.sum()) if fm is not None else 0
    H, hm = cv2.findHomography(pa, pb, cv2.USAC_MAGSAC, 8.0)
    if H is not None:
        a = H[:2, :2]
        det = float(np.linalg.det(a))
        persp = float(np.abs(H[2, :2]).max())
        if not (0.15 < det < 8 and persp < 0.003):  # folds, mirrors or extreme warps: no overlay
            H = None
    return fin, fin / max(len(pa), 1), H


def pair_image(old, new, H, path):
    h = 360
    row = [cv2.resize(x, (int(x.shape[1] * h / x.shape[0]), h)) for x in (old, new)]
    if H is not None:
        warp = cv2.warpPerspective(old, H, (new.shape[1], new.shape[0]))
        mask = cv2.warpPerspective(np.full(old.shape[:2], 255, np.uint8), H, (new.shape[1], new.shape[0])) > 0
        blend = new.copy()
        blend[mask] = (0.55 * warp[mask] + 0.45 * new[mask]).astype(np.uint8)
        row.append(cv2.resize(blend, (int(blend.shape[1] * h / blend.shape[0]), h)))
    cv2.imwrite(path, np.hstack(row))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="rephoto/work")
    ap.add_argument("--per-photo", type=int, default=60)
    ap.add_argument("--min-inliers", type=int, default=60)
    a = ap.parse_args()
    token = os.environ.get("MAPILLARY_TOKEN") or open(os.path.expanduser("~/.mapillary_token")).read().strip()
    cache = os.path.join(a.work, "cache"); pairs = os.path.join(a.work, "pairs")
    os.makedirs(cache, exist_ok=True); os.makedirs(pairs, exist_ok=True)
    data = json.load(open(os.path.join(a.work, "candidates.json")))
    feat = Features()
    print("device", DEV, flush=True)
    results = []
    for st in data["streets"]:
        if not st["photos"] or not st["mapillary"]:
            continue
        mly = spread(st["mapillary"], a.per_photo)
        urls = mapillary_urls([m["id"] for m in mly], token)
        mf = []
        for m in mly:
            img = load(urls.get(m["id"]) or "", cache) if urls.get(m["id"]) else None
            if img is not None:
                mf.append((m, img, feat.extract(img)))
        print(f"{st['street']}: {len(st['photos'])} foton mot {len(mf)} gatubilder", flush=True)
        for p in st["photos"]:
            old = load(p["img"], cache)
            if old is None:
                continue
            fo = feat.extract(old)
            best = None
            for m, img, fm in mf:
                pa, pb = feat.match(fo, fm)
                fin, ratio, H = verify(pa, pb)
                if best is None or fin > best[0]:
                    best = (fin, ratio, H, m, img)
            if best is None:
                continue
            fin, ratio, H, m, img = best
            lon, lat = m["computed_geometry"]["coordinates"]
            r = {**{k: p[k] for k in ("id", "street", "label", "img", "license", "byline", "org", "time")},
                 "inliers": fin, "ratio": round(ratio, 3), "mapillary_id": m["id"], "lat": lat, "lon": lon,
                 "heading": m["computed_compass_angle"], "captured_at": m.get("captured_at"),
                 "homography": H.tolist() if H is not None else None, "ok": fin >= a.min_inliers and ratio >= 0.3}
            results.append(r)
            tag = "OK" if r["ok"] else "--"
            print(f"  {tag} {fin:4d} {ratio:.2f} {p['label'][:60]}", flush=True)
            if fin >= a.min_inliers // 2:
                pair_image(old, img, H, os.path.join(pairs, f"{fin:04d}_{hashlib.sha1(p['id'].encode()).hexdigest()[:8]}.jpg"))
    json.dump(results, open(os.path.join(a.work, "matches.json"), "w"), ensure_ascii=False, indent=1)
    print("foton", len(results), "godkända", sum(r["ok"] for r in results))


if __name__ == "__main__":
    main()
