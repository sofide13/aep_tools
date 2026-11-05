"""
Script 00 — Telechargement DEM multi-sources (AEP Tools V2.0)

SOURCES SUPPORTEES :
  1. Copernicus GLO-30  (AWS S3, gratuit sans cle)
  2. SRTM GL1           (AWS S3, gratuit sans cle)
  3. NASADEM            (OpenTopography API key requis)
  4. AW3D30             (OpenTopography API key requis, meilleur relief)
  5. CopDEM-GLO-10      (CDSE login requis, 10m = meilleure precision)
"""

import os, math, json, time, urllib.request, urllib.error, urllib.parse

# ─── Catalogues sources gratuites (sans authentification) ────────────────────

def _tile_copernicus(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = ("Copernicus_DSM_COG_10_" + ns + str(abs(lat)).zfill(2)
            + "_00_" + ew + str(abs(lon)).zfill(3) + "_00_DEM")
    url = "https://copernicus-dem-30m.s3.amazonaws.com/" + name + "/" + name + ".tif"
    return name, url

def _tile_srtm_aws(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = ns + str(abs(lat)).zfill(2) + ew + str(abs(lon)).zfill(3)
    lat_prefix = ns + str(abs(lat)).zfill(2)
    url = ("https://s3.amazonaws.com/elevation-tiles-prod/skadi/"
           + lat_prefix + "/" + name + ".hgt.gz")
    return name, url

def _tile_copdem10_cdse(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = ("Copernicus_DSM_COG_03_" + ns + str(abs(lat)).zfill(2)
            + "_00_" + ew + str(abs(lon)).zfill(3) + "_00_DEM")
    return name

def _cdse_list_stac_collections(token, base, log_fn):
    try:
        req = urllib.request.Request(
            base + "/collections",
            headers={"Authorization": "Bearer " + token,
                     "User-Agent": "AEP-Tools/2.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        cols = [c.get("id","") for c in data.get("collections", [])]
        log_fn("  Collections STAC : " + ", ".join(cols[:10]))
        return cols
    except Exception as e:
        log_fn("  [collections] " + str(e)[:60])
        return []

def _cdse_search_copdem_stac(token, lat_min, lat_max, lon_min, lon_max, log_fn):
    bbox_str = (str(round(lon_min,4)) + "," + str(round(lat_min,4)) + ","
                + str(round(lon_max,4)) + "," + str(round(lat_max,4)))
    STAC_BASES = [
        "https://catalogue.dataspace.copernicus.eu/stac",
        "https://stac.dataspace.copernicus.eu/v1",
    ]
    COLLECTIONS = ["COP-DEM", "GLOBAL-MOSAICS", "cop-dem", "copernicus-dem"]

    for base in STAC_BASES:
        cols = _cdse_list_stac_collections(token, base, log_fn)
        dem_cols = [c for c in cols if "DEM" in c.upper()]
        search_order = (dem_cols + [c for c in COLLECTIONS if c not in dem_cols]) if dem_cols else COLLECTIONS
        if not dem_cols:
            continue

        for collection in search_order:
            url = base + "/collections/" + collection + "/items?bbox=" + bbox_str + "&limit=100"
            try:
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": "Bearer " + token,
                             "User-Agent": "AEP-Tools/2.0",
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read())
                features = data.get("features", [])
                if features:
                    log_fn("  STAC [" + collection + "] : " + str(len(features)) + " tuile(s)")
                    return features, "stac"
            except urllib.error.HTTPError as e:
                pass
            except Exception as e:
                pass
    return [], "none"

def _cdse_search_copdem(token, lat_min, lat_max, lon_min, lon_max, log_fn):
    wkt = ("POLYGON((" +
           str(lon_min) + " " + str(lat_min) + "," +
           str(lon_max) + " " + str(lat_min) + "," +
           str(lon_max) + " " + str(lat_max) + "," +
           str(lon_min) + " " + str(lat_max) + "," +
           str(lon_min) + " " + str(lat_min) + "))")

    odata_filter = (
        "Collection/Name eq 'COP-DEM'"
        " and contains(Name,'COG')"
        " and OData.CSC.Intersects(area=geography'SRID=4326;" + wkt + "')"
    )
    base   = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
    params = urllib.parse.urlencode({
        "$filter":  odata_filter,
        "$top":     "100",
        "$orderby": "ContentDate/Start desc",
    })
    url = base + "?" + params
    try:
        req = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer " + token,
                     "User-Agent": "AEP-Tools/2.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        products = data.get("value", [])
        cog = [p for p in products if "COG" in p.get("Name", "")]
        log_fn("  OData COG : " + str(len(cog)) + "/" + str(len(products)) + " produit(s)")
        return cog, "odata"
    except Exception as e:
        log_fn("  [ERR OData] " + str(e)[:100])
        return [], "none"

# ─── Download helpers (AVEC CACHE INTELLIGENT) ───────────────────────────────

def _download(url, dest, log_fn, headers=None, timeout=120):
    """Telecharge url -> dest, retourne True si succes."""
    # 1. Vérifie si on a déjà essayé de télécharger cette tuile et qu'elle n'existe pas (404)
    if os.path.exists(dest + ".404"):
        return False
        
    # 2. Vérifie si le fichier a déjà été téléchargé avec succès
    if os.path.exists(dest) and os.path.getsize(dest) > 5000:
        return True
        
    h = {"User-Agent": "AEP-Tools/2.0"}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            total = int(r.headers.get("Content-Length", 0))
            done  = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total and done % (3*1024*1024) < 65536:
                        log_fn("    " + str(round(done/total*100)) + "%  "
                               + str(round(done/1e6,1)) + "/" + str(round(total/1e6,1)) + " Mo")
        if os.path.getsize(dest) > 5000:
            return True
        os.remove(dest)
        return False
    except urllib.error.HTTPError as e:
        log_fn("    [HTTP " + str(e.code) + "] " + url[-60:])
        if os.path.exists(dest): os.remove(dest)
        # 3. Si on rencontre une erreur 404, on crée le marqueur pour ne plus réessayer
        if e.code == 404:
            with open(dest + ".404", "w") as f404:
                f404.write("404")
        return False
    except Exception as e:
        log_fn("    [ERR] " + str(e)[:80])
        if os.path.exists(dest): os.remove(dest)
        return False


def _gz_to_tif(gz_path, tif_path, log_fn):
    """Convertit HGT.GZ en GeoTIFF via GDAL /vsigzip/."""
    try:
        from osgeo import gdal
        ds = gdal.Open("/vsigzip/" + gz_path)
        if ds is None:
            raise RuntimeError("GDAL ne peut pas lire " + gz_path)
        gdal.Translate(tif_path, ds, format="GTiff",
                       creationOptions=["COMPRESS=LZW"])
        ds = None
        return True
    except Exception as e:
        log_fn("    [ERR gz->tif] " + str(e)[:60])
        return False

def _merge_tifs(tile_paths, output_tif, bbox, log_fn):
    """Fusionne plusieurs GeoTIFF en un seul, decoupage sur bbox."""
    from osgeo import gdal
    gdal.UseExceptions()

    if not output_tif:
        raise RuntimeError("Chemin DEM de sortie non renseigne (champ 'DEM sortie' vide).")
    if not tile_paths:
        raise RuntimeError("Aucune tuile a fusionner.")

    out_dir = os.path.dirname(output_tif)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    valid = [t for t in tile_paths if os.path.exists(t) and os.path.getsize(t) > 1000]
    if not valid:
        raise RuntimeError("Aucune tuile valide trouvee (toutes absentes ou vides).")

    lat_min, lat_max, lon_min, lon_max = bbox
    log_fn("  Fusion " + str(len(valid)) + " tuile(s) -> " + os.path.basename(output_tif))

    try:
        opts = gdal.WarpOptions(
            format="GTiff",
            outputBounds=(lon_min, lat_min, lon_max, lat_max),
            dstSRS="EPSG:4326",
            xRes=0.0002777, yRes=0.0002777,
            resampleAlg="bilinear",
            creationOptions=["COMPRESS=LZW", "TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256"],
            multithread=True,
        )
        ds = gdal.Warp(output_tif, valid, options=opts)
        if ds is None:
            log_fn("  [!] Warp avec bbox echoue, nouvel essai sans decoupage...")
            opts2 = gdal.WarpOptions(
                format="GTiff",
                dstSRS="EPSG:4326",
                resampleAlg="bilinear",
                creationOptions=["COMPRESS=LZW"],
            )
            ds = gdal.Warp(output_tif, valid, options=opts2)
        if ds is None:
            raise RuntimeError("GDAL Warp a retourne None.")
        ds = None
    except Exception as e:
        log_fn("  [!] Warp echoue (" + str(e)[:60] + "), copie tuile unique...")
        import shutil
        shutil.copy2(valid[0], output_tif)

    size = os.path.getsize(output_tif) / 1e6
    log_fn("  [OK] " + str(round(size, 1)) + " Mo -> " + output_tif)


# ─── Sources avec API key ─────────────────────────────────────────────────────

def _download_opentopo(demtype, bbox, output_tif, api_key, log_fn, progress_fn):
    lat_min, lat_max, lon_min, lon_max = bbox
    params = urllib.parse.urlencode({
        "demtype":      demtype,
        "south":        lat_min,
        "north":        lat_max,
        "west":         lon_min,
        "east":         lon_max,
        "outputFormat": "GTiff",
        "API_Key":      api_key,
    })
    url = "https://portal.opentopography.org/API/globaldem?" + params
    log_fn("  API OpenTopography : " + demtype)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AEP-Tools/2.0"})
        with urllib.request.urlopen(req, timeout=300) as r:
            total = int(r.headers.get("Content-Length", 0))
            done  = 0
            with open(output_tif, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total and done % (5*1024*1024) < 65536:
                        pct = done/total*100
                        progress_fn(20 + int(pct * 60))
                        log_fn("    " + str(round(pct)) + "%  "
                               + str(round(done/1e6,1)) + " Mo")
        size = os.path.getsize(output_tif)
        if size < 10_000:
            with open(output_tif) as ef:
                msg = ef.read(500)
            os.remove(output_tif)
            raise RuntimeError("API erreur : " + msg)
        log_fn("  [OK] " + str(round(size/1e6,1)) + " Mo -> " + os.path.basename(output_tif))
        return True
    except Exception as e:
        log_fn("  [ERREUR OpenTopography] " + str(e)[:120])
        if os.path.exists(output_tif): os.remove(output_tif)
        return False

def _get_cdse_token(username, password, log_fn):
    url  = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
            "/protocol/openid-connect/token")
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "username":   username,
        "password":   password,
        "client_id":  "cdse-public",
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
        return tok["access_token"]
    except Exception as e:
        log_fn("  [ERREUR CDSE auth] " + str(e)[:100])
        return None

def _download_copdem10(bbox, output_dir, output_tif, username, password, log_fn, progress_fn):
    lat_min, lat_max, lon_min, lon_max = bbox
    DOWNLOAD_BASE = "https://download.dataspace.copernicus.eu"

    log_fn("  Source : CopDEM GLO-10 via CDSE")
    token = _get_cdse_token(username, password, log_fn)
    if not token: return False

    features, src = _cdse_search_copdem_stac(token, lat_min, lat_max, lon_min, lon_max, log_fn)
    if not features:
        features, src = _cdse_search_copdem(token, lat_min, lat_max, lon_min, lon_max, log_fn)

    if not features:
        log_fn("  [3/3] Fallback Copernicus GLO-30 (AWS gratuit)...")
        tmp_dir = os.path.join(output_dir, "_cop30_fb")
        os.makedirs(tmp_dir, exist_ok=True)
        ok_paths = []
        for lat in range(int(math.floor(lat_min)), int(math.ceil(lat_max))):
            for lon in range(int(math.floor(lon_min)), int(math.ceil(lon_max))):
                name, url = _tile_copernicus(lat, lon)
                dest = os.path.join(tmp_dir, name + ".tif")
                if _download(url, dest, log_fn):
                    ok_paths.append(dest)
        if not ok_paths: return False
        _merge_tifs(ok_paths, output_tif, bbox, log_fn)
        return True

    def name_of(item):
        if src == "stac": return item.get("id", "") + str(item.get("properties", {}))
        return item.get("Name", "")

    glo10 = [x for x in features if "COG_03" in name_of(x)]
    selected = glo10 if glo10 else features

    tmp_dir = os.path.join(output_dir, "_copdem10_tiles")
    os.makedirs(tmp_dir, exist_ok=True)
    ok_paths = []

    for i, item in enumerate(selected):
        if src == "stac":
            prod_id   = item.get("id", "")
            prod_name = prod_id or ("tile_" + str(i))
            assets = item.get("assets", {})
            dl_url = None
            for key in ("data", "dem", "visual"):
                if key in assets:
                    dl_url = assets[key].get("href", "")
                    break
            if not dl_url: dl_url = DOWNLOAD_BASE + "/odata/v1/Products('" + prod_id + "')/$value"
        else:
            prod_id   = item.get("Id", "")
            prod_name = item.get("Name", "prod_" + str(i))
            dl_url    = DOWNLOAD_BASE + "/odata/v1/Products('" + prod_id + "')/$value"

        if not prod_id: continue

        dest = os.path.join(tmp_dir, prod_name[:80] + ".zip")
        tif_dest = os.path.join(tmp_dir, prod_name[:80] + ".tif")
        
        # Cache local
        if os.path.exists(tif_dest) and os.path.getsize(tif_dest) > 5000:
            ok_paths.append(tif_dest)
            continue

        log_fn("  [dl " + str(i+1) + "/" + str(len(selected)) + "] " + prod_name[:55])
        headers = {"Authorization": "Bearer " + token, "User-Agent": "AEP-Tools/2.0"}
        if not _download(dl_url, dest, log_fn, headers=headers):
            continue

        try:
            import zipfile as _zf
            with _zf.ZipFile(dest, "r") as z:
                tifs = [n for n in z.namelist() if n.lower().endswith(".tif")]
                if not tifs:
                    os.replace(dest, tif_dest)
                    ok_paths.append(tif_dest)
                    continue
                z.extract(tifs[0], tmp_dir)
                extracted = os.path.join(tmp_dir, tifs[0])
                if os.path.abspath(extracted) != os.path.abspath(tif_dest):
                    os.replace(extracted, tif_dest)
            ok_paths.append(tif_dest)
        except Exception as e:
            from osgeo import gdal as _gd
            if _gd.Open(dest):
                os.replace(dest, tif_dest)
                ok_paths.append(tif_dest)

    if not ok_paths:
        return False

    _merge_tifs(ok_paths, output_tif, bbox, log_fn)
    return True


# ─── Fonction principale (appelee par le worker QGIS) ────────────────────────

def run(params, log_fn, progress_fn, stop_fn=None):
    output_dir  = params["output_dir"]
    output_dem  = params["output_dem"]
    dem_source  = params.get("dem_source", "copernicus")
    mode_bbox   = params.get("mode_bbox", "preconfigure")
    bbox_margin = float(params.get("bbox_margin", 0.1))
    api_key     = params.get("opentopo_key", "").strip()
    cdse_user   = params.get("cdse_user", "").strip()
    cdse_pass   = params.get("cdse_pass", "").strip()

    try:
        from osgeo import gdal  # noqa
    except ImportError:
        raise RuntimeError("GDAL manquant. Lancer depuis QGIS.")

    log_fn("=" * 58)
    log_fn("  Etape 0 -- Telechargement DEM  [AEP Tools V2.0]")
    log_fn("  Source : " + dem_source.upper())
    log_fn("=" * 58)

    if mode_bbox == "preconfigure":
        lat_min, lat_max, lon_min, lon_max = 34.7, 35.9, -3.1, 0.1
    elif mode_bbox == "auto":
        import csv as _csv
        csv_path = params.get("input_csv", "")
        if not csv_path or not os.path.exists(csv_path):
            raise FileNotFoundError("input_csv requis pour mode auto")
        lats, lons = [], []
        with open(csv_path, newline="", encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                try:
                    lats.append(float(r["latitude"]))
                    lons.append(float(r["longitude"]))
                except (ValueError, KeyError):
                    pass
        if not lats:
            raise RuntimeError("Aucun point dans le CSV")
        lat_min = min(lats)-bbox_margin; lat_max = max(lats)+bbox_margin
        lon_min = min(lons)-bbox_margin; lon_max = max(lons)+bbox_margin
    else:
        lat_min = float(params["lat_min"]); lat_max = float(params["lat_max"])
        lon_min = float(params["lon_min"]); lon_max = float(params["lon_max"])

    log_fn("  Zone : lat " + str(round(lat_min,3)) + " -> " + str(round(lat_max,3))
           + "  lon " + str(round(lon_min,3)) + " -> " + str(round(lon_max,3)))
    log_fn("  Etendue : ~" + str(round((lat_max-lat_min)*111)) + " km x "
           + str(round((lon_max-lon_min)*111)) + " km")
    progress_fn(10)

    os.makedirs(output_dir, exist_ok=True)
    bbox = (lat_min, lat_max, lon_min, lon_max)

    if dem_source == "copdem10":
        ok = _download_copdem10(bbox, output_dir, output_dem, cdse_user, cdse_pass, log_fn, progress_fn)
        if ok:
            progress_fn(100)
            log_fn("\n  [OK] DEM 10m pret : " + output_dem)
        return

    OPENTOPO_SOURCES = {"nasadem": "NASADEM", "aw3d30": "AW3D30", "copdem30_ot": "COP30"}
    if dem_source in OPENTOPO_SOURCES:
        ok = _download_opentopo(OPENTOPO_SOURCES[dem_source], bbox, output_dem, api_key, log_fn, progress_fn)
        if ok:
            progress_fn(100)
            log_fn("\n  [OK] DEM pret : " + output_dem)
        return

    tiles = []
    for lat in range(int(math.floor(lat_min)), int(math.ceil(lat_max))):
        for lon in range(int(math.floor(lon_min)), int(math.ceil(lon_max))):
            if dem_source == "copernicus":
                name, url = _tile_copernicus(lat, lon)
                tiles.append({"name": name, "url": url, "fmt": "tif"})
            else:
                name, url = _tile_srtm_aws(lat, lon)
                tiles.append({"name": name, "url": url, "fmt": "hgt.gz"})

    log_fn("  " + str(len(tiles)) + " tuile(s) a telecharger")
    tmp_dir = os.path.join(output_dir, "_tiles_" + dem_source)
    os.makedirs(tmp_dir, exist_ok=True)

    ok_paths = []
    for i, tile in enumerate(tiles):
        if stop_fn and stop_fn():
            log_fn("  Annule.")
            return
        progress_fn(10 + int(i/max(len(tiles),1)*65))
        
        dest_final = os.path.join(tmp_dir, tile["name"] + ".tif")
        
        # --- VERIFICATION DU CACHE AVANT MEME DE LOGGER [dl] ---
        if os.path.exists(dest_final) and os.path.getsize(dest_final) > 5000:
            log_fn("  [cache] " + tile["name"])
            ok_paths.append(dest_final)
            continue
            
        if os.path.exists(dest_final + ".404"):
            log_fn("  [skip]  " + tile["name"] + " (deja marque 404)")
            continue

        log_fn("  [dl] " + tile["name"])

        if tile["fmt"] == "tif":
            if _download(tile["url"], dest_final, log_fn):
                ok_paths.append(dest_final)
        else:  # hgt.gz
            dest_gz = os.path.join(tmp_dir, tile["name"] + ".hgt.gz")
            if _download(tile["url"], dest_gz, log_fn):
                if _gz_to_tif(dest_gz, dest_final, log_fn):
                    ok_paths.append(dest_final)
                    os.remove(dest_gz)
                else:
                    os.remove(dest_gz)
            if os.path.exists(dest_gz + ".404"):
                os.rename(dest_gz + ".404", dest_final + ".404")

    if not ok_paths:
        raise RuntimeError("Aucune tuile telechargee. Verifier la connexion.")

    progress_fn(80)
    _merge_tifs(ok_paths, output_dem, bbox, log_fn)
    progress_fn(95)

    from osgeo import gdal as _g
    ds = _g.Open(output_dem)
    if ds:
        try:
            st = ds.GetRasterBand(1).ComputeStatistics(False)
            res_m = abs(ds.GetGeoTransform()[1]) * 111320
            log_fn("  Resolution : ~" + str(round(res_m)) + " m/pixel")
            log_fn("  Alt min/moy/max : " + str(round(st[0])) + " / "
                   + str(round(st[2])) + " / " + str(round(st[1])) + " m")
        except Exception:
            pass
        ds = None

    progress_fn(100)
    log_fn("\n  [OK] DEM pret : " + output_dem)