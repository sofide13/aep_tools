"""Fonctions partagees par les scripts AEP Tools (une seule implementation de chaque)."""
import math


def haversine_m(lon1, lat1, lon2, lat2):
    """Distance en metres entre deux points WGS84."""
    R = 6_371_000.0
    f1, f2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(f1) * math.cos(f2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def detect_sep(path, encoding="utf-8-sig"):
    """Separateur CSV (; ou ,) deduit de la ligne d'en-tete uniquement.

    Les lignes de donnees ne conviennent pas : avec ';' comme separateur et une virgule
    decimale (36,15;1,20), les virgules seraient plus nombreuses que les ';'."""
    with open(path, newline="", encoding=encoding) as f:
        for line in f:
            if line.strip():
                return ";" if line.count(";") >= line.count(",") and ";" in line else ","
    return ","


def norm_cdf(x):
    """Fonction de repartition de la loi normale centree reduite."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
