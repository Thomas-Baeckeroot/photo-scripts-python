#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Calls the sortPhoto script on each folder in *root* that:
1- contain .RAW or .CR2 files
2- does not contain a 'BRUTS' folder
"""

from sys import argv
from variables import *
import os
import sort_photos


def main(photo_root_folder):

    print("Checking folder %r..." % photo_root_folder)

    # Get list of folders of the root folder:
    folders = os.walk(photo_root_folder)

    for folder in folders:
        currentfolder = folder[0]
        print("  🅵 Current folder: \"" + currentfolder + "\"")
        if raw_folder in currentfolder:  # only check if it contains the word, should be safer if checking last folder.
            print("   ↳ allowed to contain raw filename, skipped")
            # do nothing more here, take the next folder...
        else:
            # not in a raw folder:

            contains_raw_folder = False
            subfolders = folder[1]
            for subfolder in subfolders:
                print("    🄵 subfolder: \"" + subfolder + "\"")
                if subfolder == raw_folder:
                    contains_raw_folder = True
                    break

            files = folder[2]
            for filename in files:
                print("    🀨 filename: " + filename)
                if "CR2" in filename:
                    print("     ↳ is a raw filename!")
                    if not contains_raw_folder:
                        print("*    Start sorting folder \"" + currentfolder + "\"")
                        columns = os.environ.get('COLUMNS', 80)
                        print("★" * columns)
                        sort_photos.sort_photos(currentfolder)
                        print("★" * columns)
                        # print("★*    Create folder \"" + raw_folder + "\"")
                        # fixme os.makedirs(currentfolder + os.sep + raw_folder)
                        contains_raw_folder = True
                    print("★*    Move '" + currentfolder + os.sep + filename + "'to \"" + raw_folder + "\"")
                    # fixme os.rename(
                    #    currentfolder + os.sep + filename,
                    #    currentfolder + os.sep + raw_folder + os.sep + filename)
                    # )


if __name__ == "__main__":
    argList = list(argv)
    nbArg = len(argList) - 1

    # Take the root folder to review from argument or force to my value if not given:
    if argList == 1:
        photo_folder = argList[1]
    else:
        photo_folder = default_photo_folder

    main(photo_folder)
