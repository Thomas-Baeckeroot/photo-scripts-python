#!/usr/bin/python3
# -*- coding: utf-8 -*-

# #############################################################################
# Aim of this script is to create automatically a panorama from a set of
# pictures (separated in a folder).
# Folder containing the pictures is the only parameter of this script.
#
# Note:
# This script is written
# in Python:		tested with 2.7
# for running in Linux:	Mint 16
# with Hungin:		v2011.4.0
# #############################################################################
# Date		Author			Modification description
# -----------------------------------------------------------------------------
# 2014-09-14	Thomas BAECKEROOT	CREATION
# 		(tbaecker.spam@gmail.com)
# -----------------------------------------------------------------------------
#
# #############################################################################
import logging
import os
from sys import argv
from hsi import *  # load 'hsi' module - if failing here, execute 'sudo apt install -y python-hsi'
# (if upper fails, possibly try: 'sudo apt -y install python3-pip python3-opencv && pip3 install hsi')

logging.basicConfig(
    # filename=utils.get_home() + "/tmp/logfile.log",
    level=logging.DEBUG,
    format='%(asctime)s\t%(levelname)s\t%(name)s\t%(message)s')
log = logging.getLogger("sort_photos.py")


log.info("Starting automatic panorama creation...")

argList = list(argv)

# Constants:
# Folder to analyse / take picture from:
debug = True
panoFolder = argList[1]
if debug:
    log.info("######## Folder analysed / containing panorama = " + panoFolder + " ########")

if debug:
    log.info("######## create a new object of type Panorama ########")
pano = Panorama()

if debug:
    log.info("######## Add pictures from the given folder to the newly created .pto ########")
# Normalise photoFolder (add '/' if not already included at the end):
if panoFolder[len(panoFolder) - 1] != '/':
    panoFolder = panoFolder + '/'
# List files (images) in folder:
contenuDossier = os.listdir(panoFolder)
if debug:
    log.info("  # %d files found in panorama folder" % len(contenuDossier))
contenuDossier.sort()

if debug:
    log.info("  # Create the list of image files (JPG) and add them to panorama...")
fichiersImages = list()

for fileName in contenuDossier:
    if fileName[-4:] == '.JPG':
        fichiersImages.append(fileName)
        fullPathSingleImageFile = panoFolder + fileName
        if debug:
            log.info("######## Creating object image ########")
        img = SrcPanoImage()
        img.setFilename(fullPathSingleImageFile)

        if debug:
            log.info("######## Adding image to panorama  ########")
        log.info("%r < addImage(%r) " % (pano.addImage(img), fileName))
    else:
        log.info("    ignored file: %r" % fileName)

if debug:
    log.info("  # Extract the name of the folder containing the images (without path, this will be used as prefix)...")
i = 0
for i in range(len(panoFolder) - 2, -1, -1):
    if panoFolder[i] == '/':
        break
firstCharOfLastFolder = i + 1
ptoBaseName = panoFolder[firstCharOfLastFolder:-1]
ptoFileName = ptoBaseName + "_00.pto"
ptoCleanFileName = ptoBaseName + "_01.pto"
parentOfPanoFolder = panoFolder[0:firstCharOfLastFolder - 1]
# Save .pto file:
ofs = ofstream(panoFolder + ptoFileName)  # make a c++ std::ofstream to write to
pano.writeData(ofs)  # write the modified panorama to that stream
ofs.flush()

if debug:
    log.info("######## Create the Control Points ########")
cpfindCommandLine = "cpfind --fullscale --celeste --multirow -o \"" + panoFolder + ptoFileName + "\" \"" \
                    + panoFolder + ptoFileName + "\""
# option "-n 4" can allow to paralellize on 4 CPUs, etc...
log.info("-" * 80)
log.info(cpfindCommandLine)
os.system(cpfindCommandLine)

if debug:
    log.info("######## Align images (= optimise) ########")
log.info("\n" + "-" * 80 + "\nautooptimiser...")
os.system("autooptimiser -a -l -s -m -o \"" + panoFolder + ptoFileName + "\" \"" + panoFolder + ptoFileName + "\"")

# ####### Optimise: Strainghten, Centre, Autocrop and Calculate Optimal Size ########
log.info("\n" + "-" * 80 + "\npano_modify... #1")
os.system(
    "pano_modify --straighten --center --canvas=AUTO --crop=AUTO -o \"" + panoFolder + ptoFileName + "\" \""
    + panoFolder + ptoFileName + "\"")

# ####### ( File > Preferences > Control Points Editor > Correlation Threshold: lower to 0.4 ) ########
# ####### Edit > Fine-tune all Points ########

# ####### Drop the CPoints that have correlation (distance) < 0.6 ########

''' # Uncomment this part once upper Fine-tuning is implemented
######## Optimise (again) ########
log.info( "\n" + "-"*80 + "\nautooptimiser... #1.1" )
os.system("autooptimiser -a -l -s -m -o \""+panoFolder+ptoFileName+"\" \""+panoFolder+ptoFileName+"\"")

######## Optimiser: Redresser, Centrer, calculer la taille optimale ########
log.info( "\n" + "-"*80 + "\npano_modify... #1.2")
os.system("pano_modify -s -c --canvas=AUTO --crop=AUTO -o \"" + panoFolder + ptoFileName + "\" \""
          + panoFolder + ptoFileName + "\"")
'''

# ####### Supprimer les points non significatifs  ########
log.info("\n" + "-" * 80 + "\ncpclean...")
os.system("cpclean -o \"" + panoFolder + ptoCleanFileName + "\" \"" + panoFolder + ptoFileName + "\"")

# ####### Optimiser les alignements d'images (2nde passe apres alignement initial) ########
log.info("\n" + "-" * 80 + "\nautooptimiser...")
os.system(
    "autooptimiser -a -l -s -m -o \"" + panoFolder + ptoCleanFileName + "\" \"" + panoFolder + ptoCleanFileName + "\"")

# ####### Optimise (again): Strainghten, Centre, Autocrop and Calculate Optimal Size ########
log.info("\n" + "-" * 80 + "\npano_modify... #2")
os.system(
    "pano_modify --straighten --center --canvas=AUTO --crop=AUTO -o \"" + panoFolder + ptoCleanFileName + "\" \""
    + panoFolder + ptoCleanFileName + "\"")

'''
######## Compute final picture (reference method through makefile) ########
# Note: does NOT support JPEG output
# create stitching makefile
pto2mkCommandLine = "pto2mk -o \"" + panoFolder + ptoBaseName + "_01.mk\" -p \""\
                    + panoFolder + ptoBaseName + "_01\" \"" + panoFolder + ptoCleanFileName + "\""
log.info( "-" * 80 )
log.info( pto2mkCommandLine )
os.system( pto2mkCommandLine )

#create the final panorama
makeCommandLine = "make -f \""+panoFolder+ptoBaseName+"_01.mk\" all clean"
log.info( "-" * 80 )
log.info( makeCommandLine )
os.system( makeCommandLine )

######## Convert output tiff to jpg in parent folder ########
'''

# ####### Compute final picture (alternative method: nona + enblend) ########
# Following instructions from file:///usr/share/hugin/xrc/data/help_en_EN/Panorama_scripting_in_a_nutshell.html
nonaCommandLine = "nona -m TIFF_m -o \"" + panoFolder + ptoBaseName + "_01_\" \"" + panoFolder + ptoCleanFileName + "\""
log.info("-" * 80)
log.info(nonaCommandLine)
os.system(nonaCommandLine)

filesCreatedByNona = list()
enblendCommandLine = "enblend -o \"" + parentOfPanoFolder + "/" + ptoBaseName + "_01.jpg\""
for i in range(len(fichiersImages)):
    fileCreatedByNona = panoFolder + ptoBaseName + "_01_%04d.tif" % i
    filesCreatedByNona.append(fileCreatedByNona)
    enblendCommandLine = enblendCommandLine + " \"" + fileCreatedByNona + "\""

log.info("-" * 80)
log.info(enblendCommandLine)
os.system(enblendCommandLine)

# ####### Remove intermediary/temporary tiff files ########
log.info("Deleting intermediary files: %r" % filesCreatedByNona)
for i in range(len(filesCreatedByNona)):
    os.remove(filesCreatedByNona[i])

log.info("Terminated.")
exit(0)

# Exit codes:
#   0	Normal exit
