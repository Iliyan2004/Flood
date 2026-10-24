"""python calibrate_melt_basin.py HYBAS_ID CAL_START CAL_END VAL_START VAL_END  -> JSON on stdout"""
import json, sys
import basin_utils, melt_hydrograph_utils as m
g = basin_utils.basin_geometry(int(sys.argv[1]))
geom = g["geometry"] if isinstance(g, dict) and "geometry" in g else g
print(json.dumps(m.calibrate_against_glofas(geom, *sys.argv[2:6]), indent=1))
