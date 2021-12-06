from logging import Logger
from pathlib import Path
from typing import Union, Optional, Any

logger: Logger

def set_logger(logging_file: Union[str, Path], level: int, pyaedt_module: Optional[Any]) -> None: ...
