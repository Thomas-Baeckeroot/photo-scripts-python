# Scripts to automate picture/photo management

Couple of scripts (in Python) to manage pictures:

### sort_photos.py

To be launched (manually) right after having downloaded pictures from digital camera.
It aims to organize raw files in a separated folder, group set of pictures in one folder,
geo-tags pictures if a .gpx file is given.

Idea is to start from files as downloaded from the camera. From DigiKam, I got:
```
2025/
└── 2025-03-10/
    ├── IMG_0001.RAW
    ├── IMG_0001.JPG
    ├── IMG_0002.RAW
    ├── IMG_0002.jpeg
    ├── img_0003.RAW
    ├── img_0003.avif
    ├── IMG_0011.CR2   ╮ raws alone
    ├── IMG_0012.CR3   ╯ (no associated jpeg or avif)
    ├── IMG_0020.RAW   ╮
    ├── IMG_0020.JPG   |
    ├── IMG_0021.RAW   |
    ├── IMG_0021.JPG   | panorama #1
    ├── IMG_0022.RAW   |
    ├── IMG_0022.JPG   |
    ├── IMG_0023.RAW   |
    ├── IMG_0023.JPG   ╯
    ├── IMG_0024.RAW   ╮
    ├── IMG_0025.RAW   | panorama #2 (or HDR set)
    └── IMG_0026.RAW   ╯
```

to organise it as:

```
2025/
└── 2025-03-10/
    ├── RAWs/
    │   ├── IMG_0001.RAW
    │   ├── IMG_0002.RAW
    │   ├── IMG_0003.RAW
    │   ├── IMG_0011.CR2
    │   ├── IMG_0012.CR3
    │   ├── IMG_0020.RAW
    │   ├── IMG_0021.RAW
    │   ├── IMG_0022.RAW
    │   ├── IMG_0023.RAW
    │   ├── IMG_0024.RAW
    │   ├── IMG_0025.RAW
    │   └── IMG_0026.RAW
    │   
    ├── IMG_0020-3_4/          ╮
    │   ├── IMG_0020.JPG       | (set of pictures / panorama #1)
    │   ├── IMG_0021.JPG       |
    │   ├── IMG_0022.JPG       |
    │   └── IMG_0023.JPG       ╯
    │   
    ├── IMG_0024-6_3/                ╮
    │   ├── IMG_0024_temp.PNG        | (set of generated pictures for panorama #2.
    │   ├── IMG_0025_temp.PNG        |  As of April 2025, Hugin does not manage
    │   ├── IMG_0026_temp.PNG        |  correctly RAWs as input files.)
    │   ├── generate_IMG_0024-6_3.sh |
    │   └── IMG_0024-6_3.pto         ╯
    │   
    ├── IMG_0001.JPG
    ├── IMG_0002.jpeg
    ├── IMG_0003.avif
    ├── IMG_0011.avif      (new)
    ├── IMG_0012.avif      (new)
    ├── IMG_0020-3_4.avif  (new, far from implemented)
    └── IMG_0024-6_3.avif  (new, far from implemented)
```

Panorama and set of pictures moved to a dedicated folder.  
Raw files in "RAWs" folder.  
If a raw file does not have a "small" version, then one is created (avif or jpeg).  
If a .dxf file (GPS track) is found, JPG and avif are localised if no GPS info there.

---
I'm wondering if the below would be better?
```
2025/
└── 2025-03-10/
    ├── RAWs/
    │   ├── IMG_0001.RAW
    │   ├── IMG_0002.RAW
    │   ├── IMG_0003.RAW
    │   ├── IMG_0011.CR2
    │   └── IMG_0012.CR3
    ├── IMG_0020-3_4/          ╮
    │   ├── RAWs/              |
    │   │   ├── IMG_0020.RAW   | (set of pictures / panorama)
    │   │   ├── IMG_0021.RAW   |
    │   │   ├── IMG_0022.RAW   |
    │   │   └── IMG_0023.RAW   |
    │   ├── IMG_0020.JPG       |
    │   ├── IMG_0021.JPG       |
    │   ├── IMG_0022.JPG       |
    │   └── IMG_0023.JPG       ╯
    ├── IMG_0001.JPG
    ├── IMG_0002.JPG
    ├── IMG_0003.JPG
    ├── IMG_0011.avif   (new)
    └── IMG_0012.avif   (new)
```
---

### create_panorama.py

Automates assembly of panorama from files in a folder.
This script performs some actions that are generally done manually in Hugin to create a panorama.
Result is without warranty but at least should give a decent draft.

<u>Note</u>: It has been broken by some hugin update around 2020/2022, I'll fix it some day... 
