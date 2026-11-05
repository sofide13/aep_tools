# AEP Tools — Extension QGIS pour réseaux d'eau potable

> **Auteur : Kouidri Sofiane** — © 2026 Kouidri Sofiane. Toute redistribution doit conserver cette mention.

**Version** : 2.1.4  
**Auteur** : Kouidri Sofiane (sofihydro@gmail.com)  
**Licence** : GNU GPL v2 ou ultérieure (voir LICENSE)  
**Compatibilité** : QGIS 3.16+  

---

## Description

Extension QGIS pour le traitement altimétrique des réseaux d'alimentation en eau potable (AEP).  
Développée et validée sur le réseau Oran–Tlemcen (Algérie occidentale, ~680 km de conduite, 134 206 vertices).

---

## Workflow en 6 étapes

```
Etape 1 ─ Export CSV        Exporter les couches réseau depuis QGIS en CSV
Etape 2 ─ Télécharger DEM   Télécharger le MNT Copernicus GLO-30 (automatique)
Etape 3 ─ Altitudes réseau  Extraire les altitudes + classification fiabilité
Etape 4 ─ Découpage         Découper le réseau par seuil de dénivelé
Etape 5 ─ Attribut altitude Ajouter la colonne altitude aux couches QGIS
Etape 6 ─ Extraction CSV    Extraire des altitudes pour n'importe quel CSV
```

---

## Source DEM utilisée

| Paramètre | Valeur |
|-----------|--------|
| Dataset | Copernicus DEM GLO-30 |
| Résolution | ~30 m/pixel |
| Source | AWS S3 (gratuit, sans clé API) |
| Couverture | Mondiale (90°N – 90°S) |
| Précision verticale | ±2 m (zones plates) à ±50 m (haute montagne) |

---

## Résultats de validation (réseau Oran–Tlemcen)

Validation effectuée avec **3 738 points GPS** couvrant ~30 km sur 680 km de réseau.

| Zone | N pts | Terrain | RMSE (m) | Biais (m) | Qualité |
|------|-------|---------|----------|----------|---------|
| FG   | 1 444 | Plaine (Oran côte) | **4.8** | −0.9 | ✅ EXCELLENT |
| DE   | 452   | Collines           | 27.8   | −11.2 | ⚠️ LIMITE |
| CD   | 716   | Accidenté          | 31.2   | +7.2  | ⚠️ LIMITE |
| AB   | 630   | Montagne (Tlemcen) | 38.7   | +25.7 | ❌ INSUFFISANT |
| BC   | 496   | Montagne (Mascara) | 47.8   | +15.4 | ❌ INSUFFISANT |
| **GLOBAL** | **3 738** | — | **29.1** | +6.0 | ⚠️ LIMITE |

> **Objectif initial ±3 m (0.3 bar)** : atteint uniquement en terrain plat (zone FG).

---

## Classification automatique de fiabilité

Chaque vertex reçoit une étiquette `qualite` basée sur la **pente locale du DEM** (algorithme de Horn 3×3) :

| Pente locale | Classe | Incertitude estimée |
|-------------|--------|-------------------|
| < 2° | `FIABLE` | ±3–5 m |
| 2°–5° | `ACCEPTABLE` | ±5–15 m |
| 5°–15° | `INCERTAINE` | ±15–30 m |
| > 15° | `NON_FIABLE` | > ±30 m |

---

## Limitations connues

1. **Résolution DEM** : Le GLO-30 (30 m) est insuffisant en terrain montagneux.  
   → En montagne (Tlemcen, Mascara) : RMSE 38–48 m, hors tolérance hydraulique.

2. **Surface vs Terrain** : Le GLO-30 est un DSM (surface), pas un DTM (sol nu).  
   → En zone boisée ou urbanisée, surestimation systématique de l'altitude.

3. **Couverture GPS limitée** : Les 3 738 points GPS ne couvrent que ~30/680 km.  
   → La validation ne couvre pas toutes les zones géographiques du réseau.

4. **Sources premium non accessibles** : CopDEM-GLO-10 (10 m) non disponible  
   via API standard CDSE pour les comptes gratuits.

---

## Améliorations futures recommandées

- Intégrer un MNT 5–10 m de l'**INCT** (Institut National de Cartographie, Algérie)
- Ajouter la calibration GPS par IDW (implémentée, non encore testée à grande échelle)
- Étendre la couverture GPS à l'ensemble du réseau pour une validation complète

---

## Utilisation de l'Etape 6 (Extraction CSV)

Format CSV accepté (séparateur `,` ou `;`) :

```
ID;X;Y;...autres colonnes...
1;200345;3875210;...
```

ou en coordonnées géographiques :

```
ID,LONGITUDE,LATITUDE,...
1,-1.342,34.856,...
```

Codes EPSG courants :
- `32630` : UTM Zone 30N → Oran, Tlemcen, Sidi Bel Abbès
- `32631` : UTM Zone 31N → Alger, Constantine, Annaba
- `4326`  : WGS84 Longitude/Latitude (GPS)

---

## Dépendances

- **GDAL/OGR** : inclus dans QGIS
- **PyQt5** : inclus dans QGIS
- **openpyxl** : pour la lecture des fichiers .xlsx (validation GPS)

---

*Documentation générée le 2026-05-18 — AEP Tools V2.1.0*
