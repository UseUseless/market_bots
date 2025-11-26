import sys
import os

from app.adapters.cli.launcher_logic import main

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    main()