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
    ├── IMG_0002.JPG
    ├── IMG_0003.RAW
    ├── IMG_0003.JPG
    ├── IMG_1001.CR2   ╮ raws alone
    ├── IMG_1002.CR2   ╯ (no associated jpeg or avif)
    ├── IMG_0010.RAW   ╮
    ├── IMG_0010.JPG   |
    ├── IMG_0011.RAW   |
    ├── IMG_0011.JPG   | panorama
    ├── IMG_0012.RAW   |
    ├── IMG_0012.JPG   |
    ├── IMG_0013.RAW   |
    └── IMG_0013.JPG   ╯
```

to organise it as:

```
2025/
└── 2025-03-10/
    ├── RAWs/
    │   ├── IMG_0001.RAW
    │   ├── IMG_0002.RAW
    │   ├── IMG_0003.RAW
    │   ├── IMG_0010.RAW
    │   ├── IMG_0011.RAW
    │   ├── IMG_0012.RAW
    │   ├── IMG_0013.RAW
    │   ├── IMG_1001.CR2
    │   └── IMG_1002.CR2
    ├── IMG_0010-13_4/         ╮
    │   ├── IMG_0010.JPG       | (set of pictures / panorama)
    │   ├── IMG_0011.JPG       |
    │   ├── IMG_0012.JPG       |
    │   └── IMG_0013.JPG       ╯
    ├── IMG_0001.JPG
    ├── IMG_0002.JPG
    ├── IMG_0003.JPG
    ├── IMG_1001.avif   (new)
    └── IMG_1002.avif   (new)
```

Panorama and set of pictures in on folder.  
Raw files in "RAWs" folder.  
If a raw file does not have a "small" version, then one is created.  
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
    │   ├── IMG_1001.CR2
    │   └── IMG_1002.CR2
    ├── IMG_0010-13_4/         ╮
    │   ├── RAWs/              |
    │   │   ├── IMG_0010.RAW   | (set of pictures / panorama)
    │   │   ├── IMG_0011.RAW   |
    │   │   ├── IMG_0012.RAW   |
    │   │   └── IMG_0013.RAW   |
    │   ├── IMG_0010.JPG       |
    │   ├── IMG_0011.JPG       |
    │   ├── IMG_0012.JPG       |
    │   └── IMG_0013.JPG       ╯
    ├── IMG_0001.JPG
    ├── IMG_0002.JPG
    ├── IMG_0003.JPG
    ├── IMG_1001.avif   (new)
    └── IMG_1002.avif   (new)
```
---

### create_panorama.py

Automates assembly of panorama from files in a folder.
This script performs some actions that are generally done manually in Hugin to create a panorama.
Result is without warranty but at least should give a decent draft.

<u>Note</u>: It has been broken by some hugin update around 2020/2022, I'll fix it some day... 
