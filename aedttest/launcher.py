"""IronPython shim executed by AEDT via -RunScriptAndExit.

This script's only job is to launch simulation_data.py using the repository's
virtual environment CPython interpreter, which has ansys.aedt.core installed.

Background
----------
AEDT executes -RunScriptAndExit scripts via its bundled IronPython interpreter.
Since ansys.aedt.core (v1.x) contains C extensions (.pyd files), IronPython
cannot import it. This shim works around that limitation by delegating the
actual work to CPython in the repo's .venv, which CAN load C extensions.

Flow
----
AEDT (IronPython)
  -> launcher.py           # this file, runs in IronPython - no C imports needed
     -> os.system()        # launches simulation_data.py via .venv CPython
        -> simulation_data.py  # runs in CPython, imports ansys.aedt.core OK
           -> connects back to the running AEDT session via gRPC
           -> writes <project>.json

Note on subprocess
------------------
`subprocess` is intentionally NOT imported/used here. On Linux, AEDT's
embedded IronPython runs on Mono, whose subprocess implementation is
broken/incomplete: merely `import subprocess` raises a .NET-level error
(".NET error: Value cannot be null. Parameter name: method") before any
of our code even executes. `os.system()` uses the primitive C library
`system()` call via P/Invoke, which works reliably under Mono as well as
on Windows IronPython/.NET.
"""
import os
import re
import shlex

# ScriptArgument is injected by AEDT when using -RunScriptAndExit.
_script_args = ScriptArgument.replace('"', "")  # noqa: F821

# Resolve paths relative to this file
_launcher_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_launcher_dir)

# Find the venv Python executable (Windows vs Linux/Mac)
_venv_python = os.path.join(_repo_root, ".venv", "Scripts", "python.exe")
if not os.path.isfile(_venv_python):
    _venv_python = os.path.join(_repo_root, ".venv", "bin", "python")

if not os.path.isfile(_venv_python):
    raise RuntimeError(
        "Could not find venv Python at expected location: {}".format(_venv_python)
        + "\nMake sure the virtual environment is set up at: {}/.venv".format(_repo_root)
    )

_simulation_script = os.path.join(_launcher_dir, "simulation_data.py")

# ---------------------------------------------------------------------------
# Extract design names using IronPython oDesktop APIs.
# oDesktop is available in the IronPython execution context inside AEDT.
# This is the ONLY reliable way to get design names in -RunScriptAndExit mode
# because the gRPC interface cannot see open projects in batch mode.
# ---------------------------------------------------------------------------
_design_names = []
try:
    _oproject = oDesktop.GetActiveProject()  # noqa: F821
    if _oproject:
        for _d in list(_oproject.GetTopDesignList()):
            _m = re.search(r"[^;]+$", _d)
            if _m:
                _design_names.append(_m.group(0))
except Exception:
    pass  # simulation_data.py will log the issue

_design_names_arg = ""
if _design_names:
    _design_names_arg = " --design-names '{}'".format(",".join(_design_names))

# Determine the AEDT version of the outer -RunScriptAndExit process so that
# simulation_data.py connects to the SAME version via gRPC (new_desktop=False
# requires matching versions). oDesktop.GetVersion() is only available here,
# in the IronPython execution context.
_aedt_version_arg = ""
try:
    _aedt_version = oDesktop.GetVersion()  # noqa: F821
    if _aedt_version:
        # GetVersion() typically returns something like "2025.1.0" or
        # "2025.1.0 Build ..." - keep only the leading "YYYY.R" part.
        _m_version = re.match(r"(\d{4}\.\d+)", _aedt_version)
        _aedt_version_short = _m_version.group(1) if _m_version else _aedt_version
        _aedt_version_arg = " --aedt-version '{}'".format(_aedt_version_short)
except Exception:
    pass  # simulation_data.py will fall back to default=None

# Build command and execute - blocks until simulation_data.py finishes.
_cmd_parts = [_venv_python, _simulation_script] + shlex.split(
    _script_args + _design_names_arg + _aedt_version_arg
)
_quoted_cmd = " ".join('"{}"'.format(p) if " " in p else p for p in _cmd_parts)
_ret = os.system(_quoted_cmd)

if _ret != 0:
    print("[warning] simulation_data.py returned non-zero: {}".format(_ret))
