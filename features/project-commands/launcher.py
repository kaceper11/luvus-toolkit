"""Manifest bootstrap: use this module's environment, never another module's."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
if sys.version_info < (3, 11):
    sys.exit("Project Commands requires Python 3.11+. Create its .venv as described in README.md.")
from project_commands.cli import main
sys.exit(main())
