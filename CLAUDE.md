# CLAUDE.md

## Description du projet

Scripts Python pour organiser automatiquement les photos après téléchargement depuis un appareil photo numérique (Canon). Fonctionnalités principales :
- Déplacement des fichiers RAW dans un sous-dossier `RAW/`
- Groupement des images liées (panoramas, HDR, focus bracketing)
- Géolocalisation via fichiers GPX
- Génération d'images AVIF/TIFF depuis les RAW

La vitesse d'exécution n'est pas une priorité. Ce que l'on souhaite est principalement avoir un résultat de qualité.
Le script peut prendre plusieurs minutes ou heures ce n'est pas un problème.

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
│   ├── raw_processing.py           # develop_raw(), create_avif, create_tiff (utilise dcp_profile)
│   ├── panorama.py                 # Assemblage panorama via hsi (Hugin Python bindings)
│   ├── geotag.py                   # GPX parsing, géolocalisation
│   └── pipeline.py                 # sort_photos() orchestrateur
├── requirements.txt
├── sort_photo.conf                 # Configuration (dossiers par défaut)
├── testing/                        # Données de test (CR3, JPG, GPX)
└── CLAUDE.md
```

### Graphe de dépendances (pas de cycles)

```
constants    config (→ constants)    models     (feuilles)
     \         |                     /
      \        |                    /
       display  (→ models)
      /    |    \
file_ops  metadata  grouping  dcp_profile  geotag  panorama (→ display, metadata)
      \       |        |          |        /
       \      |        |          |       /
        \     |     raw_processing (→ dcp_profile, config, display)
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

### Outils système (requis)
- `exiftool` - extraction métadonnées EXIF + géotagging via GPX
- `hugin-tools` - assemblage panorama (fournit `hsi`, `cpfind`, `nona`) — Linux x86_64 uniquement
- `enblend` - fusion d'images pour panoramas

## Structures de données clés

```python
@dataclass
class AppConfig:
    root_folder: str = "~/Images"       # Dossier racine photo
    folder_for_raws: str = "RAW"        # Sous-dossier pour les RAW
    dark_frame_path: Optional[str] = None  # Chemin vers dark frame PGM
    dcp_profile_path: Optional[str] = None # Profil DCP pour tone curve (None = auto-detect)

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
```

## Constantes importantes

```python
RAW_EXTENSIONS = {'.crw', '.cr2', '.cr3', '.nef'}
RENDERED_EXTENSIONS = {'.jpg', '.jpeg', '.heif', '.avif'}
MIN_TIME_BETWEEN_PANOS = 15  # secondes entre photos d'un même groupe
```

## Pipeline de traitement

1. `scan_directory()` - Scan récursif des fichiers
2. `extract_image_metadata()` - Extraction EXIF (timestamp, exposition, GPS)
3. `geotag_pictures()` - Géolocalisation via GPX
4. `consolidate_images()` - Fusion RAW + versions traitées par basename
5. `identify_advanced_image_groups()` - Détection panoramas/HDR/focus
6. `confirm_groups()` - Confirmation interactive utilisateur
7. `move_raws_to_folder()` - Organisation des RAW
8. `create_processed_images()` - Génération AVIF/TIFF depuis RAW

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
  Implémenté dans `dcp_profile.py` + `raw_processing.py`. Approche "DCP on BT.709" :
  rawpy produit une image BT.709 en 16-bit, puis la courbe DCP "Camera Standard" est
  appliquée par-dessus comme rehaussement de contraste/couleur (pas en remplacement du gamma).
  Luminosité mesurée à ~97% du JPEG boîtier (mean=101.8 vs 104.6 sur IMG_2378).
  - Sortie AVIF 10-bit via `imagecodecs.avif_encode(bitspersample=10)` pour préserver les
    nuances dans les dégradés (Pillow ne supporte que 8-bit)
  - Auto-détection du profil dans `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/`
  - Configurable via `dcp_profile` dans `[Processing]` du fichier de config (`none` pour désactiver)
  - Appliqué aux AVIF uniquement (les TIFF panorama/HDR restent BT.709 16-bit pour le merging)
  - Sans profil DCP : fallback AVIF 8-bit via Pillow avec BT.709 seul

- [ ] **Couleurs fades — Phase 2 (si nécessaire) : LookTable 3D du DCP**
  Le DCP "Camera Standard" contient aussi une ProfileLookTableData (tag 50982) de dimensions
  90×16×16 (hue × sat × val) soit 69 120 floats de corrections HSV (HueShift, SatScale, ValScale).
  À implémenter dans `dcp_profile.py` si la phase 1 ne donne pas un résultat satisfaisant.
  Nécessite une interpolation trilinéaire en espace HSV.

  **Profils DCP disponibles** dans `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/` :
  `Camera Standard.dcp`, `Camera Landscape.dcp`, `Camera Faithful.dcp`, `Camera Neutral.dcp`,
  `Camera Portrait.dcp`, `Camera Monochrome.dcp`.

  **Approche rejetée** : `darktable-cli` / `rawtherapee-cli` (supportent DCP nativement mais
  ajoutent une dépendance système lourde et on perd le contrôle fin de rawpy).

- [ ] **Mettre à jour `has_gps` après géotagging**
  - Dans la liste des fichiers, l'attribut `gps` reste à `no` après ajout des infos GPS
  - Devrait passer à `yes-gpx` pour indiquer que le GPS vient du fichier GPX

- [ ] **Réduire la verbosité de exiftool**
  - La sortie avec `-v1` (ligne 1198) reste trop verbeuse
  - Option : filtrer la sortie ou utiliser un niveau de verbosité différent
