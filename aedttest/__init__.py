"""
Ansys Electronics Desktop Testing Framework.
Current module allows to setup and run automated tests and validate results across multiple versions of
Ansys Electronics Desktop products, eg HFSS, Maxwell, Icepak, Q3D, etc
"""
from pathlib import Path

version_file = Path(__file__).resolve().parent / "version.txt"

if version_file.exists():
    with open(version_file) as file:
        __version__ = file.readline().strip()
else:
    pass
    # __version__ = "0.0.1.alpha0"

__authors__ = "Maksim Beliaev, Bo Yang"
