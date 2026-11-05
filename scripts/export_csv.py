"""
Script 01 — Export AEP → CSV pour GPS Visualizer (adapté plugin)
"""
import csv, math, os


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    f1, f2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(f1) * math.cos(f2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def densify(lon1, lat1, lon2, lat2, step_m):
    d = haversine_m(lon1, lat1, lon2, lat2)
    if d <= step_m:
        return []
    n = max(1, int(d / step_m))
    return [(lon1 + i / n * (lon2 - lon1), lat1 + i / n * (lat2 - lat1))
            for i in range(1, n)]


def read_shp_qgis(path, log_fn):
    from qgis.core import (QgsVectorLayer, QgsCoordinateReferenceSystem,
                           QgsCoordinateTransform, QgsProject)
    lyr = QgsVectorLayer(path, "aep_tmp", "ogr")
    if not lyr.isValid():
        raise RuntimeError(f"Impossible d'ouvrir : {path}")
    src = lyr.crs()
    dst = QgsCoordinateReferenceSystem("EPSG:4326")
    xf = QgsCoordinateTransform(src, dst, QgsProject.instance()) if src != dst else None
    if xf:
        log_fn(f"  Reprojection : {src.authid()} → WGS84")
    fields = [f.name() for f in lyr.fields()]
    feats = []
    for feat in lyr.getFeatures():
        fid = feat["ogc_fid"] if "ogc_fid" in fields else feat.id()
        label = (feat["label"] if "label" in fields else "") or ""
        g = feat.geometry()
        if xf:
            g.transform(xf)
        lines = []
        if g.isMultipart():
            for part in g.asMultiPolyline():
                lines.append([[pt.x(), pt.y()] for pt in part])
        else:
            pl = g.asPolyline()
            if pl:
                lines.append([[pt.x(), pt.y()] for pt in pl])
        if lines:
            feats.append((fid, label, lines))
    log_fn(f"  {len(feats)} features lues")
    return feats


def read_shp_standalone(path, log_fn):
    try:
        import fiona
        out = []
        with fiona.open(path) as src:
            for feat in src:
                p = feat.get("properties", {})
                fid = p.get("ogc_fid", feat["id"])
                lbl = str(p.get("label", "") or "")
                gt = feat["geometry"]["type"]
                cc = feat["geometry"]["coordinates"]
                lines = ([list(l) for l in cc] if gt == "MultiLineString"
                         else ([list(cc)] if gt == "LineString" else []))
                if lines:
                    out.append((fid, lbl, lines))
        return out
    except ImportError:
        raise ImportError("Fiona manquant. Lancer depuis la console QGIS.")


def run(params, log_fn, progress_fn, stop_fn=None):
    """
    params:
        shp_path           : str
        output_csv         : str
        densify_interval_m : int
    """
    shp_path           = params["shp_path"]
    output_csv         = params["output_csv"]
    densify_interval_m = int(params.get("densify_interval_m", 5))

    log_fn("═" * 55)
    log_fn("  Étape 1 — Export AEP → CSV")
    log_fn("═" * 55)

    if not os.path.exists(shp_path):
        raise FileNotFoundError(f"Fichier introuvable : {shp_path}")

    log_fn(f"  Source : {os.path.basename(shp_path)}")
    log_fn(f"  Densification : 1 point / {densify_interval_m} m")
    progress_fn(5)

    # Lecture du shapefile
    try:
        from qgis.core import QgsVectorLayer  # noqa
        feats = read_shp_qgis(shp_path, log_fn)
    except ImportError:
        feats = read_shp_standalone(shp_path, log_fn)

    rows = []
    n_orig = 0
    total_feats = len(feats)

    for fi, (fid, label, lines) in enumerate(feats):
        if stop_fn and stop_fn():
            log_fn("  ⛔ Annulé.")
            return
        for li, line in enumerate(lines):
            if len(line) < 2:
                continue
            for vi, pt in enumerate(line):
                lon, lat = float(pt[0]), float(pt[1])
                rows.append((lat, lon, fid, li, vi, False))
                n_orig += 1
                if densify_interval_m > 0 and vi < len(line) - 1:
                    nx, ny = float(line[vi + 1][0]), float(line[vi + 1][1])
                    for (ix, iy) in densify(lon, lat, nx, ny, densify_interval_m):
                        rows.append((iy, ix, fid, li, vi, True))
        progress_fn(5 + int((fi + 1) / max(total_feats, 1) * 70))

    log_fn(f"  Vertices originaux  : {n_orig:,}")
    log_fn(f"  Après densification : {len(rows):,}")
    progress_fn(80)

    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["latitude", "longitude", "name"])
        for (lat, lon, fid, li, vi, d) in rows:
            w.writerow([f"{lat:.8f}", f"{lon:.8f}",
                        f"fid{fid}_l{li}_v{vi}{'_d' if d else ''}"])

    ko = os.path.getsize(output_csv) / 1024
    progress_fn(100)
    log_fn(f"\n  ✅ CSV créé ({ko:.0f} Ko) : {output_csv}")
    log_fn("\n  ℹ  ÉTAPE SUIVANTE : lancer Étape 2 — Altitudes")
