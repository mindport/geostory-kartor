#!/usr/bin/env python3
"""Då och nu, steg 1: gamla foton (K-samsök) och dagens gatubilder (Mapillary) per gata.

Gamla foton har sällan koordinater, men ofta gatunamnet i titel eller beskrivning. För varje gata hämtas:
  - K-samsök: foton med fri licens (CC0, Public Domain, CC BY, CC BY-SA) som nämner gatan, i orten
  - OpenStreetMap (Nominatim): gatans utbredning
  - Mapillary: gatubilder längs gatan med beräknad position och kompassriktning (inga panoramabilder)

  python3 rephoto/fetch.py --place Stockholm --streets "Västerlånggatan,Österlånggatan" --out rephoto/work

Mapillary-token: MAPILLARY_TOKEN eller ~/.mapillary_token. Skriver <out>/candidates.json.
"""
import argparse
import json
import math
import os
import time

import requests

UA = {"User-Agent": "GeoStory-rephoto/0.1 (https://www.geostory.se)"}
FREE = {"cc0", "pdmark", "by", "by-sa"}
BUFFER_M = 25


def first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def text(v):
    v = first(v)
    if isinstance(v, dict):
        return v.get("content") or ""
    return v or ""


def ksamsok(street, place):
    q = f'text="{street}" and place="{place}" and itemType="foto" and thumbnailExists="j"'
    out, start = [], 1
    while True:
        r = requests.get("https://kulturarvsdata.se/ksamsok/api", headers={**UA, "Accept": "application/json"}, timeout=60,
                         params={"method": "search", "hitsPerPage": 500, "startRecord": start,
                                 "recordSchema": "presentation", "query": q})
        res = r.json()["result"]
        recs = (res.get("records") or {}).get("record", []) if isinstance(res.get("records"), dict) else []
        recs = recs if isinstance(recs, list) else [recs]
        for x in recs:
            it = x.get("pres:item", {})
            img = first(it.get("pres:image")) or {}
            lic = str(img.get("pres:mediaLicense", "")).split("#")[-1]
            if lic not in FREE:
                continue
            srcs = {s.get("type"): s.get("content") for s in (img.get("pres:src") or []) if isinstance(s, dict)}
            url = srcs.get("highres") or srcs.get("lowres")
            label = text(it.get("pres:itemLabel"))
            desc = text(it.get("pres:description"))
            # The street must be in the photo's own text (K-samsök's text search also hits related records).
            if not url or street.lower() not in (label + " " + desc + " " + json.dumps(it.get("pres:tag"), ensure_ascii=False)).lower():
                continue
            ctx = first(it.get("pres:context")) or {}
            out.append({"id": it.get("pres:entityUri"), "street": street, "label": label, "desc": desc[:400], "img": url,
                        "license": lic, "byline": text(img.get("pres:byline")), "org": text(it.get("pres:organization")),
                        "time": text(ctx.get("pres:timeLabel"))})
        start += 500
        if start > int(res.get("totalHits", 0)) or start > 2000:
            return out


def street_bbox(street, place):
    r = requests.get("https://nominatim.openstreetmap.org/search", headers=UA, timeout=60,
                     params={"q": f"{street}, {place}", "format": "jsonv2", "limit": 5})
    time.sleep(1.1)  # Nominatim: at most one request per second
    hits = [h for h in r.json() if h.get("category") in ("highway", "place", "amenity", "tourism", "man_made")] or r.json()
    if not hits:
        return None
    s, n, w, e = (float(v) for v in hits[0]["boundingbox"])
    dlat = BUFFER_M / 111320.0
    dlon = BUFFER_M / (111320.0 * math.cos(math.radians((s + n) / 2)))
    return (w - dlon, s - dlat, e + dlon, n + dlat)


def mapillary(bbox, token):
    # Mapillary's graph API is slow for larger areas: retry, then a smaller limit.
    for attempt, limit in enumerate((2000, 2000, 1000, 500)):
        try:
            r = requests.get("https://graph.mapillary.com/images", headers={"Authorization": "OAuth " + token}, timeout=180, params={
                "bbox": ",".join(f"{v:.6f}" for v in bbox), "limit": limit,
                "fields": "id,computed_geometry,computed_compass_angle,captured_at,is_pano,sequence"})
            r.raise_for_status()
            break
        except requests.RequestException as e:
            print("  mapillary retry", attempt + 1, type(e).__name__, flush=True)
            time.sleep(5)
    else:
        return []
    return [i for i in r.json().get("data", []) if i.get("computed_geometry") and not i.get("is_pano")
            and i.get("computed_compass_angle") is not None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--place", default="Stockholm")
    ap.add_argument("--streets", required=True)
    ap.add_argument("--out", default="rephoto/work")
    a = ap.parse_args()
    token = os.environ.get("MAPILLARY_TOKEN") or open(os.path.expanduser("~/.mapillary_token")).read().strip()
    os.makedirs(a.out, exist_ok=True)
    streets = []
    for st in [s.strip() for s in a.streets.split(",") if s.strip()]:
        photos = ksamsok(st, a.place)
        bbox = street_bbox(st, a.place)
        mly = mapillary(bbox, token) if bbox else []
        print(f"{st}: {len(photos)} gamla foton, {len(mly)} Mapillary-bilder, bbox {bbox}", flush=True)
        streets.append({"street": st, "bbox": bbox, "photos": photos, "mapillary": mly})
    json.dump({"place": a.place, "streets": streets}, open(os.path.join(a.out, "candidates.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
