# CLAUDE.md

## Description du projet

Scripts Python pour organiser automatiquement les photos après téléchargement depuis un appareil photo numérique (Canon). Fonctionnalités principales :
- Déplacement des fichiers RAW dans un sous-dossier `RAW/`
- Groupement des images liées (panoramas, HDR, focus bracketing)
- Géolocalisation via fichiers GPX
- Génération d'images AVIF/TIFF depuis les RAW

La vitesse d'exécution n'est pas une priorité. Ce que l'on souhaite est principalement avoir un résultat de qualité.
Le script peut prendre plusieurs minutes ou heures ce n'est pas un problème.

## Environnement virtuel

```bash
source .venv/bin/activate
```

## Commandes principales

```bash
# Trier un dossier photo
./sort_photos.py /chemin/vers/dossier

# Traitement par lot (tous les sous-dossiers d'un répertoire racine)
./sort_photos_from_root_folder.py /chemin/vers/racine

# Préparer un environnement de test
./clone_test_folder.py
```

## Structure du projet

```
photo-scripts-python/
├── sort_photos.py                  # CLI : tri d'un dossier photo (thin wrapper)
├── sort_photos_from_root_folder.py # CLI : traitement par lot
├── create_panorama.py              # CLI : assemblage panorama via hsi (thin wrapper)
├── clone_test_folder.py            # Préparation des données de test
├── photo_sorter/                   # Package principal
│   ├── __init__.py                 # Exporte sort_photos(), create_panorama(), load_configuration(), AppConfig
│   ├── constants.py                # RAW_EXTENSIONS, RENDERED_EXTENSIONS, DEFAULT_DCP_PROFILE_PATH, etc.
│   ├── config.py                   # AppConfig dataclass + load_configuration()
│   ├── models.py                   # ImageFile, GroupInfo dataclasses
│   ├── display.py                  # log_files(), log_title() (box-drawing)
│   ├── file_ops.py                 # scan_directory(), consolidate_images(), move_raws_to_folder()
│   ├── metadata.py                 # EXIF via exiftool
│   ├── grouping.py                 # Détection panoramas/HDR + confirmation interactive
│   ├── dcp_profile.py              # Parsing DCP Adobe, extraction/application tone curve
│   ├── lens_correction.py          # Lens distortion/vignetting/TCA correction (lensfunpy)
│   ├── raw_processing.py           # develop_raw(), create_avif, create_tiff (utilise dcp_profile)
│   ├── panorama.py                 # Assemblage panorama via hsi (Hugin Python bindings)
│   ├── hdr.py                       # Fusion d'exposition HDR via enfuse (align_image_stack)
│   ├── geotag.py                   # GPX parsing, géolocalisation
│   └── pipeline.py                 # sort_photos() orchestrateur
├── requirements.txt
├── sort_photo.conf                 # Configuration (dossiers par défaut)
├── testing/                        # Données de test (CR3, JPG, GPX)
├── docs/
│   └── prise-de-vue-et-numerisation.md  # Guide qualité : bracketing HDR, scan négatifs
└── CLAUDE.md
```

### Graphe de dépendances (pas de cycles)

```
constants    config (→ constants)    models     (feuilles)
     \         |                     /
      \        |                    /
       display  (→ models)
      /    |    \
file_ops  metadata  grouping  dcp_profile  lens_correction  geotag  panorama  hdr (→ display)
      \       |        |          |             |          /
       \      |        |          |             |         /
        \     |     raw_processing (→ dcp_profile, lens_correction, config, display)
         \    |        |         /
          pipeline  (→ tous les modules domaine)
              |
          __init__  (→ config, pipeline, panorama)
```

## Conventions de code

- **Langue** : Code, variables et commentaires en anglais (interaction développeur en français)
- **Style** : Suivre PEP 8 et les conventions Python recommandées
- **Constantes** : MAJUSCULES (`RAW_EXTENSIONS`, `MIN_TIME_BETWEEN_PANOS`)
- **Fonctions** : snake_case (`scan_directory()`, `extract_image_metadata()`)
- **Classes** : PascalCase (`ImageFile`, `GroupInfo`, `AppConfig`)
- **Dataclasses** pour les structures de données principales
- **Logging** extensif avec niveaux DEBUG/INFO/WARNING/ERROR
- Caractères box-drawing pour l'affichage console (├─ │ ┌─)
- **AppConfig** passé explicitement aux fonctions (pas de variables globales)

## Dépendances

### Python (voir `requirements.txt`)
- `Pillow` (PIL) - manipulation d'images (AVIF 8-bit, JPEG)
- `rawpy` - binding Python pour libraw (dématriçage RAW)
- `numpy` - manipulation de tableaux (sortie rawpy → PIL/tifffile)
- `tifffile` - écriture TIFF 16-bit RGB + lecture des profils DCP Adobe (format TIFF)
- `imagecodecs` - encodage AVIF 10-bit (Pillow ne supporte que 8-bit)
- `lensfunpy` - correction distorsion/vignettage/TCA (wraps liblensfun)
- `opencv-python-headless` - `cv2.remap()` pour la correction géométrique

### Outils système (requis)
- `exiftool` - extraction métadonnées EXIF + géotagging via GPX
- `hugin-tools` - assemblage panorama (fournit `hsi`, `cpfind`, `nona`) — Linux x86_64 uniquement
- `enblend` - fusion d'images pour panoramas
- `liblensfun1` - base de données de profils optiques (utilisé par `lensfunpy`)

## Structures de données clés

```python
@dataclass
class AppConfig:
    root_folder: str = "~/Images"       # Dossier racine photo
    folder_for_raws: str = "RAW"        # Sous-dossier pour les RAW
    dark_frame_path: Optional[str] = None  # Chemin vers dark frame PGM
    dcp_profile_path: Optional[str] = None # Profil DCP explicite (None = auto par PictureStyle)

@dataclass
class ImageFile:
    basename: str                    # Nom sans extension
    original_filename: str           # Nom de fichier original
    raw_relative_path: str           # Chemin relatif du RAW
    raw_filename: Optional[str]      # Fichier RAW (CR2, CR3, NEF)
    processed_filename: Optional[str] # Fichier traité (JPG, AVIF)
    timestamp: Optional[datetime]    # Date de prise de vue
    exposure_time: Optional[float]   # Temps d'exposition
    has_gps: bool                    # GPS présent
    group_id: Optional[str]          # ID groupe (panorama/HDR)
    group_type: Optional[str]        # Type: panorama, hdr, focus, burst
    picture_style: Optional[str]     # Canon PictureStyle (Standard, Portrait, ...)
    color_temperature: Optional[int] # Température couleur (Kelvin) depuis EXIF
    lens_model: Optional[str]        # Nom de l'objectif depuis EXIF (pour correction optique)
    focal_length: Optional[float]    # Focale en mm depuis EXIF
    aperture: Optional[float]        # Ouverture (f-number) depuis EXIF
```

## Constantes importantes

```python
RAW_EXTENSIONS = {'.crw', '.cr2', '.cr3', '.nef'}
RENDERED_EXTENSIONS = {'.jpg', '.jpeg', '.heif', '.avif'}
MIN_TIME_BETWEEN_PANOS = 15  # secondes entre photos d'un même groupe
```

## Pipeline de traitement

1. `scan_directory()` - Scan récursif des fichiers
2. `extract_image_metadata()` - Extraction EXIF (timestamp, exposition, GPS, objectif, focale, ouverture)
3. `geotag_pictures()` - Géolocalisation via GPX
4. `consolidate_images()` - Fusion RAW + versions traitées par basename
5. `identify_advanced_image_groups()` - Détection panoramas/HDR/focus
6. `confirm_groups()` - Confirmation interactive utilisateur
7. `move_raws_to_folder()` - Organisation des RAW
8. `create_processed_images()` - Génération AVIF/TIFF depuis RAW
   (groupes HDR → TIFF **display-referred** ; panorama → TIFF **linéaire**)
9. `create_panorama()` / `create_hdr()` - Assemblage des groupes confirmés

`confirm_groups()` propose maintenant `[P]anorama` / `[H]DR` / `[E]dit` /
`[C]ancel` / `[O]ther`. Un groupe `hdr` reçoit le suffixe `_HDR` et est fusionné
par `hdr.create_hdr()` (align_image_stack + enfuse « detailed » → AVIF).

## Tests

```bash
# Copie les données de test dans /tmp avec timestamp
./clone_test_folder.py

# Puis exécuter sort_photos.py sur le dossier créé
./sort_photos.py /tmp/2025-...-Test/

# Vérifier que le package s'importe correctement
python -c "from photo_sorter import sort_photos, load_configuration; print('OK')"
```

Données de test dans `testing/2025-03-15 - Test/` (fichiers CR3 + JPG réels).

## TODO

- [x] Réécrire `create_panorama.py` avec hsi (Hugin Python bindings) pour automatisation panorama (Linux x86_64)

- [x] **Couleurs fades — Phase 1 : Tone curve DCP + AVIF 10-bit (CR3 → AVIF)**
  Implémenté dans `dcp_profile.py` + `raw_processing.py`.

  **Pipeline linéaire** (conforme à la spécification DNG Adobe) :
  Quand des corrections DCP sont disponibles, rawpy produit une image **linéaire** 16-bit
  (`gamma=(1,1)`). La ProfileToneCurve est appliquée sur ces données linéaires conformément
  à la spec DNG (scene-referred → output-referred). L'encodage gamma BT.709 est ensuite
  ajouté explicitement pour la sortie AVIF display-ready.

  L'ancienne approche "DCP on BT.709" (courbe appliquée sur données gamma-encodées) causait
  une double-compression des hautes lumières et une saturation réduite, particulièrement
  visible sur les scènes à fort contraste (intérieur avec fenêtre lumineuse).

  - Sortie AVIF 10-bit via `imagecodecs.avif_encode(bitspersample=10, level=92)`
    pour préserver les nuances dans les dégradés (Pillow ne supporte que 8-bit)
  - Auto-détection du profil dans `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/`
  - Configurable via `dcp_profile` dans `[Processing]` du fichier de config (`none` pour désactiver)
  - Phase 1 (ToneCurve) appliquée aux AVIF uniquement — les TIFF panorama/HDR reçoivent
    Phase 2+3 (LUT + CCM) pour des couleurs correctes, mais pas la ToneCurve (données
    linéaires conservées pour le blending enblend)
  - Sans profil DCP : fallback AVIF 8-bit via Pillow avec BT.709 seul
  - HighlightMode.Blend pour une transition douce vers le blanc dans les hautes lumières

- [x] **Couleurs fades — Phase 2 : LookTable 3D du DCP**
  Implémenté dans `dcp_profile.py` : `parse_dcp_lookup_table()`, `apply_lookup_table_to_hsv()`,
  `apply_lookup_table()`. Le DCP contient une ProfileLookTableData (tag 50982) avec dimensions
  90×16×16 (hue × saturation × value) contenant 69 120 corrections HSV.

  **Ordre de traitement** (conforme à la spécification DNG Adobe) :
  1. rawpy → image **linéaire** 16-bit sRGB (`gamma=(1,1)`)
  2. **Phase 3** : matrice 3×3 correction ColorMatrix en sRGB linéaire → clip
  3. **Phase 2 (LookTable)** : sRGB linéaire → ProPhoto linéaire → HSV → LUT → RGB → sRGB linéaire
  4. **Phase 1 (ToneCurve)** : courbe S sur données **linéaires** (spec DNG)
  5. Encodage BT.709 gamma explicite (pour AVIF display-ready)
  Pour TIFF panorama/HDR : étapes 4-5 omises, données restent linéaires (meilleur blending).

  Le pipeline linéaire élimine les conversions BT.709 aller-retour (`_bt709_linearize()` /
  `_bt709_encode()`) lors du traitement : rawpy sort directement en linéaire, toutes les
  corrections opèrent nativement en linéaire, et le gamma est ajouté une seule fois à la fin.
  Paramètre `input_linear` dans `apply_lookup_table()` et `apply_color_correction()`.

  **Espace couleur ProPhoto** : le LUT du DCP est conçu pour l'espace ProPhoto/RIMM RGB
  (ISO 22028-2, point blanc D50). L'appliquer en sRGB produit des shifts de teinte incorrects
  car les mêmes couleurs spectrales ont des coordonnées HSV différentes en sRGB vs ProPhoto
  (ex: ambre = 27° en sRGB mais 43° en ProPhoto → le LUT accède aux mauvais voxels).
  La conversion sRGB↔ProPhoto utilise les matrices `M_SRGB_TO_PROPHOTO` / `M_PROPHOTO_TO_SRGB`
  calculées via Bradford CAT D65→D50.

  **Compensation de luminosité** : la désaturation HSV augmente la luminance RGB apparente
  (les couleurs désaturées → plus proches du gris → mean RGB plus élevé). On compense en
  normalisant le mean RGB après le LUT pour retrouver le niveau pré-LUT.

  Corrections retenues :
  - H correction : additive, **atténuée par h_scale** (défaut 0.0) — H_new = H + ΔH × h_scale
  - S correction : multiplicative — S_new = S × ΔS
  - V correction : multiplicative — V_new = V × ΔV

  **Atténuation de la correction de teinte (h_scale=0.0)** : les corrections ΔH du LUT sont
  calibrées pour le pipeline DNG complet (demosaïçage → ForwardMatrix → ProPhoto). Notre
  pipeline (rawpy → sRGB → ProPhoto) produit des teintes de départ légèrement différentes,
  ce qui fait que les corrections ΔH surcorrigent et augmentent le ratio G/R au lieu de
  l'améliorer. Tests empiriques sur 3 images (tungstène 3500-3700K) :
  - h_scale=1.0 (full H) : erreur G/R = 8-15% par rapport au JPEG caméra
  - h_scale=0.0 (skip H) : erreur G/R = 1-7% — **meilleur dans tous les cas**
  Les corrections S et V restent pleinement appliquées (améliorent le canal B et les
  ombres sans dégrader le ratio G/R).

  Tests unitaires : `photo_sorter/test_dcp_phase2.py` — RGB↔HSV round-trip, trilinéaire,
  LUT application, intégration Phase 1+2.

  **Profils DCP disponibles** dans `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/` :
  `Camera Standard.dcp`, `Camera Landscape.dcp`, `Camera Faithful.dcp`, `Camera Neutral.dcp`,
  `Camera Portrait.dcp`, `Camera Monochrome.dcp`.

- [x] **Sélection dynamique du profil DCP par PictureStyle EXIF**
  Chaque photo Canon a un tag EXIF `PictureStyle` (Standard, Portrait, Landscape, etc.).
  Le pipeline sélectionne maintenant automatiquement le profil DCP correspondant au style
  utilisé lors de la prise de vue, au lieu d'appliquer systématiquement "Camera Standard".
  - Nouveau champ `ImageFile.picture_style` extrait depuis EXIF dans `metadata.py`
  - `DcpProfileCache` dans `dcp_profile.py` : cache lazy-loading des profils par style
  - `resolve_dcp_path()` : mapping `PictureStyle → fichier DCP`, fallback sur "Standard"
  - `create_processed_images()` : 3 modes de fonctionnement :
    - Config explicite (`dcp_profile = /path`) → un profil pour toutes les images
    - Auto-detect (config vide) → **per-image par PictureStyle** (nouveau)
    - Pas de DCP disponible → BT.709 seul

- [x] **Couleurs fades — Phase 3 : Interpolation ColorMatrix par température de couleur**
  Implémenté dans `dcp_profile.py` + `raw_processing.py` + `metadata.py` + `constants.py` + `models.py`.

  **Problème** : rawpy/libraw utilise uniquement la ColorMatrix2 (calibrée D65, 6504K)
  quelle que soit la lumière de la scène. Sous éclairage tungstène (3000-4000K), la matrice
  correcte est une interpolation pondérée en mireds entre CM1 (Illuminant A, 2856K) et CM2.
  L'utilisation de CM2 seul introduit une teinte verte sous lumière chaude.

  **Approche** : Correction post-demosaicing via matrice 3×3 appliquée en sRGB linéaire.
  - `Correction = M_xyz2srgb × CM_interp⁻¹ × CM2 × M_srgb2xyz`
  - `CM_interp = w1 × CM1 + (1 - w1) × CM2` avec `w1` calculé sur l'échelle mireds
  - À D65 : `w1 ≈ 0 → Correction = Identity → None` (pas de correction pour lumière du jour)
  - À 3700K : correction R+3%, B+9%, G quasi inchangé (via termes hors-diagonale)

  **Ordre de traitement** dans `develop_raw()` :
  1. rawpy → **linéaire** 16-bit sRGB (`gamma=(1,1)`)
  2. Correction optique (distorsion, vignettage, TCA) en linéaire
  3. **Phase 3** : matrice 3×3 correction ColorMatrix en sRGB linéaire → clip [0, 1]
  4. **Phase 2** : sRGB linéaire → ProPhoto linéaire → HSV → LookTable → RGB → sRGB linéaire
  5. **Phase 1** : ToneCurve (courbe S sur données **linéaires**, spec DNG)
  6. Encodage BT.709 gamma explicite (AVIF uniquement, pas TIFF)

  **Fichiers modifiés** :
  - `constants.py` : `ILLUMINANT_TEMP` — dict code DNG → Kelvin (17→2856K, 21→6504K, etc.)
  - `models.py` : `ImageFile.color_temperature` (Optional[int])
  - `metadata.py` : extraction du tag EXIF `ColorTemperature`
  - `dcp_profile.py` : `_parse_rational_matrix()`, `parse_dcp_color_matrices()`,
    `compute_color_correction_matrix()`, `apply_color_correction()`.
    Matrices standard `M_SRGB_TO_XYZ` / `M_XYZ_TO_SRGB` (IEC 61966-2-1).
    Matrices `M_SRGB_TO_PROPHOTO` / `M_PROPHOTO_TO_SRGB` (via Bradford CAT D65→D50).
    `apply_lookup_table()` : accepte `color_correction_matrix` (Phase 3, en sRGB linéaire)
    et convertit en ProPhoto pour le LUT (Phase 2).
    `load_dcp_profile()` retourne 6-tuple `(tc, lut, cm1, cm2, temp1, temp2)`.
    `DcpProfileCache.get()` retourne le même 6-tuple.
  - `raw_processing.py` : `develop_raw()` et `create_avif_from_raw_file()` acceptent
    `color_correction_matrix`. `create_processed_images()` calcule la correction per-image
    dans Mode 1 (explicite) et Mode 2 (auto-detect per PictureStyle).

  **Note** : le DCP Canon EOS R7 ne contient pas de HueSatMap (tags 50937-50939)
  ni de ForwardMatrix illuminant-dépendante (FM1 = FM2). La correction ColorMatrix
  est donc la seule correction illuminant-dépendante disponible depuis le profil DCP.

- [x] **Fusion HDR (bracketing d'exposition) via enfuse**
  Implémenté dans `hdr.py` + intégré au pipeline.

  **Choix de conception : exposure fusion (enfuse), pas de radiance map / tone mapping.**
  enfuse mélange directement les vues développées via une pondération par pixel
  (bien-exposé + contraste local + saturation). Avantages retenus après comparaison
  avec Luminance HDR (Mantiuk06/08, Fattal) sur un bracket réel (contre-jour coucher
  de soleil, −3/0/+3 EV) :
  - Aucun artefact (pas de halo, pas de « HDR look »), résultat prévisible en batch.
  - **Aucun fichier `.hdr`/`.exr` produit ni conservé** (pas de gros intermédiaire).
  - Pas de courbe de réponse caméra à estimer, pas d'étape de tone mapping.
  - S'appuie sur `enfuse`/`align_image_stack` déjà présents (suite Hugin/enblend).
  Luminance HDR (Fattal « spectaculaire », Mantiuk08 « propre ») reste pertinent en
  option manuelle mais pas comme défaut automatique.

  **Points d'implémentation** :
  - `confirm_groups()` : nouveau choix `[H]DR` → `group_type='hdr'`, suffixe `_HDR`.
  - `create_processed_images()` : les groupes HDR sont développés en TIFF 16-bit
    **display-referred** (DCP ToneCurve + gamma BT.709) via
    `develop_raw(..., display_referred=True)`. **Crucial** : enfuse pondère par
    bien-exposé (gris moyen ≈ 0.5) → les TIFF *linéaires* du panorama fausseraient la
    pondération (c'est aussi ce qui faisait diverger Luminance HDR sur données linéaires).
  - `hdr.create_hdr()` : `align_image_stack -m -C -c 25 -t 2` puis
    `enfuse --exposure-weight=1.0 --saturation-weight=0.4 --contrast-weight=0.3
    --hard-mask --exposure-sigma=0.2 --depth=16` → TIFF fusionné → AVIF 8-bit.
    Intermédiaires (`aligned_*.tif`, TIFF fusionné) supprimés ; TIFF sources conservés.
  - `pipeline.py` : boucle d'assemblage HDR après les panoramas (avec fallback JPG).

  **Note** : la libraw du build Luminance HDR installé ne lit pas le CR3 (trop récent),
  et sa fusion « linear » sur TIFF linéaires diverge — raisons supplémentaires de
  préférer enfuse pour l'automatisation.

- [ ] **Format HDR compact (archivage du radiance map, optionnel)**
  Le pipeline enfuse ne produit pas de HDR flottant, donc rien à archiver. Si on
  ajoute un jour un mode « vrai HDR » (radiance map), éviter l'EXR non compressé
  (énorme). Options compactes, du plus au moins recommandé :
  - **OpenEXR + compression DWAB/DWAA** (lossy, 10–100× plus petit que PIZ/ZIP) —
    le « JPEG du HDR flottant », scene-referred.
  - **JPEG XL** (float HDR, très compact, lossy ou lossless).
  - **Radiance `.hdr` (RGBE)** : ~4 octets/pixel, plus léger mais précision moindre.
  - Pour un HDR *display-referred* (pas scene-referred) : **AVIF/HEIF 10–12 bits PQ/HLG**
    — le vrai « AVIF pour HDR », mais ce n'est pas un radiance map.

- [ ] **Mettre à jour `has_gps` après géotagging**
  - Dans la liste des fichiers, l'attribut `gps` reste à `no` après ajout des infos GPS
  - Devrait passer à `yes-gpx` pour indiquer que le GPS vient du fichier GPX

- [ ] **Réduire la verbosité de exiftool**
  - La sortie avec `-v1` (ligne 1198) reste trop verbeuse
  - Option : filtrer la sortie ou utiliser un niveau de verbosité différent

- [ ] **Option TIFF / EXR pour les fichiers intermédiaires panorama/HDR**
  Actuellement les fichiers intermédiaires sont en TIFF 16-bit entier non compressé.
  Hugin supporte aussi EXR (OpenEXR 16-bit half-float) en entrée.

  Comparaison des deux formats pour une image 32.5 MP (Canon EOS R7, 6960×4640×3) :

  |                     | TIFF 16-bit entier                               | EXR 16-bit half-float                                              |
  |---------------------|--------------------------------------------------|--------------------------------------------------------------------|
  | Précision           | 16 bits (65 536 niveaux, uniforme)               | ~11 bits effectifs (10 bits mantisse, 2 048 niveaux par magnitude) |
  | Taille par image    | ~194 MB (non compressé)                          | ~50-80 MB (compression PIZ lossless)                               |
  | Plage dynamique     | [0, 65535] fixe                                  | Flottant, peut dépasser 1.0 (utile pour merge HDR)                 |
  | Clipping highlights | Non (rawpy normalise dans la plage avant export) | Non (plage illimitée)                                              |

  **Avantages EXR** : 2-3× plus petit sur disque ; plage >1.0 utile pour le merge HDR
  (valeurs combinées de plusieurs expositions peuvent dépasser la plage d'une seule).
  **Avantages TIFF** : précision maximale (16 vs ~11 bits) ; pas de dépendance supplémentaire.
  **Pour les panoramas** (même exposition) : pas de highlights clippées dans les deux cas,
  la plage >1.0 d'EXR n'apporte rien. TIFF reste le meilleur choix en précision.
  **Pour le HDR** : EXR serait préférable grâce à la plage >1.0.

  Dépendance Python pour EXR : `imageio` ou `OpenEXR` + `Imath`.
