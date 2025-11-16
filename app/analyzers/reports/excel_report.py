import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class ExcelReportGenerator:
    """
    Генерирует детальные отчеты по результатам пакетного тестирования в формате Excel.
    """

    def __init__(self,
                 results_df: pd.DataFrame,
                 strategy_name: str,
                 interval: str,
                 risk_manager_type: str,
                 strategy_params: Optional[Dict[str, Any]] = None,
                 rm_params: Optional[Dict[str, Any]] = None):
        if results_df.empty:
            raise ValueError("DataFrame с результатами для Excel-отчета не может быть пустым.")

        self.results_df = results_df.copy()
        self.strategy_name = strategy_name
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.strategy_params = strategy_params or {}
        self.rm_params = rm_params or {}

    def _calculate_summary_metrics(self) -> pd.DataFrame:
        """Рассчитывает сводные метрики по всему портфелю инструментов."""
        finite_pf = self.results_df[np.isfinite(self.results_df['profit_factor'])]['profit_factor']
        avg_profit_factor = finite_pf.mean() if not finite_pf.empty else 0.0

        summary_data = {
            "Параметр": [
                "Средний PnL (%)", "Медианный PnL (%)", "Средний PnL (B&H %)",
                "---",
                "Доля прибыльных инструментов (%)", "Доля инструментов лучше B&H (%)",
                "---",
                "Средний Win Rate (%)", "Средний Profit Factor", "Средняя макс. просадка (%)"
            ],
            "Значение": [
                self.results_df['pnl_pct'].mean(),
                self.results_df['pnl_pct'].median(),
                self.results_df['pnl_bh_pct'].mean(),
                "---",
                (self.results_df['pnl_pct'] > 0).mean() * 100,
                (self.results_df['pnl_pct'] > self.results_df['pnl_bh_pct']).mean() * 100,
                "---",
                self.results_df['win_rate'].mean() * 100,
                avg_profit_factor,
                self.results_df['max_drawdown'].mean() * 100
            ]
        }
        return pd.DataFrame(summary_data)

    def generate(self, output_path: str):
        """Создает и сохраняет Excel-отчет с двумя листами."""
        try:
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                workbook = writer.book
                header_format = workbook.add_format({'bold': True, 'font_size': 14, 'bg_color': '#DDEBF7', 'border': 1})
                subheader_format = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2'})
                default_format = workbook.add_format({'num_format': '#,##0.00'})
                percent_format = workbook.add_format({'num_format': '0.00"%"'})

                # --- Лист 1: Сводка ---
                summary_df = self._calculate_summary_metrics()

                # Заголовок
                summary_sheet = workbook.add_worksheet('Сводка')
                summary_sheet.write('A1', f"Сводный отчет по стратегии: {self.strategy_name}", header_format)
                summary_sheet.merge_range('A2:C2', f"Интервал: {self.interval}, Риск-менеджер: {self.risk_manager_type}")
                summary_sheet.merge_range('A3:C3', f"Дата генерации: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                current_row = 5
                summary_sheet.write(f'A{current_row}', "Параметры стратегии:", subheader_format)
                for key, val in self.strategy_params.items():
                    summary_sheet.write(current_row, 1, key)
                    summary_sheet.write(current_row, 2, str(val))
                    current_row += 1

                current_row += 1
                summary_sheet.write(f'A{current_row}', "Параметры риск-менеджера:", subheader_format)
                for key, val in self.rm_params.items():
                    summary_sheet.write(current_row, 1, key)
                    summary_sheet.write(current_row, 2, str(val))
                    current_row += 1

                # Таблица со сводкой
                current_row += 2
                summary_sheet.write(f'A{current_row}', "Сводные метрики по портфелю", subheader_format)
                summary_df.to_excel(writer, sheet_name='Сводка', index=False, startrow=current_row)

                summary_sheet.set_column('A:A', 35)
                summary_sheet.set_column('B:C', 15)

                # --- Лист 2: Детализация ---
                details_df = self.results_df.rename(columns={
                    'instrument': 'Инструмент', 'pnl_abs': 'PnL (абс.)', 'pnl_pct': 'PnL (%)',
                    'pnl_bh_pct': 'PnL (B&H %)', 'total_trades': 'Кол-во сделок', 'win_rate': 'Win Rate (%)',
                    'profit_factor': 'Profit Factor', 'max_drawdown': 'Макс. просадка (%)',
                    'sharpe_ratio': 'Sharpe Ratio (ann.)', 'calmar_ratio': 'Calmar Ratio (ann.)'
                })
                details_df['Win Rate (%)'] *= 100
                details_df['Макс. просадка (%)'] *= 100

                details_df.sort_values(by='PnL (%)', ascending=False, inplace=True)
                details_df.to_excel(writer, sheet_name='Детализация', index=False)

                details_sheet = writer.sheets['Детализация']

                # Заголовки таблицы
                for col_num, value in enumerate(details_df.columns.values):
                    details_sheet.write(0, col_num, value, subheader_format)

                # Умный подбор ширины колонок
                for i, col in enumerate(details_df.columns):
                    column_len = max(details_df[col].astype(str).map(len).max(), len(col))
                    details_sheet.set_column(i, i, column_len + 2)

                # Применяем форматы к колонкам
                details_sheet.set_column('B:B', None, default_format)  # PnL (абс.)
                details_sheet.set_column('C:D', None, percent_format)  # PnL (%), PnL (B&H %)
                details_sheet.set_column('F:F', None, percent_format)  # Win Rate
                details_sheet.set_column('G:G', None, default_format)  # Profit Factor
                details_sheet.set_column('H:H', None, percent_format)  # Max Drawdown
                details_sheet.set_column('I:J', None, default_format)  # Sharpe, Calmar

                # Условное форматирование для PnL
                details_sheet.conditional_format('C2:C1000', {'type': 'cell', 'criteria': '>', 'value': 0,
                                                              'format': workbook.add_format(
                                                                  {'bg_color': '#C6EFCE', 'font_color': '#006100'})})
                details_sheet.conditional_format('C2:C1000', {'type': 'cell', 'criteria': '<', 'value': 0,
                                                              'format': workbook.add_format(
                                                                  {'bg_color': '#FFC7CE', 'font_color': '#9C0006'})})

            logger.info(f"Excel-отчет успешно сохранен в: {output_path}")

        except Exception as e:
            logger.error(f"Не удалось сгенерировать Excel-отчет: {e}", exc_info=True)
