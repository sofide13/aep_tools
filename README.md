# AEP Tools — QGIS plugin for water supply networks

> **Author: Kouidri Sofiane** (sofihydro@gmail.com) — © 2026 Kouidri Sofiane. Any redistribution must retain this notice.

**Version:** 2.1.5 · **License:** GNU GPL v2 or later (see `LICENSE`) · **QGIS:** 3.16+

AEP Tools extracts elevations for water supply (AEP) network layers from a Digital Elevation Model (DEM), tells you how reliable each elevation is, and prepares the data for hydraulic modelling.

## Workflow (6 steps)

| Step | Tab | What it does |
|---|---|---|
| 1 | Export CSV | Exports the network layers from QGIS to CSV |
| 2 | DEM | Downloads a DEM for the network area |
| 3 | Elevations | Extracts elevations at each vertex and classifies their reliability |
| 4 | Splitting | Splits the network by elevation-difference threshold |
| 5 | Elevation attribute | Adds the elevation field to QGIS layers |
| 6 | CSV elevations | Extracts elevations for any CSV of coordinates |

## DEM sources

| Source | Resolution | Access |
|---|---|---|
| Copernicus GLO-30 | ~30 m | Free, no key |
| SRTM GL1 | ~30 m | Free, no key |
| NASADEM | ~30 m | OpenTopography API key |
| AW3D30 | ~30 m | OpenTopography API key |
| CopDEM GLO-10 | ~10 m | Free Copernicus Data Space account |

The default source is Copernicus GLO-30 (global coverage, vertical accuracy depends on terrain).

## Reliability classification

Each vertex receives a `qualite` label based on the local DEM slope (Horn 3×3 algorithm):

| Local slope | Class | Estimated uncertainty |
|---|---|---|
| < 2° | `FIABLE` | ±3–5 m |
| 2°–5° | `ACCEPTABLE` | ±5–15 m |
| 5°–15° | `INCERTAINE` | ±15–30 m |
| > 15° | `NON_FIABLE` | > ±30 m |

(Class names are kept in French in the output files: reliable / acceptable / uncertain / unreliable.)

## Optional GPS calibration

If you provide a CSV of GPS survey points, the DEM–GPS residuals can be applied to each vertex with one of three methods: **IDW** (default), linear **regression**, or global **bias**.

## Reported validation (version 2.1)

Author-reported results with the Copernicus GLO-30 DEM against 4,203 GPS control points: mean error +0.29 m, **RMSE 1.43 m**, **LE90 2.25 m**, 96.6 % of points within ±3 m. Results depend on terrain, DEM source and whether GPS calibration is used; validate on your own control points before relying on them.

## Known limitations

1. A 30 m DEM is insufficient in steep terrain: check the reliability class of each vertex.
2. GLO-30 is a surface model (DSM), not bare earth: elevations are over-estimated under dense vegetation or buildings.
3. Premium sources (e.g. CopDEM GLO-10) may not be available through the standard API for free accounts.

## Step 6 — CSV format

Accepted separators: `,` or `;`.

```
ID;X;Y;...other columns...
1;500000;4500000;...
```

or in geographic coordinates:

```
ID,LONGITUDE,LATITUDE,...
1,12.3456,45.6789,...
```

Common EPSG codes: `32630` (UTM 30N), `32631` (UTM 31N), `4326` (WGS84 longitude/latitude).

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
