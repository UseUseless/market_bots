import matplotlib
import os

# Принудительно переключаем Matplotlib в неинтерактивный режим,
# чтобы избежать ошибок Tcl/Tk в многопоточных средах.
# Это должно быть сделано ДО импорта pyplot.
matplotlib.use('Agg')

# Отключаем генерацию pycache для чистоты (опционально)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'