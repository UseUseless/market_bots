import os
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import Dict, Optional


# --- Вспомогательная функция для инициализации Tkinter ---
def _initialize_tk():
    """Создает и скрывает корневое окно Tkinter."""
    root = tk.Tk()
    root.withdraw()  # Скрываем основное окно, нам нужны только диалоги
    return root


# --- Основные функции для выбора данных ---

def select_single_instrument() -> Optional[Dict[str, str]]:
    """
    Открывает системный диалог для выбора одного .parquet файла.
    Проверяет наличие соответствующего .json файла.
    Парсит путь для извлечения exchange, interval и instrument.

    :return: Словарь с параметрами или None в случае отмены/ошибки.
    """
    _initialize_tk()
    filepath_str = filedialog.askopenfilename(
        title="Выберите .parquet файл с историческими данными",
        filetypes=[("Parquet files", "*.parquet")]
    )

    if not filepath_str:
        print("\nВыбор файла отменен.")
        return None

    filepath = Path(filepath_str)

    # 1. Проверка наличия .json файла с метаданными
    json_path = filepath.with_suffix('.json')
    if not json_path.exists():
        print(f"\n[Ошибка] Файл метаданных не найден: {json_path.name}")
        print("Для каждого .parquet файла должен существовать .json файл с тем же именем.")
        return None

    # 2. Парсинг пути для извлечения компонентов
    try:
        instrument = filepath.stem  # Имя файла без расширения
        interval = filepath.parent.name
        exchange = filepath.parent.parent.name

        # Простая валидация, что структура папок похожа на правду
        if not instrument or not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить структуру 'exchange/interval' из пути к файлу.")
        print(f"Убедитесь, что файл находится в папке вида '.../{exchange}/{interval}/'.")
        return None

    print(f"\nВыбран инструмент: {exchange.upper()} / {interval} / {instrument.upper()}")
    return {
        "exchange": exchange,
        "interval": interval,
        "instrument": instrument
    }


def select_instrument_folder() -> Optional[Dict[str, str]]:
    """
    Открывает системный диалог для выбора папки с данными для одного интервала.
    Парсит путь для извлечения exchange и interval.

    :return: Словарь с параметрами или None в случае отмены/ошибки.
    """
    _initialize_tk()
    dirpath_str = filedialog.askdirectory(
        title="Выберите папку с данными (например, '.../data/tinkoff/5min')"
    )

    if not dirpath_str:
        print("\nВыбор папки отменен.")
        return None

    dirpath = Path(dirpath_str)

    # Проверка, есть ли в папке .parquet файлы
    if not any(dirpath.glob('*.parquet')):
        print(f"\n[Предупреждение] В выбранной папке '{dirpath.name}' не найдено .parquet файлов.")
        # Мы не прерываем, так как batch_tester сам обработает пустой список

    # Парсинг пути
    try:
        interval = dirpath.name
        exchange = dirpath.parent.name

        if not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить структуру 'exchange/interval' из пути к папке.")
        print(f"Убедитесь, что вы выбрали папку вида '.../{exchange}/{interval}/'.")
        return None

    print(f"\nВыбрана папка: {exchange.upper()} / {interval}")
    return {
        "exchange": exchange,
        "interval": interval
    }