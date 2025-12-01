"""
Константы и конфигурация модуля аналитики.

Этот файл содержит реестр всех доступных метрик производительности,
которые используются в системе. Реестр служит "меню" для выбора целей
оптимизации (WFO) и отображения результатов в отчетах.

Каждая метрика описывается словарем с метаданными:
- **name**: Человекочитаемое название.
- **direction**: Направление оптимизации ('maximize' или 'minimize').
- **description**: Краткое пояснение сути метрики.
"""

from typing import Dict, Any

# Реестр метрик.
# Ключ словаря должен совпадать с именем метода в `PortfolioMetricsCalculator`.
METRIC_CONFIG: Dict[str, Dict[str, Any]] = {
    "calmar_ratio": {
        "name": "Calmar Ratio",
        "direction": "maximize",
        "description": "Среднегодовая доходность / Макс. просадка. Лучший выбор для стабильного роста."
    },
    "sharpe_ratio": {
        "name": "Sharpe Ratio",
        "direction": "maximize",
        "description": "Доходность с поправкой на риск (волатильность). Классика портфельной теории."
    },
    "sortino_ratio": {
        "name": "Sortino Ratio",
        "direction": "maximize",
        "description": "Улучшенный Шарп: учитывает только волатильность убытков (Downside Risk)."
    },
    "profit_factor": {
        "name": "Profit Factor",
        "direction": "maximize",
        "description": "Отношение валовой прибыли к валовому убытку. > 1.5 считается хорошим."
    },
    "pnl_to_drawdown": {
        "name": "PnL / Max Drawdown",
        "direction": "maximize",
        "description": "Простая альтернатива Кальмару: Общий PnL / Макс. просадка."
    },
    "sqn": {
        "name": "SQN (System Quality Number)",
        "direction": "maximize",
        "description": "Метрика качества системы (Ван Тарп). Учитывает матожидание и кол-во сделок."
    },
    "pnl": {
        "name": "Total PnL (Чистая прибыль)",
        "direction": "maximize",
        "description": "Абсолютная чистая прибыль в валюте депозита."
    },
    "win_rate": {
        "name": "Win Rate (% прибыльных)",
        "direction": "maximize",
        "description": "Процент прибыльных сделок. Важен для психологии трейдера."
    },
    "max_drawdown": {
        "name": "Max Drawdown (Макс. просадка)",
        "direction": "minimize",
        "description": "Максимальное падение капитала в процентах от пика."
    },
    # Экспериментальные / Кастомные метрики
    "custom_metric": {
        "name": "Custom (PF * WR / MDD)",
        "direction": "maximize",
        "description": "Комплексная метрика: баланс между прибыльностью, точностью и безопасностью."
    }
}