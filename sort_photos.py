#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This script is expected to be launched right after having downloaded pictures from digital camera.
It aims to organize raw files in a separated folder,
group set of pictures in one folder,
geo-tags pictures if a .gpx file is given.

The expected folder (before) is as:
/Image-root
    /2024                 (year)
        /2024-06-15_Day-short-description
            /RAW          (will be created by script)
"""

import configparser
import glob
import logging
import os
import subprocess

from datetime import datetime
from PIL import Image  # If PIL module not installed, then: `pip install Pillow`, soon pillow-avif-plugin will also be required
from PIL.ExifTags import TAGS
from sys import argv

# CONSTANTS:
ERROR = '\033[1;31mError:\033[0m '
# Return codes:
#   0    Normal exit
#  -1    Invalid number of arguments
#  -2    Folder given as picture folder is not detected as a valid folder
RC_ATTRIBUTES_ERROR = -1
RC_PATH_ERROR = -2

# Folder to which backups of photos will be moved before applying geo-tagging
# (relative to photoFolder, without final '/'):
FOLDER_BACKUP_GPS = "BackupBeforeGPS!AE!"

# MAXIMUM time allowed between each picture of a panorama (in seconds):
MIN_TIME_BETWEEN_PANOS = 15  # Can be corrected between 19 and 7.5...
# 13.5 was considered good for a good time
# For room panorama, up to 40 s. were needed between 2 shots.

logging.basicConfig(
    # filename=utils.get_home() + "/tmp/logfile.log",
    level=logging.DEBUG,
    format='%(asctime)s\t%(levelname)s\t%(filename)s:%(lineno)d\t%(message)s')
log = logging.getLogger("sort_photos.py")  # %(name)s

config = configparser.ConfigParser()
config.setdefaults({'Folders': {
    'root': '~/Images',
    'raw': 'RAW'
}})

config_path = os.path.expanduser('~/.config/sort_photo.conf')
config.read(config_path)

root_folder = os.path.expanduser(config['Folders']['root'])
# Folder where raw files will be moved to (without final '/'):
FOLDER_FOR_RAWS = os.path.join(root_folder, config['Folders']['raw'])


# Example of config file sort_photo.conf with default values:
#
# [Folders]
# root = ~/Images
# raw = RAW


def log_title(title):
    log.info(" " * 20 + "┌" + "─" * (2 + len(title)) + "┐")
    log.info("╒" + "═" * 19 + "╡ " + title + " ╞" + "═" * 19 + "╕")
    log.info("│" + " " * 19 + "└" + "─" * (2 + len(title)) + "┘" + " " * 19 + "│")


def get_newest_gpx_in_parent(folder):
    list_of_files = glob.glob(folder + '../*.gpx')  # * means all if need specific format then *.csv
    log.debug(list_of_files)
    if len(list_of_files) == 0:
        log.warning("\nlatest_file: No GPX file found!")
        return None
    else:
        latest_file = max(list_of_files, key=os.path.getctime)
        log.info("\nlatest_file: '" + str(latest_file) + "'")
        # type(latest_file)
        return str(latest_file)  # FIXME Should be "../abc.gpx"


def get_exif(file_name):
    # Get exif information from file given in parameter
    ret = {}
    i = Image.open(file_name)
    info = i._getexif()
    if not info:
        # Have a try if .getexif() works better:
        info = i.getexif()
        if info:
            log.warning(f"Method .getexif() worked while ._getexit() failed for file '{file_name}'")
    if info:
        for tag, value in info.items():
            decoded = TAGS.get(tag, tag)
            ret[decoded] = value
    else:
        log.warning(f"Unable to get exif information from file '{file_name}' (returning empty dictionary).")
    return ret


def get_exposure_time_from_exif(file_name):
    # Get exposure time (in seconds) of file given in parameter from exif information
    exif_of_filename = get_exif(file_name)
    ssv = exif_of_filename['ShutterSpeedValue']

    # Example for exposure of 1/6th of second:
    # ssv._numerator    172032                int
    # ssv._denominator    65536                int
    # ssv._val            Fraction(21, 8)        <class 'fractions.Fraction'>

    # FIXME Correct value is more complicated than that...
    # With former version or Python2, ExposureTime return had the form: (1, 100) for 1/100 of second;
    # to get the result in seconds:
    # ret = exp_time_vect[0] / float(exp_time_vect[1])                             # returns a float
    # ret = timedelta(seconds=exp_time_vect[0] / float(exp_time_vect[1]))  # returns a timedelta
    # return ret

    log.info("ssv._numerator / ssv._denominator / ssv._val:")
    try:
        log.info(ssv._numerator)
        log.info(ssv._denominator)
        log.info(ssv._val)
    except AttributeError:
        log.info("AttributeError! (ignored and proceeding...)")
    return 1


def get_date_time_original_from_exif(file_name):
    # Candidates to get le exposure date-time from EXIF:
    #  DateTimeOriginal    ***     time when the original image data was generated = time the picture was taken
    #  DateTime            **      time the file was changed
    #  DateTimeDigitized   *       time when the image was stored as digital data
    # Get exposure time (in seconds) of file given in parameter from exif information
    # Times are always from the BEGINNING of the capture (make total sense for long exposures)
    # To determine: picture 4029 STARTED to be taken at 2016-11-06 00:30:~49 and ENDED at 00:31:~19
    # DateTimeOriginal = 2016:11:06 00:30:48
    # DateTimeDigitized = 2016:11:06 00:30:48
    # DateTime = 2016:11:06 00:30:48
    _EXIF_TIME_FORMAT = '%Y:%m:%d %H:%M:%S'
    exif_of_file_name = get_exif(file_name)
    try:
        dt_original_unicode = exif_of_file_name['DateTimeOriginal']
    except KeyError:
        log.error(f"KeyError occurred when trying to find exif parameter 'DateTimeOriginal' "
                  f"from file '{file_name}' (returning None as datetime).")
        return None
    # log.info("get_date_time_original_from_exif(%r)" % fileName)
    # log.info("    dt_original_unicode = %r" % dt_original_unicode)
    ret = datetime.strptime(dt_original_unicode, _EXIF_TIME_FORMAT)
    # Would it be better:
    # ret = datetime.st_mtime
    return ret


def get_files(photo_folder):
    # Obtain list of raw files:
    folder_content = sorted(os.listdir(photo_folder))
    log.info(f"{len(folder_content)} files found in folder {photo_folder}")

    # Create list of RAW files:
    raw_files = list()
    picture_files = list()
    gpx_file = ''
    for file_name in folder_content:
        file_ext = file_name[-4:].lower()
        if file_ext in {'.crw', '.cr2', '.cr3', '.nef'}:
            raw_files.append(file_name)
        elif file_ext in {'.jpg', '.jpeg', '.heif', '.avif'}:
            picture_files.append(file_name)
        elif file_ext == '.gpx':
            gpx_file = file_name
        else:
            log.warning(f"Unable to determine type of file '{file_name}' (file will be ignored).")
            pass
    return raw_files, picture_files, gpx_file


def move_raws(photo_folder):
    log_title("Search for raw files for moving to adequate folder")

    # Get list of raw files:
    (raw_files, picture_files, gpx_file) = get_files(photo_folder)

    if len(raw_files) == 0:
        log.info("No raw file found.")
    else:
        # Create folder for "RAWS" if not existing already:
        if os.path.isdir(photo_folder + FOLDER_FOR_RAWS):
            log.warning("\tFolder '%r' already exists" % FOLDER_FOR_RAWS)
        else:
            log.info("\tCreating folder '%r'..." % FOLDER_FOR_RAWS)
            os.mkdir(photo_folder + FOLDER_FOR_RAWS)

        log.info("Moving raw images to their folder...")
        for raw_file in raw_files:
            log.debug("\tMoving file '%s'..." % raw_file)
            os.rename(photo_folder + raw_file, photo_folder + FOLDER_FOR_RAWS + "/" + raw_file)


def geotag_move_backups(photo_folder):
    # sleep(5)  # Wait a bit for files to be effectively written (if not waiting, files *_original are not moved)
    # Si des sauvegardes (backup) des photos ont été crées, on les déplace vers un nouveau dossier
    backup_files = glob.glob(photo_folder + '*_original')  # get backup files within the directory
    log.info("Found Backup files by geo-tag: {0}".format(backup_files))
    while len(backup_files) > 0:
        log.info("Backup files by geo-tag: {0}".format(backup_files))
        # Create 'Backups' folder is not existing yet:
        if os.path.isdir(photo_folder + FOLDER_BACKUP_GPS):
            log.info("Folder '%r' for backup pictures already exists." % FOLDER_BACKUP_GPS)
        else:
            log.info("Create folder '%r' for backup pictures..." % FOLDER_BACKUP_GPS)
            os.mkdir(photo_folder + FOLDER_BACKUP_GPS)

        # Deplacer les images *_backup* vers le dossier des sauvegardes
        log.info("Déplacement des fichiers originaux (avant géo-taggage):")
        for fichierBackup in backup_files:
            log.info("\tDeplacement de %s..." % fichierBackup)
            # try:
            os.rename(fichierBackup, fichierBackup.replace(photo_folder, photo_folder + FOLDER_BACKUP_GPS + "/"))
        # Due to some files being missed sometime, we're getting again list of original files that may have been missed
        # in first call (just over 'while'):
        backup_files = glob.glob(photo_folder + '*_original')  # get backup files within the directory


def get_time_shift():
    # +2 for CEST (summer time for Paris, ...)
    # +1 for CET (winter time for Paris, ...)
    # +1 for summer time in Portugal
    # +0 for winter time in Portugal
    # -3 for summer time in Brasil/Curitiba
    return input('Please inform time offset (timezone) as "+n" ("+1" will be used as default): ') or "+1"


def geotag_pictures(photo_folder):
    (fichiers_bruts, fichiers_images, file_gpx) = get_files(photo_folder)
    log_title("geo-tagging jpegs pictures")

    if file_gpx == '':
        file_gpx_with_folder = get_newest_gpx_in_parent(photo_folder)
    else:
        file_gpx_with_folder = photo_folder + file_gpx

    if file_gpx_with_folder:  # variable defined (not None)

        offset = get_time_shift()
        # gpysync_cmd = "python ~/Applications/GPicSync/src/gpicsync.py" \
        #              + " --directory='" + photo_folder \
        #              + "' --gpx='" + file_gpx_with_folder \
        #              + "' --offset=" + offset \
        #              + " --time-range=3000"
        # subprocess.call(
        #     gpysync_cmd,
        #     shell=True)
        log.info("Launching GPicSync...")
        child = subprocess.Popen(
            ["python", "/home/thomas/Applications/GPicSync/src/gpicsync.py",
             "--directory=" + photo_folder,
             "--gpx=" + file_gpx_with_folder,
             "--offset=" + offset,
             "--time-range=3000"]
            # , stdout=subprocess.PIPE
        )
        streamdata = child.communicate()[0]  # Unused value but required to wait for end of process ?
        log.debug("Waiting for Return Code from GPicSync...")
        rc = child.returncode
        # log.debug("GPicSync stdout:")  # .format(str(streamdata)))
        # print(streamdata)
        if rc == 0:
            log.info("GPicSync -> return code = {0}".format(rc))
        else:
            log.warning(ERROR + "GPicSync -> return code = {0}".format(rc))

        # According to
        # http://stackoverflow.com/questions/3781851/run-a-python-script-from-another-python-script-passing-in-args
        # it would be better to use the __main__ from gpicsync.py
        # => Tentative below, but unsure about GPicSync implementation...

        # options_dir = photo_folder  # --directory
        # options_gpx = [file_gpx_with_folder]  # --gpx
        # options_offset = offset  # --offset=
        # options_timerange = 3000  # --time-range
        #
        # options_qr_time_image = None
        #
        # log.debug("Launching GPicSync with GPX file '{0}'".format(options_gpx))
        # geo = gpicsync.GpicSync(gpxFile=options_gpx,
        #                         # tcam_l=options_tcam,
        #                         # tgps_l=options_tgps,
        #                         UTCoffset=float(options_offset),
        #                         timerange=int(options_timerange),
        #                         # timezone=options_timezone,
        #                         qr_time_image=options_qr_time_image)
        #
        # log.debug("Launching GPicSync with FileList from '{0}'".format(options_dir))
        # files = list(gpicsync.getFileList(options_dir))
        #
        # if options_qr_time_image is not None:
        #     qr_time_images = [(options_qr_time_image, options_qr_time_image)]
        #     if options_qr_time_image == 'auto':
        #         qr_time_images = files
        #     geo.parseQrTime(qr_time_images)
        #
        # for fileName, filePath in files:
        #     print("\nFound fileName ", fileName, " Processing now ...")
        #     geo.syncPicture(filePath)[0]

    else:  # from "if file_gpx_with_folder" => file_gpx_with_folder is None
        log.info("No GPX file usable => GPicSync not launched.")

    geotag_move_backups(photo_folder)


def create_sub_folder_name(serie_of_photos, serie_is_hdr):
    # create sub-folder name:
    first_picture = serie_of_photos[0][:-4]  # le [:-4] permet de supprimer les 4 derniers car. (l'extension)
    last_picture = serie_of_photos[len(serie_of_photos) - 1][:-4]
    subfolder_name = first_picture + "-"  # Starts with the name of first picture
    continue_loop = True
    i = 0
    while continue_loop:
        if first_picture[i] == last_picture[i]:
            # i-th char of each file is identical
            i = i + 1
        else:
            # i-th char of each file is different => exiting the loop
            continue_loop = False
    subfolder_name = subfolder_name + last_picture[i:] + "_" + str(len(serie_of_photos))
    if serie_is_hdr:
        subfolder_name = subfolder_name + "_HDR"
    return subfolder_name


def group_pictures_by_time(photo_folder):
    (raw_files, rendered_files, file_gpx) = get_files(photo_folder)

    log_title("Manage remaining jpegs")

    rendered_files.sort()
    # os.stat_float_times(False)
    time_org_capture_began = list()
    time_org_capture_ended = list()
    time_from_previous_image = list()
    series_of_photos = list()

    log.info("Pict.\tSerie\tFile name   \ttimestamp \ttime since")
    log.info("Nr   \tNr   \t            \t          \tprevious")
    for rendered_file in rendered_files:
        i = len(time_from_previous_image)
        # time_org_capture_began.append(os.stat(photoFolder+rendered_file).st_mtime)
        date_time_org_image = get_date_time_original_from_exif(photo_folder + rendered_file)
        time_org_capture_began.append(date_time_org_image)

        # FIXME Once get_exposure_time_from_exif() is fixed, uncomment and resolve below:
        # time_org_capture_ended.append(date_time_org_image + get_exposure_time_from_exif(photo_folder + rendered_file))
        time_org_capture_ended.append(date_time_org_image)

        if i == 0:
            # First picture => time from previous = 9999 sec.
            time_from_previous_image.append(9999)
        elif (time_org_capture_began[i] is None) or (time_org_capture_ended[i - 1] is None):
            time_from_previous_image.append(9999)
        else:
            #  EXACT time between 2 pictures =
            #           (time for picture n)
            #       - [ (time from picture n-1) + (ExposureTime from picture n-1) ]
            #  ExposureTime
            time_from_previous_image.append((time_org_capture_began[i] - time_org_capture_ended[i - 1]).total_seconds())

        if time_from_previous_image[i] > MIN_TIME_BETWEEN_PANOS:
            # Then we have a new set of picture:
            series_of_photos.append(list())
            division_mark = "^^^^^^^^^^"
        else:
            # else... current picture is part of the same serie as previous image (=> panorama or HDR)
            division_mark = "          "
        series_of_photos[len(series_of_photos) - 1].append(rendered_file)
        # log.info("%d\t%d\t%s\t%d\t%d\t%s" %
        # (i, len(series_of_photos), rendered_file,
        #  time_org_capture_began[i], time_from_previous_image[i], division_mark ))
        log.info("%d\t%d\t%s\t%s\t%s\t%s" % (i,
                                             len(series_of_photos),
                                             rendered_file,
                                             time_org_capture_began[i],
                                             time_from_previous_image[i],
                                             division_mark))

    for serie_of_photos in series_of_photos:
        n_photo_in_serie = len(serie_of_photos)
        serie_is_hdr = False
        if n_photo_in_serie >= 3:
            # Then we have a series of pictures (considered as a panorama)
            log.info("Current set: %d pictures => panorama or HDR" % n_photo_in_serie)
            if n_photo_in_serie == 3:  # ! We consider that HDR are only 3 set of pictures... should be improved later
                # Exception case for HDR: only the 2 pictures over and under-exposed are moved. We keep the "middle" one
                log.info("Reading EXIF parameter 'ExposureTime' to deduce if it is an HDR...")
                exposure_time = list()
                for i in range(0, n_photo_in_serie):
                    exposure_time.append(get_exposure_time_from_exif(photo_folder + serie_of_photos[i]))
                if exposure_time[0] != exposure_time[1] \
                        and exposure_time[0] != exposure_time[2] \
                        and exposure_time[1] != exposure_time[2]:
                    # Then we have 3 different exposure values => HDR
                    serie_is_hdr = True

            subfolder_name = create_sub_folder_name(serie_of_photos, serie_is_hdr)

            if os.path.isdir(photo_folder + subfolder_name):
                log.warning("Folder '%r' already exists" % subfolder_name)
            else:
                log.info("Creating sub-folder %s'..." % subfolder_name)
                os.mkdir(photo_folder + subfolder_name)

            log.info("Moving pictures to '%r'..." % subfolder_name)
            is_first_file = True
            for rendered_file in serie_of_photos:
                if serie_is_hdr and is_first_file:
                    # If this is an HDR serie, the first file (normal exposure +0EV) is left in folder
                    log.info("\tFile '%s' from HDR set is left in main folder..." % rendered_file)
                else:
                    log.info("\tMoving file '%s'..." % rendered_file)
                    os.rename(photo_folder + rendered_file, photo_folder + subfolder_name + "/" + rendered_file)
                is_first_file = False

            # The set of pictures is now in folder, ready to be computed
            if not serie_is_hdr:
                create_panos_script = photo_folder + "create_panos_script.sh"
                if os.path.exists(create_panos_script):
                    append_write = 'a'  # append if already exists
                else:
                    append_write = 'w'  # make a new file if not

                with open(create_panos_script, append_write) as create_panos_script:
                    create_panos_script.writelines(
                        "nice -n 19 ~/python/create_panorama.py \"" + photo_folder + subfolder_name + "/\"\n"
                    )

        else:
            # we have a "serie" of 1 or 2 pictures: ignore and proceed with next pictures
            log.info("Current set: only %d picture(s) => ignored" % n_photo_in_serie)

    log.info("Terminated.")
    log.info("")
    log.info("Panorama may have been added in:")
    log.info("cat /home/thomas/Images/2017\\ PegASUS\\ seul/create_panos_script.sh")


def create_avif_for_raws(photo_folder):
    """Check if a raw that is not part of a panorama has a jpeg/avif picture in photo folder.
    If none, a default one will be created from raw."""
    return


def sort_photos(photo_folder: str, gpx_file: str) -> None:
    """


    :param photo_folder:
    :param gpx_file:
    :return:
    """
    # Normalise photoFolder (append with '/' if not already present at the end):
    if photo_folder[len(photo_folder) - 1] != '/':
        photo_folder = photo_folder + '/'

    move_raws(photo_folder)

    geotag_pictures(photo_folder)

    group_pictures_by_time(photo_folder)

    # TODO create_avif_for_raws(photo_folder)

    return


if __name__ == "__main__":

    # TODO Checks "panostart" command
    # panostart --output Makefile --projection 0 --fov 50 --nostacks --loquacious *.JPG

    argList = list(argv)
    nbArg = len(argList) - 1
    log.info("%d arguments reçus: %r" % (nbArg, argList))
    # Tests arguments validity:
    if nbArg < 1 or nbArg > 2:
        log.critical(ERROR)
        log.critical("%s requires 1 or 2 arguments:" % argList[0])
        log.critical("%s pictures_path [gpx_path]" % argList[0])
        log.critical("Where:")
        log.critical("- pictures_path is the path where the pictures to sort are")
        log.critical("- gpx_path is the path of the gpx track file (optional)")
        exit(RC_ATTRIBUTES_ERROR)

    # Repertoire à analyser:
    photo_folder_arg = argList[1]
    if nbArg > 1:
        gpx_file_arg = argList[2]
    else:
        gpx_file_arg = None

    if not os.path.isdir(photo_folder_arg):
        log.error("Folder '%r' given as argument is not detected as valid." % photo_folder_arg)
        exit(RC_PATH_ERROR)

    sort_photos(photo_folder_arg, gpx_file_arg)
