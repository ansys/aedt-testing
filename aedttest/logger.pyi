from logging import Logger
from pathlib import Path
from typing import Union

logger: Logger

def set_logger(logging_file: Union[str, Path], level: int) -> None: ...
