# GeoStory – historiska kartor

Lägger Lantmäteriets **Häradsekonomiska kartan** (1859–1934, öppna data, CC0) på plats och laddar upp den till
GeoStory som kartlagret "hek" i appens historiska karta.

- `.github/workflows/hek.yml` – körs manuellt med ett område (bbox); blad som redan finns hoppas över.
- `scripts/plan.py` – väljer blad ur Lantmäteriets bladindex.
- `scripts/build.py` – hämtar bladet, lägger det på plats med indexets hörn (GDAL), skär block om 1024 px (WebP)
  och laddar upp dem.

Källa: Lantmäteriet, Häradsekonomiska kartan, öppna data (CC0), ftp://download-opendata.lantmateriet.se/
