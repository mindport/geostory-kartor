# GeoStory – historiska kartor

Lägger Lantmäteriets **Häradsekonomiska kartan** (1859–1934, öppna data, CC0) på plats och laddar upp den till
GeoStory som kartlagret "hek" i appens historiska karta.

- `.github/workflows/hek.yml` – körs manuellt med ett område (bbox); blad som redan finns hoppas över.
- `scripts/plan.py` – väljer blad ur Lantmäteriets bladindex.
- `scripts/build.py` – hämtar bladet, lägger det på plats med indexets hörn (GDAL), skär block om 1024 px (WebP)
  och laddar upp dem.

Källa: Lantmäteriet, Häradsekonomiska kartan, öppna data (CC0), ftp://download-opendata.lantmateriet.se/

## Då och nu (`rephoto/`, test)

Gamla foton (K-samsök, fri licens) matchas mot dagens gatubilder (Mapillary) för GeoStorys "då och nu".

- `rephoto/fetch.py` – per gata: K-samsök-foton som nämner gatan, gatans läge (OpenStreetMap) och Mapillary-bilder längs den.
- `rephoto/match.py` – DISK + LightGlue (kornia) och geometrisk kontroll; godkända par får Mapillary-bildens
  position och riktning (ståplats) och en inpassning för överlägget.

Kräver Mapillary-token (`MAPILLARY_TOKEN` eller `~/.mapillary_token`). Test 2026-11-02, Gamla stan: 272 foton,
20 godkända, varav ett tiotal riktiga då-och-nu-par (Stortorget med inpassning, Österlånggatan, Svartmangatan).
Licens: K-samsök-fotots licens (CC0/PDM/CC BY/CC BY-SA) och Mapillary CC BY-SA – ange källa vid visning.
