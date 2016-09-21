#!/usr/bin/python
# -*- coding: utf-8 -*-
print("Hugin Scripting Interface - Basic test of hsi scripts.")

# Example from http://hugin.sourceforge.net/docs/manual/Hugin_Scripting_Interface.html

def main(photoFolder):

	# from sys import argv      # Not necessary yet
	from hsi import *         # load the module
	p=Panorama()              # make a new Panorama object
	ifs=ifstream('datatest/existing_panorama_file.pto')    # create a C++ std::ifstream
	p.readData(ifs)           # read the pto file into the Panorama object
	del ifs                   # don't need anymore
	img0=p.getImage(0)        # access the first image
	print img0.getWidth()     # print the image's width
	cpv=p.getCtrlPoints()     # get the control points in the panorama
	for cp in cpv[:30:2] :    # print some data from some of the CPs
	  print cp.x1
	cpv=cpv[30:50]            # throw away most of the CPs
	p.setCtrlPoints(cpv)      # pass that subset back to the panorama
	ofs=ofstream('datatest/new_panorama.pto')    # make a c++ std::ofstream to write to
	p.writeData(ofs)          # write the modified panorama to that stream
	del ofs                   # done with it

	print("Terminated.")
	exit(0)



if __name__ == "__main__":

	# DEFINITION OF CONSTANTS:
	main()

