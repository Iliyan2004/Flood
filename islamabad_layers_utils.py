"""Extra critical-facility layers for areas inside Islamabad, read from the smart-city geodatabase
(`ISLAMABAD_GPKG`, EPSG:4326, spatially indexed). The national datasets have none of these.

Each category becomes its own entry in the infrastructure risk (depth and hazard sampled at the feature's centroid),
exactly like hospitals and power lines. Outside Islamabad, or when the file is missing, nothing is returned and the
run is unaffected. Lines and polygons are reduced to a centroid; the sample is at that one point.
"""
from __future__ import annotations

import os

import data_paths

# Islamabad Capital Territory extent covered by the database (layer `Islamabad_AOI`, with a little margin).
ISB_BBOX = (72.77, 33.49, 73.34, 33.81)

# category -> layers. Waterways, canals and rivers are deliberately absent: they are where the water is meant to be.
CATEGORIES = {
    "shelters": ("emergency_shelters_point", "emergency_shelters_polygon"),
    "police": ("police_point", "police_polygon"),
    "water_facilities": ("water_facilities_islamabad", "water_supply_storage_point", "water_supply_storage_polygon",
                         "wastewater_polygon"),
    "fuel_chemical": ("fuel_chemical_point", "fuel_chemical_polygon"),
    "governance": ("governance_point", "governance_polygon"),
    "industry": ("industrial_plants_point", "industrial_plants_polygon", "waste_processing_polygon", "mining_quarry_polygon"),
}


def intersects_islamabad(lon_min, lat_min, lon_max, lat_max) -> bool:
    return not (lon_max < ISB_BBOX[0] or lon_min > ISB_BBOX[2] or lat_max < ISB_BBOX[1] or lat_min > ISB_BBOX[3])


def fetch_islamabad_facilities(lon_min, lat_min, lon_max, lat_max, path=None) -> dict:
    """{category: [ {id, name, type, centroid} ]} for the categories that have features in the box; {} otherwise."""
    path = path or data_paths.get("ISLAMABAD_GPKG")
    if not intersects_islamabad(lon_min, lat_min, lon_max, lat_max):
        return {}
    if not os.path.exists(path):
        print(f"[islamabad] geodatabase not found at {path}")
        return {}
    import pyogrio
    out = {}
    for cat, layers in CATEGORIES.items():
        feats = []
        for layer in layers:
            try:
                gdf = pyogrio.read_dataframe(path, layer=layer, bbox=(lon_min, lat_min, lon_max, lat_max))
            except Exception as e:
                print(f"[islamabad] {layer} read failed: {e}")
                continue
            for i, row in gdf.iterrows():
                g = row.geometry
                if g is None or g.is_empty:
                    continue
                c = g.centroid
                name = row.get("name") if "name" in gdf.columns else None
                feats.append({"id": f"{layer}_{i}", "name": str(name) if name not in (None, "") and name == name else cat,
                              "type": cat, "centroid": (float(c.x), float(c.y))})
        if feats:
            out[cat] = feats
    if out:
        print("[islamabad] " + ", ".join(f"{k}: {len(v)}" for k, v in out.items()))
    return out
