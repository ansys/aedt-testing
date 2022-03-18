"""
Ansys Electronics Desktop Testing Framework.
Current module allows to setup and run automated tests and validate results across multiple versions of
Ansys Electronics Desktop products, eg HFSS, Maxwell, Icepak, Q3D, etc
"""

with open("version.txt") as file:
    __version__ = file.readline().strip()
__authors__ = "Maksim Beliaev, Bo Yang"
