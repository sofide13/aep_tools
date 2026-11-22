"""
Etape 6 -- Cotes hydrauliques, coherence avec les leves et noeuds sensibles  (AEP Tools 2.3.0)

IDEE
  La charge piezometrique d'un reseau en charge ne depend PAS de l'altitude des noeuds
  (analyse a demande imposee) : ce sont les reservoirs, les conduites et les debits qui la
  fixent. L'altitude n'intervient que pour passer de la charge a la pression :
        P = charge - Z
  Deux consequences, qui font de cette etape un outil hydraulique plutot qu'un outil de MNT :
    1. Une pression statique mesuree donne la cote du sol :  Z = H_reservoir - P - h_manometre
       (nivellement hydraulique). C'est la meme grandeur qu'un leve : elle ne peut pas le
       contredire, sauf erreur de mesure ou de saisie, que l'etape detecte.
    2. Seuls comptent les noeuds ou l'incertitude de Z peut faire basculer la conclusion sur
       la pression. Ces noeuds sont designes et mesures en priorite.

  Le DEM (MNS de 30 m) reste la source des adductions en terrain ouvert. En zone baties il
  donne la hauteur des toits : il n'est plus presente comme une cote de calcul.

HIERARCHIE DES SOURCES
    1. LEVE (z_leve)                  sigma_leve
    2. HYDRAULIQUE (pression statique) sigma combine (manometre, H reservoir, perte residuelle)
    3. DEM hors zone baties            incertitude de l'etape 3, ou LE90 MESURE si assez de points
    4. DEM en zone baties              LE90 MESURE si assez de points, sinon valeur SUPPOSEE
  Levee et hydraulique ne sont combinees (moyenne 1/sigma^2) que si elles sont compatibles :
        |Z1 - Z2| <= k * sqrt(sigma1^2 + sigma2^2)
  Sinon : CONTRADICTION. Le leve est conserve, rien n'est moyenne, l'incertitude du noeud est
  elargie a l'ecart observe, et le point est a verifier.

ENTREES
  Noeuds        GeoJSON de l'etape 4 ou CSV de l'etape 3 (altitude_m).
  Modele EPANET .inp (facultatif) : lit [RESERVOIRS] (charge) et [TANKS] (radier + niveau d'eau).
                Surcharge manuelle "ID=cote;ID2=cote" pour les reservoirs dont la cote est connue
                autrement ou pas du tout dans le modele.
  Charges       CSV facultatif : latitude, longitude (EPSG au choix), charge_m. Exporter la charge
                (Head) de EPANET / QWater au moment le plus defavorable. Sans lui, on utilise
                H_source - perte de charge admise (valeurs saisies).
  Mesures       CSV : latitude, longitude + pression_m ou pression_bar et/ou z_leve ;
                facultatifs : id, hauteur_manometre_m, sigma_m, reservoir (ID de la source).
  Zones urbaines GeoJSON WGS84 facultatif (sinon : alerte BATI de l'etape 3).

SORTIES
  noeuds_hydro.geojson    tous les noeuds : cote finale, source, incertitude et son origine,
                          pressions, marge, p_verdict_faux, classe, rang de releve
  cotes_epanet.csv        id ; X ; Y ; altitude_m ; source ; incertitude : a reporter dans EPANET
  plan_releves.csv        noeuds a mesurer (colonnes X ; Y ; Z vide) : a mesurer, puis remettre
                          dans le fichier de Mesures
  coherence_cotes.csv     par mesure : levee / hydraulique / DEM, ecarts, statut
  bilan_erreur_dem.csv    biais, RMSE, LE90 du DEM contre les cotes de reference, par zone

LIMITES
  - Nivellement hydraulique : seulement aux points mesures, reseau reellement statique, charge
    du reservoir connue. Un debit residuel fausse la cote : saisir la perte residuelle.
  - Les charges lues sont celles de VOTRE simulation : leur exactitude est celle du modele.
  - Normalite et independance des erreurs supposees ; priorisation, pas garantie de conformite.
  - Le LE90 n'est dit "mesure" que si >= 10 points de reference apparies existent dans la zone.
"""

import csv
import json
import math
import os
import re

from ._common import haversine_m, detect_sep, norm_cdf

_BAR_M = 10.197
_Z90 = 1.645
_N_MIN = 10
_SIGMA_DEFAUT = 30.0
_LAT_KEYS = ("latitude", "lat", "y")
_LON_KEYS = ("longitude", "lon", "lng", "x")
_UNITES_METRIQUES = ("LPS", "LPM", "MLD", "CMH", "CMD")
_FT = 0.3048


# --- Utilitaires ------------------------------------------------------------

def _num(v):
    try:
        s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
        return float(s) if s != "" else None
    except (ValueError, TypeError):
        return None


class _Grille:
    """Index en grille (cellule ~ 110 m) pour la recherche du point le plus proche."""

    def __init__(self, items, cell=0.001):
        self.cell, self.g = cell, {}
        for it in items:
            self.g.setdefault((int(it["lon"] // cell), int(it["lat"] // cell)), []).append(it)

    def proche(self, lon, lat, rayon_m):
        c = self.cell
        reach = int(rayon_m / 110000.0 / c) + 1
        cx, cy = int(lon // c), int(lat // c)
        best, bd = None, rayon_m
        for i in range(cx - reach, cx + reach + 1):
            for j in range(cy - reach, cy + reach + 1):
                for it in self.g.get((i, j), ()):
                    d = haversine_m(lon, lat, it["lon"], it["lat"])
                    if d <= bd:
                        best, bd = it, d
        return best, (bd if best is not None else None)


def point_in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def _load_zones(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    feats = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    polys = []
    for ft in feats:
        g = ft.get("geometry", ft) or {}
        if g.get("type") == "Polygon":
            polys.append(g["coordinates"])
        elif g.get("type") == "MultiPolygon":
            polys.extend(g["coordinates"])
    if not polys:
        raise ValueError("Aucun polygone dans " + os.path.basename(path))
    return polys


def _in_zones(lon, lat, polys):
    return any(point_in_ring(lon, lat, r[0]) and not any(point_in_ring(lon, lat, h) for h in r[1:])
               for r in polys)


def _reproj(epsg):
    if int(epsg) == 4326:
        return None
    from osgeo import osr
    src = osr.SpatialReference()
    if src.ImportFromEPSG(int(epsg)) != 0:
        raise ValueError("EPSG invalide : " + str(epsg))
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(src, dst)


# --- Hydraulique : fonctions pures ------------------------------------------

def z_hydraulique(h_ref, pression_m, h_manometre=0.0, perte_residuelle=0.0):
    """Cote du sol deduite de la pression statique. Un debit residuel fait chuter la pression
    lue : on la retablit de la perte residuelle."""
    return h_ref - (pression_m + perte_residuelle) - h_manometre


def sigma_hydraulique(sig_manometre, sig_href, sig_perte, sig_hm=0.0):
    return math.sqrt(sig_manometre ** 2 + sig_href ** 2 + sig_perte ** 2 + sig_hm ** 2)


def compatibles(z1, s1, z2, s2, k):
    return abs(z1 - z2) <= k * math.sqrt(s1 ** 2 + s2 ** 2)


def combiner(z1, s1, z2, s2):
    w1, w2 = 1.0 / (s1 * s1), 1.0 / (s2 * s2)
    return (w1 * z1 + w2 * z2) / (w1 + w2), math.sqrt(1.0 / (w1 + w2))


def evaluer_noeud(z, tol90, charge_serv, charge_stat, p_min, p_max):
    """Pressions, marge et probabilite que le verdict de pression soit faux.
    P_serv = charge_serv - Z (>= p_min) ; P_stat = charge_stat - Z (<= p_max)."""
    sigma = max(tol90, 0.01) / _Z90
    p_serv, p_stat = charge_serv - z, charge_stat - z
    a, b = p_min - p_serv, p_max - p_stat
    p_ok = max(0.0, norm_cdf(b / sigma) - norm_cdf(a / sigma))
    conforme = (a <= 0.0 <= b)
    p_faux = (1.0 - p_ok) if conforme else p_ok
    return {"p_serv": p_serv, "p_stat": p_stat, "marge": min(-a, b), "conforme": conforme,
            "p_faux": min(1.0, max(0.0, p_faux)), "sigma": sigma,
            "limite": "PRESSION_MINI" if -a <= b else "PRESSION_MAXI"}


def choisir_releves(nodes, n_max, dist_min_m, seuil_min):
    cands = sorted((n for n in nodes if n["p_faux"] >= seuil_min),
                   key=lambda n: (-n["p_faux"], -n["sigma"], n["id"]))
    chosen = []
    for c in cands:
        if len(chosen) >= n_max:
            break
        if all(haversine_m(c["lon"], c["lat"], s["lon"], s["lat"]) >= dist_min_m for s in chosen):
            chosen.append(c)
    return chosen


def stats_erreur(erreurs):
    """ME, RMSE, ET, LE90 (90e centile de |e|), max |e| d'une liste d'erreurs (DEM - reference)."""
    n = len(erreurs)
    if n == 0:
        return None
    me = sum(erreurs) / n
    rmse = math.sqrt(sum(e * e for e in erreurs) / n)
    et = math.sqrt(sum((e - me) ** 2 for e in erreurs) / (n - 1)) if n > 1 else 0.0
    ab = sorted(abs(e) for e in erreurs)
    q = 0.9 * (n - 1)
    lo = int(math.floor(q))
    le90 = ab[lo] + (ab[min(lo + 1, n - 1)] - ab[lo]) * (q - lo)
    return {"n": n, "me": me, "rmse": rmse, "et": et, "le90": le90, "max": ab[-1]}


# --- Modele EPANET ----------------------------------------------------------

def lire_inp(path):
    """Sources du modele : {id: {type, head, head_max, niveau}} en metres.
    Reservoir : charge = cote. Reservoir de stockage (TANK) : radier + niveau initial,
    head_max = radier + niveau maxi."""
    sect, unites, res, tanks = None, "LPS", {}, {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.split(";", 1)[0].strip()
            if not ln:
                continue
            m = re.match(r"^\[(\w+)\]", ln)
            if m:
                sect = m.group(1).upper()
                continue
            t = ln.split()
            if sect == "OPTIONS" and t[0].upper() == "UNITS" and len(t) > 1:
                unites = t[1].upper()
            elif sect == "RESERVOIRS" and len(t) >= 2 and _num(t[1]) is not None:
                res[t[0]] = _num(t[1])
            elif sect == "TANKS" and len(t) >= 4:
                el, ini, mx = _num(t[1]), _num(t[2]), _num(t[4]) if len(t) > 4 else None
                if el is not None and ini is not None:
                    tanks[t[0]] = (el, ini, mx if mx is not None else ini)
    k = 1.0 if unites in _UNITES_METRIQUES else _FT
    src = {}
    for i, h in res.items():
        src[i] = {"type": "RESERVOIR", "head": h * k, "head_max": h * k, "niveau": None}
    for i, (el, ini, mx) in tanks.items():
        src[i] = {"type": "TANK", "head": (el + ini) * k, "head_max": (el + mx) * k, "niveau": ini * k}
    return src, unites, k != 1.0


def parse_surcharges(txt):
    out = {}
    for part in re.split(r"[;\n]+", txt or ""):
        if "=" in part:
            a, b = part.split("=", 1)
            v = _num(b)
            if a.strip() and v is not None:
                out[a.strip()] = v
    return out


# --- Lecture des noeuds, charges, mesures ------------------------------------

def _load_nodes(path):
    nodes = []
    if path.lower().endswith((".geojson", ".json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k, ft in enumerate(data.get("features", [])):
            g = ft.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            p = dict(ft.get("properties") or {})
            z = _num(p.get("altitude_m"))
            if z is None:
                continue
            nodes.append({"id": str(p.get("node_id") or "N%d" % k), "lon": float(g["coordinates"][0]),
                          "lat": float(g["coordinates"][1]), "z": z, "tol": _num(p.get("incertitude_m")),
                          "qual": p.get("qualite_finale") or "", "src": p.get("source_altitude") or "",
                          "alerte": p.get("alerte") or "", "props": p})
    else:
        sep = detect_sep(path)
        with open(path, newline="", encoding="utf-8-sig") as f:
            for k, row in enumerate(csv.DictReader(f, delimiter=sep)):
                z, la, lo = _num(row.get("altitude_m")), _num(row.get("latitude")), _num(row.get("longitude"))
                if z is None or la is None or lo is None:
                    continue
                nid = row.get("name") or "N%d" % k
                nodes.append({"id": nid, "lon": lo, "lat": la, "z": z, "tol": _num(row.get("incertitude_m")),
                              "qual": row.get("qualite_finale") or "", "src": row.get("source_altitude") or "",
                              "alerte": row.get("alerte") or "",
                              "props": {"node_id": nid, "altitude_m": z}})
    return nodes


def _read_csv_points(path, epsg, need, log_fn, label):
    """Lit un CSV de points WGS84/EPSG : retourne (lignes brutes, dict d'en-tetes)."""
    sep = detect_sep(path)
    tr = _reproj(epsg)
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f, delimiter=sep)
        hdr = {(h or "").strip().lower(): h for h in (rd.fieldnames or [])}
        kl = next((hdr[k] for k in _LAT_KEYS if k in hdr), None)
        ko = next((hdr[k] for k in _LON_KEYS if k in hdr), None)
        if kl is None or ko is None:
            raise ValueError(label + " : colonnes latitude/longitude (ou lat/lon, y/x) introuvables. "
                             "En-tetes lus : " + str(rd.fieldnames))
        if not any(h in hdr for h in need):
            raise ValueError(label + " : il faut une colonne parmi " + ", ".join(need))
        bad = 0
        for i, row in enumerate(rd, 1):
            x, y = _num(row.get(ko)), _num(row.get(kl))
            if x is None or y is None:
                bad += 1
                continue
            if tr is not None:
                x, y, _ = tr.TransformPoint(x, y)
            rows.append((i, x, y, row))
    if bad:
        log_fn("  [!] " + label + " : " + str(bad) + " ligne(s) sans coordonnees ignoree(s)")
    return rows, hdr


# --- Etape ------------------------------------------------------------------

def run(params, log_fn, progress_fn, stop_fn=None):
    P = params
    out_dir = P["output_dir"]
    path_n = P["input_nodes"]
    path_m = (P.get("input_mesures") or "").strip()
    path_i = (P.get("input_inp") or "").strip()
    path_c = (P.get("input_charges") or "").strip()
    path_z = (P.get("input_zones") or "").strip()
    g = lambda k, d: float(P.get(k, d))  # noqa: E731
    sig_href, sig_man = g("sigma_href_m", 0.3), g("sigma_manometre_m", 1.0)
    perte_res, sig_perte = g("perte_residuelle_m", 0.0), g("sigma_perte_m", 0.5)
    sig_leve, sig_urb_sup = g("sigma_leve_m", 0.10), g("sigma_dem_urbain_m", 15.0)
    k, rayon = g("k_coherence", 2.5), g("rayon_appariement_m", 30.0)
    h_src, dh = g("h_source_m", 0.0), g("perte_charge_m", 10.0)
    p_min, p_max = g("p_min_m", 15.0), g("p_max_m", 60.0)
    n_rel, dmin = int(P.get("n_releves", 20)), g("dist_min_m", 300.0)
    s_ver, s_cri = g("seuil_verifier", 0.05), g("seuil_critique", 0.25)
    r_charge = g("rayon_charges_m", 50.0)
    epsg_m, epsg_c = int(P.get("epsg_mesures", 4326)), int(P.get("epsg_charges", 4326))

    log_fn("=" * 58)
    log_fn("  Etape 6 -- Cotes hydrauliques, coherence et noeuds sensibles")
    log_fn("=" * 58)
    if not os.path.exists(path_n):
        raise FileNotFoundError("Fichier de noeuds introuvable : " + path_n)
    if not (p_min < p_max) or not (0 <= s_ver < s_cri <= 1):
        raise ValueError("Verifier p_min < p_max et 0 <= seuil verifier < seuil critique <= 1.")
    if min(sig_href, sig_man, sig_perte, sig_leve, sig_urb_sup) <= 0 or k <= 0:
        raise ValueError("Les incertitudes et k doivent etre > 0.")

    nodes = _load_nodes(path_n)
    if not nodes:
        raise RuntimeError("Aucun noeud avec altitude_m dans " + os.path.basename(path_n))
    zones = _load_zones(path_z) if path_z else None
    log_fn("  Noeuds : " + str(len(nodes)))

    # -- Sources (reservoirs) --------------------------------------------------
    sources = {}
    if path_i:
        sources, unites, converti = lire_inp(path_i)
        log_fn("  Modele EPANET : " + str(len(sources)) + " source(s), unites " + unites
               + (" (converties en m)" if converti else ""))
    for sid, v in parse_surcharges(P.get("surcharges", "")).items():
        sources[sid] = {"type": "MANUEL", "head": v, "head_max": v, "niveau": None}
        log_fn("  Cote saisie : " + sid + " = " + str(v) + " m")
    for sid, s in sources.items():
        if s["type"] == "TANK":
            log_fn("  [!] " + sid + " : cote = radier + niveau initial du modele (" + str(round(s["head"], 2))
                   + " m). Le niveau d'eau reel a l'heure des mesures peut differer : le saisir "
                   "dans 'Cotes saisies' (ID=cote).")
    ref_id = (P.get("source_ref") or "").strip()
    if ref_id and ref_id not in sources:
        raise ValueError("Source '" + ref_id + "' introuvable. Disponibles : " + ", ".join(sources))
    if not ref_id and len(sources) == 1:
        ref_id = next(iter(sources))
    h_ref_def = sources[ref_id]["head"] if ref_id else None
    h_stat = max([s["head_max"] for s in sources.values()], default=None)
    if h_stat is None:
        h_stat = h_src if h_src else None
    if h_stat is None:
        raise ValueError("Aucune cote de reservoir : fournir le modele .inp, des cotes saisies "
                         "(ID=cote) ou la charge source.")
    log_fn("  Charge statique maxi retenue (pression statique) : " + str(round(h_stat, 2)) + " m")
    progress_fn(8)

    # -- Zones ----------------------------------------------------------------
    for n in nodes:
        n["zone"] = "URBAIN" if ("BATI" in str(n["alerte"]).split(";")
                                 or (zones is not None and _in_zones(n["lon"], n["lat"], zones))) else "RURAL"
        n["z_dem"] = n["z"]
    n_urb = sum(1 for n in nodes if n["zone"] == "URBAIN")
    log_fn("  Zones : " + str(n_urb) + " urbain(s), " + str(len(nodes) - n_urb) + " rural(aux)"
           + ("" if zones else "  (zones deduites de l'alerte BATI de l'etape 3)"))

    # -- Charges simulees ------------------------------------------------------
    grid_n = _Grille(nodes)
    for n in nodes:
        n["charge_serv"], n["charge_origine"] = None, ""
    if path_c:
        rows, hdr = _read_csv_points(path_c, epsg_c, ("charge_m", "charge", "head"), log_fn, "Charges")
        kc = next(hdr[h] for h in ("charge_m", "charge", "head") if h in hdr)
        pts = []
        for _, x, y, row in rows:
            v = _num(row.get(kc))
            if v is not None:
                pts.append({"lon": x, "lat": y, "h": v})
        gr = _Grille(pts)
        for n in nodes:
            it, _ = gr.proche(n["lon"], n["lat"], r_charge)
            if it is not None:
                n["charge_serv"], n["charge_origine"] = it["h"], "MODELE"
        log_fn("  Charges du modele rattachees : " + str(sum(1 for n in nodes if n["charge_origine"]))
               + " / " + str(len(nodes)) + " noeuds (rayon " + str(r_charge) + " m)")
    n_fb = 0
    if h_src:
        fb = h_src - dh
    else:
        fb = h_stat - dh
    for n in nodes:
        if n["charge_serv"] is None:
            n["charge_serv"], n["charge_origine"] = fb, "SAISIE"
            n_fb += 1
    if n_fb:
        log_fn("  [!] " + str(n_fb) + " noeud(s) sans charge du modele : charge de service = "
               + str(round(fb, 1)) + " m (charge source - perte admise, valeurs saisies)")
    if n_fb == len(nodes) and fb < min(n["z"] for n in nodes):
        raise ValueError("La charge utile (" + str(round(fb, 1)) + " m) est sous toutes les altitudes : "
                         "verifier la charge source / les cotes de reservoirs.")
    progress_fn(20)

    # -- Mesures, nivellement hydraulique, coherence --------------------------
    mes, used, cnt = [], {}, {"LEVE": 0, "HYDRAULIQUE": 0, "LEVE+HYDRAULIQUE": 0, "CONTRADICTION": 0}
    if path_m:
        rows, hdr = _read_csv_points(path_m, epsg_m, ("pression_m", "pression_bar", "z_leve", "z"),
                                     log_fn, "Mesures")
        kp, kb = hdr.get("pression_m"), hdr.get("pression_bar")
        kz, kn = hdr.get("z_leve") or hdr.get("z"), next((hdr[c] for c in ("id", "name", "nom") if c in hdr), None)
        khm, ks, kr = hdr.get("hauteur_manometre_m"), hdr.get("sigma_m"), hdr.get("reservoir")
        for i, x, y, row in rows:
            p = _num(row.get(kp)) if kp else None
            if p is None and kb:
                pb = _num(row.get(kb))
                p = None if pb is None else pb * _BAR_M
            zl = _num(row.get(kz)) if kz else None
            if p is None and zl is None:
                continue
            hm = _num(row.get(khm)) if khm else None
            sg = _num(row.get(ks)) if ks else None
            rid = (row.get(kr) or "").strip() if kr else ""
            href = None
            if p is not None:
                if rid:
                    if rid not in sources:
                        log_fn("  [!] mesure " + str(i) + " : reservoir '" + rid + "' inconnu, ignore")
                        continue
                    href = sources[rid]["head"]
                else:
                    href = h_ref_def
                if href is None:
                    raise ValueError("Mesures de pression sans cote de reservoir : renseigner la "
                                     "'Source de reference' ou une colonne 'reservoir'.")
            m = {"id": (row.get(kn) or "M%d" % i) if kn else "M%d" % i, "lon": x, "lat": y,
                 "z_leve": zl, "z_hyd": None, "s_hyd": None, "noeud": None, "dist": None}
            if p is not None:
                m["z_hyd"] = z_hydraulique(href, p, hm or 0.0, perte_res)
                m["s_hyd"] = sg if sg else sigma_hydraulique(sig_man, sig_href, sig_perte, 0.1 if hm else 0.0)
            m["s_leve"] = sg if (sg and p is None) else sig_leve
            mes.append(m)
        log_fn("  Mesures : " + str(len(mes)))
        for m in mes:
            it, d = grid_n.proche(m["lon"], m["lat"], rayon)
            if it is not None:
                m["noeud"], m["dist"] = it["id"], d
                used.setdefault(id(it), (it, []))[1].append(m)
        for it, ms in used.values():
            zl = [(m["z_leve"], m["s_leve"]) for m in ms if m["z_leve"] is not None]
            zh = [(m["z_hyd"], m["s_hyd"]) for m in ms if m["z_hyd"] is not None]
            zl1 = min(zl, key=lambda t: t[1]) if zl else None
            zh1 = min(zh, key=lambda t: t[1]) if zh else None
            if zl1 and zh1:
                if compatibles(zl1[0], zl1[1], zh1[0], zh1[1], k):
                    z, s = combiner(zl1[0], zl1[1], zh1[0], zh1[1])
                    src, stat, tol = "LEVE+HYDRAULIQUE", "COHERENT", _Z90 * s
                else:
                    z, s = zl1
                    src, stat = "LEVE", "CONTRADICTION"
                    tol = max(_Z90 * s, abs(zh1[0] - zl1[0]))
            elif zl1:
                (z, s), src, stat = zl1, "LEVE", "LEVE_SEUL"
                tol = _Z90 * s
            else:
                (z, s), src, stat = zh1, "HYDRAULIQUE", "HYDRAULIQUE_SEUL"
                tol = _Z90 * s
            it.update({"z_new": z, "tol_new": tol, "src_new": src, "stat": stat,
                       "tol_origine": "MESUREE" if stat != "CONTRADICTION" else "ECART_OBSERVE"})
            cnt["CONTRADICTION" if stat == "CONTRADICTION" else src] += 1
        if cnt["CONTRADICTION"]:
            log_fn("  [!] " + str(cnt["CONTRADICTION"]) + " contradiction(s) leve / hydraulique : voir "
                   "coherence_cotes.csv (saisie, repere, manometre, reseau non statique ?)")
        nfar = sum(1 for m in mes if m["noeud"] is None)
        if nfar:
            log_fn("  [!] " + str(nfar) + " mesure(s) a plus de " + str(rayon) + " m de tout noeud : "
                   "non appariee(s) (pour les valider, lancer l'etape 3 sur ces points)")
    progress_fn(45)

    # -- Erreur reelle du DEM (mesuree) ---------------------------------------
    err = {"RURAL": [], "URBAIN": []}
    for it, ms in used.values():
        if it["stat"] == "CONTRADICTION":
            continue
        ref = it["z_new"]
        err[it["zone"]].append(it["z_dem"] - ref)
    bilan = {z: stats_erreur(e) for z, e in err.items()}
    bilan["TOUS"] = stats_erreur(err["RURAL"] + err["URBAIN"])
    le90_mes = {z: (bilan[z]["le90"] if bilan[z] and bilan[z]["n"] >= _N_MIN else None)
                for z in ("RURAL", "URBAIN")}
    for z in ("RURAL", "URBAIN"):
        b = bilan[z]
        if b:
            log_fn("  Erreur DEM " + z.lower() + " : n=" + str(b["n"]) + "  biais " + str(round(b["me"], 1))
                   + " m  RMSE " + str(round(b["rmse"], 1)) + " m  LE90 " + str(round(b["le90"], 1)) + " m"
                   + ("" if b["n"] >= _N_MIN else "  (moins de " + str(_N_MIN) + " points : non retenu)"))

    # -- Noeuds non mesures : incertitude (mesuree si possible) ---------------
    for n in nodes:
        if "z_new" in n:
            continue
        n["z_new"], n["src_new"] = n["z_dem"], n["src"] or "DEM"
        if le90_mes[n["zone"]] is not None:
            n["tol_new"], n["tol_origine"] = le90_mes[n["zone"]], "MESUREE"
            n["stat"] = "DEM_" + n["zone"]
        elif n["zone"] == "URBAIN":
            n["tol_new"], n["tol_origine"] = max(n["tol"] or 0.0, _Z90 * sig_urb_sup), "SUPPOSEE"
            n["stat"] = "DEM_URBAIN_NON_MESURE"
        else:
            n["tol_new"] = n["tol"] if n["tol"] is not None else _SIGMA_DEFAUT
            n["tol_origine"] = "SUPPOSEE"
            n["stat"] = "DEM_RURAL"
            if n["qual"] == "INCERTAINE":
                n["tol_new"] = max(n["tol_new"], 30.0)
    progress_fn(60)

    # -- Sensibilite de la conclusion sur la pression -------------------------
    for n in nodes:
        n.update(evaluer_noeud(n["z_new"], n["tol_new"], n["charge_serv"], h_stat, p_min, p_max))
        n["classe"] = ("CRITIQUE" if n["p_faux"] >= s_cri else "A_VERIFIER" if n["p_faux"] >= s_ver else "SUR")
    c = {x: sum(1 for n in nodes if n["classe"] == x) for x in ("SUR", "A_VERIFIER", "CRITIQUE")}
    n_nc = sum(1 for n in nodes if not n["conforme"])
    log_fn("\n  Verdict estime : " + str(len(nodes) - n_nc) + " conformes, " + str(n_nc) + " non conformes")
    log_fn("  Sensibilite    : SUR " + str(c["SUR"]) + "  |  A_VERIFIER " + str(c["A_VERIFIER"])
           + "  |  CRITIQUE " + str(c["CRITIQUE"]))
    log_fn("  Verdicts faux attendus : " + str(round(sum(n["p_faux"] for n in nodes), 1)) + " sur " + str(len(nodes)))
    if c["CRITIQUE"] > 0.8 * len(nodes):
        log_fn("  [!] Presque tous les noeuds sont critiques : verifier les charges, les cotes de "
               "reservoirs et les seuils avant de conclure.")
    nu = sum(1 for n in nodes if n["stat"] == "DEM_URBAIN_NON_MESURE")
    if nu:
        log_fn("  [!] " + str(nu) + " noeud(s) urbain(s) sans mesure ni LE90 mesure : cote DEM (toits possibles), "
               "incertitude supposee.")

    chosen = choisir_releves(nodes, n_rel, dmin, s_ver)
    for r, ch in enumerate(chosen, 1):
        ch["rang"] = r
    risk_cov = tot = 0.0
    for n in nodes:
        n["couvert_par"] = None
        if n["p_faux"] < s_ver:
            continue
        tot += n["p_faux"]
        for ch in chosen:
            if haversine_m(n["lon"], n["lat"], ch["lon"], ch["lat"]) < dmin:
                n["couvert_par"] = ch["id"]
                risk_cov += n["p_faux"]
                break
    log_fn("\n  Plan : " + str(len(chosen)) + " noeud(s) a mesurer (distance mini " + str(dmin) + " m)"
           + ("  -> " + str(round(100 * risk_cov / tot)) + " % du risque couvert (erreur DEM supposee "
              "correlee dans ce rayon)" if tot > 0 else ""))
    progress_fn(80)

    # -- Ecriture --------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    feats = []
    for n in nodes:
        pr = dict(n["props"])
        pr.update({
            "altitude_dem_origine_m": round(n["z_dem"], 2), "altitude_m": round(n["z_new"], 2),
            "source_altitude": n["src_new"], "incertitude_m": round(n["tol_new"], 2),
            "incertitude_origine": n["tol_origine"], "zone": n["zone"], "statut_cote": n["stat"],
            "charge_service_m": round(n["charge_serv"], 2), "charge_origine": n["charge_origine"],
            "pression_service_est_m": round(n["p_serv"], 1), "pression_statique_est_m": round(n["p_stat"], 1),
            "marge_altitude_m": round(n["marge"], 1), "p_verdict_faux": round(n["p_faux"], 3),
            "classe_sensibilite": n["classe"], "limite": n["limite"],
            "verdict_estime": "CONFORME" if n["conforme"] else "NON_CONFORME",
            "rang_releve": n.get("rang"), "couvert_par": n["couvert_par"]})
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [n["lon"], n["lat"]]},
                      "properties": pr})
    f1 = os.path.join(out_dir, "noeuds_hydro.geojson")
    with open(f1, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                   "features": feats}, f, ensure_ascii=False, separators=(",", ":"))

    f2 = os.path.join(out_dir, "cotes_epanet.csv")
    with open(f2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "X", "Y", "altitude_m", "source_altitude", "incertitude_m",
                    "incertitude_origine", "statut_cote", "zone"])
        for n in nodes:
            w.writerow([n["id"], "%.7f" % n["lon"], "%.7f" % n["lat"], round(n["z_new"], 2), n["src_new"],
                        round(n["tol_new"], 2), n["tol_origine"], n["stat"], n["zone"]])

    f3 = os.path.join(out_dir, "plan_releves.csv")
    with open(f3, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["rang", "id", "latitude", "longitude", "z_leve", "pression_m", "altitude_actuelle_m",
                    "incertitude_m", "p_verdict_faux", "marge_altitude_m", "classe", "limite", "zone"])
        for ch in chosen:
            w.writerow([ch["rang"], ch["id"], "%.7f" % ch["lat"], "%.7f" % ch["lon"], "", "",
                        round(ch["z_new"], 2), round(ch["tol_new"], 1), round(ch["p_faux"], 3),
                        round(ch["marge"], 1), ch["classe"], ch["limite"], ch["zone"]])

    byid = {n["id"]: n for n in nodes}
    f4 = os.path.join(out_dir, "coherence_cotes.csv")
    with open(f4, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["mesure", "noeud", "distance_m", "z_leve_m", "z_hydraulique_m", "sigma_hydraulique_m",
                    "z_dem_m", "ecart_hyd_moins_leve_m", "seuil_coherence_m", "ecart_dem_moins_ref_m", "statut"])
        r2 = lambda v: "" if v is None else round(v, 2)  # noqa: E731
        for m in mes:
            n = byid.get(m["noeud"])
            ref = m["z_leve"] if m["z_leve"] is not None else m["z_hyd"]
            e_hl = seuil = None
            stat = "NON_APPARIE" if n is None else n["stat"]
            if m["z_leve"] is not None and m["z_hyd"] is not None:
                e_hl = m["z_hyd"] - m["z_leve"]
                seuil = k * math.sqrt(m["s_leve"] ** 2 + m["s_hyd"] ** 2)
                stat = "COHERENT" if abs(e_hl) <= seuil else "CONTRADICTION"
            zd = n["z_dem"] if n else None
            w.writerow([m["id"], m["noeud"] or "", r2(m["dist"]), r2(m["z_leve"]), r2(m["z_hyd"]),
                        r2(m["s_hyd"]), r2(zd), r2(e_hl), r2(seuil),
                        r2(None if (zd is None or ref is None) else zd - ref), stat])

    f5 = os.path.join(out_dir, "bilan_erreur_dem.csv")
    with open(f5, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["zone", "n", "biais_m", "rmse_m", "ecart_type_m", "le90_m", "max_abs_m", "fiabilite"])
        for z in ("RURAL", "URBAIN", "TOUS"):
            b = bilan[z]
            if b:
                w.writerow([z, b["n"], round(b["me"], 2), round(b["rmse"], 2), round(b["et"], 2),
                            round(b["le90"], 2), round(b["max"], 2),
                            "OK" if b["n"] >= _N_MIN else "PEU_FIABLE_(n<%d)" % _N_MIN])
    progress_fn(100)
    for p_ in (f1, f2, f3, f4, f5):
        log_fn("  [OK] " + p_)
    log_fn("  Suite : reporter cotes_epanet.csv dans le modele ; mesurer les noeuds de plan_releves.csv,")
    log_fn("          remplir z_leve ou pression_m, les ajouter au fichier Mesures et relancer.")
