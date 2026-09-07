"""Manifest bootstrap: use this module's environment, never another module's."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
python = root.parents[1] / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if python.exists() and Path(sys.prefix).resolve() != (root.parents[1] / ".venv").resolve():
    os.execv(str(python), [str(python), str(__file__), *sys.argv[1:]])
if sys.version_info < (3, 11):
    sys.exit("Project Commands requires Python 3.11+. Create its .venv as described in README.md.")
from project_commands.cli import main
sys.exit(main())
