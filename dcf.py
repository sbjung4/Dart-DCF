"""
DCF calculation engine for Korean listed companies.
All monetary values are in KRW (원). The caller is responsible for unit conversion.

Growth rates and rates (WACC, tax, etc.) are passed as decimals (e.g., 0.05 = 5%).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


class DCFModel:
    """
    Discounted Cash-Flow model (FCFF approach).

    Parameters
    ----------
    historical_data : pd.DataFrame or dict
        Must contain at minimum one row with columns (or dict keys):
            year, revenue, cogs, sga, ebit, net_income, tax_rate,
            da, capex, nwc, total_assets, current_assets,
            current_liabilities, cash, total_debt, total_equity
        If a DataFrame with multiple rows is given, the latest year is used
        as the base.
    assumptions : dict
        All DCF assumptions as decimals where rates are involved.

    Key assumption keys
    -------------------
    projection_years : int (default 5)
    base_year : int

    # Revenue
    revenue_method : 'total' | 'segment'
    revenue_total  : {'base': float, 'growth_rates': [g1..g5]}  # decimals
    revenue_segments : [{'name', 'base', 'growth_rates': [...]}]

    # Costs (decimals)
    cogs_method : 'pct_revenue' | 'growth'
    cogs_pct    : 0-1
    cogs_growth : [g1..g5]
    sga_method  : 'pct_revenue' | 'growth'
    sga_pct     : 0-1
    sga_growth  : [g1..g5]

    # D&A
    da_method : 'pct_revenue' | 'fixed' | 'growth'
    da_pct    : 0-1
    da_fixed  : float (KRW)
    da_growth : [g1..g5]

    # CapEx
    capex_method : 'equal_da' | 'pct_revenue' | 'fixed'
    capex_pct    : 0-1
    capex_fixed  : float (KRW)

    # NWC
    nwc_method : 'pct_revenue' | 'fixed'
    nwc_pct    : 0-1
    nwc_fixed  : float (KRW, annual delta)

    # Rates (all decimals)
    tax_rate              : 0-1
    cost_of_equity        : 0-1  (direct Ke; overrides CAPM)
    risk_free_rate        : 0-1
    beta                  : float
    equity_risk_premium   : 0-1
    cost_of_debt          : 0-1  (pre-tax Kd)
    debt_weight           : 0-1
    equity_weight         : 0-1

    # Terminal
    tgr : 0-1  (terminal growth rate, default 0.015)

    # Equity bridge
    net_debt          : float (KRW)  total_debt - cash; positive = more debt
    shares_outstanding: float (number of shares)
    """

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(self, historical_data, assumptions: dict):
        if isinstance(historical_data, pd.DataFrame):
            self._hist_df = historical_data.sort_values("year") if "year" in historical_data.columns else historical_data
            self._base = self._hist_df.iloc[-1].to_dict()
        elif isinstance(historical_data, dict):
            self._hist_df = pd.DataFrame([historical_data])
            self._base = historical_data.copy()
        else:
            raise TypeError("historical_data must be a DataFrame or dict")

        self.assumptions = assumptions.copy()
        self.n = int(assumptions.get("projection_years", 5))
        self.base_year = int(assumptions.get("base_year", self._base.get("year", 2023)))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _b(self, key, default=0.0):
        """Get a value from the base (latest historical) row."""
        v = self._base.get(key)
        return float(v) if v is not None else default

    def _a(self, key, default=None):
        """Get a value from assumptions."""
        return self.assumptions.get(key, default)

    def _projection_years(self) -> list[int]:
        return [self.base_year + i + 1 for i in range(self.n)]

    @staticmethod
    def _compound(base: float, rates: list[float]) -> list[float]:
        """Apply sequential growth rates to base. Rates are decimals."""
        out = []
        v = base
        for r in rates:
            v = v * (1.0 + float(r))
            out.append(v)
        return out

    def _pad_rates(self, rates, default=0.03) -> list[float]:
        """Ensure rates list has exactly self.n elements."""
        rates = list(rates) if rates else []
        if not rates:
            rates = [default] * self.n
        while len(rates) < self.n:
            rates.append(rates[-1])
        return rates[: self.n]

    # ------------------------------------------------------------------
    # Revenue projection
    # ------------------------------------------------------------------

    def project_revenue(self) -> pd.DataFrame:
        """
        Returns DataFrame with columns: year, revenue [, segment_cols].
        """
        method = self._a("revenue_method", "total")
        years = self._projection_years()

        if method == "segment":
            segs = self._a("revenue_segments", [])
            if segs:
                records = []
                for i, yr in enumerate(years):
                    row = {"year": yr}
                    total = 0.0
                    for seg in segs:
                        base = float(seg.get("base", 0))
                        rates = self._pad_rates(seg.get("growth_rates", []))
                        vals = self._compound(base, rates)
                        val = vals[i] if i < len(vals) else vals[-1]
                        row[seg["name"]] = val
                        total += val
                    row["revenue"] = total
                    records.append(row)
                return pd.DataFrame(records)

        # Default: total
        cfg = self._a("revenue_total") or {}
        base = float(cfg.get("base", self._b("revenue")))
        rates = self._pad_rates(cfg.get("growth_rates", [0.05] * self.n))
        values = self._compound(base, rates)
        return pd.DataFrame({"year": years, "revenue": values})

    # ------------------------------------------------------------------
    # Cost projections
    # ------------------------------------------------------------------

    def project_costs(self, revenue_df: pd.DataFrame) -> pd.DataFrame:
        """
        Returns DataFrame with: year, cogs, sga, gross_profit, ebit.
        """
        revenues = revenue_df["revenue"].values.astype(float)
        years = revenue_df["year"].values

        def _line(label: str, hist_key: str) -> np.ndarray:
            method = self._a(f"{label}_method", "pct_revenue")
            if method == "pct_revenue":
                pct = self._a(f"{label}_pct")
                if pct is None:
                    base_rev = self._b("revenue") or 1.0
                    pct = self._b(hist_key) / base_rev
                return revenues * float(pct)
            elif method == "growth":
                rates = self._pad_rates(self._a(f"{label}_growth", []))
                base = self._b(hist_key)
                return np.array(self._compound(base, rates))
            raise ValueError(f"Unknown {label}_method: {method}")

        cogs = _line("cogs", "cogs")
        sga = _line("sga", "sga")
        gross_profit = revenues - cogs
        ebit = gross_profit - sga

        return pd.DataFrame({
            "year": years,
            "cogs": cogs,
            "sga": sga,
            "gross_profit": gross_profit,
            "ebit": ebit,
        })

    # ------------------------------------------------------------------
    # D&A
    # ------------------------------------------------------------------

    def project_da(self, revenue_df: pd.DataFrame) -> pd.Series:
        """Returns Series of D&A values (length n)."""
        revenues = revenue_df["revenue"].values.astype(float)
        method = self._a("da_method", "pct_revenue")

        if method == "pct_revenue":
            pct = self._a("da_pct")
            if pct is None:
                base_rev = self._b("revenue") or 1.0
                pct = self._b("da") / base_rev
            return pd.Series(revenues * float(pct), name="da")

        if method == "fixed":
            val = float(self._a("da_fixed") or self._b("da"))
            return pd.Series([val] * self.n, name="da")

        if method == "growth":
            rates = self._pad_rates(self._a("da_growth", []))
            return pd.Series(self._compound(self._b("da"), rates), name="da")

        raise ValueError(f"Unknown da_method: {method}")

    # ------------------------------------------------------------------
    # CapEx
    # ------------------------------------------------------------------

    def project_capex(self, da_series: pd.Series, revenue_df: Optional[pd.DataFrame] = None) -> pd.Series:
        """
        Returns Series of total CapEx (positive = outflow).
        총 CAPEX = 유지보수 CAPEX (capex_method) + 신규투자 CAPEX (new_invest_capex_fixed)
        """
        method = self._a("capex_method", "equal_da")

        # 유지보수 CAPEX: 기존 자산 유지에 필요한 최소 투자
        if method == "equal_da":
            maint = pd.Series(da_series.values, name="capex")
        elif method == "pct_revenue":
            if revenue_df is None:
                raise ValueError("revenue_df required for capex_method='pct_revenue'")
            pct = self._a("capex_pct")
            if pct is None:
                base_rev = self._b("revenue") or 1.0
                pct = self._b("capex") / base_rev
            maint = pd.Series(revenue_df["revenue"].values * float(pct), name="capex")
        elif method == "fixed":
            val = float(self._a("capex_fixed") or self._b("capex"))
            maint = pd.Series([val] * self.n, name="capex")
        else:
            raise ValueError(f"Unknown capex_method: {method}")

        # 신규투자 CAPEX: 성장을 위한 추가 투자 (연간 고정액)
        new_invest = float(self._a("new_invest_capex_fixed") or 0.0)
        # 신규투자 CAPEX를 매출 대비 비율로도 지정 가능
        new_invest_pct = self._a("new_invest_capex_pct")
        if new_invest_pct is not None and revenue_df is not None:
            new_invest = revenue_df["revenue"].values * float(new_invest_pct)
            return pd.Series(maint.values + new_invest, name="capex")

        return pd.Series(maint.values + new_invest, name="capex")

    # ------------------------------------------------------------------
    # NWC change
    # ------------------------------------------------------------------

    def project_nwc_change(self, revenue_df: pd.DataFrame) -> pd.Series:
        """Returns Series of ΔNWC (increase = outflow, positive)."""
        revenues = revenue_df["revenue"].values.astype(float)
        method = self._a("nwc_method", "pct_revenue")

        if method == "pct_revenue":
            pct = self._a("nwc_pct")
            if pct is None:
                base_rev = self._b("revenue") or 1.0
                pct = self._b("nwc") / base_rev
            pct = float(pct)
            base_nwc = self._b("revenue") * pct
            nwc_levels = revenues * pct
            prior = np.concatenate([[base_nwc], nwc_levels[:-1]])
            return pd.Series(nwc_levels - prior, name="delta_nwc")

        if method == "fixed":
            val = float(self._a("nwc_fixed") or 0.0)
            return pd.Series([val] * self.n, name="delta_nwc")

        raise ValueError(f"Unknown nwc_method: {method}")

    # ------------------------------------------------------------------
    # FCFF
    # ------------------------------------------------------------------

    def calculate_fcff(self) -> pd.DataFrame:
        """
        FCFF = EBIT*(1-t) + D&A - CapEx - ΔNWC

        Returns DataFrame with columns:
            year, revenue, gross_profit, ebit, nopat, da, capex, delta_nwc, fcff
        """
        rev_df = self.project_revenue()
        cost_df = self.project_costs(rev_df)
        da_s = self.project_da(rev_df)
        capex_s = self.project_capex(da_s, rev_df)
        dnwc_s = self.project_nwc_change(rev_df)

        tax_rate = float(self._a("tax_rate") or self._b("tax_rate") or 0.22)
        tax_rate = max(0.0, min(tax_rate, 0.99))

        ebit = cost_df["ebit"].values
        nopat = ebit * (1.0 - tax_rate)
        da = da_s.values
        capex = capex_s.values
        delta_nwc = dnwc_s.values
        fcff = nopat + da - capex - delta_nwc

        return pd.DataFrame({
            "year": rev_df["year"].values,
            "revenue": rev_df["revenue"].values,
            "gross_profit": cost_df["gross_profit"].values,
            "ebit": ebit,
            "nopat": nopat,
            "da": da,
            "capex": capex,
            "delta_nwc": delta_nwc,
            "fcff": fcff,
        })

    # ------------------------------------------------------------------
    # WACC
    # ------------------------------------------------------------------

    def calculate_wacc(self) -> float:
        """Compute WACC. All inputs assumed to be decimals."""
        # Cost of equity: direct input takes priority over CAPM
        ke = self._a("cost_of_equity")
        if ke is None:
            rf = float(self._a("risk_free_rate") or 0.035)
            beta = float(self._a("beta") or 1.0)
            erp = float(self._a("equity_risk_premium") or 0.055)
            ke = rf + beta * erp
        ke = float(ke)

        # After-tax cost of debt
        kd_pre = float(self._a("cost_of_debt") or 0.05)
        tax_rate = float(self._a("tax_rate") or self._b("tax_rate") or 0.22)
        tax_rate = max(0.0, min(tax_rate, 0.99))
        kd = kd_pre * (1.0 - tax_rate)

        # Weights
        dw = self._a("debt_weight")
        ew = self._a("equity_weight")
        if dw is None or ew is None:
            total_debt = self._b("total_debt")
            total_equity = self._b("total_equity")
            total_cap = total_debt + total_equity
            if total_cap > 0:
                dw = total_debt / total_cap
                ew = total_equity / total_cap
            else:
                dw, ew = 0.3, 0.7
        dw, ew = float(dw), float(ew)
        s = dw + ew
        if s > 0:
            dw, ew = dw / s, ew / s

        return ew * ke + dw * kd

    # ------------------------------------------------------------------
    # Terminal value
    # ------------------------------------------------------------------

    def calculate_terminal_value(self, terminal_fcff: float, wacc: float, tgr: float) -> float:
        """Gordon-Growth: TV = FCFF*(1+g) / (WACC - g)"""
        if wacc <= tgr:
            raise ValueError(f"WACC ({wacc:.3%}) must exceed TGR ({tgr:.3%})")
        return terminal_fcff * (1.0 + tgr) / (wacc - tgr)

    # ------------------------------------------------------------------
    # Enterprise value
    # ------------------------------------------------------------------

    def calculate_ev(self) -> dict:
        """
        Returns dict:
            ev, pv_fcff_by_year (DataFrame), pv_fcffs (ndarray),
            pv_tv, wacc, tgr, fcff_df,
            terminal_fcff, terminal_value, discount_factors (ndarray)
        """
        fcff_df = self.calculate_fcff()
        wacc = self.calculate_wacc()
        tgr = float(self._a("tgr") or 0.015)

        fcffs = fcff_df["fcff"].values.astype(float)
        discount_factors = np.array([1.0 / (1.0 + wacc) ** t for t in range(1, self.n + 1)])
        pv_fcffs = fcffs * discount_factors

        terminal_fcff = fcffs[-1]
        terminal_value = self.calculate_terminal_value(terminal_fcff, wacc, tgr)
        pv_tv = terminal_value * discount_factors[-1]

        ev = float(np.sum(pv_fcffs)) + pv_tv

        pv_fcff_by_year = pd.DataFrame({
            "year": fcff_df["year"].values,
            "fcff": fcffs,
            "discount_factor": discount_factors,
            "pv_fcff": pv_fcffs,
        })

        return {
            "ev": ev,
            "pv_fcff_by_year": pv_fcff_by_year,
            "pv_fcffs": pv_fcffs,
            "pv_tv": pv_tv,
            "wacc": wacc,
            "tgr": tgr,
            "fcff_df": fcff_df,
            "terminal_fcff": terminal_fcff,
            "terminal_value": terminal_value,
            "discount_factors": discount_factors,
        }

    # ------------------------------------------------------------------
    # Sensitivity analysis
    # ------------------------------------------------------------------

    def sensitivity_analysis(
        self,
        wacc_range: list,
        tgr_range: list,
        metric: str = "ev",
    ) -> pd.DataFrame:
        """
        NxM sensitivity table.
        metric : 'ev' | 'price_per_share'
        Returns DataFrame with WACC as index, TGR as columns.
        """
        fcff_df = self.calculate_fcff()
        fcffs = fcff_df["fcff"].values.astype(float)
        terminal_fcff = fcffs[-1]
        n = self.n

        shares = float(self._a("shares_outstanding") or 0)
        net_debt_val = self._a("net_debt")
        if net_debt_val is None:
            net_debt_val = self._b("total_debt") - self._b("cash")
        net_debt_val = float(net_debt_val)

        rows = []
        for w in wacc_range:
            row = {}
            for g in tgr_range:
                w, g = float(w), float(g)
                if w <= g:
                    row[g] = float("nan")
                    continue
                dfs = np.array([1.0 / (1.0 + w) ** t for t in range(1, n + 1)])
                pv_f = float(np.sum(fcffs * dfs))
                try:
                    tv = self.calculate_terminal_value(terminal_fcff, w, g)
                except ValueError:
                    row[g] = float("nan")
                    continue
                pv_tv = tv * dfs[-1]
                ev_val = pv_f + pv_tv

                if metric == "price_per_share" and shares > 0:
                    row[g] = (ev_val - net_debt_val) / shares
                else:
                    row[g] = ev_val
            rows.append(row)

        df = pd.DataFrame(rows, index=[float(w) for w in wacc_range])
        df.columns = [float(g) for g in tgr_range]
        df.index.name = "WACC"
        df.columns.name = "TGR"
        return df

    # ------------------------------------------------------------------
    # Equity value / price per share
    # ------------------------------------------------------------------

    def equity_value(self, ev: float) -> float:
        """EV - Net Debt = Equity Value."""
        net_debt = self._a("net_debt")
        if net_debt is None:
            net_debt = self._b("total_debt") - self._b("cash")
        return ev - float(net_debt)

    def price_per_share(self, equity_value: float, shares_outstanding: float) -> float:
        """Equity Value / Shares = Price per Share."""
        if shares_outstanding <= 0:
            raise ValueError("주식수는 0보다 커야 합니다.")
        return equity_value / shares_outstanding
