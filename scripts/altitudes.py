"""
Etape 3 -- Altitudes DEM, controles de coherence et altitude finale

METHODE
  1. Extraction bilineaire de l'altitude DEM a chaque vertex.
  2. Pente locale calculee depuis le DEM (taille du pixel corrigee par cos(latitude)).
  3. Classe de fiabilite selon la pente. Les incertitudes ci-dessous sont des valeurs
     INDICATIVES supposees, NON validees : l'etape 6 (Validation) mesure l'erreur reelle.
  4. Controles de coherence sans leve topo : batiments (optionnel), 2e DEM (optionnel),
     pics sur le profil de la conduite.
  5. Altitude finale vertex par vertex : DEM sans alerte ; terrain nu FABDEM (optionnel)
     ou interpolation le long de la conduite si alerte.
  6. Calibration GPS optionnelle (IDW des residus DEM - GPS). A utiliser avec des points
     proches du reseau : des points trop espaces produisent une surface sans valeur.

CLASSES (pente locale, constantes du code)
  < 3 deg    FIABLE       +/- 8 m
  3-15 deg   ACCEPTABLE   +/- 15 m
  15-30 deg  INCERTAINE   +/- 30 m
  > 30 deg   NON_FIABLE   +/- 50 m

ATTENTION : GLO-30 et SRTM sont des MNS (surface : toits, arbres). En zone baties la pente
seule ne detecte pas l'erreur ; +/-3 m n'est pas atteignable avec un DEM de 30 m.

ENTREE : CSV avec latitude/longitude (ou lat/lon, y/x) ; EPSG indique si different de 4326.

COLONNES DE SORTIE
  latitude, longitude, name, altitude_dem_m, altitude_corrigee_m, pente_deg, qualite,
  incertitude_m, methode, n_gps_voisins, ecart_dem2_m, bati, ecart_pic_m, alerte,
  qualite_finale, altitude_m (FINALE, lue par les etapes 4 et 5), source_altitude,
  dist_interp_m
"""

import csv, os, math, time, re
from bisect import bisect_left, bisect_right
from ._common import haversine_m


# ─── Constantes ───────────────────────────────────────────────────────────────

# Plages de pente → classification qualité
_PENTE_FIABLE      =  3.0   # deg
_PENTE_ACCEPTABLE  = 15.0
_PENTE_INCERTAINE  = 30.0

# Incertitudes par classe (DEM 30 m) : valeurs SUPPOSEES, non validees (voir l'etape 6)
_INCERT = {
    "FIABLE":      8.0,
    "ACCEPTABLE":  15.0,
    "INCERTAINE":  30.0,
    "NON_FIABLE":  50.0,
}

# Contrôles de cohérence (sans levé topo) — valeurs par défaut
_PIC_M          = 5.0    # écart max (m) d'un vertex par rapport à l'interpolation de ses voisins
_ECART_DEM2_M   = 5.0    # écart max (m) entre 2 DEM, après retrait du biais médian
_BATI_BUFFER_M  = 15.0   # distance (m) au bâti en dessous de laquelle le vertex est signalé
_NAME_RE        = re.compile(r"^fid(\d+)_l(\d+)_v(\d+)")

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

def _compute_slope(x, y, ds, gt, band, nd, px_m=30.0, px_y_m=None):
    """
    Calcule la pente locale (degrés) par la méthode Horn (3x3 kernel).
    px_m   : taille du pixel en mètres selon X (défaut 30 m).
    px_y_m : taille du pixel en mètres selon Y (défaut = px_m).
    En DEM géographique (degrés), px_m doit être multiplié par cos(latitude).
    Retourne None si données insuffisantes.
    """
    if px_y_m is None:
        px_y_m = px_m
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
        dzdy = (s - n) / (2 * px_y_m)
    else:
        dzdx = ((z[(1,-1)] + 2*z[(1,0)] + z[(1,1)])
               - (z[(-1,-1)] + 2*z[(-1,0)] + z[(-1,1)])) / (8 * px_m)
        dzdy = ((z[(-1,1)] + 2*z[(0,1)] + z[(1,1)])
               - (z[(-1,-1)] + 2*z[(0,-1)] + z[(1,-1)])) / (8 * px_y_m)

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


# ─── Contrôles de cohérence (sans levé topographique) ────────────────────────

def _line_groups(pts, results):
    """Regroupe les vertices par ligne (noms fidX_lY_vZ) et calcule les distances
    cumulées (m). Retourne une liste de (indices ordonnés, distances cumulées)."""
    groups = {}
    for idx, pt in enumerate(pts):
        m = _NAME_RE.match(str(pt["name"]).strip())
        if m and results[idx]["alt_dem"] is not None:
            groups.setdefault((int(m.group(1)), int(m.group(2))), []).append(
                (int(m.group(3)), idx))
    out = []
    for lst in groups.values():
        lst.sort()
        idxs = [i for _, i in lst]
        cum = [0.0]
        for a, b in zip(idxs, idxs[1:]):
            cum.append(cum[-1] + haversine_m(pts[a]["lon"], pts[a]["lat"],
                                              pts[b]["lon"], pts[b]["lat"]))
        out.append((idxs, cum))
    return out


def _detect_spikes(pts, results, d1_m, d2_m, pic_max):
    """Détecte les pics POSITIFS ÉTROITS du profil (bâtiment, arbre isolé).

    Pour chaque vertex, on compare son altitude à la corde reliant les vertices situés
    à ±d1 (puis ±d2 = 3×d1) le long de la ligne. Un bâtiment étroit donne le même écart
    aux deux échelles ; une colline (relief réel, large) donne un écart qui croît avec
    l'échelle. Un vertex est un pic si écart(d1) > pic_max et écart(d2) <= 2×écart(d1).
    Limites : pics plus larges que ~2×d1 non détectés ; extrémités de ligne non testées.
    """
    groups = _line_groups(pts, results)
    for idxs, cum in groups:
        n = len(idxs)
        if n < 3:
            continue
        z = [results[i]["alt_dem"] for i in idxs]

        def chord(k, D):
            j = bisect_right(cum, cum[k] - D) - 1
            m = bisect_left(cum, cum[k] + D)
            if j < 0 or m >= n:
                return None
            dj = cum[k] - cum[j]
            dm = cum[m] - cum[k]
            if dj + dm <= 0:
                return None
            return z[k] - (z[j] + (z[m] - z[j]) * dj / (dj + dm))

        R1 = [None] * n
        shape_ok = [False] * n          # même profil étroit aux deux échelles
        flagged = [False] * n
        for k in range(n):
            r1 = chord(k, d1_m)
            if r1 is None:
                continue
            R1[k] = r1
            r2 = chord(k, d2_m)
            shape_ok[k] = r2 is not None and r2 <= 2.0 * r1
            results[idxs[k]]["pic"] = round(r1, 2)
            if r1 > pic_max and shape_ok[k]:
                flagged[k] = True
        # Croissance de région : les « épaules » du pic (écart plus faible mais même forme)
        # sont ajoutées tant que l'écart reste > 30 % du seuil.
        seuil_bas = 0.3 * pic_max
        for k in range(n):
            if not flagged[k] or R1[k] is None or R1[k] <= pic_max:
                continue
            for step in (-1, 1):
                q = k + step
                while 0 <= q < n and R1[q] is not None and R1[q] > seuil_bas \
                        and shape_ok[q] and not flagged[q]:
                    flagged[q] = True
                    q += step
        for k in range(n):
            if flagged[k]:
                results[idxs[k]]["pic_flag"] = True
    return len(groups)


class _BatiIndex:
    """Index spatial en mémoire (grille) d'une couche de bâtiments (OGR)."""

    def __init__(self, path, pts, buffer_m, log_fn):
        from osgeo import ogr, osr
        ds = ogr.Open(path)
        if ds is None:
            raise RuntimeError("Couche bâtiments illisible : " + path)
        layer = ds.GetLayer(0)
        wgs = osr.SpatialReference()
        wgs.ImportFromEPSG(4326)
        wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        srs = layer.GetSpatialRef()
        if srs is None:
            log_fn("  [!] Bâtiments sans CRS : WGS84 supposé")
            srs = wgs
        else:
            srs = srs.Clone()
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self.geo = bool(srs.IsGeographic())
        self.tr = None if srs.IsSame(wgs) else osr.CoordinateTransformation(wgs, srs)
        self.buf = buffer_m / 111320.0 if self.geo else float(buffer_m)
        self.cell = self.buf * 4.0
        self.ogr = ogr
        self.grid = {}
        self.geoms = []

        xs, ys = [], []
        for p in pts:
            x, y = self._to_layer(p["lon"], p["lat"])
            xs.append(x); ys.append(y)
        if not xs:
            return
        layer.SetSpatialFilterRect(min(xs) - self.buf, min(ys) - self.buf,
                                   max(xs) + self.buf, max(ys) + self.buf)
        for feat in layer:
            g = feat.GetGeometryRef()
            if g is None or g.IsEmpty():
                continue
            g = g.Clone()
            minx, maxx, miny, maxy = g.GetEnvelope()
            c0 = int((minx - self.buf) // self.cell); c1 = int((maxx + self.buf) // self.cell)
            r0 = int((miny - self.buf) // self.cell); r1 = int((maxy + self.buf) // self.cell)
            if (c1 - c0 + 1) * (r1 - r0 + 1) > 2500:
                continue          # polygone anormalement grand : ignoré
            gi = len(self.geoms)
            self.geoms.append(g)
            for cc in range(c0, c1 + 1):
                for rr in range(r0, r1 + 1):
                    self.grid.setdefault((cc, rr), []).append(gi)
        log_fn("  Bâtiments : " + str(len(self.geoms)) + " polygones charges")

    def _to_layer(self, lon, lat):
        if self.tr is None:
            return lon, lat
        x, y, _ = self.tr.TransformPoint(lon, lat)
        return x, y

    def near(self, lon, lat):
        x, y = self._to_layer(lon, lat)
        cand = self.grid.get((int(x // self.cell), int(y // self.cell)))
        if not cand:
            return False
        pg = self.ogr.Geometry(self.ogr.wkbPoint)
        pg.AddPoint_2D(x, y)
        for gi in cand:
            if self.geoms[gi].Distance(pg) <= self.buf:
                return True
        return False


def _fuse_altitudes(pts, results, fab_vals):
    """Altitude finale vertex par vertex :
       - sans alerte                       -> GLO30 (altitude corrigée)
       - alerte + valeur terrain nu dispo  -> FABDEM
       - alerte PIC sans terrain nu        -> INTERP (interpolation le long de la ligne
                                              entre les deux vertices sains les plus proches)
       - autre alerte (BATI, ECART_DEM2)   -> GLO30_ALERTE (valeur gardée, à vérifier)
    """
    for r in results:
        r["alt_final"] = r["alt_corr"]
        r["source"] = "GLO30" if r["alt_corr"] is not None else "NODATA"
        r["dist_interp"] = None
    for i, r in enumerate(results):
        if r["alt_corr"] is None or not r["alerte"]:
            continue
        fv = fab_vals.get(i)
        if fv is not None:
            r["alt_final"] = round(fv, 3)
            r["source"] = "FABDEM"
        else:
            r["source"] = "GLO30_ALERTE"
    n_interp = 0
    for idxs, cum in _line_groups(pts, results):
        anchors = [k for k, i in enumerate(idxs)
                   if results[i]["alt_corr"] is not None and not results[i]["alerte"]]
        if len(anchors) < 2:
            continue
        a_cum = [cum[k] for k in anchors]
        for k, i in enumerate(idxs):
            r = results[i]
            if r["source"] != "GLO30_ALERTE" or not r.get("pic_flag"):
                continue
            q = bisect_left(a_cum, cum[k])
            if q <= 0 or q >= len(anchors):
                continue          # pas d'ancre de chaque côté : on garde GLO30_ALERTE
            ka, kb = anchors[q - 1], anchors[q]
            za = results[idxs[ka]]["alt_corr"]
            zb = results[idxs[kb]]["alt_corr"]
            da, db = cum[k] - cum[ka], cum[kb] - cum[k]
            if da + db <= 0:
                continue
            r["alt_final"] = round(za + (zb - za) * da / (da + db), 3)
            r["source"] = "INTERP"
            r["dist_interp"] = round(max(da, db), 1)
            n_interp += 1
    return n_interp


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return 0.0
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _apply_controls(results, ecart_max):
    """Construit alerte + qualite_finale à partir des contrôles disponibles."""
    for r in results:
        al = []
        if r.get("bati"):
            al.append("BATI")
        e = r.get("ecart2")
        if e is not None and abs(e) > ecart_max:
            al.append("ECART_DEM2")
        if r.get("pic_flag"):
            al.append("PIC")
        r["alerte"] = ";".join(al)
        base = r["qualite"]
        if al and (base.startswith("FIABLE") or base.startswith("ACCEPTABLE")):
            r["qualite_finale"] = "INCERTAINE"
        else:
            r["qualite_finale"] = base


# ─── Fonction principale ──────────────────────────────────────────────────────

_LAT_KEYS = ("latitude", "lat", "y")
_LON_KEYS = ("longitude", "lon", "lng", "x")


def _load_points(path, epsg_csv=4326):
    """Lit un CSV de points. Colonnes latitude/longitude (ou lat/lon, y/x), name/id facultatif.
    Si epsg_csv != 4326, les coordonnees sont reprojetees en WGS84."""
    from ._common import detect_sep
    sep = detect_sep(path)
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f, delimiter=sep)
        hdr = {(h or "").strip().lower(): h for h in (rd.fieldnames or [])}
        kl = next((hdr[k] for k in _LAT_KEYS if k in hdr), None)
        ko = next((hdr[k] for k in _LON_KEYS if k in hdr), None)
        kn = next((hdr[k] for k in ("name", "id", "nom") if k in hdr), None)
        if kl is None or ko is None:
            raise ValueError("Colonnes latitude/longitude (ou lat/lon, y/x) introuvables. "
                             "En-tetes lus : " + str(rd.fieldnames))
        raw = []
        for row in rd:
            try:
                raw.append((float(str(row[ko]).replace(",", ".")),
                            float(str(row[kl]).replace(",", ".")),
                            (row.get(kn) or "") if kn else ""))
            except (ValueError, TypeError, KeyError):
                pass
    tr = None
    if int(epsg_csv) != 4326:
        from osgeo import osr
        src = osr.SpatialReference()
        if src.ImportFromEPSG(int(epsg_csv)) != 0:
            raise ValueError("EPSG invalide : " + str(epsg_csv))
        dst = osr.SpatialReference()
        dst.ImportFromEPSG(4326)
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        tr = osr.CoordinateTransformation(src, dst)
    pts = []
    for x, y, nm in raw:
        if tr is not None:
            x, y, _ = tr.TransformPoint(x, y)
        pts.append({"lat": y, "lon": x, "name": nm})
    return pts


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
        dem2_tif     : str   — 2e DEM de contrôle (optionnel, ex. SRTM si GLO-30 en 1er)
        bati_path    : str   — couche de bâtiments (shp/gpkg/geojson, optionnel)
        bati_buffer_m: float — distance d'alerte au bâti (défaut 15 m)
        ecart_max_m  : float — écart inter-DEM déclenchant l'alerte (défaut 5 m)
        pic_max_m    : float — écart de pic déclenchant l'alerte (défaut 5 m)
        fabdem_tif   : str   — terrain nu FABDEM (optionnel, usage NON COMMERCIAL, dérivé de GLO-30)
    """
    input_csv    = params["input_csv"]
    output_csv   = params["output_csv"]
    dem_tif      = params["dem_tif"]
    methode      = params.get("methode", "bilinear")
    gps_csv      = params.get("gps_csv", "").strip()
    methode_cal  = params.get("methode_cal", "idw")   # idw | regression | biais
    gps_epsg     = int(params.get("gps_epsg", 32630))
    radius_gps_m = float(params.get("radius_gps_m", _RADIUS_GPS_M))
    dem2_tif     = str(params.get("dem2_tif", "") or "").strip()
    bati_path    = str(params.get("bati_path", "") or "").strip()
    bati_buf_m   = float(params.get("bati_buffer_m", _BATI_BUFFER_M))
    ecart_max    = float(params.get("ecart_max_m", _ECART_DEM2_M))
    pic_max      = float(params.get("pic_max_m", _PIC_M))
    fabdem_tif   = str(params.get("fabdem_tif", "") or "").strip()
    epsg_csv     = int(params.get("epsg_csv", 4326) or 4326)

    log_fn("=" * 58)
    log_fn("  Etape 3 -- Altitudes DEM")
    log_fn("=" * 58)

    for f, label in [(input_csv, "CSV"), (dem_tif, "DEM")]:
        if not os.path.exists(f):
            raise FileNotFoundError(label + " introuvable : " + f)

    try:
        from osgeo import gdal, osr  # noqa
    except ImportError:
        raise RuntimeError("GDAL manquant. Lancer depuis QGIS.")

    # ── Chargement CSV vertices (colonnes tolerantes, EPSG optionnel) ─────────
    pts = _load_points(input_csv, epsg_csv)
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
    is_geo = bool(_s.IsGeographic())
    if is_geo:
        px_x_base = abs(gt[1]) * 111320.0     # x ramené à la latitude du vertex plus bas
        px_y_m    = abs(gt[5]) * 111320.0
    else:
        px_x_base = abs(gt[1])
        px_y_m    = abs(gt[5])
    px_m = px_y_m
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
                _PT.from_crs("EPSG:4326", "EPSG:" + str(gps_epsg), always_xy=True)
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

    # ── Contrôles optionnels : 2e DEM et bâtiments ────────────────────────────
    ds2 = None
    if dem2_tif:
        if os.path.exists(dem2_tif):
            ds2, gt2, band2, nd2, srs2 = _open_dem(dem2_tif)
            transform2 = _make_transformer(srs2)
            log_fn("  DEM controle : " + os.path.basename(dem2_tif))
        else:
            log_fn("  [!] DEM de controle introuvable : " + dem2_tif)
    bati_idx = None
    if bati_path:
        if os.path.exists(bati_path):
            try:
                bati_idx = _BatiIndex(bati_path, pts, bati_buf_m, log_fn)
            except Exception as e:
                log_fn("  [!] Bâtiments ignorés : " + str(e)[:100])
        else:
            log_fn("  [!] Couche bâtiments introuvable : " + bati_path)

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
            px_x_m = (px_x_base * math.cos(math.radians(pt["lat"]))
                      if is_geo else px_x_base)
            slope = _compute_slope(x_dem, y_dem, ds, gt, band, nd,
                                   px_m=px_x_m, px_y_m=px_y_m)
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

    # ── Contrôles de cohérence ────────────────────────────────────────────────
    for r in results:
        r.update({"alt2": None, "ecart2": None, "bati": None, "pic": None})
    ecarts_bruts = []
    if ds2 is not None:
        for i, pt in enumerate(pts):
            if results[i]["alt_dem"] is None:
                continue
            x2, y2 = transform2(pt["lon"], pt["lat"])
            z2 = _sample_bilinear(x2, y2, ds2, gt2, band2, nd2)
            if z2 is not None:
                results[i]["alt2"] = z2
                ecarts_bruts.append(results[i]["alt_dem"] - z2)
        ds2 = None
        if ecarts_bruts:
            med = _median(ecarts_bruts)
            for r in results:
                if r["alt2"] is not None:
                    r["ecart2"] = round(r["alt_dem"] - r["alt2"] - med, 2)
            absd = sorted(abs(r["ecart2"]) for r in results if r["ecart2"] is not None)
            log_fn("\n  Controle inter-DEM : biais median = " + str(round(med, 2)) + " m"
                   + "  |  |ecart| median = " + str(round(_median(absd), 2)) + " m"
                   + "  |  P90 = " + str(round(absd[int(0.9 * (len(absd) - 1))], 2)) + " m")
            log_fn("  (le biais median est retire : il reflete surtout la difference de geoide)")
            p90_abs = absd[int(0.9 * (len(absd) - 1))]
            if abs(med) > 30.0 or p90_abs > 10.0 * ecart_max:
                log_fn("  [!] 2e DEM INCOHERENT avec le DEM principal (biais median "
                       + str(round(med, 1)) + " m, P90 " + str(round(p90_abs, 1)) + " m).")
                log_fn("      Un vrai 2e DEM (ex. SRTM) differe de quelques metres seulement.")
                log_fn("      Probable : surface interpolee (IDW), autre unite ou autre zone.")
                log_fn("      Controle inter-DEM IGNORE (aucune alerte ECART_DEM2 generee).")
                for r in results:
                    r["ecart2"] = None
    if bati_idx is not None:
        for i, pt in enumerate(pts):
            results[i]["bati"] = bool(bati_idx.near(pt["lon"], pt["lat"]))
    n_lines_pic = _detect_spikes(pts, results, 2.0 * px_m, 6.0 * px_m, pic_max)
    if n_lines_pic:
        log_fn("  Controle des pics : " + str(n_lines_pic) + " lignes analysees")
    _apply_controls(results, ecart_max)
    # ── Terrain nu FABDEM (optionnel) ─────────────────────────────────────────
    fab_vals = {}
    if fabdem_tif:
        if os.path.exists(fabdem_tif):
            dsf, gtf, bandf, ndf, srsf = _open_dem(fabdem_tif)
            trf = _make_transformer(srsf)
            diffs = []
            for i, pt in enumerate(pts):
                if results[i]["alt_dem"] is None:
                    continue
                xf_, yf_ = trf(pt["lon"], pt["lat"])
                zf = _sample_bilinear(xf_, yf_, dsf, gtf, bandf, ndf)
                if zf is None:
                    continue
                corr_i = results[i]["alt_dem"] - results[i]["alt_corr"]
                fab_vals[i] = zf - corr_i
                if not results[i]["alerte"]:
                    diffs.append(zf - results[i]["alt_dem"])
            dsf = None
            if diffs:
                mdiff = _median(diffs)
                log_fn("\n  FABDEM : ecart median FABDEM - GLO30 sur vertices sains = "
                       + str(round(mdiff, 2)) + " m")
                if abs(mdiff) > 1.5:
                    log_fn("  [!] Ecart median eleve : FABDEM derive de GLO-30, verifier que le DEM "
                           "principal est bien GLO-30 (referentiel vertical identique).")
            log_fn("  FABDEM : donnees NON COMMERCIALES (CC BY-NC-SA 4.0), citer la source.")
        else:
            log_fn("  [!] FABDEM introuvable : " + fabdem_tif)
    n_interp = _fuse_altitudes(pts, results, fab_vals)
    src_cnt = {}
    for r in results:
        src_cnt[r["source"]] = src_cnt.get(r["source"], 0) + 1
    log_fn("\n  Altitude finale par source : "
           + "  ".join(k + "=" + str(v) for k, v in sorted(src_cnt.items())))
    n_alert = sum(1 for r in results if r["alerte"])
    if n_alert:
        log_fn("\n  [!] " + str(n_alert) + " / " + str(len(results))
               + " vertices avec alerte (BATI / ECART_DEM2 / PIC) -> qualite_finale = INCERTAINE")
        for tag in ("BATI", "ECART_DEM2", "PIC"):
            c = sum(1 for r in results if tag in r["alerte"].split(";"))
            if c:
                log_fn("      " + tag.ljust(11) + ": " + str(c))
    if bati_idx is None and not ecarts_bruts:
        log_fn("\n  [i] Aucun controle de coherence actif : en zone batie, la pente seule"
               " ne detecte pas l'erreur d'un MNS. Fournir un 2e DEM et/ou une couche de batiments.")

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
            "methode", "n_gps_voisins",
            "ecart_dem2_m", "bati", "ecart_pic_m", "alerte", "qualite_finale",
            "altitude_m", "source_altitude", "dist_interp_m"
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
                str(r["ecart2"]) if r["ecart2"] is not None else "",
                "" if r["bati"] is None else ("OUI" if r["bati"] else "NON"),
                str(r["pic"]) if r["pic"] is not None else "",
                r["alerte"],
                r["qualite_finale"],
                str(r["alt_final"]) if r["alt_final"] is not None else "",
                r["source"],
                str(r["dist_interp"]) if r["dist_interp"] is not None else "",
            ])

    out_ko = os.path.getsize(output_csv) / 1024
    progress_fn(100)
    log_fn("\n  [OK] " + output_csv + " (" + str(round(out_ko)) + " Ko)")
    log_fn("  Colonnes cles pour WaterGEMS/EPANET :")
    log_fn("    altitude_m           <- altitude FINALE (lue par les etapes 4 et 5)")
    log_fn("    source_altitude      <- GLO30 / FABDEM / INTERP / GLO30_ALERTE")
    log_fn("    qualite_finale       <- filtrer sur FIABLE/ACCEPTABLE (tient compte des alertes)")
    log_fn("    alerte               <- verifier manuellement les vertices signales")
    log_fn("    incertitude_m        <- tolerance hydraulique (+/-3m = 0.3 bar)")
