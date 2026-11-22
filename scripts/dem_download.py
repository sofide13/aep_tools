"""
Etape 2 -- Telechargement du DEM (AEP Tools)

SOURCES (gratuites, sans cle ni inscription, via AWS) :
  1. Copernicus GLO-30 : Copernicus DEM GLO-30 (ESA / Airbus), radar TanDEM-X 2011-2015,
     1 seconde d'arc (~30 m), modele de SURFACE, altitudes au-dessus du geoide EGM2008.
  2. SRTM : NASA Shuttle Radar Topography Mission (fevrier 2000), radar bande C,
     1 seconde d'arc (~30 m), modele de SURFACE, altitudes au-dessus du geoide EGM96,
     fourni sous forme de tuiles .hgt via AWS terrain-tiles (compose base sur SRTM).

Ce sont des modeles numeriques de SURFACE : ils incluent toits et vegetation.
Zone : calculee depuis le CSV de l'etape 1 (auto) ou saisie a la main (manuel).
"""

import os, math, urllib.request, urllib.error

from ._net import https_urlopen

# ─── Catalogues sources gratuites (sans authentification) ────────────────────

SOURCES_INFO = {
    "copernicus": {
        "nom": "Copernicus DEM GLO-30",
        "detail": "ESA / Airbus, radar TanDEM-X (2011-2015), 30 m, modele de SURFACE, geoide EGM2008",
        "credit": "Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence "
                  "and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; "
                  "all rights reserved.",
    },
    "srtm": {
        "nom": "SRTM (NASA)",
        "detail": "NASA Shuttle Radar Topography Mission (fevrier 2000), 30 m, modele de SURFACE, "
                  "geoide EGM96, tuiles AWS terrain-tiles",
        "credit": "NASA SRTM, via AWS Terrain Tiles.",
    },
}

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
        with https_urlopen(req, timeout=timeout) as r:
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

def run(params, log_fn, progress_fn, stop_fn=None):
    output_dir  = params["output_dir"]
    output_dem  = params["output_dem"]
    dem_source  = params.get("dem_source", "copernicus")
    mode_bbox   = params.get("mode_bbox", "auto")
    bbox_margin = float(params.get("bbox_margin", 0.1))

    try:
        from osgeo import gdal  # noqa
    except ImportError:
        raise RuntimeError("GDAL manquant. Lancer depuis QGIS.")

    log_fn("=" * 58)
    log_fn("  Etape 2 -- Telechargement DEM")
    info = SOURCES_INFO.get(dem_source)
    if info:
        log_fn("  Source : " + info["nom"])
        log_fn("           " + info["detail"])
        log_fn("  Credit : " + info["credit"])
    else:
        log_fn("  Source : " + dem_source.upper())
    log_fn("  Rappel : modele de SURFACE (toits, arbres), pas un MNT. Utiliser les altitudes du DEM")
    log_fn("           en terrain ouvert ; en zone batie voir l'etape 6.")
    log_fn("=" * 58)

    if mode_bbox == "auto":
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

    if not (-90 <= lat_min < lat_max <= 90 and -180 <= lon_min < lon_max <= 180):
        raise ValueError("Zone invalide : lat_min < lat_max (-90..90) et lon_min < lon_max "
                         "(-180..180) requis.")
    n_tiles = (math.ceil(lat_max) - math.floor(lat_min)) * (math.ceil(lon_max) - math.floor(lon_min))
    if dem_source in ("copernicus", "srtm") and n_tiles > 100:
        raise ValueError("Zone trop grande : " + str(n_tiles) + " tuiles de 1 deg (maximum 100). "
                         "Reduire la zone.")

    log_fn("  Zone : lat " + str(round(lat_min,3)) + " -> " + str(round(lat_max,3))
           + "  lon " + str(round(lon_min,3)) + " -> " + str(round(lon_max,3)))
    log_fn("  Etendue : ~" + str(round((lat_max-lat_min)*111)) + " km x "
           + str(round((lon_max-lon_min)*111)) + " km")
    progress_fn(10)

    os.makedirs(output_dir, exist_ok=True)
    bbox = (lat_min, lat_max, lon_min, lon_max)

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
            log_fn("  Statistiques du MNT indisponibles")
        ds = None

    progress_fn(100)
    log_fn("\n  [OK] DEM pret : " + output_dem)