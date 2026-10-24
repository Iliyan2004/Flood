"""
ONE place for every external dataset path this app reads from the shared
DGX share. When the share is reorganised again, this is the only file to
edit -- and `check_data_paths()` (run at server startup, and exposed at
/data-status) reports exactly which paths are missing instead of letting a
module silently fall back or return empty results.

Every path can also be overridden per-machine with an environment variable
(SAILAB_<NAME>), so a move needs no code edit at all.
"""
import os

SHARE = os.environ.get("SAILAB_SHARE", os.path.expanduser("~/DGX-Shared"))
VECTOR = os.path.join(SHARE, "4. Vector Data")

_DEFAULTS = {
    # name: (default path, required?)  -- required=False means the app degrades
    # gracefully and visibly without it; required=True means runs are wrong/impossible.
    "PAKISTAN_DEM": (os.path.join(SHARE, "3. Raster Data", "DEM_SRTM", "pakDEM", "Pak_srtm.tif"), True),
    "HOSPITALS_SHP": (os.path.join(VECTOR, "Hospital", "Hospitals.shp"), False),
    "SCHOOLS_SHP": (os.path.join(VECTOR, "Schools", "Schools.shp"), False),
    "POWER_LINES_SHP": (os.path.join(VECTOR, "Electricity Transmission Line",
                                     "Electricity_transmission_line.shp"), False),
    "AIRPORTS_SHP": (os.path.join(VECTOR, "National and International Airports", "Airports.shp"), False),
    "SEA_PORTS_SHP": (os.path.join(VECTOR, "Sea Ports", "Sea_ports.shp"), False),
    "RAILWAY_STATIONS_SHP": (os.path.join(VECTOR, "Railway data", "Railway_stationpoint_shp",
                                          "Railway_stationpoint.shp"), False),
    "RAILWAY_LINES_SHP": (os.path.join(VECTOR, "Railway data", "Railway_lines_shp",
                                       "Railway_lines.shp"), False),
    "HYDROLAKES_POINTS_SHP": (os.path.join(VECTOR, "Hydrolakes", "Hydrolakes_points.shp"), False),
    "PMD_GLACIER_SHP": (os.path.join(SHARE, "Glof data", "PMD_Glacier_Lake_Inventory", "PMD_Glacier_Lake_Inventory",
                                     "Glacier_2024_PMD.shp"), False),
    "PMD_LAKES_SHP": (os.path.join(SHARE, "Glof data", "PMD_Glacier_Lake_Inventory", "PMD_Glacier_Lake_Inventory",
                                   "Lakes_2024_PMD.shp"), False),
    "ERA5_T2M_ZARR": (os.path.join(SHARE, "0. ERA-5 Variables", "t2m_zarr_space_opt"), False),
    # IceBoost v2.0 (RGI v7 region 14) modelled ice thickness, m, and a dimensionless debris melt factor
    # (<1 insulating debris, >1 thin dark debris), both on one 26.6 m EPSG:32643 grid. Melt model only.
    "GLACIER_THICKNESS_TIF": (os.path.expanduser("~/flood/global_data/glacier_inputs/Glacier thickness/glacier_thickness.tif"), False),
    "DEBRIS_FACTOR_TIF": (os.path.expanduser("~/flood/global_data/glacier_inputs/Debris/debris_factor.tif"), False),
    "ERA5_TP_ZARR": (os.path.join(SHARE, "0. ERA-5 Variables", "tp_zarr_space_opt"), False),
    "LSM_ENSEMBLE_CLASS": (os.path.join(SHARE, "MERGED LANDSLIDE LAYERS", "LSM_CNN_DNN", "outputs", "04_maps",
                                        "susceptibility_class_Ensemble.tif"), False),
    "LSM_ENSEMBLE_PROB": (os.path.join(SHARE, "MERGED LANDSLIDE LAYERS", "LSM_CNN_DNN", "outputs", "04_maps",
                                       "susceptibility_prob_Ensemble.tif"), False),
    "CONFINEMENT_TIF": (os.path.join(SHARE, "TopograhicVariables", "05_confinement", "confinement.tif"), False),
    "VALLEY_DEPTH_TIF": (os.path.join(SHARE, "TopograhicVariables", "06_valley_depth", "valley_depth.tif"), False),
    # Islamabad smart-city vector geodatabase (104 indexed layers); only used for areas inside Islamabad.
    "ISLAMABAD_GPKG": (os.path.join(SHARE, "Iliyan", "Smart City", "GEODATABASE", "Islamabad_SmartCity.gpkg"), False),
    "CMIP6_ROOT": (os.path.expanduser("~/Desktop/DGX-Shared/CMIP6"), False),
}

PATHS = {name: os.environ.get(f"SAILAB_{name}", default) for name, (default, _) in _DEFAULTS.items()}


def get(name):
    return PATHS[name]


def check_data_paths():
    """[(name, path, exists, required)] for every central path, plus the
    app-local datasets (symlinked into global_data/ and friends), so a
    broken symlink is reported too."""
    base = os.path.dirname(os.path.abspath(__file__))
    rows = [(n, PATHS[n], os.path.exists(PATHS[n]), _DEFAULTS[n][1]) for n in _DEFAULTS]
    local = {
        "SOIL_DATA_DIR": "global_data/soil_data", "BUILDINGS_PK": "global_data/buildings_pk",
        "ROADS_PK_GPKG": "global_data/roads_pk/pak_roads_indexed.gpkg",
        "RAINFALL_IDF_DIR": "global_data/rainfall_idf", "GTSR_DIR": "global_data/gtsr",
        "HYDROSHEDS_BASINS": "global_data/hydrosheds_basins_pk", "GLIM_PAKISTAN": "global_data/glim/glim_pakistan.gpkg",
        "GLOF_EVENTS_CSV": "global_data/glof/HMAGLOFDB_events.csv", "ITSLIVE_CACHE": "itslive_cache",
        "TIDE_GAUGE_CSV": "global_data/tide/karachi_hourly_h147b.csv", "COASTAL_SEA_MASK": "global_data/coastal/pakistan_sea_mask.tif", "COASTAL_GTSM_LEVELS": "global_data/coastal/pakistan_coast_all_return_levels.csv",
        "COASTAL_MANGROVES": "global_data/coastal/mangroves_2025_dem_aligned.tif", "COASTAL_VEG_BUFFER": "global_data/coastal/vegetation_buffer_0to1.tif",
        "DAMS_DATA": "Dams data", "SPILLWAY_EVENTS_CSV": "dam_spillway_opening_events.csv",
    }
    for n, rel in local.items():
        p = os.path.join(base, rel)
        rows.append((n, p, os.path.exists(p), False))
    return rows


def missing_required():
    return [(n, p) for n, p, ok, req in check_data_paths() if req and not ok]
