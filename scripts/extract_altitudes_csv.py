"""
extract_altitudes_csv.py  —  AEP Tools V2.0
Extrait les altitudes depuis un DEM pour n'importe quel fichier CSV
contenant des colonnes de coordonnees (X/Y UTM ou Lon/Lat WGS84).
"""
import os, csv, math


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _detect_columns(headers):
    """
    Detecte automatiquement les colonnes X, Y et optionnellement ID/nom.
    Retourne (idx_x, idx_y, idx_id) ou leve ValueError.
    """
    h_up = [h.strip().upper() for h in headers]

    XCOLS = ["X", "LON", "LONGITUDE", "EASTING",  "E", "X_UTM", "XCOORD", "COORD_X"]
    YCOLS = ["Y", "LAT", "LATITUDE",  "NORTHING", "N", "Y_UTM", "YCOORD", "COORD_Y"]
    IDCOLS = ["ID", "NOM", "NAME", "CODE", "LABEL", "POINT_ID", "FID"]

    def find(candidates):
        for cand in candidates:
            if cand in h_up:
                return h_up.index(cand)
        # Recherche partielle
        for cand in candidates:
            for i, h in enumerate(h_up):
                if cand in h:
                    return i
        return None

    ix = find(XCOLS)
    iy = find(YCOLS)
    if ix is None or iy is None:
        raise ValueError(
            "Colonnes X/Y non detectees automatiquement.\n"
            "Colonnes trouvees : " + ", ".join(headers) + "\n"
            "Nommez vos colonnes : X, Y  ou  LON, LAT  ou  LONGITUDE, LATITUDE"
        )
    ii = find(IDCOLS)
    return ix, iy, ii


def _bilinear(band, gt, nd, W, H, x, y):
    """Interpolation bilineaire dans le raster."""
    fx = (x - gt[0]) / gt[1] - 0.5
    fy = (y - gt[3]) / gt[5] - 0.5
    c0, r0 = int(math.floor(fx)), int(math.floor(fy))

    def safe(c, r):
        c = max(0, min(W - 1, c))
        r = max(0, min(H - 1, r))
        v = float(band.ReadAsArray(c, r, 1, 1)[0][0])
        return None if (nd is not None and abs(v - nd) < 0.001) else v

    v00 = safe(c0,     r0)
    v10 = safe(c0 + 1, r0)
    v01 = safe(c0,     r0 + 1)
    v11 = safe(c0 + 1, r0 + 1)

    vals = [v for v in (v00, v10, v01, v11) if v is not None]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    v00 = v00 if v00 is not None else m
    v10 = v10 if v10 is not None else m
    v01 = v01 if v01 is not None else m
    v11 = v11 if v11 is not None else m

    tx = fx - c0
    ty = fy - r0
    return (v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty


# ─── Fonction principale ───────────────────────────────────────────────────────

def run(params, log_fn, progress_fn, stop_fn=None):
    """
    params:
        input_csv   : str  — chemin CSV entree
        output_csv  : str  — chemin CSV sortie
        dem_path    : str  — chemin DEM (GeoTIFF)
        epsg_csv    : int  — EPSG des coordonnees CSV (ex: 32630 ou 4326)
        col_x       : str  — nom colonne X (vide = auto-detection)
        col_y       : str  — nom colonne Y (vide = auto-detection)
        nodata_val  : float — valeur a ecrire si DEM absent (-9999)
    """
    input_csv  = params["input_csv"]
    output_csv = params["output_csv"]
    dem_path   = params["dem_path"]
    epsg_csv   = int(params.get("epsg_csv", 32630))
    col_x_usr  = params.get("col_x", "").strip()
    col_y_usr  = params.get("col_y", "").strip()
    nodata_out = float(params.get("nodata_val", -9999))

    log_fn("=" * 58)
    log_fn("  Extraction Altitudes CSV  —  AEP Tools V2.0")
    log_fn("=" * 58)

    # ── Vérifications ────────────────────────────────────────────────────────
    for p, label in [(input_csv, "CSV entree"), (dem_path, "DEM")]:
        if not p:
            raise ValueError(label + " : chemin non renseigne.")
        if not os.path.exists(p):
            raise FileNotFoundError(label + " introuvable : " + p)

    if not output_csv:
        base = os.path.splitext(input_csv)[0]
        output_csv = base + "_avec_altitude.csv"
        log_fn("  Sortie auto : " + os.path.basename(output_csv))

    out_dir = os.path.dirname(output_csv)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # ── Ouvrir DEM ────────────────────────────────────────────────────────────
    from osgeo import gdal, osr
    gdal.UseExceptions()

    log_fn("  Ouverture DEM : " + os.path.basename(dem_path))
    ds   = gdal.Open(dem_path)
    if ds is None:
        raise RuntimeError("GDAL ne peut pas ouvrir le DEM : " + dem_path)
    gt   = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nd   = band.GetNoDataValue()
    W, H = ds.RasterXSize, ds.RasterYSize

    epsg_dem = 4326  # defaut
    try:
        srs_dem = osr.SpatialReference()
        srs_dem.ImportFromWkt(ds.GetProjection())
        srs_dem.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        epsg_dem = int(srs_dem.GetAttrValue("AUTHORITY", 1) or 4326)
    except Exception:
        pass

    px_m = abs(gt[1]) * 111320
    log_fn(f"  DEM EPSG:{epsg_dem}  |  resolution ~{px_m:.0f} m/pixel  |  {W}x{H} pixels")

    # ── Transformation CRS CSV → DEM ─────────────────────────────────────────
    need_transform = (epsg_csv != epsg_dem)
    xf = None
    if need_transform:
        srs_csv = osr.SpatialReference()
        srs_csv.ImportFromEPSG(epsg_csv)
        srs_csv.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        xf = osr.CoordinateTransformation(srs_csv, srs_dem)
        log_fn(f"  Projection : EPSG:{epsg_csv} → EPSG:{epsg_dem}")
    else:
        log_fn(f"  Projection : EPSG:{epsg_csv} (meme CRS que DEM, pas de transformation)")

    # ── Lire CSV ──────────────────────────────────────────────────────────────
    log_fn("  Lecture CSV : " + os.path.basename(input_csv))

    # Detecter separateur
    with open(input_csv, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
    sep = ";" if sample.count(";") > sample.count(",") else ","

    rows = []
    headers = []
    with open(input_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=sep)
        headers = next(reader)
        for row in reader:
            rows.append(row)

    log_fn(f"  {len(rows)} lignes, {len(headers)} colonnes")
    log_fn("  Colonnes : " + ", ".join(headers[:8]))

    # Detecter colonnes X/Y
    if col_x_usr and col_y_usr:
        h_up = [h.strip().upper() for h in headers]
        ix = h_up.index(col_x_usr.upper()) if col_x_usr.upper() in h_up else None
        iy = h_up.index(col_y_usr.upper()) if col_y_usr.upper() in h_up else None
        if ix is None or iy is None:
            raise ValueError("Colonnes specifiees introuvables : " + col_x_usr + ", " + col_y_usr)
    else:
        ix, iy, _ = _detect_columns(headers)

    log_fn(f"  Colonne X : '{headers[ix]}'  |  Colonne Y : '{headers[iy]}'")

    # ── Extraction ────────────────────────────────────────────────────────────
    log_fn("  Extraction en cours ...")
    progress_fn(20)

    out_rows   = []
    n_ok = n_err = n_nd = 0
    REPORT = max(1, len(rows) // 10)

    for i, row in enumerate(rows):
        try:
            x = float(str(row[ix]).replace(",", "."))
            y = float(str(row[iy]).replace(",", "."))
        except (ValueError, IndexError):
            out_rows.append(row + [""])
            n_err += 1
            continue

        # Transformer si necessaire
        if xf:
            xd, yd, _ = xf.TransformPoint(x, y)
        else:
            xd, yd = x, y

        alt = _bilinear(band, gt, nd, W, H, xd, yd)
        if alt is None:
            out_rows.append(row + [str(nodata_out)])
            n_nd += 1
        else:
            out_rows.append(row + [f"{alt:.2f}"])
            n_ok += 1

        if (i + 1) % REPORT == 0:
            pct = round((i + 1) / len(rows) * 70)
            progress_fn(20 + pct)
            log_fn(f"  {round((i+1)/len(rows)*100)}%  ({i+1}/{len(rows)})")

    ds = None

    # ── Ecrire CSV sortie ─────────────────────────────────────────────────────
    out_headers = headers + ["altitude_dem"]
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=sep)
        writer.writerow(out_headers)
        writer.writerows(out_rows)

    progress_fn(100)
    log_fn("")
    log_fn("─" * 50)
    log_fn(f"  Points traites : {n_ok + n_err + n_nd}")
    log_fn(f"  Altitudes OK   : {n_ok}")
    log_fn(f"  Hors DEM       : {n_nd}  (valeur={nodata_out})")
    log_fn(f"  Erreurs coords : {n_err}")
    log_fn(f"  Fichier sortie : {output_csv}")
    log_fn("─" * 50)
    if n_ok == 0:
        raise RuntimeError(
            "Aucun point extrait. Verifiez :\n"
            "  - Le code EPSG des coordonnees CSV\n"
            "  - Que le DEM couvre bien la zone du CSV"
        )
