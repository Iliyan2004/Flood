# Surrogate pilot: U-Net vs current Instant estimate
Held-out: 70 terrain windows from areas the network never trained on (233 used for training, 200 epochs).
Truth = LISFLOOD-FP GPU run (roughness 0.02-0.15 and infiltration 0-20 mm/h vary per sample, no buildings; Instant ignores both). Wet = depth > 0.05 m. Mean / median over held-out windows.

| metric | U-Net surrogate | Instant (HAND + ponding) |
|---|---|---|
| CSI (higher better) | 0.435 / 0.436 | 0.341 / 0.352 |
| POD (hit rate) | 0.723 / 0.742 | 0.683 / 0.684 |
| FAR (false alarms) | 0.496 / 0.492 | 0.560 / 0.556 |
| flooded-area ratio (1 = exact) | 1.92 / 1.44 | 3.05 / 1.57 |
| depth MAE where either is wet (m) | 0.146 / 0.078 | 1.556 / 1.257 |

CSI gain (surrogate minus Instant), mean +0.094, 95% bootstrap interval [+0.067, +0.120]; surrogate better on 79% of windows.
