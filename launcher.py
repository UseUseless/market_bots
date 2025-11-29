import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.adapters.cli.launcher_logic import main



if __name__ == "__main__":
    """
        Блок запуска.
        Выполняется только если файл запущен напрямую (не импортирован как модуль).
        """
    main()