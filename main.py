"""Entry point when running directly with `python main.py` (development convenience).

For normal use, run: runna-intervals --help
"""

from runna_intervals.cli import app

if __name__ == "__main__":
    app()
