# -*- coding: utf-8 -*-
#
# Copyright (C) 2013 GNS3 Technologies Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from setuptools import setup, find_packages

setup(
    name="vboxwrapper",
    version="0.9.2",
    url="http://github.com/GNS3/vboxwrapper",
    license="GNU General Public License v2 (GPLv2)",
    author="Jeremy Grossmann & Alexey Eromenko",
    author_email="package-maintainer@gns3.net",
    description="Script to control VirtualBox on Linux/Unix",
    long_description=open("README.md", "r").read(),
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "vboxwrapper = vboxwrapper.vboxwrapper:main",
            ]
        },
    platforms="any",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Information Technology",
        "Topic :: System :: Networking",
        "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
        'Natural Language :: English',
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: Implementation :: CPython",
        ],
)
