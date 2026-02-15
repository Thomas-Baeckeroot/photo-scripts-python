# CLAUDE.md

## Description du projet

Scripts Python pour organiser automatiquement les photos après téléchargement depuis un appareil photo numérique (Canon). Fonctionnalités principales :
- Déplacement des fichiers RAW dans un sous-dossier `RAW/`
- Groupement des images liées (panoramas, HDR, focus bracketing)
- Géolocalisation via fichiers GPX
- Génération d'images AVIF/TIFF depuis les RAW

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
├── create_panorama.py              # CLI : placeholder (à réécrire avec hsi)
├── clone_test_folder.py            # Préparation des données de test
├── photo_sorter/                   # Package principal
│   ├── __init__.py                 # Exporte sort_photos(), load_configuration(), AppConfig
│   ├── constants.py                # RAW_EXTENSIONS, RENDERED_EXTENSIONS, etc.
│   ├── config.py                   # AppConfig dataclass + load_configuration()
│   ├── models.py                   # ImageFile, GroupInfo dataclasses
│   ├── display.py                  # log_files(), log_title() (box-drawing)
│   ├── file_ops.py                 # scan_directory(), consolidate_images(), move_raws_to_folder()
│   ├── metadata.py                 # EXIF via exiftool
│   ├── grouping.py                 # Détection panoramas/HDR + confirmation interactive
│   ├── raw_processing.py           # develop_raw(), create_avif, create_tiff
│   ├── geotag.py                   # GPX parsing, géolocalisation
│   └── pipeline.py                 # sort_photos() orchestrateur
├── requirements.txt
├── sort_photo.conf                 # Configuration (dossiers par défaut)
├── testing/                        # Données de test (CR3, JPG, GPX)
└── CLAUDE.md
```

### Graphe de dépendances (pas de cycles)

```
constants    config    models     (feuilles, pas d'import interne)
     \         |         /
      \        |        /
       display  (→ models)
      /    |    \
file_ops  metadata  grouping  raw_processing  geotag
      \       |        |           |          /
       \      |        |           |         /
        pipeline  (→ tous les modules domaine)
            |
        __init__  (→ config, pipeline)
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
- `Pillow` (PIL) - manipulation d'images (AVIF, JPEG)
- `rawpy` - binding Python pour libraw (dématriçage RAW)
- `numpy` - manipulation de tableaux (sortie rawpy → PIL)
- `tifffile` - écriture TIFF 16-bit RGB (Pillow ne gère pas uint16 RGB)

### Outils système (requis)
- `exiftool` - extraction métadonnées EXIF

## Structures de données clés

```python
@dataclass
class AppConfig:
    root_folder: str = "~/Images"       # Dossier racine photo
    folder_for_raws: str = "RAW"        # Sous-dossier pour les RAW
    dark_frame_path: Optional[str] = None  # Chemin vers dark frame PGM

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

- [ ] Les AVIF générés depuis les RAW sont trop sombres (investiguer `no_auto_bright` et/ou appliquer une courbe gamma dans `develop_raw()`)
- [ ] La création des fichiers TIFF 16-bit pour les panoramas a échoué (investiguer `develop_raw()` avec `output_bps=16` / `output_format="tiff"`)
- [ ] Appliquer les profils DCP Canon (Camera Standard) pour un rendu plus fidèle aux couleurs du boîtier. Profils disponibles dans `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/`. Nécessite un parser DCP Python (matrice couleur + tone curve + look table).
- [ ] Réécrire `create_panorama.py` avec hsi (Hugin Python bindings) pour automatisation panorama (Linux x86_64)
