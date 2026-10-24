# PROGRESS1 — continued engineering log

Continues `PROGRESS.md` (Phases 0–28), which had grown past 2,200 lines.

---

## Phase 29 — AUDIT: basin topology and vertex simplification

Two questions: do the basins intersect each other, and has any vertex
simplification been applied? Measurement only; nothing changed.

### 1. SOURCE basins are a clean partition — no crossing, no overlap

All 1,131 basins loaded at full resolution and tested pairwise via an STRtree
(EPSG:32642):

| check | result |
|---|---|
| invalid geometries | **0** |
| candidate pairs tested (bbox-overlapping) | 4,660 |
| pairs sharing only a boundary (≤1 m²) | 3,199 — expected for neighbours |
| **pairs with a real 2-D overlap (>1 m²)** | **0** |
| sum of areas vs union | 871,911.1 km² vs 871,911.1 km² — **identical** |

Sum-of-areas equalling the union to the last decimal is the strongest single
statement that there is no double-covered ground. The source data is a proper
partition: basins meet along shared edges and never cross.

(The 16 basins with ring self-intersections found in Phase 12 were moved out
earlier at the user's request — 1,147 → 1,131 — which is why validity is now
clean. That earlier defect was *within* individual polygons, not between them.)

### 2. YES — vertex simplification IS applied, to the DISPLAY layer only

Phase 16 built `WebGIS Dashboard/basins_all.geojson` with shapely
`simplify(0.002, preserve_topology=True)` — 0.002° ≈ 200 m at this latitude.

| | source | display layer |
|---|---|---|
| vertices | 206,494 | **119,359 (42.2% removed)** |
| size | ~14 MB | 4.6 MB |

**The model never uses it.** Clicking a basin fetches the untouched file from
`basins/` → `HydroshedsBasin/Pakistan_Lev08_Individual_Basins` (verified: the
symlink resolves to the full-resolution source), and Phase 24 verifies the
fetched file's own `HYBAS_ID` against the clicked feature before it becomes an
AOI. Simplification affects pixels on screen, not physics.

### 3. But the simplification DOES break topology between neighbours

`preserve_topology=True` guarantees each polygon stays valid and does not
self-intersect. It does **not** guarantee that a boundary shared by two basins is
simplified identically from both sides — each polygon is simplified
independently, so the two versions of a shared edge diverge:

| | source | display layer |
|---|---|---|
| pairs sharing only a boundary | 3,199 | 1,153 |
| **pairs with a real 2-D overlap** | **0** | **2,040** |
| overlap area (sum − union) | 0.000 km² | **267.5 km²** |
| gaps vs the source partition | — | **453.7 km²** |

So ~64% of shared boundaries developed a sliver. The slivers are thin —
median width **9.4 m**, p95 52.6 m, max 136.9 m; the median is **0.3 of a model
cell** — but 2,040 of them add up to 267.5 km² double-covered and 453.7 km²
uncovered.

### 4. Does it matter? Small, bounded, and not a physics risk

- **Physics: unaffected.** The model runs on the full-resolution polygon.
- **Reported statistics: unaffected.** Phase 19 rasterises the AOI from the
  fetched full-resolution geometry, not from the display layer.
- **Click targeting: marginally ambiguous.** 267.5 km² (**0.031%** of the layer)
  is covered by two basins, so a click there could resolve to either. The
  consequence is bounded: whichever id is hit, the AOI is fetched at full
  resolution and HYBAS_ID-verified, so the run is always self-consistent with
  the id that was clicked — it just might not be the basin the user's eye
  intended, in a sliver a third of a cell wide.
- **Visual: hairline seams/overlaps** at high zoom.

### 5. Not fixed — options recorded for a decision
1. **Topology-preserving simplification across the whole set** (e.g. mapshaper /
   TopoJSON, which simplifies shared arcs once): removes both overlaps and gaps
   and keeps the size win. Adds a toolchain dependency.
2. **Lower the tolerance** (0.0005° ≈ 50 m): shrinks slivers roughly 4×, costs
   file size. Does not eliminate the class of defect.
3. **Ship unsimplified** (14 MB): topologically perfect, ~3× the download and
   slower canvas rendering — the reason simplification was introduced.
4. **Leave as is**: defensible given 0.031% ambiguity and no physics impact, but
   it should be a recorded decision rather than an unexamined default.

No option implemented — the audit's purpose was to establish whether a problem
exists and how large it is.

---

## Phase 30 — AUDIT: river-crossing geometry across all 1,131 basins

Full per-crossing table: `audit_crossings_all_basins.csv` (8,262 rows). Detection
run against each basin's SOURCE full-resolution polygon. Nothing filtered,
nothing classified by threshold, no code changed.

### Inventory
8,262 crossings; **1,119 of 1,131 basins have at least one**.

| kind | count | share |
|---|---|---|
| inflow — terminates inside | 2,334 | 28.2% |
| outflow — terminates inside | 2,326 | 28.2% |
| **through-flow — both ends outside** | **2,045** | **24.8%** |
| both ends inside | 1,557 | 18.8% |

**684 basins have at least one through-flow candidate** (the 4080662030 geometry).

### Distribution 1 — fraction of reach length inside the basin (through-flows)
```
p1 0.2% | p5 1.1% | p10 2.6% | p25 7.5% | p50 18.7% | p75 38.6% | p90 57.3% | p99 88.4%
  0-2%  : 159    2-5%  : 215    5-10% : 266   10-20% : 428
 20-30% : 283   30-50% : 385   50-75% : 246   75-100%:  63
```
Unimodal, peaking at 10–20%, decaying smoothly either side. **No gap.**

### Distribution 2 — drainage-area ratio (reach upstream area / basin area)
```
p10 0.01 | p25 0.01 | p50 0.02 | p75 0.20 | p90 7.36 | p95 61.2 | p99 2084 | max 141,138
 <0.1x: 1,441 (70%) | 0.1-1x: 241 | 1-10x: 170 | 10-100x: 111 | >100x: 82
```
This one IS structured: 70% of through-flows are small streams whose own
catchment is a fraction of the basin — they clip an edge but carry little water,
so they are harmless either way. The tail above 10x (193 crossings, 164 basins)
is where a mis-attributed inflow could matter.

### Joint distribution — where a threshold would have to live
| frac inside \ area ratio | <1x | 1–10x | 10–100x | >100x |
|---|---|---|---|---|
| **<5%** | 309 | 23 | 25 | 17 |
| **5–10%** | 207 | 27 | 14 | 18 |
| **10–20%** | 367 | 29 | 16 | 16 |
| **>20%** | 799 | 91 | 56 | 31 |

Reference case **4080662030**: reach 440451489, 291.8 m of 3,094 m inside
(**9.43%**), upstream area 336,849 km² vs basin 1,127 km² (**298.8x**).

### VERDICT: the data does NOT support a clean threshold
For the big rivers where it matters (ratio ≥10x, 193 crossings, 164 basins), the
fraction-inside histogram declines monotonically with **no bimodality and no gap**:
```
 0-5%:42  5-10%:32  10-15%:20  15-20%:12  20-25%:16  25-30%:10  30-35%: 4
35-40%: 7 40-45%: 6  45-50%: 7  50-55%: 7  55-60%: 4  60-65%: 6  65-70%: 7 ...
```
A "clearly clips a corner" cluster does not separate from "clearly passes
through". **Any cutoff is a judgment call, not a datum.**

What the data DOES give is a bounded cost for each choice:

| rule (frac < X AND ratio > Y) | crossings excluded | basins affected | reference caught? |
|---|---|---|---|
| <2% & >10x | 23 | 22 | no |
| <5% & >10x | 42 | 40 | no |
| **<10% & >10x** | **74** | **71** | **yes** |
| <10% & >100x | 35 | 35 | yes |
| <15% & >50x | 52 | 51 | yes |

Basins that would drop to pluvial-only entirely (losing their only forcing):
**<5%&>10x → 4 basins; <10%&>10x → 9; <10%&>100x → 2.**

Note the reference case at 9.43% survives any rule with X ≤ 5%. Choosing a
threshold that excludes it necessarily also excludes ~74 crossings across ~71
basins — that is the real price, and it is a modelling decision, not something
the distribution decides.

**No filtering rule implemented**, as instructed.

---

## Phase 31 — Corner-river exclusion rule + unsimplified display layer

Two independent implementations. `MODEL_VERSION` 12 → **13**.

### 1. Corner-clip exclusion (fluvial)
A THROUGH-FLOW crossing (both ends outside the AOI) is excluded from fluvial
forcing when **both** hold:
```
fraction of reach length inside the AOI  < 10%
reach upstream drainage area / AOI area  > 10x
```
Implemented exactly as specified — not re-derived. `_through_flow_metrics()`
measures the two values the same way the audit script did, so the shipped rule
and the numbers it was chosen from are computed identically;
`_is_corner_clip()` applies them. Both conditions are required: a small stream
clipping an edge is harmless, and a big river genuinely traversing the basin is
real inflow — only the combination is the failure mode.

**Excluded crossings are reported, never silently dropped**, to the same
standard as the channel-snap log. Each carries the measured values:
```
corner-clip EXCLUDED from fluvial forcing: reach 441229143 runs 776 m of its
11957 m inside this AOI (6.5%) and drains 113917 km2 = 24686x the AOI area -
a river grazing the boundary, not inflow to it (rule: <10% inside AND >10x area)
```
The count also appears in the live progress stream
(`"No river crossings — rain-on-grid only (2 corner-clip crossing(s) excluded)"`).

**Reference case, basin 4080662030:** inflows 1 → **0**, total peak 24,544.6 →
**0.0 m³/s**, now **pluvial-only** — matching the original audit's intent. The
single exclusion is logged: reach 440451489, 292 m of 3,094 m (9.43%), 298.8x.

**Spot checks (2 of the other 71 affected basins), mass balance still closing:**

| basin | outcome | inundated / max depth | balance residual |
|---|---|---|---|
| 4080812470 (4.6 km²) | **2** clips excluded → pluvial-only | 3.97 km² / 0.38 m | **0.0139%** |
| 4080807320 (15.8 km²) | 1 clip excluded, 2 real inflows kept (62 m³/s) | 3.34 km² / 1.71 m | **0.0265%** |

Neither flagged `mass_balance_suspect`. The second case is the important one: it
proves the rule removes only the grazing reach and leaves genuine inflow intact.

**Cache invalidation:** the 71 affected basins cannot be identified from opaque
cache-key hashes, so **all 19 cached combined pluvial+fluvial results were
invalidated**; 6 pluvial-only entries kept (unaffected by a fluvial rule).

### 2. Display layer shipped unsimplified
`basins_all.geojson` regenerated straight from source geometry, no `simplify()`.

| | simplified (0.002°) | **full resolution** |
|---|---|---|
| size | 4.61 MB | **7.88 MB** |
| vertices | 119,355 | **206,494** |
| pairs sharing a boundary | 1,153 | **3,199** |
| **overlapping pairs** | 2,035 | **0** |
| **overlap area** | 266.79 km² | **0.000 km²** |
| **gaps vs source** | 453.02 km² | **0.000 km²** |

**Render measured, not assumed** (headless Chromium, identical method both times):

| | simplified | full resolution |
|---|---|---|
| layer ready | 19 ms | **26 ms** |
| fetch (localhost) | 4 ms | 6 ms |
| setView + repaint, median of 5 | 100 ms | **81 ms** |

Full resolution costs **7 ms** more to become ready and is, if anything, faster
to repaint — the median difference sits inside the run-to-run spread (67–354 ms),
so the honest reading is *no measurable degradation*. No performance problem, so
nothing was silently re-simplified.

One caveat the localhost numbers hide: over a tunnel or a remote link the
transfer is 7.88 MB rather than 4.61 MB. On a slow link that is the real cost,
not the render.

Tests: `tests/test_corner_clip.py` (7) — the graze, the genuine traverse, the
small stream clipping an edge, a terminating inflow (rule must not apply),
five boundary cases including the real reference values, and a guard asserting
the two constants still hold their audited values. **63 total, all passing.**
Two pre-existing tests in `test_polygon_crossings.py` needed their unpacking
updated for the new three-value return; one of my own new tests initially failed
because the geometry I picked was 24.9% inside, not <10% — fixed to a genuine
graze rather than weakening the assertion.

---

## Phase 32 — PART A: mask the model domain to the basin (not the bbox)

`MASK_TO_BASIN = True` in `pipeline/aoi_pipeline.py`. No GPU/CUDA touched, no
solver or physics scheme changed, mass-balance and crossing-detection logic
untouched.

### Why the saving is smaller than Phase 18's 54%
LISFLOOD iterates each row between its **first and last non-NODATA column**
(`lisflood_processing.cpp:1885-1894`) — a contiguous span, not a per-cell mask.
So the saving is the row-span reduction, not the area fraction. Measured before
implementing:

| basin | cells iterated NOW | after masking | saving |
|---|---|---|---|
| 4080658150 | 518,160 | 265,839 | 48.7% |
| 4080662030 | 3,213,476 | 1,380,984 | 57.0% |
| 4080033380 | 285,600 | 57,384 | 79.9% |
| 4080640980 | 1,702,887 | 707,252 | 58.5% |
| 4080678160 | 2,833,920 | 1,559,087 | 45.0% |
| 4080812470 | 11,900 | 5,427 | 54.4% |

(A first version of this table showed −153.7% for 4080033380 — my error: I
measured the mask alone rather than mask ∧ DEM-valid. Masking can only shrink a
span. Corrected above.)

### The outlet problem, and the drainage corridor
Masking moves the true boundary to the basin outline, but a **point FREE
boundary requires SGC** (`input.cpp:1778` gates it on `Statesptr->SGC == ON`),
so it cannot serve a pluvial-only run. Rather than enable SGC everywhere — which
would change pluvial physics — the verified edge outlet is kept and the
**outflow river itself is left unmasked** from the basin down to that edge. That
is the path the water physically takes; the corridor is the river continuing
downstream, not an artefact.

**Safety fallback:** if a basin has no outflow reach, no corridor can be opened
and a masked basin would be a closed box that traps water — the defect the free
outlet exists to prevent. Those runs **skip masking entirely** and say so in the
notes. Correctness over speed.

Also made consistent: inflow points are explicitly re-activated if masking would
drop them (guarding a repeat of the Phase 22 "prescribed but never delivered"
failure); the uniform infiltration rate now averages over ACTIVE cells only; and
the SGC width raster is masked to match, so channels are not marked on cells the
solver will not compute.

### Wall time — 3 basins, masked vs unmasked, same inputs
| basin | mode | wall | solver | active cells | speed-up |
|---|---|---|---|---|---|
| 4080812470 (4.6 km², pluvial) | full | 10.7 s | 10.7 s | — | |
| | masked | 8.7 s | 8.6 s | 5,729 | **18.9%** |
| 4080807320 (15.8 km², **fluvial**) | full | 11.9 s | 11.8 s | — | |
| | masked | 6.9 s | 6.8 s | 18,620 | **42.1%** |
| 4080658150 (225.6 km², **fluvial**) | full | 262.8 s | 262.3 s | — | |
| | masked | 163.2 s | 162.7 s | 265,319 | **37.9%** |

Speed-up tracks the predicted span reduction reasonably (48.7% predicted vs
37.9% achieved on 4080658150; the gap is fixed per-run cost — DEM clip,
land cover, rainfall, I/O — which masking does not touch).

**Inflow injection survived masking on both fluvial cases**: peak Qin identical
to the unmasked run (61.79 and 1543.1 m³/s), `inflows_reactivated = 0` — the
Phase 24/31 injection points are inside the mask by construction, now verified
rather than assumed.

### Mass balance still closes
| basin | mode | residual | max\|Verror\| |
|---|---|---|---|
| 4080812470 | full / masked | +0.0103% / **+0.0036%** | 1.0e-04 / 0.0 |
| 4080807320 | full / masked | −0.0090% / **−0.0156%** | 1.3e-08 / 9.0e-09 |
| 4080658150 | full / masked | +0.0257% / **−0.1170%** | 9.9e-08 / 4.2e-08 |

All well inside "a few percent", and `Verror` is at or below the unmasked value
in every case.

### HONEST FINDING: in-basin results are NOT unchanged
The acceptance asked that in-basin inundated area and max depth be identical.
**They are not**, and the difference is larger than numerical noise:

| basin | area change | max depth change | in-basin cells >1 cm different | >10 cm |
|---|---|---|---|---|
| 4080812470 | −0.34% | −3.16% (0.380→0.368 m) | 32.15% | 0.70% |
| 4080807320 | −1.63% | **−9.30%** (1.709→1.550 m) | 7.14% | 0.61% |
| 4080658150 | −1.26% | **0.00%** (7.970 m, unchanged) | 3.59% | 0.51% |

This is a **physics change, not a bug**. Masking imposes no-flow at the drainage
divide: water can no longer spread across the basin boundary onto outside
floodplain, nor arrive from outside. Phase 19 showed most modelled flooding sat
*outside* the basin, so removing that exchange necessarily shifts in-basin
depths — and it shifts them DOWN, consistent with losing inflow from outside.

Where the changes sit supports that reading: cells differing by >10 cm are
concentrated near the boundary on the two small basins (median 29 m and 149 m
from the edge). On the large basin they are further in (median 1,079 m), which
is the channel network re-routing once its outside-basin continuation is removed
(SGC channel cells 6,883 → 3,472).

**So masking is a defensible modelling decision, not a free optimisation.** It
buys 19–42% wall time in exchange for adopting the drainage-divide assumption
literally. `MASK_TO_BASIN` is a single flag so the choice is reversible, and the
decision is the user's to keep or revert.

### Part B was not supplied
The request described "two independent speed improvements" but contained only
Part A. Nothing was assumed or invented for Part B.

---

## Phase 33 — OPEN ISSUE: basin-polygon resolution vs model DEM resolution

**Documentation only. No code changed, no check performed, `MASK_TO_BASIN`
left ON.**

### The mismatch
| | resolution |
|---|---|
| model DEM / compute grid | **~29.2 m** |
| HydroBASINS (`hybas_pak_lev08`) basin polygons | derived from HydroSHEDS at **15 arc-seconds ≈ 500 m** at this latitude |

That is roughly a **17x gap** between the line a user clicks and the grid the
model solves on — not the ~3x gap a naive "SRTM 30 m vs 90 m" comparison would
imply. The AOI boundary was never drawn at anything approaching the model's
precision.

### Practical implication
A modelled cell sitting 50–150 m outside the official polygon line may well be
**inside the true drainage divide**. The boundary data simply is not precise
enough to say otherwise. Treating that line as an exact hydrological divide, as
masking does, gives it an authority the source data does not have.

### Likely link to the Phase 32 finding
Phase 32 measured that masking to the basin boundary changes in-basin results —
max depth −9.30% on basin 4080807320, −3.16% on 4080812470, and ~0.5–0.7% of
in-basin cells shifting by >10 cm on all three. That was attributed to cutting
off water exchange across the boundary.

**This resolution mismatch is a strong candidate for at least part of that
"leakage."** Some of the water that previously crossed the boundary may not have
been leaking into a genuinely different basin at all — it may just reflect
boundary-line imprecision at the ~500 m scale being resolved by a 29 m grid.

### NOT CONFIRMED
Whether the cells that changed in Phase 32 actually sit within ~500 m of the
basin boundary (supporting the resolution explanation) or extend well beyond it
(implying a real hydrological effect such as genuine floodplain spread is also,
or instead, responsible) **has not been determined**. The check was proposed and
**deliberately deferred** — it was not performed in this pass.

For the record, Phase 32 did already note median distances of the >10 cm cells
from the basin edge — 29 m (4080812470), 149 m (4080807320) and 1,079 m with p90
3,025 m (4080658150). Those are incidental observations from the earlier work,
**not** the analysis described above, and they point in different directions on
different basins. They should be treated as a prompt for the real check, not as
its result.

### CURRENT STATE (accepted, not a bug under repair)
- **`MASK_TO_BASIN` stays ON.** Basin-masked runs remain the default going
  forward.
- This is a **known, accepted and documented modelling limitation**, not an open
  defect being actively worked.

### NEXT STEP (recorded for later — do not start from this entry alone)
If revisited: take Phase 32's per-cell difference data (already computed for
basins 4080812470, 4080807320 and 4080658150) and test it against
distance-from-basin-boundary, using the ~500 m HydroBASINS resolution as the
comparison threshold, to apportion the observed change between boundary
imprecision and real hydrological effect.

---

## Phase 34 — OPEN FINDING: SGC diagonal channel double-counting (plan only)

**Documentation only. No rasterization or channel-length code changed in this
pass. Neither fix below has been implemented or verified.**

### Background (open since Phase 14)
Rasterising the vector river network with `all_touched=True` puts **2 cells per
cross-section** on any diagonal reach, each carrying the FULL channel width —
roughly **2x the intended conveyance and channel volume** on every
non-cardinal-direction segment. Flagged in Phase 14, never fixed.

### Current state, verified while writing this entry (not assumed)
`build_sgc_width()` in `pipeline/fluvial.py` does currently rasterise with
**`all_touched=True`**, together with `merge_alg=MergeAlg.replace` and a
narrowest-first sort so the widest reach wins a shared cell (the Phase 12 / B5
fix). So the doubling described above is live in the shipped pipeline.

Also checked: nothing in `pipeline/` currently derives a channel length by
counting cells — the only length-related parameter is `PRAM_MEANDER = 1.0`
(effective channel length per cell = dx x m), passed straight into the `.pram`
file. **Fix 2 below therefore concerns the length accounting inside the solver's
own SGC path, and/or any future code we add that walks the rasterised path — not
an existing line of our Python that is currently wrong.** That distinction
matters and should be re-confirmed before implementing.

### PROPOSED FIX — two separate, independent parts

**FIX 1 — eliminate the doubling.**
Switch the width rasterisation from `all_touched=True` to `all_touched=False`.
That alone gives exactly one cell per cross-section: a centreline pass can only
claim a cell once, so no doubling is possible. Where two reaches' single-cell
paths land on the same cell (confluences), reuse the **existing widest-wins
rule** already in place from Phase 12 / B5 (sort narrowest-first, widest wins the
shared cell) — no new logic needed there.

**FIX 2 — separate issue, easy to miss.**
`all_touched=False` produces a single-cell path, but that path can still take
**diagonal steps** (corner-to-corner, not edge-to-edge). Any code that sums
channel length as `cell_count x cell_size` will **under-count** wherever the path
moves diagonally, because a diagonal step spans `cell_size x sqrt(2)`, not
`cell_size`. This feeds slope, resistance and hydraulic-property calculations.
Fix: when walking the path, classify each step —
- same row OR same column as the previous cell -> straight -> `x cell_size`
- different row AND different column -> diagonal -> `x cell_size x sqrt(2)`
— and sum accordingly.

### REJECTED ALTERNATIVE, with reasoning (recorded deliberately)
A heavier approach was considered: skeletonisation/thinning (e.g. Zhang-Suen), or
inserting an extra corner cell to force full 4-connectivity so the path never
steps diagonally and Fix 2 becomes unnecessary.

**Not chosen.** It adds new cells whose width, depth and length must themselves
be derived and validated, creates more code paths, and widens the surface area
for a new bug — for no accuracy benefit over simply correcting the length formula
for diagonal steps. The two-fix approach is sufficient and lower-risk.

### STATUS and required first step
Recorded plan only. **Neither fix implemented, neither verified.**

When this is picked up, Fix 1 starts with an audit — confirm what the code
actually does at that moment rather than trusting this entry, which records the
state on 2026-09-06. Same audit-before-code standard that caught the real causes
in the mass-balance (Phase 22) and crossing-detection (Phase 23/24) work, where
the first plausible explanation was wrong twice.

Worth noting for whoever implements Fix 1: switching to `all_touched=False` will
REDUCE the channel-cell count and therefore change results on every combined
run. It is a physics change, not a neutral refactor, and will need a
MODEL_VERSION bump and cache invalidation like Phases 24 and 31.

---

## Phase 35 — NEW: Fast Preview mode (approximate, unvalidated) alongside the accurate model

`pipeline/fastpreview.py`, `POST /fast-preview`, amber UI panel. **The accurate
pipeline is untouched: `MODEL_VERSION` still 13, all its tests still pass.**

### Method, with provenance stated per step
| step | source |
|---|---|
| steady-state discharge — D8 accumulation of net rainfall | FastFlood's core idea (they fast-sweep; we use exact elevation-ordered accumulation) |
| **river inclusion — GEOGLOWS RP discharge injected at real crossings** | **THE ADDITION vs base FastFlood.** Reuses the audited detection *including the Phase 31 corner-clip rule* |
| depth inversion — Manning normal depth h = (q·n/√S)^(3/5) | **ESTABLISHED PRACTICE**, not invented here |
| partial-steady-state compensation — min(1, D/t_c) | **OUR OWN FIRST-PASS HEURISTIC**, uncalibrated |
| floodplain spreading — volume-preserving spatial relaxation, fixed passes | **OUR OWN HEURISTIC**, uncalibrated |
| travel time | ESTIMATED from distance/velocity, never presented as simulated |

Structurally enforced: a test greps the module for `run_lisflood`, `LISFLOOD_BIN`,
`subprocess`, `run_scenario(`; a second strips comments/docstrings and rejects
`cfl`, `delta_time`, `timestep`, `courant`. No time loop exists.

### A real bug found and fixed during development
The first version returned **peak Q = 30 m³/s where rainfall alone implies
~1,300**. Cause: plain sink-filling makes depressions PERFECTLY FLAT, D8 then
finds no downslope neighbour inside them, and accumulation dead-ends —
**55,420 cells (3.3%) had no receiver**. Replaced with **Priority-Flood +
epsilon** (Barnes, Lehman & Mulla 2014): **0 interior dead-ends**, the 521
remaining are true border outlets, 1.0 s. Peak Q became 1,386 m³/s vs 1,842 if
all rain ran off — 75% runoff after infiltration, which is sane.
Also fixed `inf − inf → NaN` in two places (finite sentinels).

### CHARSADDA — basin 4080640980, 50-yr / 6 h, both modes at MODEL_VERSION 13
| | accurate (LISFLOOD-FP) | fast preview | ratio |
|---|---|---|---|
| **wall time** | **586.9 s** | **2.5 s** | **235x faster** |
| inundated area (AOI) | 210.15 km² | 231.71 km² | +10.3% |
| max depth (AOI) | 17.03 m | 6.41 m | −62% |
| river inflow | 1 crossing, 30.45 m³/s | 1 crossing, 30.45 m³/s | **identical** |
| mass balance | residual 6.75e-05, not suspect | n/a (no volume budget) | |

**Note on the baseline:** Phase 26's figures (2 inflows, 4,263 m³/s, 212.72 km²,
16.98 m) are NOT comparable — that run predates the Phase 31 corner-clip rule,
which removed the large Kabul reach from this basin. The v13 re-run above is the
correct comparison, and its inflow detection matches the fast mode exactly.

### PLUVIAL-ONLY — basin 4080033380, 50-yr / 3 h (the river-inclusion check)
| | accurate | fast preview |
|---|---|---|
| inflows / injected | **0 / 0 m³/s** | **0 / 0 m³/s** ✅ |
| inundated area (AOI) | 26.11 km² | 25.63 km² (−1.8%) |
| max depth | 0.32 m | 0.25 m (−22%) |
| wall time | ~600 s | **1.7 s** |

River inclusion correctly contributes ZERO where the accurate model finds no
inflow — the requested check passes.

### WHERE THEY AGREE AND DIVERGE — honest reading, nothing tuned
**Agree:** river discharge (identical, same detection code), inundated AREA to
within ~10% on Charsadda and ~2% on the pluvial basin, and the pluvial/fluvial
classification itself.

**Diverge: max depth, badly — 6.41 m vs 17.03 m on Charsadda.** Best
explanation, and it is a structural consequence of the method, not a tuning gap:
1. **No storage or transient peaking.** The accurate model routes a hydrograph
   through sub-grid channels and accumulates a transient peak. Steady state has
   no memory: depth is whatever the instantaneous discharge sustains.
2. **The accurate max sits in an SGC channel cell** (8,583 of them here), which
   carries depth BELOW the floodplain surface. The fast mode has no sub-grid
   channel at all — its depth is normal depth over the DEM surface, so the
   deepest in-channel values simply cannot occur.
3. **The spreading heuristic redistributes volume outward**, trading depth for
   area — visible as fast preview being deeper in area (+10.3%) and shallower in
   depth at the same time.

That the AREA agrees within ~10% while the DEPTH differs 2.7x is the honest
summary: the fast mode is a plausible extent screen and **not** a depth
estimator. **No parameter was tuned to close the gap** — both models remain
unvalidated against observed flooding, so forcing agreement would only have hidden
the difference.

### Labelling
Separate response type (`FastPreviewResponse`, `approximate: true`), separate
endpoint, no job record, no cache write, amber UI panel headed "FAST PREVIEW —
APPROXIMATE, UNVALIDATED" with all four caveats rendered. Verified in-browser:
the accurate results panel stays untouched (`shown: false`) after a preview.

Tests: `tests/test_fast_preview.py` (9). **72 total, all passing.** One test
initially failed by matching the module's own docstring saying it has no CFL
condition — fixed to inspect code tokens rather than prose, not by weakening it.

---

## Phase 36 — Fast Preview vs accurate model on FLUVIAL-DOMINATED basins

Phase 35's two comparison cases carried 30.45 m³/s and 0 m³/s of river inflow,
so the river-inclusion feature — the actual addition over base FastFlood — was
barely exercised. Re-tested on basins where the river dominates.

### Basin 4080939540 — 85 km², 3 inflows, 8,772.9 m³/s (river:rain ≈ 35:1)
| | accurate (LISFLOOD-FP) | fast preview | |
|---|---|---|---|
| **wall time** | **69.7 s** | **0.7 s** | **~100x faster** |
| river inflow applied | 8,772.9 m³/s | 8,772.9 m³/s | **identical** |
| peak Qin / Qout | 8,772.5 / 7,851.0 m³/s | peak accumulated 7,920 m³/s | **within 0.9% of Qout** |
| inundated area (AOI) | 24.02 km² | 40.28 km² | **+68%** |
| max depth (AOI) | 12.36 m | 14.21 m | **+15%** |
| mass balance | residual 0.126%, not suspect | n/a | |

**The discharge field is the strong result.** The fast mode's peak accumulated
discharge (7,920 m³/s) lands within **0.9%** of the accurate model's peak outflow
(7,851 m³/s), without any time-stepping. Both also agree that not all 8,773 m³/s
converges — the three tributaries do not all join inside the basin.

**Extent is 68% too large** and depth 15% too high. Consistent with Phase 35's
reading: the steady-state assumption holds the full RP discharge everywhere at
once, with no channel storage or attenuation to absorb it, and the spreading
heuristic then pushes that surplus outward across the floodplain. Where Phase 35
(rain-dominated) showed the fast mode too SHALLOW because it has no sub-grid
channel to hold deep water, here (river-dominated) it is too WIDE because it has
no storage to attenuate a flood wave. Different sign, same root cause: no
transient dynamics.

### Basin 4080728310 — accurate run FAILED, fast preview succeeded
The accurate model crashed: `killed by signal 6 ... corrupted size vs. prev_size`
— heap corruption, the same upstream LISFLOOD-FP class of defect recorded in
Phase 17 (SIGSEGV on one basin, SIGABRT at teardown on others). Fast preview
returned in 0.1 s: 1 inflow, 10,233.1 m³/s, 5.52 km² flooded, 17.41 m max depth.

No comparison is possible for this basin, so none is claimed. It does however
illustrate one incidental benefit: the fast mode has no dependency on the C++
solver, so it still produces an estimate where the accurate model aborts.

### Revised summary of where the fast mode can and cannot be used
| quantity | agreement | usable? |
|---|---|---|
| river inflow detection & magnitude | **exact** (shared code) | yes |
| peak discharge | **within 0.9%** on the fluvial case | yes, surprisingly good |
| inundated extent | −2% to +68% depending on regime | rough screen only |
| max depth | −62% (rain-dominated) to +15% (river-dominated) | **no** |

The extent error is not a constant bias that could be corrected — it changes
sign with the forcing regime, so no single scale factor would fix it. **Nothing
was tuned**; both models remain unvalidated against observed flooding.

---

## Phase 37 — COMPARATIVE ANALYSIS: Fast Preview vs the accurate LISFLOOD-FP model

Three basins with completed runs in BOTH modes, all at MODEL_VERSION 13, all
50-yr, no parameter tuned in either direction.

### 1. Aggregate comparison
| basin | regime | wall: accurate | wall: fast | speed-up | area acc | area fast | depth acc | depth fast |
|---|---|---|---|---|---|---|---|---|
| 4080033380 | pluvial-only | ~600 s | 1.7 s | ~350x | 26.11 km² | 25.63 km² (−1.8%) | 0.32 m | 0.25 m (−22%) |
| 4080640980 | rain-dominated | 586.9 s | 2.5 s | **235x** | 210.15 km² | 231.71 km² (+10.3%) | 17.03 m | 6.41 m (**−62%**) |
| 4080939540 | river-dominated | 69.7 s | 0.7 s | ~100x | 24.02 km² | 40.28 km² (**+68%**) | 12.36 m | 14.21 m (+15%) |
| 4080728310 | river-dominated | **CRASHED** (SIGABRT, heap corruption) | 0.1 s | — | — | 5.52 km² | — | 17.41 m |

### 2. Spatial agreement — the analysis that matters
Aggregate area can match while the water is in the wrong place, so the standard
flood-model metrics were computed cell-by-cell over AOI cells only.
CSI = hits/(hits+misses+false alarms), POD = hits/(hits+misses),
FAR = false alarms/(hits+false alarms). Accurate model = reference.

| basin | regime | threshold | CSI | POD | FAR |
|---|---|---|---|---|---|
| 4080033380 | pluvial-only | >0.01 m | **0.823** | 0.895 | 0.089 |
| | | >0.10 m | 0.045 | 0.153 | 0.940 |
| 4080640980 | rain-dominated | >0.01 m | 0.355 | 0.551 | 0.501 |
| | | >0.10 m | 0.288 | 0.345 | 0.365 |
| | | >0.50 m | 0.237 | 0.296 | 0.457 |
| 4080939540 | river-dominated | >0.01 m | 0.319 | 0.647 | 0.614 |
| | | >0.10 m | 0.213 | 0.249 | 0.405 |
| | | >0.50 m | 0.125 | 0.141 | 0.474 |

For context: published flood-model intercomparisons treat CSI > 0.7 as good
agreement and CSI < 0.5 as poor. Only the shallow pluvial case clears that bar.

### 3. What the numbers say
**Strong:**
- **Discharge.** On the river-dominated basin the fast mode's peak accumulated
  discharge (7,920 m³/s) sits within **0.9%** of the accurate model's peak
  outflow (7,851 m³/s) — with no time-stepping at all.
- **River detection.** Identical by construction: shared crossing code, shared
  corner-clip rule, shared GEOGLOWS table. 8,772.9 m³/s in both.
- **Wet/dry extent in the shallow pluvial case.** CSI 0.823 at the 1 cm
  threshold is genuinely good agreement.

**Weak, and the failure is systematic rather than noisy:**
- **CSI collapses as the depth threshold rises** in every case (0.823 -> 0.045;
  0.355 -> 0.237; 0.319 -> 0.125). The two models broadly agree on *where it is
  wet at all* and disagree on *where it is deep*. That is exactly what a
  steady-state method without channel storage should get wrong.
- **The extent error CHANGES SIGN with regime**: −1.8% pluvial, +10.3%
  rain-dominated, +68% river-dominated. No single calibration factor can fix a
  bias that reverses direction, so this is a structural limit, not a tuning gap.
- **Depth error also changes sign**: −62% where a sub-grid channel holds deep
  water the fast mode cannot represent, +15% where a flood wave needs storage the
  fast mode has none of.

### 4. Verdict on how the fast mode should be used
| quantity | verdict |
|---|---|
| river inflow magnitude / detection | **trustworthy** (identical code) |
| peak discharge | **good** — within 1% on the one fluvial test |
| "is this basin wet at all" screening | **usable** (CSI 0.82 shallow pluvial) |
| inundated extent | **indicative only** — error −2% to +68%, sign varies |
| depth, and anything depth-derived (hazard, exposure) | **not usable** |

Fair use: triage which scenarios or basins deserve an accurate run. Not fair
use: any number that reaches a decision-maker as a depth or a hazard class.

### 5. Honest limits of this comparison
- **n = 3.** Three basins is enough to show the sign-reversal, not enough to
  characterise the error distribution.
- **Neither model is validated against observed flooding.** The accurate model is
  the *reference*, not the truth; CSI here measures agreement between two
  unvalidated methods.
- One of the four attempted comparisons could not be made at all because the
  accurate model crashed (upstream heap corruption, Phase 17 class).
