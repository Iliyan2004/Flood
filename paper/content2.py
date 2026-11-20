"""Sections 4-7."""
from doc import todo
import numpy as np


def engine(D, R):
    t = [D.h(2, "engine", "Simulation engine")]
    t.append(D.h(3, "eqs", "Governing equations"))
    t.append("""<p>The full run solves the two-dimensional shallow-water equations in their local-inertial form [[c:bates2010]], which drops the convective-acceleration term. With water depth <i>h</i>, bed elevation <i>z</i>, unit discharges <i>q<sub>x</sub></i>, <i>q<sub>y</sub></i> on cell faces, rainfall rate <i>r</i> and infiltration rate <i>i</i>, mass is conserved by</p>""")
    t.append(D.eq("cont", r"\frac{\partial h}{\partial t}+\frac{\partial q_x}{\partial x}+\frac{\partial q_y}{\partial y}=r-i"))
    t.append("<p>and the face discharge is updated semi-implicitly (shown for <i>x</i>; <i>y</i> is analogous),</p>")
    t.append(D.eq("mom", r"q_x^{\,t+\Delta t}=\frac{q_x^{\,t}-g\,h_f\,\Delta t\,\partial (h+z)/\partial x}{1+g\,\Delta t\,n^{2}\,|q_x^{\,t}|\,/\,h_f^{\,7/3}},\qquad h_f=\max(h_i+z_i,\,h_j+z_j)-\max(z_i,\,z_j)"))
    t.append("""<p>where <i>n</i> is Manning’s roughness, <i>g</i> is gravity and <i>h<sub>f</sub></i> is the flow depth between neighbouring cells <i>i</i> and <i>j</i>. The time step follows a Courant-type condition</p>""")
    t.append(D.eq("cfl", r"\Delta t=\alpha\,\frac{\Delta x}{\sqrt{g\,h_{\max}}},\qquad \alpha=0.7"))
    t.append("""<p>with <i>h</i><sub>max</sub> the largest depth in the domain. The convective-acceleration term is what separates this scheme from the full equations; where it matters, in steep and fast flow, a second-order discontinuous Galerkin solver (DG2) that solves the full equations is also built (Section [[s:solver]]).</p>""")
    t.append(D.h(3, "solver", "Solver"))
    t.append("""<p>SAILAB uses LISFLOOD-FP 8.2, an established, peer-reviewed, open-source (GPL-3.0) solver from the University of Bristol and collaborators [[c:bates2010,neal2012,sharifian2023]]. We built it from source for CUDA 13 on an ARM64 Blackwell-class GPU, wrapped it for automatic set-up, and changed the following:</p>
<ul>
<li><b>Build compatibility.</b> Four source fixes for a 2022-era code base on CUDA 13 (C++17 standard, a <code>cbrt</code> macro that corrupted CUDA’s own declaration, removed device-property fields, a <code>sync()</code> name clash).</li>
<li><b>An upstream argument bug in the full-equation GPU solvers.</b> The max-depth buffer was created for the wrong solver variant, so it was too small for DG2/FV runs; this caused an illegal-address crash at the first step on domains of about half a million cells and larger (250,000 cells ran, 490,000 crashed). With the one-argument fix a 4.0 million-cell DG2 run completed.</li>
<li><b>Stage boundaries.</b> The GPU local-inertial kernel applied a stage (tide) boundary only to edge cells that were already wet, so a dry coast received no water. We changed the condition on all four edges. On a Karachi coastal test the GPU result matches the CPU build (41.4370 km², 4.00 m maximum depth, both) and runs about three times faster (34 s against 97 s).</li>
<li><b>Constant inflow.</b> The GPU kernel has no constant-inflow case, so a constant discharge is written as a flat two-point time series.</li>
<li><b>Outputs the GPU path does not write.</b> The hazard raster and the cumulative-rainfall column are computed by the wrapper from the per-step depth and velocity files and from the rainfall file.</li>
<li><b>Threading.</b> CPU runs are pinned to one thread: in a controlled test, two to twenty OpenMP threads were six to seven times <em>slower</em> than one, apparently because of a critical section inside the parallel loop.</li>
</ul>
<p>Grid resolution is that of the terrain (about 29 m). The time step is adaptive (<i>α</i>=0.7), starting at 5 s, but at 0.2 s for runs with a river or outburst inflow and 1.0 s on terrain steeper than 10&nbsp;% mean slope, where the larger step was found to blow up. Flux across a face is switched off below a depth of 1 mm, and a cell counts as wet in outputs above 5 cm. The platform currently uses the local-inertial solver for every run (a setting chosen for speed); the full-equation DG2 solver can be selected from the command line and is used for steep-terrain tests. Where the local-inertial solver is run on terrain steeper than 10&nbsp;% mean slope, depths are divided by an empirical factor, 2.97 + 0.037 × (mean slope in %), fitted to seven comparisons with DG2 (R² = 0.992); it corrects magnitude only, not flow paths.</p>
<p><b>Verification.</b> On a 104 km² benchmark the port gives bit-identical results to the original CPU release (v5.9) on the CPU, and the GPU run differs from the CPU run by 0.003 m in maximum depth (2.816 m against 2.818 m; 31,896 against 31,924 flooded cells) while taking about 2 s against 24 s. Section [[s:eval]] adds a comparison with three independent codes.</p>""")
    t.append(D.h(3, "preview", "Instant preview"))
    t.append("""<p>The preview does not run the solver. It computes, from the terrain alone, flow directions and accumulation (D8, after filling depressions), a stream network where at least 500 cells drain in, and the height of every cell above its nearest stream (HAND) [[c:nobre2011]]. For rain of intensity <i>i</i> over catchment area <i>A</i> the flow in the channel follows the rational method with runoff coefficient <i>C</i>=0.5,</p>""")
    t.append(D.eq("rational", r"Q=\frac{C\,i\,A}{3.6}\quad (Q\ \mathrm{in\ m^3/s,\ }i\ \mathrm{in\ mm/h,\ }A\ \mathrm{in\ km^2})"))
    t.append("""<p>and the depth in each stream cell is found from Manning’s equation in a trapezoidal channel whose width follows a hydraulic-geometry relation, <i>W</i>=0.6<i>A</i><sup>0.5</sup> [[c:leopold1953,chow1959]]. A cell is flooded where its HAND is below the stage of its nearest stream cell. Rain that falls into closed depressions fills them from the lowest point up to the spill level. A library of 12 discharge levels is stored per area so that a slider interpolates between levels in the browser without any server call. Development logs give 0.34 s for a first request on a small Pakistani area and 0.014 s when the terrain analysis is cached; we did not benchmark large areas for this paper.</p>
<p><b>How close is the preview to the full run?</b> We compared it with the full solver on 315 terrain windows of 128 × 128 cells (3.7 km), spread over Pakistan, each simulated with a random storm (8–120 mm/h, 1–6 h), roughness (0.02–0.15) and infiltration (0–20 mm/h). The preview ignores roughness and infiltration, as the instant page does. Agreement is modest ([[t:prev]], [[f:preview]]).</p>""")
    rows = R["prev_rows"]
    t.append(D.table("prev", "Agreement between the instant preview and the full solver on 315 terrain windows (wet = depth above 5 cm).",
                     ["Terrain (mean slope)", "n", "CSI", "POD", "FAR", "Wet share: full run", "Wet share: preview", "Median max depth: full / preview (m)"], rows,
                     numcols=(1, 2, 3, 4, 5, 6), hl=(0,),
                     note="CSI is averaged over windows where either result has any wet cell. Terrain thirds are by mean slope: below 1.55 %, 1.55–30.6 %, above 30.6 %. Full run and preview use the same terrain and storm."))
    t.append(D.fig("preview", "fig_preview.png", "Instant preview against the full solver on 315 windows. <b>(a)</b> CSI by terrain third. <b>(b)</b> Share of the window flagged wet: the preview over-predicts on flat terrain (points above the 1:1 line), where it floods the land beside every drainage line.", "100%"))
    t.append("""<p>The preview finds most of what the full run floods (POD 0.67 overall, 0.84 on the flattest third) but flags about 1.6 times too much area and overstates depth by roughly four times. It is therefore a screening aid for ranking scenarios and areas, not a substitute for a run.</p>""")
    t.append(D.h(3, "forcing", "Forcing and boundary conditions"))
    t.append("""<ul>
<li><b>Rainfall.</b> A triangular storm (peak at half the duration) from a peak intensity and a duration; an uploaded station series (for example 15-minute gauge data) used point by point; or a return-period storm. Return-period depths come from a Gumbel fit to annual-maximum daily rainfall of CMIP6 historical simulations bias-corrected by the NEX-GDDP method (1985–2014, ensemble median of up to five models) [[c:thrasher2022]]; the 24-hour depth becomes a 24-hour triangular storm with the same total. Real intensity–duration curves from ERA5-Land and IMERG [[c:munoz2021]] are available for design intensities at shorter durations. Rain is applied uniformly over the area unless an areal-reduction option is switched on.</li>
<li><b>Losses.</b> A constant infiltration rate, scaled by an antecedent-moisture slider (multipliers 1.8, 1.0 and 0.25 for dry, average and saturated soil, interpolated between), or an area-mean saturated conductivity from soil texture [[c:rawls1983,poggio2021]]. The GPU solver takes one loss rate per run, so the soil-derived value is an area mean, not a per-cell field.</li>
<li><b>Roughness.</b> Manning’s <i>n</i> per cell from land cover: tree cover 0.130, mangroves 0.140, shrubland 0.070, cropland 0.037, grassland 0.035, wetland 0.045, built-up 0.045, snow 0.030, bare ground 0.025, water 0.025 (typical overland-flow values [[c:chow1959]]). The user value, default 0.05, is used where land cover is missing. Buildings are burned into the terrain as obstacles using their full footprints.</li>
<li><b>Boundaries.</b> All four edges are open by default (free outflow, slope 0.001); a closed basin can be chosen. Inflows and stage boundaries are described in [[t:types]].</li>
</ul>""")
    return "\n".join(t)


def damage(D, R):
    t = [D.h(2, "damage", "Exposure, damage and loss")]
    t.append(D.h(3, "hazard", "Hazard metrics extracted from each run"))
    t.append("""<p>For every cell the solver stores the peak depth <i>h</i>, the time of first wetting and the total wet time, and per time step the depth and the face velocities. The speed |<b>u</b>| = (<i>q<sub>x</sub></i>² + <i>q<sub>y</sub></i>²)<sup>1/2</sup>/<i>h</i> is computed after averaging the staggered velocity components back to cell centres. A hazard rating is the peak over time of a simplified form of the Defra/Environment Agency rating for people [[c:defra2006]],</p>""")
    t.append(D.eq("hr", r"HR=h\,(|\mathbf{u}|+1.5)"))
    t.append("""<p>banded as low (below 0.75), moderate (0.75–1.25), significant (1.25–2.0) and extreme (above 2.0). The depth–velocity product is not stored separately.</p>""")
    t.append(D.h(3, "classes", "Building damage classes"))
    t.append("""<p>Each building’s peak depth is sampled at its footprint centroid and converted to a damage fraction <i>D</i>(<i>h</i>) with the JRC Asia residential curve [[c:huizinga2017]], linearly interpolated: 0 at 0 m, 0.30 at 0.5 m, 0.45 at 1 m, 0.55 at 1.5 m, 0.62 at 2 m, 0.72 at 3 m, 0.80 at 4 m, 0.86 at 5 m and 0.90 at 6 m. The class follows from the fraction: <em>minor</em> below 0.30 (depth under 0.5 m), <em>moderate</em> 0.30–0.55 (to 1.5 m), <em>major</em> 0.55–0.72 (to 3 m) and <em>severe</em> above, with <em>dry</em> for depths of 5 cm or less. A damage class built on absolute depth was chosen over depth relative to building height because the usual single-storey height assumption made almost every real flood look minor.</p>""")
    t.append(D.h(3, "loss", "Loss in rupees and annual average loss"))
    t.append("""<p>Structure loss of building <i>k</i> is its floor area times the maximum damage value times the damage fraction, and contents add a fixed share,</p>""")
    t.append(D.eq("loss", r"L_k=A_k\,s_k\,V\,D(h_k)\,(1+\kappa),\qquad V=111\ \mathrm{EUR/m^2}\times 322.5\ \mathrm{PKR/EUR},\quad \kappa=0.5"))
    t.append("""<p>with footprint area <i>A<sub>k</sub></i> from the building polygon, the number of storeys <i>s<sub>k</sub></i> = max(1, height/3 m), and the JRC Asia residential maximum damage value <i>V</i>, 111 EUR/m² at 2010 prices [[c:huizinga2017]]. The content ratio of 50&nbsp;% of structure loss is a common residential assumption in US flood-loss practice. Buildings without a recorded height are given 6 m, that is two storeys, which doubles their floor area relative to a single storey; we state this because it moves the totals. Pakistan has no country-specific value in the JRC table, so the Asia average is used. Euros are converted at a current mid-market rate (322.5 PKR/EUR, September 2026) and are <em>not</em> adjusted for inflation since 2010, so loss figures are indicators for comparing options rather than forecasts of cost. Cropland adds area × damage fraction × the JRC agriculture value (0.03 EUR/m²); built-up land is not priced a second time.</p>
<p>For events with annual exceedance probabilities <i>p<sub>k</sub></i> (<i>p</i><sub>1</sub> &gt; <i>p</i><sub>2</sub> &gt; …) and losses <i>L<sub>k</sub></i>, the annual average loss is the area under the loss–exceedance curve,</p>""")
    t.append(D.eq("aal", r"\mathrm{AAL}=\int_{0}^{1}L(p)\,dp\;\approx\;\sum_{k}\frac{1}{2}\,(L_k+L_{k+1})\,(p_k-p_{k+1})"))
    t.append("""<p>by the trapezoid rule. Beyond the rarest return period tested the loss is held constant, since a rarer event is not assumed cheaper; below the most frequent one it falls linearly to zero. We use return periods of 10, 50 and 100 years from the design storms above (Section [[s:case]]).</p>""")
    t.append(D.h(3, "fac", "Critical facilities and access"))
    t.append("""<p>Schools, hospitals, power lines, railway lines, airports, ports and, inside Islamabad, shelters, police posts, water and fuel facilities are sampled at their centroids: a facility is <em>flooded</em> if the peak depth exceeds 5 cm there, and its hazard band is read from the hazard raster. Road segments are treated the same way, so disruption is graded by hazard band and not a binary passability test. Roads are further split into major (motorway to tertiary) and local, with kilometres flooded and bridges counted; the terrain model has no bridge decks, so a bridge counts as flooded when the ground beneath it is, which makes bridge counts an upper bound.</p>""")
    return "\n".join(t)


def mitigation(D, R):
    t = [D.h(2, "mitig", "Mitigation module")]
    t.append(D.h(3, "opts", "Options"))
    t.append("""<p>Every option changes the inputs of the same solver; nothing is a separate model.</p>
<ul>
<li><b>Embankment or barrage.</b> A user-drawn line is buffered into a corridor (half-width 8 m and 12 m by default) and the terrain is raised there by the design height (3.0 m and 2.5 m). Square caps tie the ends into higher ground.</li>
<li><b>Drainage pipe or diversion canal.</b> The corridor (half-width 1.5 m and 6 m) is lowered by 1.0 m and 2.0 m. The drain is a one-dimensional approximation: a lowered channel in the terrain, not a closed pipe network.</li>
<li><b>Retention basin.</b> A wide lowered footprint that captures runoff and overflows when full, as a real detention basin does; its storage volume is reported as an upper bound.</li>
<li><b>Nature-based options.</b> The land cover of the whole area is changed to forest, wetland or grassland, immediately (active) or by one succession step (passive). This changes the roughness grid and the area-mean infiltration rate.</li>
</ul>
<p>Structures are applied to the same building-conditioned terrain as the baseline, so the effect of buildings is the same in both runs. Dimensions are defaults, not engineering designs.</p>""")
    t.append(D.h(3, "opt", "Placement optimiser"))
    t.append("""<p>The optimiser searches for the placement of one to three structures that minimises a loss-weighted objective: the negative percentage of baseline building loss avoided, plus half the percentage of cells that become newly flooded because water was displaced, so a design that merely moves the problem next door is penalised. Each structure has seven decision variables: centre position (two), orientation, length, a height multiplier, a width multiplier and the structure type. The search uses differential evolution [[c:storn1997]] as implemented in <code>mystic</code> [[c:mckerns2011]], with a seed derived from the request so identical requests repeat. <b>Every candidate is evaluated with a full solver run</b>, not with the instant preview, because the preview cannot represent a structure’s effect on flow. The budget is therefore small and user-chosen: 12, 24 or 48 trials (Quick, Balanced, Thorough). The best <em>evaluated</em> trial is reported rather than the solver’s final vector, so the reported metrics always match the geometry shown.</p>""")
    t.append(D.h(3, "econ", "Economic comparison"))
    t.append("""<p>Because loss is in rupees, an option can be compared by avoided loss against cost. With Δ<i>AAL</i> the reduction in annual average loss, <i>T</i> the lifetime in years, <i>δ</i> the discount rate and <i>C</i> the capital cost, the benefit–cost ratio and the break-even capital cost are</p>""")
    t.append(D.eq("bcr", r"\mathrm{BCR}=\frac{\Delta\mathrm{AAL}\,\sum_{t=1}^{T}(1+\delta)^{-t}}{C},\qquad C^{*}=\Delta\mathrm{AAL}\;\frac{1-(1+\delta)^{-T}}{\delta}"))
    t.append("""<p><i>C</i><sup>*</sup> is the most that could be spent before BCR falls below 1. SAILAB does not contain construction costs, so the case study (Section [[s:case]]) reports <i>C</i><sup>*</sup> for <i>T</i>=30 years and <i>δ</i>=10&nbsp;%, which are illustrative values of ours, not site-specific.</p>""")
    return "\n".join(t)


def monitor(D, R):
    t = [D.h(2, "monitor", "Monitoring and alerting")]
    t.append("""<p>A user registers a site (a drawn area or a point), a rainfall threshold in mm/h, a check interval (3 hours by default) and, optionally, a webhook address. A background scheduler wakes every 10 minutes and checks each site that is due. A check fetches the Open-Meteo hourly forecast (three days ahead, three days back) [[c:openmeteo]], finds the peak hourly rate in the future part of the series and the contiguous rain event around it, and compares the peak with the threshold. If it is crossed, the platform submits a full physics run through its own run endpoint, using the forecast peak and duration as a triangular storm, and posts an alert. The alert goes to any Slack-, Discord-, ntfy- or custom-style webhook with the site, peak, duration, threshold and run identifier, and includes a report link when the platform has a public address. Forecast times are in the site’s local time zone, and “now” is computed in that same zone; a UTC comparison once misplaced the future–past boundary by five hours for Pakistan and was fixed.</p>""")
    m = R.get("mon")
    if m:
        import content4 as c4
        t.append(c4.mon_block(D, m))
    else:
        t.append(f"<p>{todo('latency measurements pending: the shared drive holding the solver was unavailable')}</p>")
    t.append("""<p>The module is deliberately simple. It uses one deterministic forecast and a single threshold, with no ensemble or probability, so it can both miss a localised burst that a 10 km model does not resolve and raise an alert for rain that does not fall. Every logged forecast is scored after 54 hours against a later analysis from the same provider, so a false-alarm and miss rate can be measured; we have not yet analysed that ledger. The alert is sent when the run is submitted, not when it finishes, so the first message carries a run identifier and the results follow.</p>""")
    return "\n".join(t)
