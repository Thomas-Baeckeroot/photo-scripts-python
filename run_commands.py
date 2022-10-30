#!/usr/bin/python
# -*- coding: utf-8 -*-

import os

"""Run one line from file, execute and comment it."""
commands_file = "/home/thomas/Images/2017 PegASUS seul/createPanosScript.sh"


def read_next_command_and_comment(filename):
    lines = open(filename, 'r').readlines()
    rewrite_file = open(filename, 'w')
    next_command = None
    for line in lines:
        if next_command is None and line[0] != '#':
            # Current line is the first uncommented line
            next_command = line
            line = "# " + line
        rewrite_file.write(line)
    rewrite_file.close()
    return next_command


command = read_next_command_and_comment(commands_file)
while command is not None:
    print("★" * os.environ.get('COLUMNS', 80))
    print(command)
    print("-" * os.environ.get('COLUMNS', 80))
    os.system(command)
    command = read_next_command_and_comment(commands_file)
