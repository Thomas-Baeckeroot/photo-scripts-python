#!/usr/bin/python
# -*- coding: utf-8 -*-

###############################################################################
# Aim of this script is to create automatically a panorama from a set of
# pictures (separated in a folder).
# Folder containing the pictures is the only parameter of this script.
#
# Note:
# This script is written
# in Python:		tested with 2.7
# for running in Linux:	Mint 16
# with Hungin:		v2011.4.0
###############################################################################
# Date		Author			Modification description
#------------------------------------------------------------------------------
# 2014-09-14	Thomas BAECKEROOT	CREATION
#		(tbaecker.spam@gmail.com)
#------------------------------------------------------------------------------
# 2016-09-13    Thomas BAECKEROOT       Init of git repo
#
###############################################################################
print("Starting automatic panorama creation...")

from sys import argv
from hsi import *         # load the module
import os

argList = list(argv)

# Constants:
# Folder to analyse / take picture from:
debug = True
panoFolder = argList[1]
if debug :
	print("######## Folder analysed / containing panorama = "+panoFolder+" ########")

if debug :
	print("######## create a new object of type Panorama ########")
pano=Panorama()

if debug :
        print("######## Add pictures from the given folder to the newly created .pto ########")
# Normalise photoFolder (add '/' if not already included at the end):
if panoFolder[len(panoFolder)-1] <> '/':
	panoFolder = panoFolder + '/'
# List files (images) in folder:
contenuDossier = os.listdir( panoFolder )
if debug :
        print("  # %d files found in panorama folder" % len(contenuDossier))
contenuDossier.sort()

if debug :
        print("  # Create the list of image files (JPG) and add them to panorama...")
fichiersImages = list()

for fileName in contenuDossier:
	if fileName[-4:] == '.JPG':
		fichiersImages.append(fileName)
		fullPathSingleImageFile = panoFolder+fileName
		if debug :
			print("######## Creating object image ########")
		img = SrcPanoImage(fullPathSingleImageFile)
		if debug :
			print("######## Linking object image to file '"+fullPathSingleImageFile+"' ########")
# Method inherited from BaseSrcPanoImage:
#  linkFilename(self, target)
#      linkFilename(BaseSrcPanoImage self, BaseSrcPanoImage target)

		img = img.linkFilename(img, pano)
		if debug :
			print("######## Adding image to panorama  ########")
		print("%r < addImage(%r) " % ( pano.addImage(img) , fileName ) )
	else:
		print("    ignored file: %r" % fileName )

if debug :
        print("  # Extract the name of the folder containing the images (without path, this will be used as prefix)...")
i = 0
for i in range(len(panoFolder)-2, -1, -1):
	if panoFolder[i] == '/':
		break
firstCharOfLastFolder = i+1
ptoBaseName = panoFolder[firstCharOfLastFolder:-1]
ptoFileName = ptoBaseName + "_00.pto"
ptoCleanFileName = ptoBaseName + "_01.pto"
parentOfPanoFolder = panoFolder[0:firstCharOfLastFolder-1]
# Save .pto file:
ofs=ofstream(panoFolder+ptoFileName)    # make a c++ std::ofstream to write to
pano.writeData(ofs)                     # write the modified panorama to that stream
ofs.flush()

if debug :
        print("######## Create the Control Points ########")
cpfindCommandLine = "cpfind --fullscale --celeste --multirow -o \""+panoFolder+ptoFileName+"\" \""+panoFolder+ptoFileName+"\""
## option "-n 4" can allow to paralellize on 4 CPUs, etc...
print( "-" * 80 )
print( cpfindCommandLine )
os.system( cpfindCommandLine )

if debug :
        print("######## Align images (= optimise) ########")
print( "\n" + "-"*80 + "\nautooptimiser..." )
os.system("autooptimiser -a -l -s -m -o \""+panoFolder+ptoFileName+"\" \""+panoFolder+ptoFileName+"\"")

######## Optimise: Strainghten, Centre, Autocrop and Calculate Optimal Size ########
print( "\n" + "-"*80 + "\npano_modify...")
os.system("pano_modify -s -c --canvas=AUTO --crop=AUTO -o \""+panoFolder+ptoFileName+"\" \""+panoFolder+ptoFileName+"\"")

######## ( File > Preferences > Control Points Editor > Correlation Threshold: lower to 0.4 ) ########
######## Edit > Fine-tune all Points ########

######## Drop the CPoints that have correlation (distance) < 0.6 ########

''' # Uncomment this part once upper Fine-tuning is implemented
######## Optimise (again) ########
print( "\n" + "-"*80 + "\nautooptimiser..." )
os.system("autooptimiser -a -l -s -m -o \""+panoFolder+ptoFileName+"\" \""+panoFolder+ptoFileName+"\"")

######## Optimiser: Redresser, Centrer, calculer la taille optimale ########
print( "\n" + "-"*80 + "\npano_modify...")
os.system("pano_modify -s -c --canvas=AUTO --crop=AUTO -o \""+panoFolder+ptoFileName+"\" \""+panoFolder+ptoFileName+"\"")
'''

######## Supprimer les points non significatifs  ########
print( "\n" + "-"*80 + "\ncpclean..." )
os.system("cpclean -o \""+panoFolder+ptoCleanFileName+"\" \""+panoFolder+ptoFileName+"\"")

######## Optimiser les alignements d'images (2nde passe apres alignement initial) ########
print( "\n" + "-"*80 + "\nautooptimiser..." )
os.system("autooptimiser -a -l -s -m -o \""+panoFolder+ptoCleanFileName+"\" \""+panoFolder+ptoCleanFileName+"\"")

######## Optimise (again): Strainghten, Centre, Autocrop and Calculate Optimal Size ########
print( "\n" + "-"*80 + "\npano_modify...")
os.system("pano_modify -s -c --canvas=AUTO --crop=AUTO -o \""+panoFolder+ptoCleanFileName+"\" \""+panoFolder+ptoCleanFileName+"\"")

'''
######## Compute final picture (reference method through makefile) ########
# Note: does NOT support JPEG output
#create stitching makefile
pto2mkCommandLine = "pto2mk -o \""+panoFolder+ptoBaseName+"_01.mk\" -p \""+panoFolder+ptoBaseName+"_01\" \""+panoFolder+ptoCleanFileName+"\""
print( "-" * 80 )
print( pto2mkCommandLine )
os.system( pto2mkCommandLine )

#create the final panorama
makeCommandLine = "make -f \""+panoFolder+ptoBaseName+"_01.mk\" all clean"
print( "-" * 80 )
print( makeCommandLine )
os.system( makeCommandLine )

######## Convert output tiff to jpg in parent folder ########
'''

######## Compute final picture (alternative method: nona + enblend) ########
# Following instructions from file:///usr/share/hugin/xrc/data/help_en_EN/Panorama_scripting_in_a_nutshell.html
nonaCommandLine = "nona -m TIFF_m -o \""+panoFolder+ptoBaseName+"_01_\" \""+panoFolder+ptoCleanFileName+"\""
print( "-" * 80 )
print( nonaCommandLine )
os.system( nonaCommandLine )

filesCreatedByNona = list()
enblendCommandLine = "enblend -o \""+parentOfPanoFolder+"/"+ptoBaseName+"_01.jpg\""
for i in range( len(fichiersImages) ):
	fileCreatedByNona = panoFolder+ptoBaseName+"_01_%04d.tif" % i
	filesCreatedByNona.append( fileCreatedByNona )
	enblendCommandLine = enblendCommandLine + " \""+fileCreatedByNona+"\""

print( "-" * 80 )
print( enblendCommandLine )
os.system( enblendCommandLine )


######## Remove intermediary/temporary tiff files ########
print("Deleting intermediary files: %r" % filesCreatedByNona)
for i in range( len(filesCreatedByNona) ):
	os.remove( filesCreatedByNona[i] )

print("Terminated.")
exit(0)

# Exit codes:
#   0	Normal exit
