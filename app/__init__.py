"""
Основной пакет приложения.
"""

import matplotlib

# Принудительно переключаем Matplotlib в неинтерактивный режим,
# чтобы избежать ошибок Tcl/Tk в многопоточных средах
# и отключения GUI matplotlib'а
matplotlib.use('Agg')