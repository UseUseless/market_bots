from typing import Dict, Any

METRIC_CONFIG: Dict[str, Dict[str, Any]] = {
    "calmar_ratio": {
        "name": "Calmar Ratio",
        "direction": "maximize",
        "description": "Годовая доходность / Макс. просадка. Идеально для минимизации просадок."
    },
    "sharpe_ratio": {
        "name": "Sharpe Ratio",
        "direction": "maximize",
        "description": "Доходность / Волатильность. Классический универсальный выбор."
    },
    "sortino_ratio": {
        "name": "Sortino Ratio",
        "direction": "maximize",
        "description": "Доходность / Волатильность убытков. Улучшенный Шарп."
    },
    "profit_factor": {
        "name": "Profit Factor",
        "direction": "maximize",
        "description": "Суммарная прибыль / Суммарный убыток. Просто и понятно."
    },
    "pnl_to_drawdown": {
        "name": "PnL / Max Drawdown",
        "direction": "maximize",
        "description": "Общий PnL / Макс. просадка. Интуитивно понятная метрика."
    },
    "sqn": {
        "name": "SQN (System Quality Number)",
        "direction": "maximize",
        "description": "Комплексная метрика качества системы от Вана Тарпа."
    },
    "pnl": {
        "name": "Total PnL (Чистая прибыль)",
        "direction": "maximize",
        "description": "Максимизация итоговой чистой прибыли."
    },
    "win_rate": {
        "name": "Win Rate (Процент прибыльных сделок)",
        "direction": "maximize",
        "description": "Максимизация доли прибыльных сделок."
    },
    "max_drawdown": {
        "name": "Max Drawdown (Макс. просадка)",
        "direction": "minimize",
        "description": "Минимизация максимальной просадки капитала."
    },
    # КАСТОМНАЯ МЕТРИКА
    "custom_metric": {
        "name": "Custom (PF * WR / MDD)",
        "direction": "maximize",
        "description": "Наша уникальная функция: (Profit Factor * Win Rate) / Max Drawdown."
    }
}
