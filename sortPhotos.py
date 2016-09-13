#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
from sys import argv
from PIL import Image
from PIL.ExifTags import TAGS
import subprocess
import glob
import shutil





def main(photoFolder):

	print("    ______________________________________________________________________")
	print("===[ Recherche de fichiers Bruts puis déplacement dans le dossier adéquat ]====")

	def get_exif(fileName):
		ret = {}
		i = Image.open(fileName)
		info = i._getexif()
		for tag, value in info.items():
			decoded = TAGS.get(tag, tag)
			ret[decoded] = value
		return ret

	def get_ExposureTime_from_exif_in_s(fileName):
		exifOfFileName = get_exif( fileName )
		expTimeVect = exifOfFileName['ExposureTime']
		ret = expTimeVect[0] / float(expTimeVect[1])
		return ret

	# Obtenir la liste des images brutes:
	contenuDossier = os.listdir( photoFolder )
	print( "%d fichiers trouves dans le dossier %r" % (len(contenuDossier), photoFolder) )

	# Normaliser photoFolder (ajout de '/' si non present a la fin):
	if photoFolder[len(photoFolder)-1] <> '/':
		photoFolder = photoFolder + '/'

	# Creer la liste des fichiers BRUTS:
	fichiersBruts = list()
	fichiersImages = list()
	fichierGpx = ''
	for fileName in contenuDossier:
		if fileName[-4:] == '.CR2':
			fichiersBruts.append(fileName)
		elif fileName[-4:] == '.JPG':
			fichiersImages.append(fileName)
		elif fileName[-4:] == '.gpx':
			fichierGpx = fileName
		else:
			pass

	# Si la liste des images brutes n'est pas vide:
	if len(fichiersBruts) == 0 :
		print("INFO:\tAucun fichier brut trouve")
	else:
		# Crée les repertoire 'BRUTS' si encore inexistant
		if os.path.isdir(photoFolder+FOLDER_FOR_RAWS):
			print("WARNING!\tLe dossier %r existe deja." % FOLDER_FOR_RAWS)
		else:
			print("Creation du dossier %r..." % FOLDER_FOR_RAWS)
			os.mkdir( photoFolder+FOLDER_FOR_RAWS )

		# Deplacer les images *.CR2 vers BRUTS
		print("Deplacement des fichiers bruts:")
		for fichierBrut in fichiersBruts:
			print("\tDéplacement de %s..." % fichierBrut)
			os.rename( photoFolder+fichierBrut , photoFolder+FOLDER_FOR_RAWS+"/"+fichierBrut )

	print("")
	print("                            _______________________")
	print("===========================[ Géo-taggage des jpegs ]===========================")

	subprocess.call("python ~/Applications/GPicSync/src/gpicsync.py --directory='"+photoFolder+"' --gpx='"+photoFolder+fichierGpx+"' --offset=-3 --time-range=3000", shell=True)
	# D'après http://stackoverflow.com/questions/3781851/run-a-python-script-from-another-python-script-passing-in-args
	# il serait mieux d'utiliser de __main__ de gpicsync.py 

	# Si des sauvegardes (backup) des photos ont été crées, on les déplace vers un nouveau dossier
	fichiersBackup = glob.glob(photoFolder+'*_original')   # all backuped files within the directory
	print(fichiersBackup)
	if len(fichiersBackup) == 0:
		print("INFO:\tAucun fichier de sauvegarde trouvé (aucun fichier géo-taggué?)")
	else:
		# Crée les repertoire 'Backups' si encore inexistant
		if os.path.isdir(photoFolder+FOLDER_BACKUP_GPS):
			print("WARNING!\tLe dossier %r existe deja." % FOLDER_BACKUP_GPS)
		else:
			print("Creation du dossier %r..." % FOLDER_BACKUP_GPS)
			os.mkdir( photoFolder+FOLDER_BACKUP_GPS )

		# Deplacer les images *_backup* vers le dossier des sauvegardes
		print("Déplacement des fichiers originaux (avant géo-taggage):")
		for fichierBackup in fichiersBackup:
			print("\tDeplacement de %s..." % fichierBackup)
			import sys
			try:
				shutil.move( fichierBackup , photoFolder+FOLDER_BACKUP_GPS+"/" )
			except shutil.Error:
				print( "Warning: Error happened when trying to move backup file to backup folder. Does a previous backup exist in destination folder?")

	print("")
	print("                    _______________________________________")
	print("===================[ Traitement des images jpegs restantes ]===================")

	fichiersImages.sort()
	os.stat_float_times(False)
	timeCreationFichiers = list()
	timeFromPreviousImage = list()
	seriesOfPhotos = list()

	print("Nro  \tNro  \tNom de      \thorodatage\ttemps depuis")
	print("Photo\tSerie\t     fichier\t          \tprecedente (s)")
	for fichierImage in fichiersImages:
		i = len(timeFromPreviousImage)
		timeCreationFichiers.append(os.stat(photoFolder+fichierImage).st_mtime)
		if i == 0:
			timeFromPreviousImage.append(9999)
		else:
			timeFromPreviousImage.append(timeCreationFichiers[i]-timeCreationFichiers[i-1])
		if timeFromPreviousImage[i] > MIN_TIME_BETWEEN_PANOS:
			# Alors on a une nouvelle serie
			seriesOfPhotos.append(list())
			marqueSeparateur = "^^^^^^^^^^"
		else:
			# Alors la photo courrante appartient a la meme serie que l'image precedente (panorama our HDR)
			marqueSeparateur = "          "
		seriesOfPhotos[len(seriesOfPhotos)-1].append(fichierImage)
		print("%d\t%d\t%s\t%d\t%d\t%s" % (i, len(seriesOfPhotos), fichierImage, timeCreationFichiers[i], timeFromPreviousImage[i], marqueSeparateur ))

	#print(seriesOfPhotos)

	for serieOfPhotos in seriesOfPhotos:
		nPhotoInSerie = len(serieOfPhotos)
		serieIsHDR = False
		if nPhotoInSerie >= 3:
			# Alors on a un groupe de photo (considere comme panorama)
			print("Serie courrante: %d photos => panorama ou HDR" % nPhotoInSerie)
			if nPhotoInSerie == 3:
				# Traitement du cas d'un HDR: seules les 2 sur- et sous- exposees sont deplacees
				print("Lecture du parametre EXIF 'ExposureTime' pour deduire si il s'agit d'un HDR...")
				exposureTime = list()
				for i in range (0,nPhotoInSerie):
					exposureTime.append( get_ExposureTime_from_exif_in_s(photoFolder+serieOfPhotos[i]) )
				if exposureTime[0]<>exposureTime[1] and exposureTime[0]<>exposureTime[2] and exposureTime[1]<>exposureTime[2]:
					# Alors on a affaire a 3 expositions differentes, c'est donc un HDR
					serieIsHDR = True

			# Determination du nom du dossier:
			firstPicture = serieOfPhotos[0][:-4]	# le [:-4] permet de supprimer les 4 derniers caracteres (l'extension)
			lastPicture = serieOfPhotos[len(serieOfPhotos)-1][:-4]
			subFolderName = firstPicture+"-"	# Commence par le nom de la premiere photo
			continueLoop = True
			i = 0
			while continueLoop:
				if firstPicture[i]==lastPicture[i]:
					# Le i eme caractere des 2 fichiers est identique
					i = i+1
				else:
					# Le i eme caractere des 2 fichiers n'est PAS identique => on quite la boucle
					continueLoop = False
			subFolderName = subFolderName+lastPicture[i:]+"_"+str(len(serieOfPhotos))
			if serieIsHDR:
				subFolderName = subFolderName + "_HDR"
			if os.path.isdir(photoFolder+subFolderName):
				print("WARNING!\tLe dossier %r existe deja." % subFolderName)
			else:
				print("Creation du sous-dossier %s..." % subFolderName )
				os.mkdir( photoFolder+subFolderName )

			print("Deplacement des photos dans %r..." % subFolderName )
			isFirstFile = True
			for fichierImage in serieOfPhotos:
				if serieIsHDR and isFirstFile:
					print("\tLe fichier %s de la serie HDR est laisse..." % fichierImage)
				else:
					print("\tDeplacement de %s..." % fichierImage)
					os.rename( photoFolder+fichierImage , photoFolder+subFolderName+"/"+fichierImage )
				isFirstFile = False

		else:
			# Alors on a une serie de 1 ou 2 photo: on ignore et passe a la suite
			print("Serie courrante: seulement %d photo(s) => serie ignoree" % nPhotoInSerie)

	print("Terminated.")
	exit(0)

	# Codes de sortie:
	#   0	Sortie normale
	#  -1	Nombre d'arguments invalides
	#  -2	Premier argument (dossier de base des photos) non detecte comme dossier valide.






if __name__ == "__main__":

	# A ETUDIER TODO : la commande:
	# panostart --output Makefile --projection 0 --fov 50 --nostacks --loquacious *.JPG

	argList = list(argv)
	nbArg   = len(argList) - 1
	print("%d arguments reçus: %r" % (nbArg, argList))
	# Test la validite des arguments:
	if nbArg <> 1:
		print("ERREUR!")
		print("%s ne s'attend qu'a un seul attribut contenant le chemin (absolu)." % argList[0] )
		print("Nombre d'arguments detectes = %d (contre 1 seul attendu)" % nbArg )
		exit(-1)

	# CONSTANTS:

	# Dossier vers lequel seront deplaces les fichiers bruts (sans '/' final):
	FOLDER_FOR_RAWS = "BRUTS"

	# Folder to which backups of photos will be moved before applying geo-tagging (relative to photoFolder, without final '/'):
	FOLDER_BACKUP_GPS = "BackupBeforeGPS!AE!"

	# Temps MAXI entre chaque photo d'un panorama (en secondes):
	MIN_TIME_BETWEEN_PANOS = 13.5		# Can be corrected between 19 and 7.5...

	# Repertoire a analyser:
	photoFolder = argList[1]

	if not os.path.isdir(photoFolder):
		print("ERREUR!")
		print("Le dossier passe en argument n'a pas ete reconnu comme valide:")
		print("%r n'est pas un dossier valide." % photoFolder )
		exit(-2)
	
	main(photoFolder)
