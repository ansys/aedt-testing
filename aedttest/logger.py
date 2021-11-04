import logging
from pathlib import Path
from typing import Union

# set logger to be configurable from external

logger = logging.getLogger("aedt-test-framework")
# Set default logging handler to avoid "No handler found" warnings.
logger.addHandler(logging.NullHandler())


def set_logger(logging_file: Union[str, Path]) -> None:
    """
    Function to setup default logging output to stream and log file.

    Args:
        logging_file: (str/Path) path to log file

    Returns:
        None
    """

    logger.setLevel(logging.DEBUG)

    # add logging to console and log file
    # If you set the log level to INFO, it will include INFO, WARNING, ERROR, and CRITICAL messages

    file_handler = logging.FileHandler(filename=logging_file)

    formatter = logging.Formatter(fmt="%(asctime)s (%(levelname)s) %(message)s", datefmt="%d.%m.%Y %H:%M:%S")
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(logging.StreamHandler())
