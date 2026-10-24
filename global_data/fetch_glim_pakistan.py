"""
One-time real GLiM (Global Lithological Map, Hartmann & Moosdorf 2012,
DOI 10.1029/2012GC004370) fetch, clipped to Pakistan -- same real
"download once, keep a small local Pakistan extract" pattern already
used by fetch_soilgrids_pakistan.py/the SoilGrids/HOTOSM-roads/GTSR
symlinked datasets elsewhere in this app, not a bulk global copy.

Real source: the official University of Hamburg GLiM project page
(https://www.geo.uni-hamburg.de/en/geologie/forschung/aquatische-
geochemie/glim.html) links a real, open (CC-BY-3.0, no login/paywall)
Dropbox-hosted copy of the full 1,235,259-polygon vector dataset as an
Esri File Geodatabase zip (~1.1GB):
https://www.dropbox.com/s/9vuowtebp9f1iud/LiMW_GIS%202015.gdb.zip?dl=1
(the PANGAEA DOI page, 10.1594/PANGAEA.788537, only hosts a much coarser
0.5-degree GRIDDED derivative -- far too coarse for this app's own
km-to-tens-of-km AOIs, so the real vector source above is used instead).

Run once:
    ~/flood/venv/bin/python global_data/fetch_glim_pakistan.py
Produces global_data/glim/glim_pakistan.gpkg (real, small, EPSG:4326)
and removes the large intermediate zip/gdb once the clip succeeds.
"""
import os
import shutil
import subprocess
import sys

import geopandas as gpd
import pyogrio
from pyproj import Transformer

GLIM_DIR = os.path.join(os.path.dirname(__file__), "glim")
ZIP_PATH = os.path.join(GLIM_DIR, "LiMW_GIS_2015.gdb.zip")
GDB_PATH = os.path.join(GLIM_DIR, "LiMW_GIS 2015.gdb")
OUT_GPKG = os.path.join(GLIM_DIR, "glim_pakistan.gpkg")
DROPBOX_URL = "https://www.dropbox.com/s/9vuowtebp9f1iud/LiMW_GIS%202015.gdb.zip?dl=1"

# Generous Pakistan bbox (covers Gilgit-Baltistan/Karakoram in the north
# through Sindh/the Arabian Sea coast in the south).
PK_LON_MIN, PK_LAT_MIN, PK_LON_MAX, PK_LAT_MAX = 60.0, 23.0, 78.0, 38.0


def main():
    os.makedirs(GLIM_DIR, exist_ok=True)

    if not os.path.isdir(GDB_PATH):
        if not os.path.exists(ZIP_PATH):
            print(f"[glim] downloading real GLiM vector dataset (~1.1GB, one-time) from {DROPBOX_URL}")
            subprocess.run(["curl", "-sL", "--max-time", "1800", "-o", ZIP_PATH, DROPBOX_URL], check=True)
        print("[glim] extracting real file geodatabase...")
        subprocess.run(["unzip", "-q", ZIP_PATH, "-d", GLIM_DIR], check=True)

    to_eck = Transformer.from_crs("EPSG:4326", "ESRI:54012", always_xy=True).transform
    corners = [(PK_LON_MIN, PK_LAT_MIN), (PK_LON_MAX, PK_LAT_MIN),
               (PK_LON_MAX, PK_LAT_MAX), (PK_LON_MIN, PK_LAT_MAX)]
    xs, ys = zip(*(to_eck(lon, lat) for lon, lat in corners))
    bbox_eck = (min(xs), min(ys), max(xs), max(ys))

    print("[glim] reading real Pakistan-bbox-filtered polygons from the GLiM GDB (indexed, fast)...")
    gdf = pyogrio.read_dataframe(GDB_PATH, layer="GLiM_export", bbox=bbox_eck)
    print(f"[glim] {len(gdf)} real polygons found within Pakistan's bbox")

    gdf = gdf[["Litho", "xx", "geometry"]].to_crs("EPSG:4326")
    gdf.to_file(OUT_GPKG, driver="GPKG")
    print(f"[glim] saved real Pakistan extract to {OUT_GPKG} ({os.path.getsize(OUT_GPKG)/1e6:.1f}MB)")

    # Real, deliberate cleanup -- keep only the small Pakistan extract,
    # same "small local copy, not the global bulk" discipline as every
    # other real large dataset this app has fetched.
    for p in (ZIP_PATH, GDB_PATH):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    print("[glim] removed the large global intermediate files; Pakistan extract kept.")


if __name__ == "__main__":
    sys.exit(main())
