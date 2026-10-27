#!/usr/bin/env python3
"""Run the shared five-round native benchmark for v4."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_bench import main
if __name__ == '__main__':
    main(4)
