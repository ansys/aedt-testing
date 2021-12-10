from logging import Logger
from pathlib import Path
from typing import Any
from typing import Optional
from typing import Union

logger: Logger

def set_logger(logging_file: Union[str, Path], level: Optional[int], pyaedt_module: Optional[Any]) -> None: ...
