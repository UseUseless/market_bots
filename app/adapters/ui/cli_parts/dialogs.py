import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import Dict, Optional

def _initialize_tk():
    """
    Создает и немедленно скрывает корневое окно Tkinter.

    Это стандартный паттерн, когда нам не нужно полноценное GUI-приложение,
    а требуются только системные диалоговые окна (выбор файла, папки и т.д.).
    """
    root = tk.Tk()
    root.withdraw()  # Скрываем основное окно
    # Дополнительные настройки, чтобы окно не появлялось на панели задач в некоторых ОС
    root.wm_attributes("-topmost", 1)
    return root

def select_single_instrument() -> Optional[Dict[str, str]]:
    """
    Открывает системный диалог для выбора одного .parquet файла с данными инструмента.

    Эта функция выполняет несколько ключевых проверок:
    1. Убеждается, что пользователь выбрал файл, а не отменил операцию.
    2. Проверяет наличие обязательного .json файла с метаданными рядом с .parquet файлом.
    3. Парсит путь к файлу, чтобы автоматически извлечь биржу, интервал и имя инструмента,
       основываясь на структуре папок `.../data/{exchange}/{interval}/{instrument}.parquet`.

    :return: Словарь с ключами "exchange", "interval", "instrument" в случае успеха,
             иначе None, если пользователь отменил выбор или структура папок некорректна.
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
        instrument = filepath.stem  # Имя файла без расширения (SBER)
        interval = filepath.parent.name  # Имя родительской папки (5min)
        exchange = filepath.parent.parent.name  # Имя "дедушкиной" папки (tinkoff)

        # Простая валидация, что структура папок похожа на правду
        if not instrument or not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить структуру 'exchange/interval' из пути к файлу.")
        print(f"Убедитесь, что файл находится в папке вида '.../data/{exchange}/{interval}/'.")
        return None

    print(f"\nВыбран инструмент: {exchange.upper()} / {interval} / {instrument.upper()}")
    return {
        "exchange": exchange,
        "interval": interval,
        "instrument": instrument
    }


def select_instrument_folder() -> Optional[Dict[str, str]]:
    """
    Открывает системный диалог для выбора папки, содержащей .parquet файлы
    для одного интервала (например, папки '.../data/tinkoff/5min').

    Функция также парсит путь для извлечения биржи и интервала.

    :return: Словарь с ключами "exchange", "interval" в случае успеха,
             иначе None, если пользователь отменил выбор или структура папок некорректна.
    """
    _initialize_tk()
    dirpath_str = filedialog.askdirectory(
        title="Выберите папку с данными (например, '.../data/tinkoff/5min')"
    )

    if not dirpath_str:
        print("\nВыбор папки отменен.")
        return None

    dirpath = Path(dirpath_str)

    # Проверка, есть ли в папке .parquet файлы (необязательная, но полезная)
    if not any(dirpath.glob('*.parquet')):
        print(f"\n[Предупреждение] В выбранной папке '{dirpath.name}' не найдено .parquet файлов.")
        # Мы не прерываем выполнение, так как batch_tester сам обработает пустой список

    # Парсинг пути
    try:
        interval = dirpath.name
        exchange = dirpath.parent.name

        if not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить структуру 'exchange/interval' из пути к папке.")
        print(f"Убедитесь, что вы выбрали папку вида '.../data/{exchange}/{interval}/'.")
        return None

    print(f"\nВыбрана папка: {exchange.upper()} / {interval}")
    return {
        "exchange": exchange,
        "interval": interval
    }