# CLAUDE.md

## Description du projet

Scripts Python pour organiser automatiquement les photos après téléchargement depuis un appareil photo numérique (Canon). Fonctionnalités principales :
- Déplacement des fichiers RAW dans un sous-dossier `RAW/`
- Groupement des images liées (panoramas, HDR, focus bracketing)
- Géolocalisation via fichiers GPX
- Génération d'images TIFF 16-bit depuis les RAW pour traitement panorama

## Commandes principales

```bash
# Trier un dossier photo
./sort_photos.py /chemin/vers/dossier

# Préparer un environnement de test
./test_sort_photo.py
```

## Structure du projet

| Fichier | Description |
|---------|-------------|
| `sort_photos.py` | Script principal (point d'entrée) |
| `sort_photos_from_root_folder.py` | Traitement par lot |
| `sort_photo.conf` | Configuration (dossiers par défaut) |
| `test_sort_photo.py` | Préparation des tests |
| `testing/` | Données de test (CR3, JPG, GPX) |

## Conventions de code

- **Langue** : Code, variables et commentaires en anglais (interaction développeur en français)
- **Style** : Suivre PEP 8 et les conventions Python recommandées
- **Constantes** : MAJUSCULES (`RAW_EXTENSIONS`, `MIN_TIME_BETWEEN_PANOS`)
- **Fonctions** : snake_case (`scan_directory()`, `extract_image_metadata()`)
- **Classes** : PascalCase (`ImageFile`, `GroupInfo`)
- **Dataclasses** pour les structures de données principales
- **Logging** extensif avec niveaux DEBUG/INFO/WARNING/ERROR
- Caractères box-drawing pour l'affichage console (├─ │ ┌─)

## Dépendances

### Python (voir `requirements.txt`)
- `Pillow` (PIL) - manipulation d'images (AVIF, TIFF, JPEG)
- `rawpy` - binding Python pour libraw (dématriçage RAW)
- `numpy` - manipulation de tableaux (sortie rawpy → PIL)

### Outils système (requis)
- `exiftool` - extraction métadonnées EXIF

## Structures de données clés

```python
@dataclass
class ImageFile:
    basename: str                    # Nom sans extension
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
3. `consolidate_images()` - Fusion RAW + versions traitées par basename
4. `identify_advanced_image_groups()` - Détection panoramas/HDR/focus
5. `confirm_groups()` - Confirmation interactive utilisateur
6. `move_raws_to_folder()` - Organisation des RAW
7. `create_processed_images()` - Génération TIFF depuis RAW
8. `geotag_pictures()` - Géolocalisation via GPX

## Tests

```bash
# Copie les données de test dans /tmp avec timestamp
./test_sort_photo.py

# Puis exécuter sort_photos.py sur le dossier créé
./sort_photos.py /tmp/2025-...-Test/
```

Données de test dans `testing/2025-03-15 - Test/` (fichiers CR3 + JPG réels).

## TODO

- [ ] Les AVIF générés depuis les RAW sont trop sombres (investiguer `no_auto_bright` et/ou appliquer une courbe gamma dans `develop_raw()`)
- [ ] La création des fichiers TIFF 16-bit pour les panoramas a échoué (investiguer `develop_raw()` avec `output_bps=16` / `output_format="tiff"`)
- [ ] Appliquer les profils DCP Canon (Camera Standard) pour un rendu plus fidèle aux couleurs du boîtier. Profils disponibles dans `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/`. Nécessite un parser DCP Python (matrice couleur + tone curve + look table).
