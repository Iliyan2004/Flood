"""Numbers of the controlled multi-model study (31 Oct 2026; model_comparison/results_all.json and results_extra.json in the repository).
Transcribed from the study report because the shared drive holding the JSON files was unavailable while this paper was assembled;
replace by a read of the JSON files when it is back."""
AREAS = ["A", "B", "C"]
ROWS = [  # name, CSI A, B, C, mean, POD, FAR, wet-area ratio, mean run time (s) or None, hardware
 ("Flood everything",                    0.486, 0.505, 0.509, 0.500, 1.00, 0.50, 2.00, None, "–"),
 ("Lowest terrain (told the true area)", 0.392, 0.507, 0.347, 0.415, 0.58, 0.42, 1.00, None, "–"),
 ("SAILAB (LISFLOOD-FP ACC)",            0.504, 0.528, 0.481, 0.504, 0.89, 0.46, 1.65, 58,  "GPU, 29 m"),
 ("ANUGA",                               0.495, 0.535, 0.478, 0.503, 0.86, 0.45, 1.57, 477, "CPU, 90 m mesh"),
 ("SFINCS",                              0.486, 0.505, 0.509, 0.500, 1.00, 0.50, 2.00, 528, "CPU, 20 threads, 29 m"),
 ("TELEMAC-2D",                          0.490, 0.531, 0.469, 0.497, 0.84, 0.45, 1.53, 141, "CPU, 1 core, 90 m mesh"),
]
OVERLAP = {("SAILAB", "ANUGA"): 0.94, ("SAILAB", "TELEMAC-2D"): 0.90, ("SAILAB", "SFINCS"): 0.82,
           ("ANUGA", "TELEMAC-2D"): 0.90, ("ANUGA", "SFINCS"): 0.79, ("SFINCS", "TELEMAC-2D"): 0.76}
OBS_FRAC = {"A": 0.486, "B": 0.505, "C": 0.509}
E2E = {"A": (187, 81, 6023), "B": (81, 52, 10394), "C": (87, 53, 5959)}    # total s, solver s, buildings
CUTOFF_CM = [5, 10, 20, 30, 50]
CUTOFF_CSI = {"Flood everything": [0.5] * 5, "Lowest terrain (told the true area)": [0.415] * 5,
              "SAILAB": [0.504, 0.489, 0.397, 0.299, 0.155], "ANUGA": [0.503, 0.481, 0.382, 0.285, 0.153],
              "SFINCS": [0.500, 0.493, 0.411, 0.296, 0.136], "TELEMAC-2D": [0.497, 0.462, 0.353, 0.263, 0.134]}
