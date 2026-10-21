#!/usr/bin/env python3
"""Plan: which sheets of Häradsekonomiska kartan to build in this run.

Reads the two sheet indexes (Lantmäteriet's open FTP, converted to GeoJSON in EPSG:4326 and EPSG:3006 by the
workflow), skips the sheets the server already has (api/histupload.php?list=1), keeps the sheets inside the bbox and
writes a GitHub Actions matrix: {"include":[{"chunk":0,"sheets":"<json list>"}, ...]}.

Usage: plan.py <bbox minlat,minlon,maxlat,maxlon> <max sheets> <sheets per job> <done ids json file>
"""
import json
import sys

bbox = [float(v) for v in sys.argv[1].split(",")]
max_sheets = int(sys.argv[2])
per_job = int(sys.argv[3])
done = set(json.load(open(sys.argv[4])).get("ids", []))

sheets = []
for half in ("sodra", "norra"):
    g4326 = json.load(open(f"idx_{half}_4326.json"))
    g3006 = json.load(open(f"idx_{half}_3006.json"))
    for f4, f3 in zip(g4326["features"], g3006["features"]):
        p = f4["properties"]
        sid = p.get("Bladnummer")
        if not sid or (sid in done and sid[-1] not in "ab"):
            continue
        ring4 = f4["geometry"]["coordinates"][0]
        ring3 = f3["geometry"]["coordinates"][0]
        lats = [c[1] for c in ring4]
        lons = [c[0] for c in ring4]
        if max(lats) < bbox[0] or min(lats) > bbox[2] or max(lons) < bbox[1] or min(lons) > bbox[3]:
            continue
        sheets.append({
            "id": sid,
            "half": half,
            "name": p.get("Bladnamn") or "",
            # The year field is "År" (the encoding of the name can vary): take the field that is not one of the others.
            "years": next((str(v) for k, v in p.items() if k not in ("Kartserie", "Bladnummer", "Bladnamn") and v), ""),
            "ring4326": [[round(c[1], 7), round(c[0], 7)] for c in ring4[:-1]],
            "ring3006": [[round(c[0], 2), round(c[1], 2)] for c in ring3[:-1]],
        })

# a/b sheets (2026-10-21): the index draws a and b as the west and east half of a sheet, but each TIFF shows the
# whole sheet, in two editions (e.g. J112-91-15a/b Östervåla, 1859-1863 and 1905-1911). Each edition is built with
# the whole sheet as frame ("<id>.full"): the newer in the layer hek, the older in hek2 (a second choice in the app).
def first_year(y):
    import re
    m = re.findall(r"\d{4}", y or "")
    return int(m[0]) if m else 0

def corners_xy(ring):
    pts = sorted(ring, key=lambda p: -p[1])
    top = sorted(pts[:2], key=lambda p: p[0])
    bottom = sorted(pts[2:4], key=lambda p: p[0])
    return top[0], top[1], bottom[1], bottom[0]

def union(ra, rb, xy):
    """The whole sheet from the two halves: the west half's left corners and the east half's right corners."""
    conv = (lambda p: p) if xy else (lambda p: [p[1], p[0]])
    back = (lambda p: p) if xy else (lambda p: [p[1], p[0]])
    a = [conv(p) for p in ra]
    b = [conv(p) for p in rb]
    west, east = (a, b) if sum(p[0] for p in a) <= sum(p[0] for p in b) else (b, a)
    wul, wur, wlr, wll = corners_xy(west)
    eul, eur, elr, ell = corners_xy(east)
    return [back(wul), back(eur), back(elr), back(wll)]

halves = {}
singles = []
for sh in sheets:
    if sh["id"][-1] in "ab" and len(sh["ring3006"]) == 4:
        halves.setdefault(sh["id"][:-1], {})[sh["id"][-1]] = sh
    else:
        singles.append(sh)
paired = []
for base, d in halves.items():
    if len(d) != 2:
        singles.extend(d.values())
        continue
    a, b = d["a"], d["b"]
    ya, yb = first_year(a["years"]), first_year(b["years"])
    older, newer = (b, a) if ya > yb else (a, b)
    r3006 = union(a["ring3006"], b["ring3006"], True)
    r4326 = union(a["ring4326"], b["ring4326"], False)
    for sh, layer in ((newer, "hek"), (older, "hek2")):
        full = dict(sh)
        full["id"] = sh["id"] + ".full"
        full["folder"] = sh["id"]
        full["layer"] = layer
        full["ring3006"] = r3006
        full["ring4326"] = r4326
        if full["id"] not in done:
            paired.append(full)
sheets = singles + paired
sheets = sheets[:max_sheets]
chunks = [sheets[i:i + per_job] for i in range(0, len(sheets), per_job)]
matrix = {"include": [{"chunk": i, "sheets": json.dumps(c, ensure_ascii=False)} for i, c in enumerate(chunks)]}
print(f"{len(sheets)} sheets to build in {len(chunks)} jobs ({len(done)} already on the server)", file=sys.stderr)
print(json.dumps(matrix, ensure_ascii=False))
