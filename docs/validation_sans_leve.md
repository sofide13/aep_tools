# Cotes en zone bâtie : protocole hydraulique (étape 6)

Le DEM utilisé (GLO-30, SRTM) est un **modèle de surface** : en ville il mesure des toits. Sur une adduction en terrain
ouvert, il reste utilisable (à contrôler). Dans un réseau de distribution bâti, la cote de calcul vient des mesures
ci-dessous, jamais du DEM seul.

## Principe
La charge piézométrique d'un réseau ne dépend pas de l'altitude des nœuds ; l'altitude ne sert qu'à passer de la charge à la
pression (`P = charge − Z`). Une pression statique mesurée donne donc la cote du sol :

`Z = H_réservoir − P − h_manomètre`   (1 bar ≈ 10,197 m)

## 1. Mesure de la pression statique (méthode principale)
- Où : bouches, poteaux d'incendie, branchements. Commencer par les nœuds du fichier `plan_releves.csv`.
- Quand : la nuit, sans débit (consommation minimale, vannes de sectorisation stables). Noter l'heure.
- Manomètre : zéro vérifié, classe adaptée à l'étendue (précision type ±0,1 bar ≈ ±1 m). Noter la hauteur du manomètre
  au-dessus du sol (`hauteur_manometre_m`).
- Réservoir : relever en même temps le niveau d'eau, et ajouter `ID=cote` (radier + hauteur d'eau) dans « Cotes saisies ».
  Un réservoir à niveau variable doit toujours être saisi : la cote du modèle est celle d'un instant fixe.
- Si un débit résiduel subsiste, renseigner la perte de charge résiduelle (elle abaisse la pression lue).
- Réservoirs partiellement connus : saisir ceux que l'on connaît ; pour un point alimenté par un autre réservoir, ajouter
  la colonne `reservoir` dans le CSV des mesures.

## 2. Cotes connues (sans nouveau levé)
Plans d'exécution et de récolement (radiers, regards, ouvrages), repères de nivellement, points cotés des cartes. À saisir
dans `z_leve`. Un levé est prioritaire sur la mesure hydraulique ; s'ils se contredisent, l'étape le signale (`CONTRADICTION`)
et ne les moyenne pas.

## 3. Altimétrie laser spatiale (ICESat-2 ATL08, GEDI)
Points de sol précis en terrain dégagé, peu nombreux en centre dense. Vérifier le référentiel vertical (ellipsoïde WGS84
ou géoïde EGM2008/EGM96) avant d'utiliser ces cotes comme `z_leve`.

## Protocole conseillé
1. Étapes 1 à 4 avec un 2e DEM et une couche de bâtiments à l'étape 3 (alerte `BATI`).
2. Étape 6 une première fois avec le modèle EPANET, ses charges (CSV) et les seuils de pression du cahier des charges, sans
   mesure : le `plan_releves.csv` désigne les nœuds à mesurer.
3. Mesurer (pression statique la nuit ou levé), remplir `pression_m` / `z_leve`, puis relancer l'étape 6 avec ce fichier.
4. Lire `coherence_cotes.csv` : traiter chaque `CONTRADICTION` (saisie, repère, manomètre, réseau non statique ?).
5. Lire `bilan_erreur_dem.csv` : avec au moins 10 points par zone, le LE90 mesuré remplace l'incertitude supposée.
6. Reporter `cotes_epanet.csv` dans le modèle, puis relancer la simulation et l'étape 6 : les charges changent peu avec la cote,
   les pressions changent avec elle.

Modèles de fichiers : `docs/modele_mesures.csv` (mesures), `docs/modele_charges.csv` (charges du modèle).
