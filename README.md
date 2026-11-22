# AEP Tools — QGIS plugin for water supply networks

> **Author: Kouidri Sofiane** (sofihydro@gmail.com) — © 2026 Kouidri Sofiane. Any redistribution must retain this notice.

**Version:** 2.3.0 · **License:** GNU GPL v2 or later (see `LICENSE`) · **QGIS:** 3.16+

AEP Tools extracts elevations for water supply (AEP) network layers from a Digital Elevation Model (DEM), tells you how reliable each elevation is, and prepares the data for hydraulic modelling.

## Workflow (6 steps)

| Step | Tab | What it does |
|---|---|---|
| 1 | Export CSV | Exports the vertices of the pipe layer to CSV (densified, 5 m by default) |
| 2 | DEM | Downloads a 30 m DEM (Copernicus GLO-30 or SRTM) for the network area |
| 3 | Altitudes | Elevation + slope at each vertex, consistency checks, final altitude with its source. Also accepts any point CSV (lat/lon or X/Y + EPSG) |
| 4 | Splitting | Splits the pipes at each elevation-difference threshold; nodes get an ID and keep the source, quality and uncertainty of their elevation |
| 5 | Altitude to layers | Adds the elevation field to the project's shapefiles |
| 6 | Hydraulics | Reads the EPANET reservoirs, derives ground elevation from static pressure, checks it against surveys, measures the real DEM error by zone, and finds the nodes where elevation can change the pressure verdict |

**Two uses, two rules.** Transmission mains in open terrain: steps 1 to 5 (DEM, splitting by elevation difference, nodes for EPANET). Distribution networks in built-up areas: the DEM is a surface model (roofs), so its value is not used as a calculation elevation; step 6 replaces it with a hydraulic elevation where pressure was measured, and ranks the other nodes by how much their elevation matters.

See `docs/validation_sans_leve.md` for the field protocol.

## Step 2: downloading a DEM

Two free sources, no account or key. Both are **surface models (DSM)**: they include roofs and vegetation, and are not a bare-earth terrain model.

| | Copernicus GLO-30 (recommended first choice) | SRTM |
|---|---|---|
| Full name | Copernicus DEM GLO-30 | NASA Shuttle Radar Topography Mission |
| Producer | ESA / Airbus | NASA / NGA |
| Sensor, date | TanDEM-X radar, 2011-2015 | C-band radar on the Space Shuttle, February 2000 |
| Resolution | 1 arc-second (about 30 m) | 1 arc-second (about 30 m) |
| Vertical datum | EGM2008 geoid | EGM96 geoid |
| Delivery | Cloud-optimised GeoTIFF tiles (AWS open data) | `.hgt` tiles from AWS Terrain Tiles; a SRTM-based composite, not the raw SRTM product |
| Credit to quote | Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. | NASA SRTM, via AWS Terrain Tiles |

Use GLO-30 as the main DEM and download SRTM over the same zone to use it as the independent control DEM of step 3 (the two come from different sensors and dates; the check removes the median bias between the two datums). The download log repeats which source was fetched and its credit.

Choose the zone: `auto` (bounding box computed from the step 1 CSV plus a margin) or `manuel` (lat/lon min/max in decimal degrees). Limit: 100 one-degree tiles.

## Consistency checks without a field survey (v2.1.7)

Because GLO-30 / SRTM are surface models (DSM), the plugin can flag suspicious vertices even when no survey is available.
All checks are optional and add columns to the output CSV (`alerte`, `qualite_finale`):

| Check | Needs | What it detects | Column |
|---|---|---|---|
| Buildings | a footprint layer (OSM, Microsoft or Google Open Buildings, local data) | vertex within N m of a building | `bati` |
| Second DEM | a second DEM of the same area (e.g. SRTM when GLO-30 is primary) | local disagreement between sources, after removing the median bias | `ecart_dem2_m` |
| Profile spikes | line vertices named `fidX_lY_vZ` (step 1 output) | isolated jump vs. the straight line between neighbours | `ecart_pic_m` |

Any alert downgrades `FIABLE`/`ACCEPTABLE` to `INCERTAINE` in `qualite_finale`.

### Final altitude: DSM / bare-earth blend (v2.1.8)

Step 3 now writes `altitude_m` (the column read by steps 4 and 5) and `source_altitude`, vertex by vertex:

| Situation | `source_altitude` | `altitude_m` |
|---|---|---|
| No alert | `GLO30` | GLO-30 (GPS-calibrated if provided) |
| Alert and a bare-earth DEM (FABDEM) is provided | `FABDEM` | FABDEM value |
| `PIC` alert, no FABDEM, healthy vertices on both sides | `INTERP` | linear interpolation along the pipe (`dist_interp_m` = distance to the farthest anchor) |
| Other alert (`BATI`, `ECART_DEM2`) without FABDEM | `GLO30_ALERTE` | GLO-30 kept, **to be verified** |

* The spike check only detects **narrow positive spikes** (building, isolated tree, up to ~2 pixels wide). Wide hills are not flagged (two-scale test); wide built-up blocks are only caught by the buildings layer.
* **FABDEM is released under CC BY-NC-SA 4.0 (non-commercial)**. The plugin does not bundle or download it: you provide the file yourself and are responsible for its licence. Use it together with GLO-30 as the main DEM (same vertical datum).
* Interpolation assumes the pipe follows a smooth street profile; it is an estimate, not a measurement.
* Tested on synthetic data only; validate on real control points (see `docs/validation_sans_leve.md`).
The second DEM must be a real, independent elevation model (e.g. SRTM). A surface interpolated (IDW) from sparse points is not one: if the two DEMs are incoherent, the check is ignored with a warning.
Limit: both DEMs are DSMs, so a building present in both is **not** caught by the second-DEM check; use the buildings layer
and the spike check for that. The checks flag doubtful vertices, they do not correct them.

See `docs/validation_sans_leve.md` for field-free validation methods (static pressure, as-built levels, ICESat-2).

## Step 6 — Hydraulics: elevations, consistency and sensitive nodes

**Principle.** In a pressurised network the hydraulic head does not depend on node elevation (demand-driven analysis); elevation only turns head into pressure, `P = head - Z`. Two consequences:

1. **Hydraulic levelling.** A static pressure measured at night (no flow) gives the ground elevation: `Z = H_reservoir - P - gauge_height`. It is the same physical quantity as a survey, so it cannot contradict one except through a measurement or data-entry error, which the step detects.
2. **Where it matters.** An elevation error only counts if it can flip the pressure verdict. The step computes, per node, the probability that the verdict is wrong and proposes where to measure first.

**Inputs.** Nodes (step 4 GeoJSON or step 3 CSV); optional EPANET `.inp` (reads `[RESERVOIRS]` head and `[TANKS]` bottom + water level, converts US units); manual reservoir levels `ID=level;ID2=level` (needed for tanks whose level at measurement time differs from the model, or reservoirs only partly known); optional heads CSV (`latitude, longitude, charge_m`, exported from EPANET/QWater for the worst case); optional measurements CSV (`latitude, longitude`, `pression_m` or `pression_bar` and/or `z_leve`; optional `id`, `hauteur_manometre_m`, `sigma_m`, `reservoir`); optional urban-zone polygons (otherwise the step 3 `BATI` alert is used). Without heads, `H_source - head_loss` entered by the user is used and flagged.

**Source hierarchy.** Survey, then hydraulic, then DEM. Survey and hydraulic values are merged (1/sigma^2 weighted mean) only if `|Z1 - Z2| <= k * sqrt(s1^2 + s2^2)`. Otherwise the node is `CONTRADICTION`: the survey is kept, nothing is averaged, the uncertainty is widened to the observed gap, and the point is listed for checking.

**Measured error.** Matched nodes give ME, RMSE and LE90 of the DEM against the reference, separately for rural and urban nodes (`bilan_erreur_dem.csv`). With at least 10 reference points in a zone the LE90 is used as the uncertainty of the unmeasured nodes of that zone (`incertitude_origine = MESUREE`); otherwise the uncertainty is assumed (`SUPPOSEE`).

**Pressure sensitivity.** `P_service = head - Z >= P_min` and `P_static = H_static_max - Z <= P_max`. Classes `SUR` (<5 %), `A_VERIFIER` (5-25 %), `CRITIQUE` (>=25 %). The sampling plan is greedy (highest probability first, minimum spacing, since DEM errors are spatially correlated).

**Outputs.** `noeuds_hydro.geojson` (all fields), `cotes_epanet.csv` (final elevations to copy into the model), `plan_releves.csv` (nodes to measure; fill `z_leve` or `pression_m` and add the rows to the measurements file), `coherence_cotes.csv`, `bilan_erreur_dem.csv`.

**Limits.** Hydraulic levelling exists only where pressure was measured, needs a truly static network and a known reservoir level; residual flow biases it (enter the residual head loss). Heads are those of your own model and are as accurate as the model. Errors are assumed normal and independent. Default sigmas and pressure thresholds are starting values. This is a prioritisation tool, not a compliance guarantee. Tested on synthetic data only; not yet on real networks or inside QGIS.

## Optional GPS calibration

If you provide a CSV of GPS survey points, the DEM–GPS residuals can be applied to each vertex with one of three methods: **IDW** (default), linear **regression**, or global **bias**.

## Accuracy

No validated accuracy figure is published for this plugin yet. A 30 m surface model cannot guarantee ±3 m. Measure the error
on your own zone with step 6 (`bilan_erreur_dem.csv`) and quote the LE90 per zone. Earlier author-reported figures (v2.1) were removed because their
study area and conditions were not documented.

## Known limitations

1. A 30 m DEM is insufficient in steep terrain: check the reliability class of each vertex.
   (Vertical datums differ between sources: GLO-30 uses EGM2008, SRTM uses EGM96. The second-DEM check removes the median bias.)
2. GLO-30 is a surface model (DSM), not bare earth: elevations are over-estimated under dense vegetation or buildings.
3. Step 6 reads heads from your model; without them it falls back to a user-entered source head and head loss, which is flagged.

## Requirements

- GDAL/OGR and PyQt (included with QGIS)
- `openpyxl` for reading `.xlsx` GPS files (optional)
- Internet access for DEM download (HTTPS only)

## Installation

In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP*, then select the plugin ZIP.

## Citation

See `CITATION.cff`. Please cite: Kouidri, S. (2026). *AEP Tools — QGIS plugin*.

## Contact

Kouidri Sofiane — sofihydro@gmail.com
