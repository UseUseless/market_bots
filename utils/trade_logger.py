import json
import os
from datetime import datetime, UTC
import logging

def log_trade(
    trade_log_file: str, strategy_name: str, instrument: str, direction: str,
    entry_price: float, exit_price: float, pnl: float, exit_reason: str,
    interval: str, risk_manager: str
):
    """
    Записывает информацию о завершенной сделке в указанный файл в формате JSONL.
    """
    try:
        os.makedirs(os.path.dirname(trade_log_file), exist_ok=True)

        row_data = {
            'timestamp_utc': datetime.now(UTC).isoformat(),
            'strategy_name': strategy_name,
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