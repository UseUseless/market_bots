SEARCH_SPACE = {
    "strategy_params": {
        "TripleFilterStrategy": {
            "ema_fast_period": {
                "method": "suggest_int",
                "kwargs": {"name": "ema_fast", "low": 5, "high": 20}
            },
            "ema_slow_period": {
                "method": "suggest_int",
                "kwargs": {"name": "ema_slow", "low": 21, "high": 50}
            },
        },
        # Сюда можно будет добавлять другие стратегии
    },

    "risk_manager_params": {
        "FIXED": {
            "DEFAULT_RISK_PERCENT_LONG": {
                "method": "suggest_float",
                "kwargs": {"name": "rm_risk_long", "low": 0.5, "high": 5.0, "step": 0.1}
            },
            "FIXED_TP_RATIO": {
                "method": "suggest_float",
                "kwargs": {"name": "rm_tp_ratio", "low": 1.0, "high": 7.0, "step": 0.25}
            },
        },
        "ATR": {
            "DEFAULT_RISK_PERCENT_LONG": {
                "method": "suggest_float",
                "kwargs": {"name": "rm_risk_atr_long", "low": 0.5, "high": 5.0, "step": 0.1}
            },
            "ATR_MULTIPLIER_SL": {
                "method": "suggest_float",
                "kwargs": {"name": "rm_atr_sl", "low": 1.0, "high": 4.0, "step": 0.25}
            },
            "ATR_MULTIPLIER_TP": {
                "method": "suggest_float",
                "kwargs": {"name": "rm_atr_tp", "low": 2.0, "high": 8.0, "step": 0.25}
            },
        }
    },
}