"""
Script 03 — Découpage AEP par dénivelé (adapté plugin)
"""
import csv, re, json, math, os, time
from collections import defaultdict

NAME_RE = re.compile(r"^fid(\d+)_l(\d+)_v(\d+)(_d)?$")


def parse_name(name):
    m = NAME_RE.match(str(name).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) is not None


def interp(a, b, t):
    return a + t * (b - a)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000.0
    f1, f2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(f1) * math.cos(f2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_csv_points(path):
    groups = defaultdict(list)
    n_skip = 0
    with open(path, newline="", encoding="utf-8") as f:
        for seq, row in enumerate(csv.DictReader(f)):
            parsed = parse_name(row.get("name", ""))
            if parsed is None:
                n_skip += 1
                continue
            fid, li, vi, is_d = parsed
            alt_str = row.get("altitude_m", "").strip()
            if alt_str == "":
                n_skip += 1
                continue
            try:
                groups[(fid, li)].append({
                    "seq": seq,
                    "lon": float(row["longitude"]),
                    "lat": float(row["latitude"]),
                    "alt": float(alt_str),
                    "vi": vi, "is_d": is_d,
                })
            except ValueError:
                n_skip += 1
    for key in groups:
        groups[key].sort(key=lambda x: x["seq"])
    return groups, n_skip


def load_geojson_attrs(path, log_fn):
    attrs = {}
    if not path or not os.path.exists(path):
        log_fn("  ⚠  GeoJSON source absent — attributs non récupérés.")
        return attrs
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for feat in data.get("features", []):
        p = feat.get("properties", {}) or {}
        fid = p.get("ogc_fid")
        if fid is not None:
            attrs[int(fid)] = p
    return attrs


def cut_line_by_denivele(pts, denivele_m):
    if len(pts) < 2:
        return [[dict(p) for p in pts]], []
    segments, cut_pts = [], []
    cur_seg = [pts[0]]
    ref_alt = pts[0]["alt"]
    for i in range(1, len(pts)):
        prev = cur_seg[-1]
        curr = pts[i]
        delta = curr["alt"] - ref_alt
        while abs(delta) >= denivele_m:
            target_alt = ref_alt + math.copysign(denivele_m, delta)
            alt_a, alt_b = prev["alt"], curr["alt"]
            t = 0.5 if abs(alt_b - alt_a) < 1e-9 else (target_alt - alt_a) / (alt_b - alt_a)
            t = max(0.0, min(1.0, t))
            cut = {"lon": interp(prev["lon"], curr["lon"], t),
                   "lat": interp(prev["lat"], curr["lat"], t),
                   "alt": target_alt}
            cur_seg.append(cut)
            segments.append(cur_seg)
            cut_pts.append(cut)
            cur_seg = [cut]
            ref_alt = target_alt
            prev = cut
            delta = curr["alt"] - ref_alt
        cur_seg.append(curr)
    if len(cur_seg) >= 2:
        segments.append(cur_seg)
    elif segments:
        segments[-1].extend(cur_seg[1:])
    return segments, cut_pts


def make_line_feature(seg_pts, fid, li, seg_idx, attrs):
    coords = [[p["lon"], p["lat"]] for p in seg_pts]
    alt_debut = round(seg_pts[0]["alt"], 2)
    alt_fin = round(seg_pts[-1]["alt"], 2)
    length_m = sum(haversine_m(seg_pts[j]["lon"], seg_pts[j]["lat"],
                               seg_pts[j+1]["lon"], seg_pts[j+1]["lat"])
                   for j in range(len(seg_pts) - 1))
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "fid": fid, "line_idx": li, "seg_id": seg_idx,
            "label": attrs.get("label"), "diametre": attrs.get("diametre"),
            "materiau": attrs.get("materiau"), "etat": attrs.get("etat"),
            "alt_debut_m": alt_debut, "alt_fin_m": alt_fin,
            "denivelé_m": round(abs(alt_fin - alt_debut), 2),
            "longueur_m": round(length_m, 2), "n_pts": len(seg_pts),
        }
    }


def make_point_feature(pt, fid, li, pt_idx, pt_type, attrs):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [pt["lon"], pt["lat"]]},
        "properties": {
            "fid": fid, "line_idx": li, "pt_id": pt_idx,
            "label": attrs.get("label"), "diametre": attrs.get("diametre"),
            "altitude_m": round(pt["alt"], 2), "type": pt_type,
        }
    }


def run(params, log_fn, progress_fn, stop_fn=None):
    """
    params:
        input_csv      : str
        input_geojson  : str  (optionnel)
        output_lines   : str
        output_points  : str
        denivele_m     : float
    """
    input_csv     = params["input_csv"]
    input_geojson = params.get("input_geojson", "")
    output_lines  = params["output_lines"]
    output_points = params["output_points"]
    denivele_m    = float(params.get("denivele_m", 10.0))

    log_fn("═" * 55)
    log_fn("  Étape 3 — Découpage par dénivelé")
    log_fn("═" * 55)

    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"CSV introuvable : {input_csv}")

    t0 = time.time()
    log_fn(f"  CSV  : {os.path.basename(input_csv)}")
    log_fn(f"  Seuil dénivelé : {denivele_m} m")
    progress_fn(5)

    groups, n_skip = load_csv_points(input_csv)
    n_lines = len(groups)
    n_total = sum(len(v) for v in groups.values())
    log_fn(f"  Lignes : {n_lines}  ({n_total:,} points)")
    if n_skip:
        log_fn(f"  Ignorés : {n_skip:,}")

    fid_attrs = load_geojson_attrs(input_geojson, log_fn)
    log_fn(f"  Attributs GeoJSON : {len(fid_attrs)} entités")
    progress_fn(15)

    line_features = []
    point_features = []
    n_cuts_total = 0
    sorted_keys = sorted(groups.keys())

    for i, (fid, li) in enumerate(sorted_keys):
        if stop_fn and stop_fn():
            log_fn("  ⛔ Annulé.")
            return
        pts = groups[(fid, li)]
        attr = fid_attrs.get(fid, {})
        segs, cuts = cut_line_by_denivele(pts, denivele_m)
        n_cuts_total += len(cuts)

        pt_idx = 0
        point_features.append(make_point_feature(pts[0], fid, li, pt_idx, "depart", attr))
        pt_idx += 1
        for seg_idx, seg_pts in enumerate(segs):
            line_features.append(make_line_feature(seg_pts, fid, li, seg_idx, attr))
        for cut in cuts:
            point_features.append(make_point_feature(cut, fid, li, pt_idx, "coupe", attr))
            pt_idx += 1
        point_features.append(make_point_feature(pts[-1], fid, li, pt_idx, "fin", attr))

        progress_fn(15 + int((i + 1) / max(len(sorted_keys), 1) * 70))

    n_segs = len(line_features)
    log_fn(f"\n  Coupes totales    : {n_cuts_total:,}")
    log_fn(f"  Segments créés    : {n_segs:,}")
    log_fn(f"  Points accrochage : {len(point_features):,}")
    progress_fn(88)

    # Écriture GeoJSON lignes
    os.makedirs(os.path.dirname(output_lines) or ".", exist_ok=True)
    with open(output_lines, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection",
                   "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                   "features": line_features}, f, ensure_ascii=False, separators=(",", ":"))

    # Écriture GeoJSON points
    os.makedirs(os.path.dirname(output_points) or ".", exist_ok=True)
    with open(output_points, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection",
                   "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                   "features": point_features}, f, ensure_ascii=False, separators=(",", ":"))

    elapsed = time.time() - t0
    progress_fn(100)
    log_fn(f"\n  Durée : {elapsed:.1f} s")
    log_fn(f"  ✅ Segments : {output_lines}")
    log_fn(f"     ({os.path.getsize(output_lines)/1024:.0f} Ko — {n_segs:,} segments)")
    log_fn(f"  ✅ Points   : {output_points}")
    log_fn(f"     ({os.path.getsize(output_points)/1024:.0f} Ko — {len(point_features):,} points)")
