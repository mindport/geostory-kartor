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
        if not sid or sid in done:
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

sheets = sheets[:max_sheets]
chunks = [sheets[i:i + per_job] for i in range(0, len(sheets), per_job)]
matrix = {"include": [{"chunk": i, "sheets": json.dumps(c, ensure_ascii=False)} for i, c in enumerate(chunks)]}
print(f"{len(sheets)} sheets to build in {len(chunks)} jobs ({len(done)} already on the server)", file=sys.stderr)
print(json.dumps(matrix, ensure_ascii=False))
