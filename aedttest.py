import argparse
import re

__authors__ = "Maksim Beliaev, Bo Yang"


def run(version: str) -> None:
    """
    Main function to start test sweet
    Returns:
    """


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Add long and short argument
    parser.add_argument("--aedt-version", "-av", help="Electronics Desktop version to test, e.g. 2022.1", required=True)
    args = parser.parse_args()

    aedt_version_pattern = re.compile(r"\d\d\d\d\.\d$")
    if not aedt_version_pattern.match(args.aedt_version):
        raise ValueError("Electronics Desktop version value is invalid. Valid format example: 2022.1")

    run(version=args.aedt_version)
