import json
import os
from datetime import datetime
import logging


def log_trade(
    trade_log_file: str | None,
    strategy_name: str, exchange: str, instrument: str, direction: str,
    entry_timestamp: datetime, exit_timestamp: datetime,
    entry_price: float, exit_price: float, pnl: float, exit_reason: str,
    interval: str, risk_manager: str
):
    """
    Записывает информацию о завершенной сделке в указанный файл в формате JSONL.
    Если trade_log_file равен None, функция ничего не делает.
    """

    if trade_log_file is None:
        return

    try:
        os.makedirs(os.path.dirname(trade_log_file), exist_ok=True)

        row_data = {
            'entry_timestamp_utc': entry_timestamp.isoformat(),
            'exit_timestamp_utc': exit_timestamp.isoformat(),
            'strategy_name': strategy_name,
            'exchange': exchange,
            'instrument': instrument,
            'direction': direction,
            'entry_price': round(entry_price, 4),
            'exit_price': round(exit_price, 4),
            'pnl': round(pnl, 4),
            'exit_reason': exit_reason,
            'interval': interval,
            'risk_manager': risk_manager
        }

        with open(trade_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row_data) + '\n')

    except (IOError, TypeError) as e:
        logging.error(f"Не удалось записать сделку в файл {trade_log_file}: {e}")
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при записи лога сделки: {e}")