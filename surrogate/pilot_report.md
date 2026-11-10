# Surrogate pilot: U-Net vs current Instant estimate
Held-out: 36 terrain windows from areas the network never trained on (81 used for training, 120 epochs).
Truth = LISFLOOD-FP GPU run (friction 0.05, infiltration 5 mm/h, no buildings). Wet = depth > 0.05 m. Mean / median over held-out windows.

| metric | U-Net surrogate | Instant (HAND + ponding) |
|---|---|---|
| CSI (higher better) | 0.406 / 0.414 | 0.332 / 0.350 |
| POD (hit rate) | 0.682 / 0.707 | 0.667 / 0.693 |
| FAR (false alarms) | 0.473 / 0.467 | 0.524 / 0.543 |
| flooded-area ratio (1 = exact) | 1.63 / 1.36 | 2.53 / 1.50 |
| depth MAE where either is wet (m) | 0.740 / 0.084 | 1.884 / 1.112 |

CSI gain (surrogate minus Instant), mean +0.074, 95% bootstrap interval [+0.038, +0.115]; surrogate better on 69% of windows.
