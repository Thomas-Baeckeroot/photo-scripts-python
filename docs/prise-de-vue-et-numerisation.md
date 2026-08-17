# Prise de vue & numérisation — guide qualité

Notes de stratégie de capture pour obtenir la **meilleure qualité en entrée**
du pipeline `photo_sorter` (Canon EOS R7). La vitesse n'est pas une priorité :
on cherche le résultat optimal.

---

## 1. Faut-il bracketer ? (scènes contrastées / HDR)

Le R7 à **ISO 100** offre ~**11 stops** de plage dynamique utile. Beaucoup de
scènes « qui semblent HDR » tiennent dans **un seul RAW** bien exposé, dont le
pipeline DCP récupère proprement les ombres. Un RAW unique évite tout problème
d'alignement et de *ghosting* (sujets qui bougent) — c'est souvent l'option la
**plus qualitative** quand la scène rentre.

- **Ne bracketer que si l'écart de la scène dépasse ~11 EV** : ciel brillant +
  ombres profondes, intérieur avec fenêtre, contre-jour avec le soleil dans le
  cadre.
- RAW unique : viser un **ETTR** (Expose To The Right) — exposer le plus à
  droite possible sans cramer les hautes lumières importantes (maximise le
  rapport signal/bruit dans les ombres).

### Si bracketing : 3 principes

1. La vue **la plus sombre** protège les **hautes lumières** (rien de cramé).
2. La vue **la plus claire** sort les **ombres du bruit**.
3. Les vues doivent être **alignables** (scène statique, capture rapide).

### Espacement et nombre de vues

| Scène | Recommandation | Pourquoi |
|---|---|---|
| Écart modéré (ciel + sol) | **3 vues à 2 EV** (−2 / 0 / +2) | 4 EV ajoutés, transitions lisses |
| Écart extrême (soleil dans le cadre, intérieur + fenêtre) | **5 vues à 2 EV** (−4…+4) | large couverture **et** transitions propres |

Pour **enfuse** (fusion d'exposition, cf. `photo_sorter/hdr.py`), plus de vues
avec des écarts plus petits = pondération « bien-exposé » mieux échantillonnée.
À nombre de vues égal, **2 EV > 3 EV**. Un bracketing **−3/0/+3** fonctionne mais
laisse des zones de transition un peu plus bruitées qu'un espacement de 2 EV.

### Pièges de capture

- ⚠️ **Ouverture qui change** entre les vues → profondeur de champ / parallaxe
  incohérentes. → Bracketer en mode **Av** ou **M** pour ne faire varier **que la
  vitesse** (ouverture = DoF constante).
- ⚠️ **ISO qui change** → bruit supplémentaire dans la vue qui sert aux ombres.
  → **ISO 100 fixe** sur toute la série (plage dynamique maximale du R7).
- **Trépied**, **rafale** (AEB continu, capture rapide), **WB fixe**,
  **retardateur 2 s / obturateur électronique** contre le flou de bougé.

---

## 2. Numérisation de négatifs (banc de repro)

### Le bracketing est en général **inutile** pour un négatif

Un négatif développé a une **plage de densité modérée** (~7–8 stops utiles — les
négatifs sont peu contrastés par nature). Un **seul RAW R7 à ISO 100** capture
largement toute sa plage. Le bracketing n'apporte alors quasi rien et ajoute des
risques (micro-décalages, poussières qui bougent).

- **Négatif (N&B ou couleur)** → **1 seul RAW**, pas de bracketing.
- **Diapositive / positif** (Velvia, Kodachrome…) → densité bien plus forte
  (Dmax ~3,5 = 11–13 stops, ombres très denses). **Là seulement**, un bracketing
  **2 EV** (ex. −2/0/+2) + fusion `create_hdr()` peut aider à fouiller les ombres.

### ⚠️ Ouverture : « petite » ≠ « plus net »

Sur un film **plat et parallèle au capteur**, la profondeur de champ n'a **aucune
importance** — fermer le diaphragme ne rajoute pas de netteté utile et la
**détruit par diffraction**. Le R7 a de petits photosites (~3,2 µm), la
diffraction frappe tôt :

| Ouverture marquée | Netteté R7 |
|---|---|
| f/4 – f/5,6 | **optimum** (sweet spot MTF) |
| f/8 | encore bon |
| f/11 | ramollit |
| f/16 – f/22 | nettement mou (diffraction) |

**Piège macro** : à fort grandissement, l'ouverture *effective* est plus fermée
que la valeur marquée. Pour du 35 mm rempli sur l'APS-C (grandissement ≈ 0,6) :
`N_eff ≈ N_marqué × 1,6`.
- f/5,6 marqué → ~f/9 effectif ✅ (idéal)
- f/8 marqué → ~f/13 effectif (déjà en perte)

→ **Viser f/5,6 marqué** (voire f/5), pas plus fermé. Vérifier empiriquement :
test f/4 / 5,6 / 8 / 11 sur le même négatif, comparaison à 100 % sur le grain.

### Les vrais leviers qualité (bien plus que le bracketing)

Par ordre d'impact :

1. **Rétro-éclairage à haut IRC (CRI 95+)**, spectre continu — critique pour la
   couleur d'un négatif (panneau LED CRI≥95 ou source dédiée).
2. **Planéité du film** (porte-film / verre ANR) — un film gondolé = mise au
   point inégale.
3. **Alignement capteur ∥ film** (banc à niveau) — sinon un côté est flou.
4. **Mise au point manuelle** en live view zoomé **sur le grain**.
5. **Propreté** : soufflette, antistatique (la poussière est l'ennemi n°1).
6. **RAW 14 bits → TIFF 16 bits** pour l'inversion (on étire énormément la
   tonalité d'un négatif ; le 16 bits évite le banding).
7. **Anti-vibration** : obturateur électronique + retardateur 2 s (même sur banc).
8. **Workflow d'inversion** (le plus dur pour la couleur, à cause du masque
   orange) : darktable **negadoctor** (gratuit, excellent) ou Negative Lab Pro.
   Photographier une zone de **base du film** (l'orange inter-vues) comme
   référence.

### Réglages recommandés

- **Négatif** : 1 RAW, **ISO 100**, **f/5,6 marqué**, mise au point manuelle
  zoomée, obturateur électronique + retardateur, backlight CRI 95+, film maintenu
  plat. Développer en **TIFF 16 bits** puis inverser (negadoctor). **Pas de
  bracketing.**
- **Diapositive dense** : même réglage, mais possibilité d'un bracketing **2 EV**
  (0/+2 ou −2/0/+2) passé par `create_hdr()` (enfuse) pour ouvrir les ombres.

---

**En résumé** : garder l'artillerie HDR/enfuse pour les **diapositives** et les
scènes très contrastées. Pour les **négatifs**, la qualité vient de **f/5,6, un
éclairage CRI élevé, la planéité, la mise au point et l'inversion 16 bits** — pas
du bracketing ni d'une ouverture fermée.
