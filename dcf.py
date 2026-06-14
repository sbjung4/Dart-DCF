import pandas as pd
import numpy as np
from typing import Optional


class DCFModel:
    def __init__(self, historical_data: dict, assumptions: dict):
        """
        historical_data: dict with keys like 'revenue', 'ebit', 'net_income', 'da', 'capex',
                        'nwc', 'total_debt', 'cash', 'total_equity', 'tax_rate', 'shares_outstanding'
                        Values are dicts or scalars
        assumptions: dict with all DCF assumptions
        """
        self.historical_data = historical_data
        self.assumptions = assumptions
        self.projection_years = assumptions.get('projection_years', 5)
        self.base_year = assumptions.get('base_year', 2023)
        self.years = [self.base_year + i + 1 for i in range(self.projection_years)]

    def project_revenue(self) -> pd.DataFrame:
        """Project revenue by segment or total"""
        # If segment-based
        if self.assumptions.get('revenue_method') == 'segment':
            segments = self.assumptions.get('revenue_segments', [])
            df = pd.DataFrame(index=[s['name'] for s in segments], columns=self.years)
            for seg in segments:
                base = float(seg['base'])
                for i, yr in enumerate(self.years):
                    growth = float(seg['growth_rates'][i]) / 100
                    if i == 0:
                        df.loc[seg['name'], yr] = base * (1 + growth)
                    else:
                        df.loc[seg['name'], yr] = df.loc[seg['name'], self.years[i-1]] * (1 + growth)
            df.loc['Total'] = df.sum()
            return df
        else:
            # Total revenue
            total = self.assumptions.get('revenue_total', {})
            base = float(total.get('base', self.historical_data.get('revenue', 0)))
            growth_rates = total.get('growth_rates', [0.05] * self.projection_years)
            revenues = []
            prev = base
            for i, gr in enumerate(growth_rates):
                rev = prev * (1 + float(gr) / 100)
                revenues.append(rev)
                prev = rev
            df = pd.DataFrame({'Total': revenues}, index=self.years).T
            return df

    def project_costs(self, revenue_df: pd.DataFrame) -> pd.DataFrame:
        """Project COGS and SGA"""
        total_revenue = revenue_df.loc['Total'] if 'Total' in revenue_df.index else revenue_df.iloc[0]
        result = pd.DataFrame(index=['COGS', 'SGA', 'Gross_Profit', 'EBIT'], columns=self.years)

        # COGS
        cogs_method = self.assumptions.get('cogs_method', 'pct_revenue')
        if cogs_method == 'pct_revenue':
            cogs_pct = float(self.assumptions.get('cogs_pct', 60)) / 100
            for yr in self.years:
                result.loc['COGS', yr] = total_revenue[yr] * cogs_pct
        else:
            base_cogs = float(self.historical_data.get('cogs', 0))
            cogs_growths = self.assumptions.get('cogs_growth', [0.03] * self.projection_years)
            prev = base_cogs
            for i, yr in enumerate(self.years):
                val = prev * (1 + float(cogs_growths[i]) / 100)
                result.loc['COGS', yr] = val
                prev = val

        # SGA
        sga_method = self.assumptions.get('sga_method', 'pct_revenue')
        if sga_method == 'pct_revenue':
            sga_pct = float(self.assumptions.get('sga_pct', 20)) / 100
            for yr in self.years:
                result.loc['SGA', yr] = total_revenue[yr] * sga_pct
        else:
            base_sga = float(self.historical_data.get('sga', 0))
            sga_growths = self.assumptions.get('sga_growth', [0.03] * self.projection_years)
            prev = base_sga
            for i, yr in enumerate(self.years):
                val = prev * (1 + float(sga_growths[i]) / 100)
                result.loc['SGA', yr] = val
                prev = val

        # Gross profit and EBIT
        for yr in self.years:
            result.loc['Gross_Profit', yr] = total_revenue[yr] - result.loc['COGS', yr]
            result.loc['EBIT', yr] = result.loc['Gross_Profit', yr] - result.loc['SGA', yr]

        # Override with EBIT margin if specified
        if self.assumptions.get('ebit_margin_override'):
            ebit_margins = self.assumptions.get('ebit_margins', [0.10] * self.projection_years)
            for i, yr in enumerate(self.years):
                result.loc['EBIT', yr] = total_revenue[yr] * float(ebit_margins[i]) / 100

        return result.astype(float)

    def project_da(self, revenue_df: pd.DataFrame) -> pd.Series:
        """Project D&A"""
        total_revenue = revenue_df.loc['Total'] if 'Total' in revenue_df.index else revenue_df.iloc[0]
        da_method = self.assumptions.get('da_method', 'pct_revenue')
        da_values = {}

        if da_method == 'pct_revenue':
            da_pct = float(self.assumptions.get('da_pct', 3)) / 100
            for yr in self.years:
                da_values[yr] = total_revenue[yr] * da_pct
        elif da_method == 'fixed':
            fixed_da = float(self.assumptions.get('da_fixed', self.historical_data.get('da', 0)))
            for yr in self.years:
                da_values[yr] = fixed_da
        else:  # growth
            base_da = float(self.historical_data.get('da', 0))
            da_growths = self.assumptions.get('da_growth', [0.03] * self.projection_years)
            prev = base_da
            for i, yr in enumerate(self.years):
                val = prev * (1 + float(da_growths[i]) / 100)
                da_values[yr] = val
                prev = val

        return pd.Series(da_values)

    def project_capex(self, da_series: pd.Series) -> pd.Series:
        """Project CapEx"""
        capex_method = self.assumptions.get('capex_method', 'equal_da')
        capex_values = {}

        if capex_method == 'equal_da':
            for yr in self.years:
                capex_values[yr] = da_series[yr]
        elif capex_method == 'pct_revenue':
            # Need revenue - will be recalculated in calculate_fcff
            capex_pct = float(self.assumptions.get('capex_pct', 3)) / 100
            for yr in self.years:
                capex_values[yr] = 0  # placeholder
        else:  # fixed
            fixed_capex = float(self.assumptions.get('capex_fixed', self.historical_data.get('capex', 0)))
            for yr in self.years:
                capex_values[yr] = fixed_capex

        return pd.Series(capex_values)

    def project_nwc_change(self, revenue_df: pd.DataFrame) -> pd.Series:
        """Project change in NWC"""
        total_revenue = revenue_df.loc['Total'] if 'Total' in revenue_df.index else revenue_df.iloc[0]
        nwc_method = self.assumptions.get('nwc_method', 'pct_revenue')
        nwc_changes = {}

        if nwc_method == 'pct_revenue':
            nwc_pct = float(self.assumptions.get('nwc_pct', 10)) / 100
            prev_nwc = float(self.historical_data.get('nwc', 0))
            for yr in self.years:
                curr_nwc = total_revenue[yr] * nwc_pct
                nwc_changes[yr] = curr_nwc - prev_nwc
                prev_nwc = curr_nwc
        else:  # fixed
            fixed_change = float(self.assumptions.get('nwc_fixed_change', 0))
            for yr in self.years:
                nwc_changes[yr] = fixed_change

        return pd.Series(nwc_changes)

    def calculate_fcff(self) -> pd.DataFrame:
        """Calculate FCFF = EBIT*(1-t) + D&A - CapEx - ΔNWC"""
        revenue_df = self.project_revenue()
        total_revenue = revenue_df.loc['Total'] if 'Total' in revenue_df.index else revenue_df.iloc[0]
        cost_df = self.project_costs(revenue_df)
        da_series = self.project_da(revenue_df)

        # Recalculate capex with revenue if pct method
        capex_method = self.assumptions.get('capex_method', 'equal_da')
        if capex_method == 'pct_revenue':
            capex_pct = float(self.assumptions.get('capex_pct', 3)) / 100
            capex_series = pd.Series({yr: total_revenue[yr] * capex_pct for yr in self.years})
        else:
            capex_series = self.project_capex(da_series)

        nwc_series = self.project_nwc_change(revenue_df)
        tax_rate = float(self.assumptions.get('tax_rate', 25)) / 100

        rows = []
        for yr in self.years:
            ebit = cost_df.loc['EBIT', yr]
            nopat = ebit * (1 - tax_rate)
            da = da_series[yr]
            capex = capex_series[yr]
            dnwc = nwc_series[yr]
            fcff = nopat + da - capex - dnwc
            rows.append({
                'Year': yr,
                'Revenue': total_revenue[yr],
                'EBIT': ebit,
                'EBIT_after_tax': nopat,
                'DA': da,
                'CapEx': capex,
                'Delta_NWC': dnwc,
                'FCFF': fcff
            })

        return pd.DataFrame(rows).set_index('Year')

    def calculate_wacc(self) -> float:
        """Calculate WACC"""
        # Cost of equity
        ke_method = self.assumptions.get('ke_method', 'direct')
        if ke_method == 'capm':
            rf = float(self.assumptions.get('risk_free_rate', 3.5)) / 100
            beta = float(self.assumptions.get('beta', 1.0))
            erp = float(self.assumptions.get('equity_risk_premium', 5.0)) / 100
            ke = rf + beta * erp
        else:
            ke = float(self.assumptions.get('cost_of_equity', 10)) / 100

        kd_pretax = float(self.assumptions.get('cost_of_debt', 5)) / 100
        tax_rate = float(self.assumptions.get('tax_rate', 25)) / 100
        kd = kd_pretax * (1 - tax_rate)

        # Capital structure weights
        if self.assumptions.get('use_market_weights', False):
            debt_weight = float(self.assumptions.get('debt_weight', 30)) / 100
        else:
            total_debt = float(self.historical_data.get('total_debt', 0))
            total_equity = float(self.historical_data.get('total_equity', 1))
            total_capital = total_debt + total_equity
            debt_weight = total_debt / total_capital if total_capital > 0 else 0.3

        equity_weight = 1 - debt_weight
        wacc = ke * equity_weight + kd * debt_weight
        return wacc

    def calculate_terminal_value(self, terminal_fcff: float, wacc: float, tgr: float) -> float:
        """Gordon Growth Model TV"""
        if wacc <= tgr:
            raise ValueError("WACC must be greater than terminal growth rate")
        return terminal_fcff * (1 + tgr) / (wacc - tgr)

    def calculate_ev(self) -> dict:
        """Calculate Enterprise Value"""
        fcff_df = self.calculate_fcff()
        wacc = self.calculate_wacc()
        tgr = float(self.assumptions.get('terminal_growth_rate', 1.5)) / 100

        # PV of FCFFs
        pv_fcffs = {}
        discount_factors = {}
        for i, yr in enumerate(self.years):
            t = i + 1
            df_factor = 1 / (1 + wacc) ** t
            discount_factors[yr] = df_factor
            pv_fcffs[yr] = fcff_df.loc[yr, 'FCFF'] * df_factor

        total_pv_fcff = sum(pv_fcffs.values())

        # Terminal value
        terminal_fcff = fcff_df.loc[self.years[-1], 'FCFF']
        tv = self.calculate_terminal_value(terminal_fcff, wacc, tgr)
        pv_tv = tv / (1 + wacc) ** self.projection_years

        ev = total_pv_fcff + pv_tv

        return {
            'ev': ev,
            'pv_fcff_by_year': pv_fcffs,
            'pv_tv': pv_tv,
            'tv': tv,
            'wacc': wacc,
            'tgr': tgr,
            'fcff_df': fcff_df,
            'discount_factors': discount_factors,
            'total_pv_fcff': total_pv_fcff
        }

    def sensitivity_analysis(self, wacc_range: list, tgr_range: list) -> pd.DataFrame:
        """5x5 sensitivity grid of equity value per share"""
        results = {}
        shares = float(self.historical_data.get('shares_outstanding', 1))
        net_debt = float(self.historical_data.get('total_debt', 0)) - float(self.historical_data.get('cash', 0))

        for tgr in tgr_range:
            col_data = {}
            for wacc in wacc_range:
                try:
                    orig_wacc = self.assumptions.get('wacc_override')
                    orig_tgr = self.assumptions.get('terminal_growth_rate')
                    self.assumptions['wacc_override'] = wacc * 100
                    self.assumptions['terminal_growth_rate'] = tgr * 100

                    fcff_df = self.calculate_fcff()
                    wacc_val = wacc
                    tgr_val = tgr

                    pv_fcffs = sum(
                        fcff_df.loc[yr, 'FCFF'] / (1 + wacc_val) ** (i+1)
                        for i, yr in enumerate(self.years)
                    )
                    terminal_fcff = fcff_df.loc[self.years[-1], 'FCFF']
                    if wacc_val > tgr_val:
                        tv = terminal_fcff * (1 + tgr_val) / (wacc_val - tgr_val)
                        pv_tv = tv / (1 + wacc_val) ** self.projection_years
                        ev = pv_fcffs + pv_tv
                        equity_val = ev - net_debt
                        price = equity_val / shares if shares > 0 else 0
                        col_data[f'{wacc*100:.1f}%'] = price
                    else:
                        col_data[f'{wacc*100:.1f}%'] = None

                    # Restore
                    if orig_wacc is None:
                        self.assumptions.pop('wacc_override', None)
                    else:
                        self.assumptions['wacc_override'] = orig_wacc
                    if orig_tgr is None:
                        self.assumptions.pop('terminal_growth_rate', None)
                    else:
                        self.assumptions['terminal_growth_rate'] = orig_tgr
                except Exception:
                    col_data[f'{wacc*100:.1f}%'] = None
            results[f'{tgr*100:.1f}%'] = col_data

        df = pd.DataFrame(results)
        df.index.name = 'WACC'
        df.columns.name = 'TGR'
        return df

    def equity_value(self, ev: float) -> float:
        """EV - Net Debt = Equity Value"""
        net_debt = float(self.historical_data.get('total_debt', 0)) - float(self.historical_data.get('cash', 0))
        return ev - net_debt

    def price_per_share(self, equity_value: float, shares_outstanding: float) -> float:
        """Equity value per share"""
        if shares_outstanding <= 0:
            return 0
        return equity_value / shares_outstanding
