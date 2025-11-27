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

        # Фильтруем бесконечные значения для корректного расчета среднего
        finite_pf = self.results_df[np.isfinite(self.results_df['profit_factor'])]['profit_factor']
        avg_profit_factor = finite_pf.mean() if not finite_pf.empty else 0.0

        # Находим лучший и худший инструменты
        best_instrument = self.results_df.loc[self.results_df['pnl_pct'].idxmax()]
        worst_instrument = self.results_df.loc[self.results_df['pnl_pct'].idxmin()]

        summary_data = {
            "Параметр": [
                "Всего инструментов",
                "---",
                "Суммарный PnL портфеля (абс.)",
                "Средний PnL на инструмент (%)",
                "Средний PnL (B&H %)",
                "---",
                "Лучший инструмент",
                "Худший инструмент",
                "---",
                "Доля прибыльных инструментов (%)",
                "Доля инструментов лучше B&H (%)",
                "---",
                "Средний Win Rate (%)",
                "Средний Profit Factor",
                "Средняя макс. просадка (%)"
            ],
            "Значение": [
                len(self.results_df),
                "---",
                self.results_df['pnl_abs'].sum(),  # Сумма денег со всех ботов
                self.results_df['pnl_pct'].mean(),
                self.results_df['pnl_bh_pct'].mean(),
                "---",
                f"{best_instrument['instrument']} ({best_instrument['pnl_pct']:.2f}%)",
                f"{worst_instrument['instrument']} ({worst_instrument['pnl_pct']:.2f}%)",
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

                # --- Стили ---
                header_format = workbook.add_format({'bold': True, 'font_size': 14, 'bg_color': '#DDEBF7', 'border': 1})
                subheader_format = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2', 'border': 1})
                default_format = workbook.add_format({'num_format': '#,##0.00'})
                percent_format = workbook.add_format({'num_format': '0.00"%"'})
                int_format = workbook.add_format({'num_format': '0'})

                # Стили для условного форматирования (Зеленый / Красный текст)
                green_fmt = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100'})
                red_fmt = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})

                # ==========================================
                # ЛИСТ 1: СВОДКА (Summary)
                # ==========================================
                summary_df = self._calculate_summary_metrics()
                summary_sheet = workbook.add_worksheet('Сводка')

                summary_sheet.write('A1', f"Сводный отчет: {self.strategy_name}", header_format)
                summary_sheet.merge_range('A2:C2', f"Интервал: {self.interval} | RM: {self.risk_manager_type}")
                summary_sheet.merge_range('A3:C3', f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                # Параметры запуска
                curr_row = 5
                summary_sheet.write(f'A{curr_row}', "Параметры стратегии:", subheader_format)
                for key, val in self.strategy_params.items():
                    summary_sheet.write(curr_row, 1, key)
                    summary_sheet.write(curr_row, 2, str(val))
                    curr_row += 1

                curr_row += 1
                summary_sheet.write(f'A{curr_row}', "Параметры риск-менеджера:", subheader_format)
                for key, val in self.rm_params.items():
                    summary_sheet.write(curr_row, 1, key)
                    summary_sheet.write(curr_row, 2, str(val))
                    curr_row += 1

                # Таблица метрик
                curr_row += 2
                summary_sheet.write(f'A{curr_row}', "Ключевые показатели", subheader_format)

                # Пишем таблицу сводки вручную или через pandas (через pandas проще)
                summary_df.to_excel(writer, sheet_name='Сводка', index=False, startrow=curr_row, header=False)

                # Форматирование колонок сводки
                summary_sheet.set_column('A:A', 35)
                summary_sheet.set_column('B:C', 20)

                # ==========================================
                # ЛИСТ 2: ДЕТАЛИЗАЦИЯ (Detailed)
                # ==========================================

                # 1. Подготовка данных
                df_export = self.results_df.copy()

                # Добавляем полезную метрику: Средний PnL на сделку
                # (защита от деления на ноль)
                df_export['avg_trade_pnl'] = df_export.apply(
                    lambda x: x['pnl_abs'] / x['total_trades'] if x['total_trades'] > 0 else 0, axis=1
                )

                # Переименование для красоты
                rename_map = {
                    'instrument': 'Инструмент',
                    'pnl_pct': 'PnL (%)',
                    'pnl_abs': 'PnL (абс.)',
                    'pnl_bh_pct': 'B&H (%)',
                    'avg_trade_pnl': 'Ср. PnL сделки',
                    'total_trades': 'Сделок',
                    'win_rate': 'Win Rate',
                    'profit_factor': 'PF',
                    'max_drawdown': 'Max DD (%)',
                    'sharpe_ratio': 'Sharpe',
                    'calmar_ratio': 'Calmar'
                }
                df_export.rename(columns=rename_map, inplace=True)

                # 2. ЖЕСТКИЙ ПОРЯДОК КОЛОНОК (Инструмент - ПЕРВЫЙ)
                cols_order = [
                    'Инструмент',
                    'PnL (%)', 'PnL (абс.)', 'B&H (%)',
                    'Ср. PnL сделки', 'Сделок', 'Win Rate', 'PF', 'Max DD (%)',
                    'Sharpe', 'Calmar'
                ]
                # Оставляем только те, что есть в наличии (на всякий случай)
                final_cols = [c for c in cols_order if c in df_export.columns]
                df_export = df_export[final_cols]

                # 3. Сортировка по PnL % (от лучших к худшим)
                df_export.sort_values(by='PnL (%)', ascending=False, inplace=True)

                # 4. Запись в Excel
                df_export.to_excel(writer, sheet_name='Детализация', index=False)
                details_sheet = writer.sheets['Детализация']

                # 5. Форматирование колонок
                # Заголовки
                for col_num, value in enumerate(df_export.columns.values):
                    details_sheet.write(0, col_num, value, subheader_format)

                # Ширина
                details_sheet.set_column('A:A', 15, None)  # Инструмент
                details_sheet.set_column('B:E', 12, default_format)  # PnL и деньги
                details_sheet.set_column('F:F', 8, int_format)  # Сделки
                details_sheet.set_column('G:G', 10, percent_format)  # WinRate
                details_sheet.set_column('H:K', 10, default_format)  # PF, DD, Ratios

                # Спец. форматы для процентов
                # PnL % (Col B, индекс 1) и B&H % (Col D, индекс 3) и Max DD (Col I, индекс 8)
                details_sheet.set_column(1, 1, 12, percent_format)
                details_sheet.set_column(3, 3, 12, percent_format)
                details_sheet.set_column(6, 6, 10, percent_format)  # Winrate
                details_sheet.set_column(8, 8, 10, percent_format)  # Max DD

                # Условное форматирование (Зеленый/Красный) для PnL % (Колонка B)
                # B2:B1000
                details_sheet.conditional_format(1, 1, len(df_export), 1,
                                                 {'type': 'cell', 'criteria': '>', 'value': 0, 'format': green_fmt})
                details_sheet.conditional_format(1, 1, len(df_export), 1,
                                                 {'type': 'cell', 'criteria': '<', 'value': 0, 'format': red_fmt})

            logger.info(f"Excel-отчет успешно сохранен в: {output_path}")

        except Exception as e:
            logger.error(f"Не удалось сгенерировать Excel-отчет: {e}", exc_info=True)