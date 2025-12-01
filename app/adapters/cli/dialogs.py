"""
Диалоговые окна для выбора файлов (GUI Helpers).

Этот модуль предоставляет функции для открытия системных диалоговых окон
(выбор файла/папки) через библиотеку `tkinter`. Это позволяет пользователю
визуально выбирать данные для бэктестов, вместо ручного ввода путей в консоль.

Особенности:
- Работает поверх консольного приложения (скрывает главное окно GUI).
- Автоматически парсит структуру папок `data/{exchange}/{interval}/{instrument}`
  для извлечения метаданных из пути.
"""

import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import Dict, Optional


def _initialize_tk() -> tk.Tk:
    """
    Инициализирует скрытое корневое окно Tkinter.

    Необходимо для корректного отображения диалоговых окон без запуска
    полноценного графического интерфейса приложения.

    Returns:
        tk.Tk: Объект корневого окна.
    """
    root = tk.Tk()
    root.withdraw()  # Скрываем главное окно, оставляем только диалог

    # Хак, чтобы окно диалога появилось поверх всех окон (особенно актуально для Windows)
    root.wm_attributes("-topmost", 1)
    root.update()
    return root


def select_single_instrument() -> Optional[Dict[str, str]]:
    """
    Открывает диалог выбора одного файла данных (.parquet).

    Выполняет валидацию выбранного файла:
    1. Проверяет наличие парного файла метаданных (.json).
    2. Пытается определить биржу, интервал и тикер из структуры папок.

    Returns:
        Optional[Dict[str, str]]: Словарь с параметрами инструмента:
            {
                "exchange": "tinkoff",
                "interval": "5min",
                "instrument": "SBER"
            }
            Возвращает None, если пользователь отменил выбор или файл некорректен.
    """
    root = _initialize_tk()

    filepath_str = filedialog.askopenfilename(
        title="Выберите .parquet файл с историческими данными",
        filetypes=[("Parquet files", "*.parquet")]
    )

    root.destroy()  # Освобождаем ресурсы GUI

    if not filepath_str:
        print("\nВыбор файла отменен.")
        return None

    filepath = Path(filepath_str)

    # 1. Проверка целостности данных (должен быть JSON с лотностью)
    json_path = filepath.with_suffix('.json')
    if not json_path.exists():
        print(f"\n[Ошибка] Файл метаданных не найден: {json_path.name}")
        print("Для каждого .parquet файла должен существовать .json файл с тем же именем.")
        return None

    # 2. Автоматическое определение параметров из пути
    # Ожидаемая структура: .../data/{exchange}/{interval}/{instrument}.parquet
    try:
        instrument = filepath.stem  # Имя файла без расширения
        interval = filepath.parent.name  # Папка интервала
        exchange = filepath.parent.parent.name  # Папка биржи

        if not instrument or not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить структуру папок.")
        print(f"Файл должен находиться в пути вида: '.../data/{{exchange}}/{{interval}}/'")
        return None

    print(f"\nВыбран инструмент: {exchange.upper()} / {interval} / {instrument.upper()}")

    return {
        "exchange": exchange,
        "interval": interval,
        "instrument": instrument
    }


def select_instrument_folder() -> Optional[Dict[str, str]]:
    """
    Открывает диалог выбора папки с данными для пакетного тестирования.

    Используется, когда нужно запустить стратегию сразу на всех инструментах
    внутри выбранной директории (например, на всех акциях с 5-минутным таймфреймом).

    Returns:
        Optional[Dict[str, str]]: Словарь с параметрами группы:
            {
                "exchange": "tinkoff",
                "interval": "5min"
            }
            Возвращает None при отмене или ошибке.
    """
    root = _initialize_tk()

    dirpath_str = filedialog.askdirectory(
        title="Выберите папку с данными (например, '.../data/tinkoff/5min')"
    )

    root.destroy()

    if not dirpath_str:
        print("\nВыбор папки отменен.")
        return None

    dirpath = Path(dirpath_str)

    # Предупреждение, если папка пуста (но не блокировка, так как может быть дозагрузка)
    if not any(dirpath.glob('*.parquet')):
        print(f"\n[Предупреждение] В папке '{dirpath.name}' не найдено .parquet файлов.")

    try:
        interval = dirpath.name
        exchange = dirpath.parent.name

        if not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить параметры из пути к папке.")
        print(f"Папка должна соответствовать структуре: '.../data/{{exchange}}/{{interval}}/'")
        return None

    print(f"\nВыбрана папка: {exchange.upper()} / {interval}")

    return {
        "exchange": exchange,
        "interval": interval
    }