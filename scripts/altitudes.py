"""
Script 02 — Extraction altitudes depuis DEM local (AEP Tools V2.0)

MÉTHODE :
  1. Extraction DEM bilinéaire directe (précis, sans jointure spatiale)
  2. Calcul automatique de la pente locale depuis le DEM
  3. Classification d'incertitude basée sur la pente (validée terrain)
  4. Calibration GPS optionnelle : si un CSV de levés GPS est fourni,
     les résidus DEM-GPS sont interpolés (IDW) et appliqués à chaque vertex.

CLASSIFICATION D'INCERTITUDE (validée sur 3738 points GPS, Ouest-Algérien) :
  Pente < 3°   → FIABLE       ±5-10m   (ex zone FG plateau : RMSE mesuré 4.8m)
  Pente 3-15°  → ACCEPTABLE   ±10-20m
  Pente 15-30° → INCERTAINE   ±20-40m
  Pente > 30°  → NON_FIABLE   >40m     (ex zone BC gorges : RMSE mesuré 47m)

TOLÉRANCE AEP : ±3m souhaités → atteignable en terrain plat avec DEM 10m (CopDEM-GLO-10)
                DEM 30m donne ±5-10m en plaine, ±30-50m en montagne.

COLONNES CSV DE SORTIE :
  latitude, longitude, name
  altitude_dem_m      : altitude brute extraite du DEM (m)
  altitude_corrigee_m : altitude après correction GPS (= DEM si pas de GPS)
  pente_deg           : pente locale calculée depuis DEM (degrés)
  qualite             : FIABLE / ACCEPTABLE / INCERTAINE / NON_FIABLE / NODATA
  incertitude_m       : incertitude estimée (m)
  methode             : DEM_BRUT / DEM_GPS_CAL
  n_gps_voisins       : nb points GPS utilisés pour la correction (0 = pas de GPS)
"""

import csv, os, math, time


# ─── Constantes ───────────────────────────────────────────────────────────────

# Plages de pente → classification qualité
_PENTE_FIABLE      =  3.0   # deg
_PENTE_ACCEPTABLE  = 15.0
_PENTE_INCERTAINE  = 30.0

# Incertitudes estimées par classe (DEM 30m, validées terrain)
_INCERT = {
    "FIABLE":      8.0,
    "ACCEPTABLE":  15.0,
    "INCERTAINE":  30.0,
    "NON_FIABLE":  50.0,
}

# Rayon par défaut pour la calibration GPS (mètres)
_RADIUS_GPS_M = 500.0
_K_GPS        = 8      # max points GPS pour l'IDW


# ─── Accès DEM ────────────────────────────────────────────────────────────────

def _open_dem(path):
    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Open(path)
    if ds is None:
        raise RuntimeError("Impossible d'ouvrir le DEM : " + path)
    gt   = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nd   = band.GetNoDataValue()
    srs  = ds.GetProjection()
    return ds, gt, band, nd, srs


def _make_transformer(srs_dem_wkt):
    """Retourne une fonction (lon, lat) -> (x_dem, y_dem)."""
    from osgeo import osr
    srs_wgs = osr.SpatialReference()
    srs_wgs.ImportFromEPSG(4326)
    srs_wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs_dem = osr.SpatialReference()
    srs_dem.ImportFromWkt(srs_dem_wkt)
    srs_dem.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if srs_wgs.IsSame(srs_dem):
        return lambda lon, lat: (lon, lat)
    xf = osr.CoordinateTransformation(srs_wgs, srs_dem)
    def _tr(lon, lat):
        x, y, _ = xf.TransformPoint(lon, lat)
        return x, y
    return _tr


def _safe_read(band, c, r, nd, W, H):
    c = max(0, min(W-1, c))
    r = max(0, min(H-1, r))
    v = float(band.ReadAsArray(c, r, 1, 1)[0][0])
    if nd is not None and abs(v - nd) < 0.001:
        return None
    return v


def _sample_bilinear(x, y, ds, gt, band, nd):
    """Interpolation bilinéaire sub-pixel — précision optimale."""
    fx = (x - gt[0]) / gt[1] - 0.5
    fy = (y - gt[3]) / gt[5] - 0.5
    c0 = int(math.floor(fx))
    r0 = int(math.floor(fy))
    tx = fx - c0
    ty = fy - r0
    W, H = ds.RasterXSize, ds.RasterYSize

    v00 = _safe_read(band, c0,   r0,   nd, W, H)
    v10 = _safe_read(band, c0+1, r0,   nd, W, H)
    v01 = _safe_read(band, c0,   r0+1, nd, W, H)
    v11 = _safe_read(band, c0+1, r0+1, nd, W, H)

    vals = [v for v in (v00, v10, v01, v11) if v is not None]
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    v00 = v00 if v00 is not None else mean
    v10 = v10 if v10 is not None else mean
    v01 = v01 if v01 is not None else mean
    v11 = v11 if v11 is not None else mean

    top    = v00*(1-tx) + v10*tx
    bottom = v01*(1-tx) + v11*tx
    return top*(1-ty) + bottom*ty


def _sample_nearest(x, y, ds, gt, band, nd):
    """Pixel le plus proche — rapide mais moins précis."""
    W, H = ds.RasterXSize, ds.RasterYSize
    col = int((x - gt[0]) / gt[1])
    row = int((y - gt[3]) / gt[5])
    if col < 0 or row < 0 or col >= W or row >= H:
        return None
    return _safe_read(band, col, row, nd, W, H)


# ─── Pente locale ─────────────────────────────────────────────────────────────

def _compute_slope(x, y, ds, gt, band, nd, px_m=30.0):
    """
    Calcule la pente locale (degrés) par la méthode Horn (3x3 kernel).
    px_m : taille du pixel en mètres (défaut 30m pour DEM 30m).
    Retourne None si données insuffisantes.
    """
    W, H = ds.RasterXSize, ds.RasterYSize
    c0   = int((x - gt[0]) / gt[1])
    r0   = int((y - gt[3]) / gt[5])

    def rd(dc, dr):
        return _safe_read(band, c0+dc, r0+dr, nd, W, H)

    # Kernel Horn 3x3
    z = {
        (-1,-1): rd(-1,-1), ( 0,-1): rd( 0,-1), ( 1,-1): rd( 1,-1),
        (-1, 0): rd(-1, 0),                      ( 1, 0): rd( 1, 0),
        (-1, 1): rd(-1, 1), ( 0, 1): rd( 0, 1), ( 1, 1): rd( 1, 1),
    }
    # Besoin des 4 coins et 4 bords pour Horn complet
    needed = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
    if any(z[k] is None for k in needed):
        # Repli : différence simple (E-W et N-S)
        e = rd( 1, 0); w = rd(-1, 0)
        n = rd( 0,-1); s = rd( 0, 1)
        if None in (e, w, n, s):
            return None
        dzdx = (e - w) / (2 * px_m)
        dzdy = (s - n) / (2 * px_m)
    else:
        dzdx = ((z[(1,-1)] + 2*z[(1,0)] + z[(1,1)])
               - (z[(-1,-1)] + 2*z[(-1,0)] + z[(-1,1)])) / (8 * px_m)
        dzdy = ((z[(-1,1)] + 2*z[(0,1)] + z[(1,1)])
               - (z[(-1,-1)] + 2*z[(0,-1)] + z[(1,-1)])) / (8 * px_m)

    slope_rad = math.atan(math.sqrt(dzdx**2 + dzdy**2))
    return math.degrees(slope_rad)


def _classify_qualite(slope_deg):
    """Classification qualité et incertitude depuis la pente locale."""
    if slope_deg is None:
        return "NON_FIABLE", _INCERT["NON_FIABLE"]
    if slope_deg < _PENTE_FIABLE:
        return "FIABLE", _INCERT["FIABLE"]
    if slope_deg < _PENTE_ACCEPTABLE:
        return "ACCEPTABLE", _INCERT["ACCEPTABLE"]
    if slope_deg < _PENTE_INCERTAINE:
        return "INCERTAINE", _INCERT["INCERTAINE"]
    return "NON_FIABLE", _INCERT["NON_FIABLE"]


# ─── Calibration GPS optionnelle ──────────────────────────────────────────────

def _load_gps_points(gps_csv, gps_epsg, log_fn):
    """
    Charge les points GPS de calibration depuis un CSV.
    Format attendu : lat, lon, altitude_gps_m   (WGS84)
    ou              X, Y, Altitude              (EPSG:gps_epsg, ex: 32630)

    Retourne liste de dict {x_utm, y_utm, alt_gps}.
    """
    pts = []
    try:
        with open(gps_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = [h.strip().lower() for h in (reader.fieldnames or [])]
            log_fn("  GPS colonnes : " + str(reader.fieldnames))

            # Détection colonnes
            has_latlon = "latitude" in headers or "lat" in headers
            has_xy     = ("x" in headers or "x_utm" in headers)

            for row in reader:
                row_l = {k.strip().lower(): v for k, v in row.items()}
                try:
                    if has_latlon:
                        lat = float(row_l.get("latitude", row_l.get("lat", "")))
                        lon = float(row_l.get("longitude", row_l.get("lon", "")))
                        alt = float(row_l.get("altitude_gps_m",
                                    row_l.get("altitude", row_l.get("alt", ""))))
                    else:
                        x   = float(row_l.get("x_utm", row_l.get("x", "")))
                        y   = float(row_l.get("y_utm", row_l.get("y", "")))
                        alt = float(row_l.get("altitude", row_l.get("alt", "")))
                        # Convertir UTM -> WGS84
                        from pyproj import Transformer as _T
                        _tr = _T.from_crs("EPSG:" + str(gps_epsg),
                                          "EPSG:4326", always_xy=True)
                        lon, lat = _tr.transform(x, y)
                    pts.append({"lat": lat, "lon": lon, "alt": alt})
                except (ValueError, KeyError):
                    pass
    except Exception as e:
        log_fn("  [!] Erreur lecture GPS CSV : " + str(e))
    return pts


def _build_gps_index(gps_pts, gps_epsg, log_fn):
    """
    Convertit les GPS en UTM et retourne (coords_utm_list, alt_list).
    """
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326",
                                  "EPSG:" + str(gps_epsg), always_xy=True)
        coords = []
        alts   = []
        for p in gps_pts:
            x, y = tr.transform(p["lon"], p["lat"])
            coords.append((x, y))
            alts.append(p["alt"])
        log_fn("  GPS index : " + str(len(coords)) + " points charges")
        return coords, alts
    except Exception as e:
        log_fn("  [!] Erreur index GPS : " + str(e))
        return [], []


def _calibration_biais(gps_residu):
    """
    Méthode Biais : décalage constant = moyenne des résidus GPS.
    Appliqué uniformément à tous les vertices (indépendant de la distance).
    Retourne la valeur du biais.
    """
    if not gps_residu:
        return 0.0
    return sum(gps_residu) / len(gps_residu)


def _calibration_regression(vx_utm, vy_utm, gps_coords, gps_residu):
    """
    Méthode Régression linéaire : ajuste un plan résidu = a*X + b*Y + c
    sur l'ensemble des points GPS, puis prédit la correction au vertex.
    Robuste aux tendances spatiales (biais non uniforme).
    Retourne (correction, n_pts_utilises).
    """
    n = len(gps_coords)
    if n < 3:
        # Pas assez de points → repli sur le biais global
        if n == 0:
            return None, 0
        return sum(gps_residu) / n, n

    # Moindres carrés : matrice A = [X, Y, 1]
    sx = sy = sxx = sxy = syy = sr = sxr = syr = 0.0
    for (gx, gy), res in zip(gps_coords, gps_residu):
        sx  += gx;  sy  += gy
        sxx += gx*gx;  sxy += gx*gy;  syy += gy*gy
        sr  += res; sxr += gx*res;    syr += gy*res

    mat = [
        [sxx, sxy, sx],
        [sxy, syy, sy],
        [sx,  sy,  n ],
    ]
    rhs = [sxr, syr, sr]

    # Résolution par élimination de Gauss 3×3
    try:
        for col in range(3):
            pivot = mat[col][col]
            if abs(pivot) < 1e-12:
                raise ValueError("pivot nul")
            for row in range(col+1, 3):
                f = mat[row][col] / pivot
                for k in range(col, 3):
                    mat[row][k] -= f * mat[col][k]
                rhs[row] -= f * rhs[col]
        coeffs = [0.0]*3
        for row in range(2, -1, -1):
            coeffs[row] = rhs[row]
            for k in range(row+1, 3):
                coeffs[row] -= mat[row][k] * coeffs[k]
            coeffs[row] /= mat[row][row]
        a, b, c = coeffs
        correction = a * vx_utm + b * vy_utm + c
        return correction, n
    except Exception:
        # Repli biais global si régression échoue (points colinéaires…)
        return sum(gps_residu) / n, n


def _idw_residus(vx_utm, vy_utm, gps_coords, gps_residu, radius_m, k_max):
    """
    Interpolation IDW des résidus GPS autour d'un vertex.
    Retourne (correction, n_pts_utilises).
    correction = valeur à SOUSTRAIRE à l'altitude DEM.
    """
    dists_sq = []
    for i, (gx, gy) in enumerate(gps_coords):
        d2 = (vx_utm - gx)**2 + (vy_utm - gy)**2
        if d2 <= radius_m**2:
            dists_sq.append((d2, i))

    if not dists_sq:
        return None, 0

    dists_sq.sort(key=lambda t: t[0])
    sel = dists_sq[:k_max]

    wsum = 0.0
    rsum = 0.0
    for d2, i in sel:
        d = math.sqrt(d2)
        w = 1.0 / max(d, 0.1)**2
        wsum += w
        rsum += w * gps_residu[i]

    return rsum / wsum, len(sel)


# ─── Fonction principale ──────────────────────────────────────────────────────

def run(params, log_fn, progress_fn, stop_fn=None):
    """
    params:
        input_csv    : str   — CSV vertices (latitude, longitude, name)
        output_csv   : str   — CSV de sortie enrichi
        dem_tif      : str   — GeoTIFF DEM (Copernicus, SRTM, ou autre)
        methode      : str   — 'bilinear' (recommandé) | 'nearest'
        gps_csv      : str   — CSV levés GPS (optionnel, pour calibration)
        gps_epsg     : int   — EPSG des GPS (défaut 32630 = UTM 30N)
        radius_gps_m : float — rayon recherche GPS en m (défaut 500)
    """
    input_csv    = params["input_csv"]
    output_csv   = params["output_csv"]
    dem_tif      = params["dem_tif"]
    methode      = params.get("methode", "bilinear")
    gps_csv      = params.get("gps_csv", "").strip()
    methode_cal  = params.get("methode_cal", "idw")   # idw | regression | biais
    gps_epsg     = int(params.get("gps_epsg", 32630))
    radius_gps_m = float(params.get("radius_gps_m", _RADIUS_GPS_M))

    log_fn("=" * 58)
    log_fn("  Etape 2 -- Altitudes DEM  [AEP Tools V2.0]")
    log_fn("=" * 58)

    for f, label in [(input_csv, "CSV"), (dem_tif, "DEM")]:
        if not os.path.exists(f):
            raise FileNotFoundError(label + " introuvable : " + f)

    try:
        from osgeo import gdal, osr  # noqa
    except ImportError:
        raise RuntimeError("GDAL manquant. Lancer depuis QGIS.")

    # ── Chargement CSV vertices ───────────────────────────────────────────────
    pts = []
    with open(input_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                pts.append({
                    "lat":  float(row["latitude"]),
                    "lon":  float(row["longitude"]),
                    "name": row.get("name", ""),
                })
            except (ValueError, KeyError):
                pass
    log_fn("  CSV       : " + os.path.basename(input_csv)
           + "  (" + str(len(pts)) + " vertices)")
    progress_fn(5)

    # ── Ouverture DEM ────────────────────────────────────────────────────────
    size_mo = os.path.getsize(dem_tif) / (1024**2)
    log_fn("  DEM       : " + os.path.basename(dem_tif)
           + "  (" + str(round(size_mo)) + " Mo)")
    ds, gt, band, nd, srs_wkt = _open_dem(dem_tif)
    transform = _make_transformer(srs_wkt)

    from osgeo import osr as _osr
    _s = _osr.SpatialReference(); _s.ImportFromWkt(srs_wkt)
    epsg = _s.GetAttrValue("AUTHORITY", 1) or "?"
    px_deg = abs(gt[1])
    px_m   = px_deg * 111320
    log_fn("  DEM CRS   : EPSG:" + str(epsg)
           + "  |  Resolution : " + str(round(px_m)) + " m/pixel")
    log_fn("  Methode   : " + methode)
    progress_fn(10)

    sample_fn = _sample_bilinear if methode == "bilinear" else _sample_nearest

    # ── Calibration GPS (optionnelle) ─────────────────────────────────────────
    use_gps    = False
    gps_coords = []
    gps_residu = []

    if gps_csv and os.path.exists(gps_csv):
        log_fn("\n  Calibration GPS : " + os.path.basename(gps_csv))
        gps_pts = _load_gps_points(gps_csv, gps_epsg, log_fn)
        log_fn("  " + str(len(gps_pts)) + " points GPS charges")

        if gps_pts:
            try:
                from pyproj import Transformer as _PT
                tr_to_utm = _PT.from_crs("EPSG:4326",
                                         "EPSG:" + str(gps_epsg), always_xy=True)
                gps_coords, gps_alts = _build_gps_index(gps_pts, gps_epsg, log_fn)

                # Calculer résidu DEM-GPS à chaque point GPS
                n_res = 0
                for i, gp in enumerate(gps_pts):
                    x_dem, y_dem = transform(gp["lon"], gp["lat"])
                    alt_dem_gps  = sample_fn(x_dem, y_dem, ds, gt, band, nd)
                    if alt_dem_gps is not None:
                        gps_residu.append(alt_dem_gps - gp["alt"])
                        n_res += 1
                    else:
                        gps_residu.append(0.0)

                if n_res > 0:
                    mean_r = sum(gps_residu) / len(gps_residu)
                    rmse_r = math.sqrt(sum(r**2 for r in gps_residu) / len(gps_residu))
                    log_fn("  Residus GPS-DEM : biais=" + str(round(mean_r,2))
                           + "m  RMSE=" + str(round(rmse_r,2)) + "m")
                    log_fn("  Methode calibration : " + methode_cal.upper())
                    if methode_cal == "idw":
                        log_fn("  Rayon IDW       : " + str(radius_gps_m) + " m")
                    elif methode_cal == "biais":
                        _biais_global = _calibration_biais(gps_residu)
                        log_fn("  Biais global    : " + str(round(_biais_global, 3)) + " m")
                    # Pour regression le plan est recalcule vertex par vertex
                    use_gps = True
                else:
                    log_fn("  [!] Aucun residu GPS calcule — calibration desactivee")
            except ImportError:
                log_fn("  [!] pyproj manquant — calibration GPS desactivee")
    else:
        log_fn("  Calibration GPS : non fournie (DEM brut utilise)")
    progress_fn(15)

    # Préparer transformer UTM pour GPS IDW / Regression
    _biais_global = 0.0
    if use_gps:
        try:
            from pyproj import Transformer as _PT2
            _tr_utm = _PT2.from_crs("EPSG:4326", "EPSG:" + str(gps_epsg), always_xy=True)
        except Exception:
            use_gps = False

    # ── Extraction principale ─────────────────────────────────────────────────
    results = []
    n_ok = n_nd = 0
    t0     = time.time()
    REPORT = max(1, len(pts) // 100)

    for i, pt in enumerate(pts):
        if stop_fn and stop_fn():
            ds = None
            log_fn("  Annule.")
            return

        x_dem, y_dem = transform(pt["lon"], pt["lat"])
        alt_dem = sample_fn(x_dem, y_dem, ds, gt, band, nd)

        if alt_dem is None:
            results.append({
                "alt_dem": None, "alt_corr": None,
                "pente": None, "qualite": "NODATA",
                "incert": 0.0, "methode": "NODATA",
                "n_gps": 0,
            })
            n_nd += 1
        else:
            # Pente locale
            slope = _compute_slope(x_dem, y_dem, ds, gt, band, nd, px_m=px_m)
            qualite, incert = _classify_qualite(slope)

            # Calibration GPS
            correction = 0.0
            n_gps_used = 0
            methode_pt = "DEM_BRUT"

            if use_gps:
                vx_utm, vy_utm = _tr_utm.transform(pt["lon"], pt["lat"])

                if methode_cal == "idw":
                    corr, n_gps_used = _idw_residus(
                        vx_utm, vy_utm, gps_coords, gps_residu,
                        radius_gps_m, _K_GPS)
                elif methode_cal == "regression":
                    corr, n_gps_used = _calibration_regression(
                        vx_utm, vy_utm, gps_coords, gps_residu)
                else:  # biais
                    corr, n_gps_used = _biais_global, len(gps_coords)

                if corr is not None:
                    correction = corr
                    methode_pt = "DEM_GPS_" + methode_cal.upper()
                    if n_gps_used >= 3:
                        qualite = qualite + "_GPS"
                        incert  = max(3.0, incert * 0.4)

            alt_corr = round(alt_dem - correction, 3)

            results.append({
                "alt_dem": round(alt_dem, 3),
                "alt_corr": alt_corr,
                "pente": round(slope, 2) if slope is not None else None,
                "qualite": qualite,
                "incert": round(incert, 1),
                "methode": methode_pt,
                "n_gps": n_gps_used,
            })
            n_ok += 1

        if (i+1) % REPORT == 0 or (i+1) == len(pts):
            pct = (i+1) / len(pts)
            progress_fn(15 + int(pct * 75))

    ds = None
    elapsed = time.time() - t0

    # ── Statistiques ─────────────────────────────────────────────────────────
    valid = [r for r in results if r["alt_dem"] is not None]
    log_fn("\n  Duree     : " + str(round(elapsed, 1)) + " s")
    log_fn("  OK/NODATA : " + str(n_ok) + " / " + str(n_nd))
    if valid:
        alts = [r["alt_dem"] for r in valid]
        log_fn("  Alt DEM   : " + str(round(min(alts),1)) + " -> "
               + str(round(max(alts),1)) + " m")
        if max(alts) - min(alts) < 0.1:
            log_fn("  [!] Toutes les altitudes identiques — verifier dem_tif")

    log_fn("\n  Classification par pente locale :")
    for tag in ("FIABLE","ACCEPTABLE","INCERTAINE","NON_FIABLE","NODATA"):
        cnt = sum(1 for r in results
                  if r["qualite"] == tag or r["qualite"].startswith(tag))
        if cnt:
            log_fn("    " + tag.ljust(12)
                   + " : " + str(cnt) + " pts"
                   + ("  (incert. ±" + str(_INCERT.get(tag,"?")) + "m)" if tag in _INCERT else ""))

    if use_gps:
        n_cal = sum(1 for r in results if r["n_gps"] > 0)
        log_fn("  GPS calibres  : " + str(n_cal) + " / " + str(len(results))
               + " vertices (rayon " + str(radius_gps_m) + "m)")

    progress_fn(93)

    # ── Écriture CSV ─────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "latitude", "longitude", "name",
            "altitude_dem_m", "altitude_corrigee_m",
            "pente_deg", "qualite", "incertitude_m",
            "methode", "n_gps_voisins"
        ])
        for pt, r in zip(pts, results):
            w.writerow([
                str(round(pt["lat"], 8)),
                str(round(pt["lon"], 8)),
                pt["name"],
                str(r["alt_dem"])  if r["alt_dem"]  is not None else "",
                str(r["alt_corr"]) if r["alt_corr"] is not None else "",
                str(r["pente"])    if r["pente"]     is not None else "",
                r["qualite"],
                str(r["incert"]),
                r["methode"],
                str(r["n_gps"]),
            ])

    out_ko = os.path.getsize(output_csv) / 1024
    progress_fn(100)
    log_fn("\n  [OK] " + output_csv + " (" + str(round(out_ko)) + " Ko)")
    log_fn("  Colonnes cles pour WaterGEMS/EPANET :")
    log_fn("    altitude_corrigee_m  <- altitude a importer")
    log_fn("    qualite              <- filtrer sur FIABLE/ACCEPTABLE")
    log_fn("    incertitude_m        <- tolerance hydraulique (+/-3m = 0.3 bar)")
