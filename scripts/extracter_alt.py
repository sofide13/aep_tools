"""
Script 04 — Ajout attribut altitude à toutes les couches vecteur (adapté plugin)
"""
import csv, os, math


def load_alti_pts(csv_path, log_fn):
    alti_pts = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        sample = f.read(2048); f.seek(0)
        sep = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(f, delimiter=sep)
        header = [c.lower().strip() for c in next(reader)]
        try:
            ic = next(i for i, c in enumerate(header) if "lat" in c)
            jc = next(i for i, c in enumerate(header) if "lon" in c)
            kc = next(i for i, c in enumerate(header)
                      if c in ("altitude_m", "altitude", "ele", "elevation", "alt"))
        except StopIteration:
            raise ValueError("Colonnes latitude/longitude/altitude introuvables dans le CSV.")
        for row in reader:
            try:
                la, lo, al = float(row[ic]), float(row[jc]), float(row[kc])
                if al > -500:
                    alti_pts.append((lo, la, al))
            except (ValueError, IndexError):
                pass
    log_fn(f"  {len(alti_pts):,} altitudes chargées depuis CSV")
    return alti_pts


def get_alti_in_epsg(alti_pts, epsg):
    if epsg == 4326:
        return alti_pts
    from osgeo import osr, ogr
    src = osr.SpatialReference(); src.ImportFromEPSG(4326)
    dst = osr.SpatialReference(); dst.ImportFromEPSG(epsg)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    xf = osr.CoordinateTransformation(src, dst)
    result = []
    for lo, la, al in alti_pts:
        p = ogr.Geometry(ogr.wkbPoint)
        p.AddPoint(lo, la); p.Transform(xf)
        result.append((p.GetX(), p.GetY(), al))
    return result


def nearest_z(px, py, pts):
    best_d, best_z = float("inf"), None
    for cx, cy, cz in pts:
        d = math.hypot(px - cx, py - cy)
        if d < best_d:
            best_d, best_z = d, cz
    return best_z, best_d


def process_layer(lyr_path, alti_pts, epsg, geom_type, out_dir, log_fn):
    from osgeo import ogr
    out_path = os.path.join(out_dir, os.path.basename(lyr_path))
    if out_path == lyr_path:
        base, ext = os.path.splitext(out_path)
        out_path = base + "_alt" + ext

    alti_rep = get_alti_in_epsg(alti_pts, epsg)
    src_ds = ogr.Open(lyr_path)
    src_lyr = src_ds.GetLayer(0)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(out_path):
        drv.DeleteDataSource(out_path)
    dst_ds = drv.CreateDataSource(out_path)
    dst_lyr = dst_ds.CreateLayer("out", src_lyr.GetSpatialRef(), geom_type)

    for i in range(src_lyr.GetLayerDefn().GetFieldCount()):
        dst_lyr.CreateField(src_lyr.GetLayerDefn().GetFieldDefn(i))
    dst_lyr.CreateField(ogr.FieldDefn("altitude", ogr.OFTReal))
    dst_lyr.CreateField(ogr.FieldDefn("dist_m", ogr.OFTReal))
    defn = dst_lyr.GetLayerDefn()

    found = missing = 0
    for feat in src_lyr:
        g = feat.GetGeometryRef()
        if g is None:
            continue
        if g.GetGeometryType() in (ogr.wkbLineString, ogr.wkbLineString25D,
                                   ogr.wkbMultiLineString):
            env = g.GetEnvelope()
            px = (env[0] + env[1]) / 2
            py = (env[2] + env[3]) / 2
        else:
            px, py = g.GetX(), g.GetY()

        z, d = nearest_z(px, py, alti_rep)
        new_feat = ogr.Feature(defn)
        new_feat.SetGeometry(g.Clone())
        for i in range(src_lyr.GetLayerDefn().GetFieldCount()):
            new_feat.SetField(i, feat.GetField(i))
        if z is not None:
            new_feat.SetField("altitude", round(z, 2))
            new_feat.SetField("dist_m", round(d, 1))
            found += 1
        else:
            missing += 1
        dst_lyr.CreateFeature(new_feat)

    dst_ds = None; src_ds = None
    return out_path, found, missing


def run(params, log_fn, progress_fn, stop_fn=None):
    """
    params:
        csv_alt  : str
        out_dir  : str
    Utilise toutes les couches vecteur SHP du projet QGIS actif.
    """
    csv_alt = params["csv_alt"]
    out_dir = params["out_dir"]

    log_fn("═" * 55)
    log_fn("  Étape 4 — Ajout altitude aux couches")
    log_fn("═" * 55)

    if not os.path.exists(csv_alt):
        raise FileNotFoundError(f"CSV altitudes introuvable : {csv_alt}")

    os.makedirs(out_dir, exist_ok=True)
    progress_fn(5)

    try:
        from osgeo import ogr  # noqa
    except ImportError:
        raise RuntimeError("GDAL/OGR manquant. Lancer depuis QGIS.")

    alti_pts = load_alti_pts(csv_alt, log_fn)
    progress_fn(15)

    try:
        from qgis.core import QgsProject, QgsWkbTypes, QgsVectorLayer
        from osgeo import ogr as _ogr
        OGR_TYPES = {
            QgsWkbTypes.PointGeometry: _ogr.wkbPoint,
            QgsWkbTypes.LineGeometry: _ogr.wkbLineString,
            QgsWkbTypes.PolygonGeometry: _ogr.wkbPolygon,
        }
        layers = list(QgsProject.instance().mapLayers().values())
        shp_layers = []
        for layer in layers:
            if layer.type() != layer.VectorLayer:
                continue
            src_path = layer.dataProvider().dataSourceUri().split("|")[0]
            if not src_path.endswith(".shp") or not os.path.exists(src_path):
                continue
            auth = layer.crs().authid()
            try:
                epsg = int(auth.split(":")[1])
            except (IndexError, ValueError):
                log_fn(f"  ⚠  {layer.name()} — SCR inconnu ({auth}), ignoré")
                continue
            geom_type = OGR_TYPES.get(layer.geometryType(), _ogr.wkbUnknown)
            shp_layers.append((layer, src_path, epsg, geom_type))

        log_fn(f"\n  {len(shp_layers)} couche(s) SHP trouvée(s) dans le projet")
        processed = []
        for i, (layer, src_path, epsg, geom_type) in enumerate(shp_layers):
            if stop_fn and stop_fn():
                log_fn("  ⛔ Annulé.")
                return
            log_fn(f"\n  ▶  {layer.name()}  [EPSG:{epsg}]  {layer.featureCount()} entités")
            try:
                out_path, found, miss = process_layer(
                    src_path, alti_pts, epsg, geom_type, out_dir, log_fn)
                new_lyr = QgsVectorLayer(out_path, layer.name() + "_alt", "ogr")
                QgsProject.instance().addMapLayer(new_lyr)
                log_fn(f"     ✅ {found} ok, {miss} manquants → {os.path.basename(out_path)}")
                processed.append(layer.name())
            except Exception as e:
                log_fn(f"     ❌ Erreur : {e}")
            progress_fn(15 + int((i + 1) / max(len(shp_layers), 1) * 80))

        progress_fn(100)
        log_fn(f"\n  ✅ TERMINÉ — {len(processed)} couche(s) traitée(s)")
        log_fn(f"     Fichiers dans : {out_dir}")

    except ImportError:
        raise RuntimeError("Module QGIS non disponible. Lancer depuis QGIS.")
