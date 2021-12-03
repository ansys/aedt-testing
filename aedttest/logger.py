import logging
import warnings

# set logger to be configurable from external
logger = logging.getLogger("aedt-test-framework")
# Set default logging handler to avoid "No handler found" warnings.
logger.addHandler(logging.NullHandler())


# suppress warnings from pyaedt
warnings.filterwarnings("ignore")


def set_logger(logging_file, level=logging.DEBUG):
    """
    Function to setup default logging output to stream and log file.

    Args:
        logging_file: (str/Path) path to log file
        level: (int) level of logging to emit

    Returns:
        None
    """

    logger.setLevel(level)

    # add logging to console and log file
    # If you set the log level to INFO, it will include INFO, WARNING, ERROR, and CRITICAL messages

    file_handler = logging.FileHandler(filename=logging_file)
    stream_handler = logging.StreamHandler()

    formatter = logging.Formatter(fmt="%(asctime)s (%(levelname)s) %(message)s", datefmt="%d.%m.%Y %H:%M:%S")
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
