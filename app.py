import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import os
import io
import datetime
from dotenv import load_dotenv

load_dotenv()

from dart_api import (
    search_company, get_company_info, get_financial_statements,
    get_business_segments, get_corp_code_list
)
from dcf import DCFModel

st.set_page_config(
    page_title="DART DCF 분석",
    page_icon="📊",
    layout="wide"
)

# ─── Session State Defaults ───────────────────────────────────────────────────
def init_session_state():
    cur_year = datetime.datetime.now().year
    defaults = {
        'api_key': os.getenv('DART_API_KEY', ''),
        'selected_company': None,
        'financial_data': None,
        'historical_summary': None,
        'dcf_assumptions': {},
        'dcf_results': None,
        'search_results': [],
        'base_year': cur_year - 1,
        'page': '기업 검색',
        'fs_div': 'CFS',
        'reprt_code': '11011',
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session_state()

# ─── Utility ──────────────────────────────────────────────────────────────────
def fmt_억(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    억 = value / 1e8
    return f"{억:,.1f}"

def safe_div(a, b, default=0.0):
    try:
        if b == 0 or b is None:
            return default
        return a / b
    except Exception:
        return default

def fmt_df(df):
    """Apply fmt_억 to a DataFrame (pandas 2.1+ compatible)."""
    return df.map(lambda x: fmt_억(x) if isinstance(x, (int, float)) and x != 0 else ('-' if x == 0 else x))

def calc_korea_corp_tax_rate(taxable_income_억: float) -> float:
    """2026 Korea progressive corporate tax rate (법인세법 기준, 2023년 개정 후 유지)."""
    if taxable_income_억 <= 0:
        return 0.22
    if taxable_income_억 <= 2:
        tax = taxable_income_억 * 0.09
    elif taxable_income_억 <= 200:
        tax = 2 * 0.09 + (taxable_income_억 - 2) * 0.19
    elif taxable_income_억 <= 3000:
        tax = 2 * 0.09 + 198 * 0.19 + (taxable_income_억 - 200) * 0.21
    else:
        tax = 2 * 0.09 + 198 * 0.19 + 2800 * 0.21 + (taxable_income_억 - 3000) * 0.24
    return tax / taxable_income_억

def extract_historical_summary(financial_data: dict, years: list) -> dict:
    summary = {
        'revenue': {}, 'cogs': {}, 'gross_profit': {}, 'sga': {},
        'ebit': {}, 'net_income': {}, 'da': {}, 'capex': {},
        'total_assets': {}, 'total_debt': {}, 'total_equity': {}, 'cash': {},
        'operating_cf': {}, 'investing_cf': {}, 'nwc': {}
    }
    for yr in years:
        yr_data = financial_data.get(str(yr), {})
        if not yr_data:
            continue
        is_data = yr_data.get('income_statement', {})
        bs_data = yr_data.get('balance_sheet', {})
        cf_data = yr_data.get('cash_flow', {})

        summary['revenue'][yr] = is_data.get('revenue', 0)
        summary['cogs'][yr] = is_data.get('cogs', 0)
        summary['gross_profit'][yr] = is_data.get('revenue', 0) - is_data.get('cogs', 0)
        summary['sga'][yr] = is_data.get('sga', 0)
        summary['ebit'][yr] = is_data.get('ebit', 0)
        summary['net_income'][yr] = is_data.get('net_income', 0)
        summary['da'][yr] = cf_data.get('da', is_data.get('da', 0))
        summary['capex'][yr] = cf_data.get('capex', 0)
        summary['total_assets'][yr] = bs_data.get('total_assets', 0)
        summary['total_debt'][yr] = bs_data.get('total_debt', 0)
        summary['total_equity'][yr] = bs_data.get('total_equity', 0)
        summary['cash'][yr] = bs_data.get('cash', 0)
        summary['operating_cf'][yr] = cf_data.get('operating_cf', 0)
        summary['investing_cf'][yr] = cf_data.get('investing_cf', 0)
        curr_assets = bs_data.get('current_assets', 0)
        curr_liab = bs_data.get('current_liabilities', 0)
        summary['nwc'][yr] = curr_assets - curr_liab
    return summary

def preview_revenue(base_rev, growth_rates):
    """Return DataFrame of projected revenue by year."""
    rows = []
    v = base_rev
    for i, g in enumerate(growth_rates):
        v = v * (1 + g / 100)
        rows.append({'연도': f'Y{i+1}', '매출액(억원)': v / 1e8, 'YoY성장률': f'{g:.1f}%'})
    df = pd.DataFrame(rows).set_index('연도')
    df['매출액(억원)'] = df['매출액(억원)'].map(lambda x: f'{x:,.1f}')
    return df

def preview_costs(base_rev, growth_rates, cogs_method, cogs_pct, cogs_growth,
                  labor_method, labor_pct, labor_growth,
                  other_sga_method, other_sga_pct, other_sga_growth,
                  base_cogs, base_labor, base_other_sga):
    rows = []
    rev = base_rev
    cogs_v = base_cogs
    labor_v = base_labor
    osga_v = base_other_sga
    for i, g in enumerate(growth_rates):
        rev = rev * (1 + g / 100)
        if cogs_method == 'pct_revenue':
            cogs_v = rev * cogs_pct / 100
        else:
            cogs_v = cogs_v * (1 + (cogs_growth[i] if i < len(cogs_growth) else cogs_growth[-1]) / 100)
        if labor_method == 'pct_revenue':
            labor_v = rev * labor_pct / 100
        else:
            labor_v = labor_v * (1 + (labor_growth[i] if i < len(labor_growth) else labor_growth[-1]) / 100)
        if other_sga_method == 'pct_revenue':
            osga_v = rev * other_sga_pct / 100
        else:
            osga_v = osga_v * (1 + (other_sga_growth[i] if i < len(other_sga_growth) else other_sga_growth[-1]) / 100)
        ebit_v = rev - cogs_v - labor_v - osga_v
        rows.append({
            '연도': f'Y{i+1}',
            '매출원가': f'{cogs_v/1e8:,.1f}',
            'COGS%': f'{safe_div(cogs_v,rev)*100:.1f}%',
            '인건비': f'{labor_v/1e8:,.1f}',
            '기타판관비': f'{osga_v/1e8:,.1f}',
            '영업이익': f'{ebit_v/1e8:,.1f}',
            'EBIT%': f'{safe_div(ebit_v,rev)*100:.1f}%',
        })
    return pd.DataFrame(rows).set_index('연도')

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 DART DCF 분석")
    st.divider()

    api_key_input = st.text_input(
        "DART API Key",
        value=st.session_state.api_key,
        type="password",
        help="dart.fss.or.kr에서 발급받은 API 키를 입력하세요"
    )
    if api_key_input:
        st.session_state.api_key = api_key_input

    st.divider()
    page = st.radio(
        "메뉴",
        ['기업 검색', '재무제표 확인', 'DCF 가정 입력', 'DCF 결과'],
        key='nav_radio'
    )
    st.session_state.page = page

    if st.session_state.selected_company:
        st.divider()
        co = st.session_state.selected_company
        st.success(f"선택된 기업: **{co['corp_name']}**")
        if co.get('stock_code'):
            st.caption(f"종목코드: {co['stock_code']}")
        st.caption(f"재무제표: {st.session_state.fs_div}")

# ─── Page 1: 기업 검색 ────────────────────────────────────────────────────────
if page == '기업 검색':
    st.header("🔍 기업 검색")

    if not st.session_state.api_key:
        st.warning("먼저 사이드바에서 DART API Key를 입력해주세요.")

    col1, col2 = st.columns([3, 1])
    with col1:
        keyword = st.text_input("기업명 또는 종목코드", placeholder="예: 삼성전자, 005930")
    with col2:
        st.write("")
        st.write("")
        search_btn = st.button("🔍 검색", use_container_width=True)

    if search_btn and keyword:
        with st.spinner("기업 목록을 검색 중입니다..."):
            try:
                results = search_company(keyword, st.session_state.api_key)
                st.session_state.search_results = results
            except Exception as e:
                st.error(f"검색 오류: {str(e)}")
                st.session_state.search_results = []

    if st.session_state.search_results:
        st.subheader(f"검색 결과 ({len(st.session_state.search_results)}건)")
        df_results = pd.DataFrame(st.session_state.search_results)
        df_results = df_results[['corp_name', 'stock_code', 'corp_code']].rename(columns={
            'corp_name': '기업명', 'stock_code': '종목코드', 'corp_code': '기업코드'
        })
        st.dataframe(df_results, use_container_width=True, height=300)

        selected_idx = st.selectbox(
            "기업 선택",
            options=range(len(st.session_state.search_results)),
            format_func=lambda i: f"{st.session_state.search_results[i]['corp_name']} ({st.session_state.search_results[i].get('stock_code', 'N/A')})"
        )

        selected_co = st.session_state.search_results[selected_idx]
        is_listed = bool(selected_co.get('stock_code', '').strip())

        st.divider()
        col1, col2, col3, col4 = st.columns(4)

        cur_year = datetime.datetime.now().year
        year_options = list(range(cur_year, 2018, -1))

        with col1:
            base_year = st.selectbox("기준연도", year_options, index=0)

        with col2:
            # 상장사: 분기 선택 가능 / 비상장: 감사보고서만
            if is_listed:
                reprt_label = st.selectbox("보고서 종류", [
                    "사업보고서 (연간)",
                    "반기보고서 (2Q)",
                    "1분기보고서 (1Q)",
                    "3분기보고서 (3Q)",
                ])
                reprt_map = {
                    "사업보고서 (연간)": "11011",
                    "반기보고서 (2Q)": "11012",
                    "1분기보고서 (1Q)": "11013",
                    "3분기보고서 (3Q)": "11014",
                }
                reprt_code = reprt_map[reprt_label]
            else:
                st.selectbox("보고서 종류", ["감사보고서 (연간)"], disabled=True)
                reprt_code = "11011"

        with col3:
            fs_div_label = st.radio("재무제표 구분", ["연결(CFS)", "개별(OFS)"], horizontal=True)
            fs_div = "CFS" if "CFS" in fs_div_label else "OFS"

        with col4:
            st.write("")
            st.write("")
            fetch_btn = st.button("📥 재무제표 불러오기", use_container_width=True)

        if fetch_btn:
            st.session_state.selected_company = selected_co
            st.session_state.base_year = base_year
            st.session_state.fs_div = fs_div
            st.session_state.reprt_code = reprt_code

            if not st.session_state.api_key:
                st.error("API Key가 필요합니다.")
            else:
                with st.spinner(f"{selected_co['corp_name']}의 재무제표를 불러오는 중..."):
                    try:
                        corp_code = selected_co['corp_code']
                        api_key = st.session_state.api_key
                        # 최근 3개 연간 연도 (분기 선택과 무관하게 역사적 데이터는 연간)
                        years = [base_year, base_year - 1, base_year - 2]

                        financial_data = {}
                        for yr in years:
                            try:
                                fs = get_financial_statements(corp_code, yr, api_key,
                                                              report_type='11011',
                                                              fs_div=fs_div)
                                financial_data[str(yr)] = fs
                            except Exception as e:
                                st.warning(f"{yr}년 데이터 로드 실패: {str(e)}")
                                financial_data[str(yr)] = {}

                        # 선택한 보고서 기준 데이터도 별도 로드 (기준연도용)
                        if reprt_code != '11011':
                            try:
                                fs_selected = get_financial_statements(corp_code, base_year, api_key,
                                                                        report_type=reprt_code,
                                                                        fs_div=fs_div)
                                financial_data[f"{base_year}_selected"] = fs_selected
                            except Exception:
                                pass

                        st.session_state.financial_data = financial_data

                        hist = extract_historical_summary(financial_data, years)
                        st.session_state.historical_summary = hist

                        # Auto-fill DCF defaults
                        latest_rev = hist['revenue'].get(base_year, 0)
                        latest_da = hist['da'].get(base_year, 0)
                        latest_capex = hist['capex'].get(base_year, 0)
                        latest_nwc = hist['nwc'].get(base_year, 0)
                        latest_tax_expense = financial_data.get(str(base_year), {}).get('income_statement', {}).get('tax_expense', 0)
                        pretax_income = financial_data.get(str(base_year), {}).get('income_statement', {}).get('pretax_income', 0)

                        eff_tax = safe_div(latest_tax_expense, pretax_income, 0.25) * 100 if pretax_income > 0 else 25.0
                        cogs = hist['cogs'].get(base_year, 0)
                        sga = hist['sga'].get(base_year, 0)
                        cogs_pct = safe_div(cogs, latest_rev, 0.6) * 100
                        sga_pct = safe_div(sga, latest_rev, 0.2) * 100
                        labor_pct = sga_pct * 0.5
                        other_sga_pct = sga_pct * 0.5
                        da_pct = safe_div(latest_da, latest_rev, 0.03) * 100
                        capex_pct = safe_div(latest_capex, latest_rev, 0.03) * 100
                        nwc_pct = safe_div(latest_nwc, latest_rev, 0.1) * 100

                        total_debt = hist['total_debt'].get(base_year, 0)
                        total_equity = hist['total_equity'].get(base_year, 0)
                        total_cap = total_debt + total_equity
                        debt_weight = safe_div(total_debt, total_cap, 0.3) * 100

                        # Progressive tax estimate
                        ebit_억 = hist['ebit'].get(base_year, 0) / 1e8
                        prog_tax_rate = calc_korea_corp_tax_rate(ebit_억) * 100

                        st.session_state.dcf_assumptions = {
                            'base_year': base_year,
                            'projection_years': 5,
                            'revenue_method': 'total',
                            'revenue_total': {
                                'base': latest_rev,
                                'growth_rates': [5.0, 5.0, 4.0, 4.0, 3.0]
                            },
                            'cogs_method': 'pct_revenue',
                            'cogs_pct': round(cogs_pct, 1),
                            'labor_method': 'pct_revenue',
                            'labor_pct': round(labor_pct, 1),
                            'other_sga_method': 'pct_revenue',
                            'other_sga_pct': round(other_sga_pct, 1),
                            'da_method': 'pct_revenue',
                            'da_pct': round(da_pct, 1),
                            'capex_method': 'equal_da',
                            'capex_pct': round(capex_pct, 1),
                            'nwc_method': 'turnover',
                            'nwc_dso': 60.0,
                            'nwc_dio': 45.0,
                            'nwc_dpo': 30.0,
                            'nwc_other_dpo': 20.0,
                            'nwc_pct': round(nwc_pct, 1),
                            'tax_method': 'progressive',
                            'tax_rate': round(min(max(prog_tax_rate, 0), 40), 1),
                            'ke_method': 'capm',
                            'risk_free_rate': 3.5,
                            'beta': 1.0,
                            'equity_risk_premium': 5.0,
                            'cost_of_debt': 4.5,
                            'debt_weight': round(debt_weight, 1),
                            'terminal_growth_rate': 1.5,
                            'shares_outstanding': 0.0,
                        }

                        st.success("✅ 재무제표를 성공적으로 불러왔습니다!")
                        st.info("👉 '재무제표 확인' 메뉴에서 확인하세요.")
                    except Exception as e:
                        st.error(f"재무제표 로드 오류: {str(e)}")
                        import traceback
                        st.code(traceback.format_exc())

# ─── Page 2: 재무제표 확인 ────────────────────────────────────────────────────
elif page == '재무제표 확인':
    st.header("📋 재무제표 확인")

    if not st.session_state.historical_summary:
        st.info("먼저 '기업 검색' 메뉴에서 기업을 선택하고 재무제표를 불러오세요.")
        st.stop()

    hist = st.session_state.historical_summary
    base_year = st.session_state.base_year
    years = sorted([k for k in hist['revenue'].keys()], reverse=True)

    st.subheader(f"📊 {st.session_state.selected_company['corp_name']} 재무 현황")
    st.caption(f"재무제표 구분: {st.session_state.fs_div} | 기준연도: {base_year}")

    # Income Statement
    st.markdown("#### 손익계산서 (단위: 억원)")
    ebitda = {yr: hist['ebit'].get(yr, 0) + hist['da'].get(yr, 0) for yr in years}
    is_rows = {
        '매출액': hist['revenue'],
        '매출원가': hist['cogs'],
        '매출총이익': hist['gross_profit'],
        '판관비': hist['sga'],
        '영업이익(EBIT)': hist['ebit'],
        'EBITDA': ebitda,
        '순이익': hist['net_income'],
    }
    is_df = pd.DataFrame(is_rows).T
    is_df.columns = [str(yr) for yr in is_df.columns]
    st.dataframe(fmt_df(is_df), use_container_width=True)

    # Ratios
    st.markdown("#### 주요 재무비율")
    ratio_rows = {}
    for yr in years:
        rev = hist['revenue'].get(yr, 1) or 1
        ebit = hist['ebit'].get(yr, 0)
        ni = hist['net_income'].get(yr, 0)
        eq = hist['total_equity'].get(yr, 1) or 1
        assets = hist['total_assets'].get(yr, 1) or 1
        ratio_rows[yr] = {
            '영업이익률': f"{ebit/rev*100:.1f}%",
            '순이익률': f"{ni/rev*100:.1f}%",
            'ROE': f"{ni/eq*100:.1f}%",
            'ROA': f"{ni/assets*100:.1f}%",
        }
    st.dataframe(pd.DataFrame(ratio_rows).T, use_container_width=True)

    # Balance Sheet
    st.markdown("#### 재무상태 (단위: 억원)")
    bs_rows = {
        '총자산': hist['total_assets'],
        '총부채': {yr: hist['total_assets'].get(yr,0) - hist['total_equity'].get(yr,0) for yr in years},
        '자기자본': hist['total_equity'],
        '차입금': hist['total_debt'],
        '현금': hist['cash'],
        '순차입금': {yr: hist['total_debt'].get(yr,0) - hist['cash'].get(yr,0) for yr in years},
    }
    bs_df = pd.DataFrame(bs_rows).T
    bs_df.columns = [str(yr) for yr in bs_df.columns]
    st.dataframe(fmt_df(bs_df), use_container_width=True)

    # Cash Flow
    st.markdown("#### 현금흐름 (단위: 억원)")
    cf_rows = {
        '영업활동CF': hist['operating_cf'],
        '투자활동CF': hist['investing_cf'],
        'D&A': hist['da'],
        'CapEx': hist['capex'],
        '잉여현금흐름(FCF)': {yr: hist['operating_cf'].get(yr,0) - hist['capex'].get(yr,0) for yr in years},
    }
    cf_df = pd.DataFrame(cf_rows).T
    cf_df.columns = [str(yr) for yr in cf_df.columns]
    st.dataframe(fmt_df(cf_df), use_container_width=True)

    # Charts
    st.markdown("#### 📈 추이 차트")
    col1, col2 = st.columns(2)
    yrs_sorted = sorted(years)

    with col1:
        fig_rev = go.Figure()
        rev_vals = [hist['revenue'].get(yr, 0) / 1e8 for yr in yrs_sorted]
        ebit_vals = [hist['ebit'].get(yr, 0) / 1e8 for yr in yrs_sorted]
        fig_rev.add_trace(go.Bar(name='매출액', x=[str(y) for y in yrs_sorted], y=rev_vals, marker_color='steelblue'))
        fig_rev.add_trace(go.Bar(name='영업이익', x=[str(y) for y in yrs_sorted], y=ebit_vals, marker_color='orange'))
        fig_rev.update_layout(title='매출액 및 영업이익 추이', yaxis_title='억원', barmode='group', height=350)
        st.plotly_chart(fig_rev, use_container_width=True)

    with col2:
        fig_margin = go.Figure()
        ebit_margins = [safe_div(hist['ebit'].get(yr,0), hist['revenue'].get(yr,1), 0)*100 for yr in yrs_sorted]
        ni_margins = [safe_div(hist['net_income'].get(yr,0), hist['revenue'].get(yr,1), 0)*100 for yr in yrs_sorted]
        fig_margin.add_trace(go.Scatter(name='영업이익률', x=[str(y) for y in yrs_sorted], y=ebit_margins, mode='lines+markers', line=dict(color='orange')))
        fig_margin.add_trace(go.Scatter(name='순이익률', x=[str(y) for y in yrs_sorted], y=ni_margins, mode='lines+markers', line=dict(color='green')))
        fig_margin.update_layout(title='수익성 추이', yaxis_title='%', height=350)
        st.plotly_chart(fig_margin, use_container_width=True)

# ─── Page 3: DCF 가정 입력 ────────────────────────────────────────────────────
elif page == 'DCF 가정 입력':
    st.header("⚙️ DCF 가정 입력")

    if not st.session_state.historical_summary:
        st.info("먼저 '기업 검색' 메뉴에서 기업을 선택하고 재무제표를 불러오세요.")
        st.stop()

    asmp = st.session_state.dcf_assumptions
    hist = st.session_state.historical_summary
    base_year = st.session_state.base_year
    n = int(asmp.get('projection_years', 5))

    # ── Revenue ─────────────────────────────────────────────────────────────
    st.subheader("1. 매출액 가정")
    rev_method = st.radio("매출액 방법", ['전체 매출 기준', '사업부문별'], horizontal=True,
                          index=0 if asmp.get('revenue_method','total') == 'total' else 1)
    asmp['revenue_method'] = 'total' if rev_method == '전체 매출 기준' else 'segment'

    if asmp['revenue_method'] == 'total':
        base_rev = asmp.get('revenue_total', {}).get('base', hist['revenue'].get(base_year, 0))
        st.write(f"기준연도 매출: **{fmt_억(base_rev)}억원** ({base_year})")

        prev_growths = asmp.get('revenue_total', {}).get('growth_rates', [5.0]*n)
        cols = st.columns(n)
        growth_rates = []
        for i, col in enumerate(cols):
            with col:
                gr = col.number_input(f"Y{i+1} 성장률(%)", value=float(prev_growths[i] if i < len(prev_growths) else 3.0), step=0.5, key=f"rev_gr_{i}")
                growth_rates.append(gr)
        asmp['revenue_total'] = {'base': base_rev, 'growth_rates': growth_rates}

        # Live preview
        st.markdown("**📊 매출액 추정 미리보기 (억원)**")
        st.dataframe(preview_revenue(base_rev, growth_rates), use_container_width=True)

    else:
        segments = asmp.get('revenue_segments', [])
        if not segments:
            segments = [{'name': '세그먼트1', 'base': hist['revenue'].get(base_year, 0), 'growth_rates': [5.0]*n}]
        n_seg = st.number_input("사업부문 수", min_value=1, max_value=10, value=len(segments))
        while len(segments) < n_seg:
            segments.append({'name': f'세그먼트{len(segments)+1}', 'base': 0, 'growth_rates': [5.0]*n})
        segments = segments[:n_seg]
        for j, seg in enumerate(segments):
            with st.expander(f"사업부문 {j+1}: {seg['name']}", expanded=True):
                seg['name'] = st.text_input("부문명", value=seg['name'], key=f"seg_name_{j}")
                seg['base'] = st.number_input("기준 매출(원)", value=float(seg['base']), step=1e8, key=f"seg_base_{j}")
                cols = st.columns(n)
                for i, col in enumerate(cols):
                    with col:
                        seg['growth_rates'][i] = col.number_input(f"Y{i+1}(%)", value=float(seg['growth_rates'][i] if i < len(seg['growth_rates']) else 3.0), step=0.5, key=f"seg_gr_{j}_{i}")
                st.dataframe(preview_revenue(seg['base'], seg['growth_rates']), use_container_width=True)
        asmp['revenue_segments'] = segments

        # Total preview
        total_base = sum(s['base'] for s in segments)
        # approximate combined growth as weighted average
        total_growth = [(sum(s['base'] * s['growth_rates'][i] for s in segments) / max(total_base,1)) for i in range(n)]
        st.markdown("**📊 전체 매출 합계 미리보기**")
        st.dataframe(preview_revenue(total_base, total_growth), use_container_width=True)

    st.divider()

    # ── Costs ────────────────────────────────────────────────────────────────
    st.subheader("2. 비용 가정")

    base_rev_val = asmp.get('revenue_total', {}).get('base', hist['revenue'].get(base_year, 0))
    base_cogs_val = hist['cogs'].get(base_year, 0)
    base_sga_val = hist['sga'].get(base_year, 0)
    base_labor_val = base_sga_val * 0.5
    base_other_sga_val = base_sga_val * 0.5
    rev_growths = asmp.get('revenue_total', {}).get('growth_rates', [5.0]*n)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**매출원가 (COGS)**")
        cogs_method = st.radio("COGS 방법", ['매출 대비 비율', '성장률'], horizontal=True,
                               key='cogs_method_radio',
                               index=0 if asmp.get('cogs_method','pct_revenue')=='pct_revenue' else 1)
        asmp['cogs_method'] = 'pct_revenue' if cogs_method == '매출 대비 비율' else 'growth'
        cogs_growth_vals = asmp.get('cogs_growth', [3.0]*n)
        if asmp['cogs_method'] == 'pct_revenue':
            asmp['cogs_pct'] = st.slider("COGS/매출 (%)", 0.0, 100.0, float(asmp.get('cogs_pct', 60.0)), 0.5)
        else:
            cogs_growth_vals = []
            prev_cg = asmp.get('cogs_growth', [3.0]*n)
            for i in range(n):
                cg = st.number_input(f"COGS Y{i+1} 성장률(%)", value=float(prev_cg[i] if i < len(prev_cg) else 3.0), step=0.5, key=f"cogs_gr_{i}")
                cogs_growth_vals.append(cg)
            asmp['cogs_growth'] = cogs_growth_vals

    with col2:
        st.markdown("**인건비 (Labor)**")
        labor_method = st.radio("인건비 방법", ['매출 대비 비율', '성장률'], horizontal=True,
                                key='labor_method_radio',
                                index=0 if asmp.get('labor_method','pct_revenue')=='pct_revenue' else 1)
        asmp['labor_method'] = 'pct_revenue' if labor_method == '매출 대비 비율' else 'growth'
        labor_growth_vals = asmp.get('labor_growth', [3.0]*n)
        if asmp['labor_method'] == 'pct_revenue':
            asmp['labor_pct'] = st.slider("인건비/매출 (%)", 0.0, 50.0, float(asmp.get('labor_pct', 10.0)), 0.5)
        else:
            labor_growth_vals = []
            prev_lg = asmp.get('labor_growth', [3.0]*n)
            for i in range(n):
                lg = st.number_input(f"인건비 Y{i+1} 성장률(%)", value=float(prev_lg[i] if i < len(prev_lg) else 3.0), step=0.5, key=f"labor_gr_{i}")
                labor_growth_vals.append(lg)
            asmp['labor_growth'] = labor_growth_vals

    col3, col4 = st.columns(2)
    with col3:
        st.markdown("**기타 판관비 (Other SGA)**")
        other_sga_method = st.radio("기타판관비 방법", ['매출 대비 비율', '성장률'], horizontal=True,
                                    key='other_sga_method_radio',
                                    index=0 if asmp.get('other_sga_method','pct_revenue')=='pct_revenue' else 1)
        asmp['other_sga_method'] = 'pct_revenue' if other_sga_method == '매출 대비 비율' else 'growth'
        other_sga_growth_vals = asmp.get('other_sga_growth', [3.0]*n)
        if asmp['other_sga_method'] == 'pct_revenue':
            asmp['other_sga_pct'] = st.slider("기타판관비/매출 (%)", 0.0, 30.0, float(asmp.get('other_sga_pct', 10.0)), 0.5)
        else:
            other_sga_growth_vals = []
            prev_og = asmp.get('other_sga_growth', [3.0]*n)
            for i in range(n):
                og = st.number_input(f"기타판관비 Y{i+1} 성장률(%)", value=float(prev_og[i] if i < len(prev_og) else 3.0), step=0.5, key=f"other_sga_gr_{i}")
                other_sga_growth_vals.append(og)
            asmp['other_sga_growth'] = other_sga_growth_vals

    # SGA = labor + other_sga (for DCFModel compatibility)
    if asmp['labor_method'] == 'pct_revenue' and asmp['other_sga_method'] == 'pct_revenue':
        asmp['sga_method'] = 'pct_revenue'
        asmp['sga_pct'] = asmp.get('labor_pct', 10.0) + asmp.get('other_sga_pct', 10.0)
    else:
        asmp['sga_method'] = 'growth'
        asmp['sga_growth'] = [3.0]*n  # fallback; DCF results page uses combined

    # Live preview
    st.markdown("**📊 비용 추정 미리보기 (억원)**")
    st.dataframe(preview_costs(
        base_rev_val, rev_growths,
        asmp['cogs_method'], asmp.get('cogs_pct', 60.0), cogs_growth_vals,
        asmp['labor_method'], asmp.get('labor_pct', 10.0), labor_growth_vals,
        asmp['other_sga_method'], asmp.get('other_sga_pct', 10.0), other_sga_growth_vals,
        base_cogs_val, base_labor_val, base_other_sga_val
    ), use_container_width=True)

    st.divider()

    # ── D&A and CapEx ─────────────────────────────────────────────────────────
    st.subheader("3. D&A 및 CapEx 가정")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**감가상각비 (D&A)**")
        da_method = st.radio("D&A 방법", ['매출 대비 비율', '고정금액', '전년 대비 성장'], horizontal=True,
                             key='da_method_radio',
                             index=['pct_revenue','fixed','growth'].index(asmp.get('da_method','pct_revenue')))
        method_map = {'매출 대비 비율': 'pct_revenue', '고정금액': 'fixed', '전년 대비 성장': 'growth'}
        asmp['da_method'] = method_map[da_method]
        da_growth_vals = asmp.get('da_growth', [3.0]*n)

        if asmp['da_method'] == 'pct_revenue':
            asmp['da_pct'] = st.number_input("D&A/매출 (%)", value=float(asmp.get('da_pct', 3.0)), step=0.1)
        elif asmp['da_method'] == 'fixed':
            asmp['da_fixed'] = st.number_input("고정 D&A (원)", value=float(asmp.get('da_fixed', hist['da'].get(base_year, 0))), step=1e8)
        else:
            da_growth_vals = []
            prev_dag = asmp.get('da_growth', [3.0]*n)
            for i in range(n):
                dg = st.number_input(f"D&A Y{i+1} 성장률(%)", value=float(prev_dag[i] if i < len(prev_dag) else 3.0), step=0.5, key=f"da_gr_{i}")
                da_growth_vals.append(dg)
            asmp['da_growth'] = da_growth_vals

        # D&A preview
        base_da = hist['da'].get(base_year, 0)
        da_rows = []
        rev_v = base_rev_val
        da_v = base_da
        for i, g in enumerate(rev_growths):
            rev_v = rev_v * (1 + g / 100)
            if asmp['da_method'] == 'pct_revenue':
                da_v = rev_v * asmp.get('da_pct', 3.0) / 100
            elif asmp['da_method'] == 'fixed':
                da_v = asmp.get('da_fixed', base_da)
            else:
                dg = da_growth_vals[i] if i < len(da_growth_vals) else (da_growth_vals[-1] if da_growth_vals else 3.0)
                da_v = da_v * (1 + dg / 100)
            da_rows.append({'연도': f'Y{i+1}', 'D&A(억원)': f'{da_v/1e8:,.1f}', 'D&A/매출': f'{safe_div(da_v,rev_v)*100:.1f}%'})
        st.dataframe(pd.DataFrame(da_rows).set_index('연도'), use_container_width=True)

    with col2:
        st.markdown("**설비투자 (CapEx)**")
        capex_method = st.radio("CapEx 방법", ['감가상각만큼 재투자', '매출 대비 비율', '고정금액'], horizontal=True,
                                key='capex_method_radio',
                                index=['equal_da','pct_revenue','fixed'].index(asmp.get('capex_method','equal_da')))
        capex_map = {'감가상각만큼 재투자': 'equal_da', '매출 대비 비율': 'pct_revenue', '고정금액': 'fixed'}
        asmp['capex_method'] = capex_map[capex_method]

        if asmp['capex_method'] == 'pct_revenue':
            asmp['capex_pct'] = st.number_input("CapEx/매출 (%)", value=float(asmp.get('capex_pct', 3.0)), step=0.1)
        elif asmp['capex_method'] == 'fixed':
            asmp['capex_fixed'] = st.number_input("고정 CapEx (원)", value=float(asmp.get('capex_fixed', hist['capex'].get(base_year, 0))), step=1e8)

        # CapEx preview
        base_capex = hist['capex'].get(base_year, 0)
        capex_rows = []
        rev_v2 = base_rev_val
        da_v2 = base_da
        capex_v = base_capex
        for i, g in enumerate(rev_growths):
            rev_v2 = rev_v2 * (1 + g / 100)
            if asmp['da_method'] == 'pct_revenue':
                da_v2 = rev_v2 * asmp.get('da_pct', 3.0) / 100
            elif asmp['da_method'] == 'fixed':
                da_v2 = asmp.get('da_fixed', base_da)
            else:
                dg = da_growth_vals[i] if i < len(da_growth_vals) else (da_growth_vals[-1] if da_growth_vals else 3.0)
                da_v2 = da_v2 * (1 + dg / 100)
            if asmp['capex_method'] == 'equal_da':
                capex_v = da_v2
            elif asmp['capex_method'] == 'pct_revenue':
                capex_v = rev_v2 * asmp.get('capex_pct', 3.0) / 100
            else:
                capex_v = asmp.get('capex_fixed', base_capex)
            capex_rows.append({'연도': f'Y{i+1}', 'CapEx(억원)': f'{capex_v/1e8:,.1f}', 'CapEx/매출': f'{safe_div(capex_v,rev_v2)*100:.1f}%', 'CapEx/D&A': f'{safe_div(capex_v,da_v2):.2f}x'})
        st.dataframe(pd.DataFrame(capex_rows).set_index('연도'), use_container_width=True)

    st.divider()

    # ── NWC ──────────────────────────────────────────────────────────────────
    st.subheader("4. 순운전자본 (NWC) 변동")
    nwc_method = st.radio("NWC 방법", ['Turnover 기반 (DSO/DIO/DPO)', '매출 대비 비율', '고정 변동액'], horizontal=True,
                          index=['turnover','pct_revenue','fixed'].index(asmp.get('nwc_method','turnover')))
    nwc_method_map = {'Turnover 기반 (DSO/DIO/DPO)': 'turnover', '매출 대비 비율': 'pct_revenue', '고정 변동액': 'fixed'}
    asmp['nwc_method'] = nwc_method_map[nwc_method]

    if asmp['nwc_method'] == 'turnover':
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            asmp['nwc_dso'] = st.number_input("DSO (매출채권 회수일수)", value=float(asmp.get('nwc_dso', 60.0)), step=1.0, help="매출채권 / 매출액 × 365")
        with col2:
            asmp['nwc_dio'] = st.number_input("DIO (재고자산 회전일수)", value=float(asmp.get('nwc_dio', 45.0)), step=1.0, help="재고자산 / 매출원가 × 365")
        with col3:
            asmp['nwc_dpo'] = st.number_input("DPO (매입채무 지급일수)", value=float(asmp.get('nwc_dpo', 30.0)), step=1.0, help="매입채무 / 매출원가 × 365")
        with col4:
            asmp['nwc_other_dpo'] = st.number_input("기타 미지급금 일수", value=float(asmp.get('nwc_other_dpo', 20.0)), step=1.0)
        nwc_days = asmp['nwc_dso'] + asmp['nwc_dio'] - asmp['nwc_dpo'] - asmp['nwc_other_dpo']
        eff_nwc_pct = nwc_days / 365 * 100
        st.info(f"유효 NWC/매출 = ({asmp['nwc_dso']} + {asmp['nwc_dio']} - {asmp['nwc_dpo']} - {asmp['nwc_other_dpo']}) / 365 × 100 = **{eff_nwc_pct:.1f}%**")
        asmp['nwc_pct'] = eff_nwc_pct  # store for model conversion

        # NWC preview
        nwc_rows = []
        rev_v3 = base_rev_val
        prev_nwc = base_rev_val * eff_nwc_pct / 100
        for i, g in enumerate(rev_growths):
            rev_v3 = rev_v3 * (1 + g / 100)
            nwc_level = rev_v3 * eff_nwc_pct / 100
            delta = nwc_level - prev_nwc
            prev_nwc = nwc_level
            nwc_rows.append({'연도': f'Y{i+1}', 'NWC(억원)': f'{nwc_level/1e8:,.1f}', 'ΔNWC(억원)': f'{delta/1e8:,.1f}'})
        st.dataframe(pd.DataFrame(nwc_rows).set_index('연도'), use_container_width=True)

    elif asmp['nwc_method'] == 'pct_revenue':
        asmp['nwc_pct'] = st.number_input("NWC/매출 (%)", value=float(asmp.get('nwc_pct', 10.0)), step=0.5)
        nwc_rows = []
        rev_v3 = base_rev_val
        prev_nwc = base_rev_val * asmp['nwc_pct'] / 100
        for i, g in enumerate(rev_growths):
            rev_v3 = rev_v3 * (1 + g / 100)
            nwc_level = rev_v3 * asmp['nwc_pct'] / 100
            delta = nwc_level - prev_nwc
            prev_nwc = nwc_level
            nwc_rows.append({'연도': f'Y{i+1}', 'NWC(억원)': f'{nwc_level/1e8:,.1f}', 'ΔNWC(억원)': f'{delta/1e8:,.1f}'})
        st.dataframe(pd.DataFrame(nwc_rows).set_index('연도'), use_container_width=True)
    else:
        asmp['nwc_fixed'] = st.number_input("연간 NWC 변동액 (원)", value=float(asmp.get('nwc_fixed', 0)), step=1e8)

    st.divider()

    # ── Tax Rate ──────────────────────────────────────────────────────────────
    st.subheader("5. 세율")

    tax_method = st.radio("세율 방법", ['누진세율 자동계산 (2026 한국)', '직접 입력'], horizontal=True,
                          index=0 if asmp.get('tax_method','progressive')=='progressive' else 1)
    asmp['tax_method'] = 'progressive' if tax_method == '누진세율 자동계산 (2026 한국)' else 'direct'

    if asmp['tax_method'] == 'progressive':
        st.markdown("""
| 과세표준 | 세율 |
|---|---|
| 2억원 이하 | 9% |
| 2억 ~ 200억 | 19% |
| 200억 ~ 3,000억 | 21% |
| 3,000억 초과 | 24% |
""")
        ebit_억 = hist['ebit'].get(base_year, 0) / 1e8
        prog_rate = calc_korea_corp_tax_rate(ebit_억) * 100
        taxable_income = st.number_input("과세표준 (억원, 기준연도 영업이익 기준)", value=round(ebit_억, 1), step=1.0)
        auto_rate = calc_korea_corp_tax_rate(taxable_income) * 100
        asmp['tax_rate'] = round(auto_rate, 2)
        st.success(f"유효 법인세율 = **{auto_rate:.2f}%** (과세표준 {taxable_income:.1f}억원 기준)")
    else:
        asmp['tax_rate'] = st.number_input("유효 법인세율 (%)", value=float(asmp.get('tax_rate', 22.0)),
                                            min_value=0.0, max_value=50.0, step=0.5)

    st.divider()

    # ── WACC ──────────────────────────────────────────────────────────────────
    st.subheader("6. WACC 입력")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**자기자본비용 (Ke)**")
        ke_method = st.radio("Ke 방법", ['CAPM', '직접 입력'], horizontal=True,
                             index=0 if asmp.get('ke_method','capm')=='capm' else 1)
        asmp['ke_method'] = 'capm' if ke_method == 'CAPM' else 'direct'
        if asmp['ke_method'] == 'capm':
            asmp['risk_free_rate'] = st.number_input("무위험수익률 (%)", value=float(asmp.get('risk_free_rate', 3.5)), step=0.1)
            asmp['beta'] = st.number_input("베타 (β)", value=float(asmp.get('beta', 1.0)), step=0.05)
            asmp['equity_risk_premium'] = st.number_input("주식위험프리미엄 (%)", value=float(asmp.get('equity_risk_premium', 5.0)), step=0.1)
            ke = asmp['risk_free_rate'] + asmp['beta'] * asmp['equity_risk_premium']
            st.info(f"계산된 Ke = {ke:.2f}%")
        else:
            asmp['cost_of_equity'] = st.number_input("자기자본비용 (%)", value=float(asmp.get('cost_of_equity', 10.0)), step=0.1)

    with col2:
        st.markdown("**타인자본비용 (Kd) 및 자본구조**")
        asmp['cost_of_debt'] = st.number_input("세전 타인자본비용 (%)", value=float(asmp.get('cost_of_debt', 4.5)), step=0.1)
        use_auto_weight = st.checkbox("재무상태표 기준 자본구조 자동계산", value=True)
        if not use_auto_weight:
            asmp['debt_weight'] = st.slider("부채 비중 (%)", 0.0, 100.0, float(asmp.get('debt_weight', 30.0)), 1.0)
        try:
            wacc_preview_asmp = {
                'tax_rate': asmp.get('tax_rate', 22.0) / 100.0,
                'cost_of_debt': asmp.get('cost_of_debt', 4.5) / 100.0,
                'risk_free_rate': asmp.get('risk_free_rate', 3.5) / 100.0,
                'beta': asmp.get('beta', 1.0),
                'equity_risk_premium': asmp.get('equity_risk_premium', 5.0) / 100.0,
                'cost_of_equity': (asmp['cost_of_equity'] / 100.0) if 'cost_of_equity' in asmp else None,
                'debt_weight': asmp.get('debt_weight', 30.0) / 100.0,
                'equity_weight': 1.0 - asmp.get('debt_weight', 30.0) / 100.0,
            }
            hist_data_for_wacc = {
                'total_debt': hist['total_debt'].get(base_year, 0),
                'total_equity': hist['total_equity'].get(base_year, 0),
            }
            temp_model = DCFModel(hist_data_for_wacc, wacc_preview_asmp)
            preview_wacc = temp_model.calculate_wacc() * 100
            kd_after = asmp['cost_of_debt'] * (1 - asmp['tax_rate'] / 100)
            st.success(f"계산된 WACC = **{preview_wacc:.2f}%**")
            st.caption(f"세후 Kd = {kd_after:.2f}%")
        except Exception as e:
            st.warning(f"WACC 미리보기 오류: {str(e)}")

    st.divider()

    # ── Terminal Value ────────────────────────────────────────────────────────
    st.subheader("7. 터미널 밸류")
    asmp['terminal_growth_rate'] = st.number_input("영구성장률 TGR (%)",
                                                    value=float(asmp.get('terminal_growth_rate', 1.5)),
                                                    min_value=0.0, max_value=5.0, step=0.1,
                                                    help="장기 영구성장률. 한국 장기 GDP 성장률 이하 권장.")

    st.divider()

    # ── Shares ────────────────────────────────────────────────────────────────
    st.subheader("8. 기타")
    col1, col2 = st.columns(2)
    with col1:
        asmp['shares_outstanding'] = st.number_input("발행주식수 (주)",
                                                       value=float(asmp.get('shares_outstanding', 0)),
                                                       step=1000.0,
                                                       help="총 발행주식수 (주식수 입력 시 주당가치 계산)")
    with col2:
        net_debt_auto = hist['total_debt'].get(base_year, 0) - hist['cash'].get(base_year, 0)
        st.metric("자동 산출 순차입금", f"{fmt_억(net_debt_auto)}억원")

    st.session_state.dcf_assumptions = asmp
    st.divider()
    if st.button("💾 가정 저장 및 DCF 결과로 이동", use_container_width=True, type='primary'):
        st.success("✅ 가정이 저장되었습니다. 'DCF 결과' 메뉴에서 확인하세요.")

# ─── Page 4: DCF 결과 ─────────────────────────────────────────────────────────
elif page == 'DCF 결과':
    st.header("📈 DCF 분석 결과")

    if not st.session_state.historical_summary or not st.session_state.dcf_assumptions:
        st.info("먼저 재무제표 및 DCF 가정을 입력해주세요.")
        st.stop()

    hist = st.session_state.historical_summary
    asmp = st.session_state.dcf_assumptions
    base_year = st.session_state.base_year

    hist_data = {
        'revenue': hist['revenue'].get(base_year, 0),
        'cogs': hist['cogs'].get(base_year, 0),
        'sga': hist['sga'].get(base_year, 0),
        'ebit': hist['ebit'].get(base_year, 0),
        'net_income': hist['net_income'].get(base_year, 0),
        'da': hist['da'].get(base_year, 0),
        'capex': hist['capex'].get(base_year, 0),
        'nwc': hist['nwc'].get(base_year, 0),
        'total_debt': hist['total_debt'].get(base_year, 0),
        'total_equity': hist['total_equity'].get(base_year, 0),
        'cash': hist['cash'].get(base_year, 0),
        'total_assets': hist['total_assets'].get(base_year, 0),
        'shares_outstanding': asmp.get('shares_outstanding', 0),
    }

    try:
        model_asmp = asmp.copy()
        # Convert % to decimals
        for pct_key in ('tax_rate', 'risk_free_rate', 'equity_risk_premium',
                        'cost_of_debt', 'cost_of_equity',
                        'cogs_pct', 'sga_pct', 'da_pct', 'capex_pct', 'nwc_pct',
                        'terminal_growth_rate'):
            if pct_key in model_asmp and model_asmp[pct_key] is not None:
                model_asmp[pct_key] = model_asmp[pct_key] / 100.0
        if 'debt_weight' in model_asmp:
            model_asmp['debt_weight'] = model_asmp['debt_weight'] / 100.0
            model_asmp['equity_weight'] = 1.0 - model_asmp['debt_weight']
        if 'revenue_total' in model_asmp:
            rt = model_asmp['revenue_total']
            rt['growth_rates'] = [g / 100.0 for g in rt.get('growth_rates', [])]
        if 'revenue_segments' in model_asmp:
            for seg in model_asmp['revenue_segments']:
                seg['growth_rates'] = [g / 100.0 for g in seg.get('growth_rates', [])]
        for list_key in ('cogs_growth', 'sga_growth', 'da_growth'):
            if list_key in model_asmp:
                model_asmp[list_key] = [g / 100.0 for g in model_asmp[list_key]]
        # NWC turnover → pct_revenue
        if model_asmp.get('nwc_method') == 'turnover':
            model_asmp['nwc_method'] = 'pct_revenue'
            # nwc_pct already stored as effective %
        model_asmp['tgr'] = model_asmp.pop('terminal_growth_rate', 0.015)
        model_asmp['net_debt'] = hist_data['total_debt'] - hist_data['cash']

        with st.spinner("DCF 계산 중..."):
            model = DCFModel(hist_data, model_asmp)
            results = model.calculate_ev()

        fcff_df = results['fcff_df']
        wacc = results['wacc']
        pv_fcff_df = results['pv_fcff_by_year']
        pv_tv = results['pv_tv']
        ev = results['ev']
        total_pv_fcff = float(np.sum(results['pv_fcffs']))

        net_debt = hist_data['total_debt'] - hist_data['cash']
        eq_val = model.equity_value(ev)
        shares = hist_data['shares_outstanding']
        price_per_share = model.price_per_share(eq_val, shares) if shares > 0 else None

        # KPIs
        st.subheader("핵심 지표")
        kpi_cols = st.columns(4)
        with kpi_cols[0]:
            st.metric("기업가치 (EV)", f"{fmt_억(ev)}억원")
        with kpi_cols[1]:
            st.metric("순차입금", f"{fmt_억(net_debt)}억원")
        with kpi_cols[2]:
            st.metric("자기자본가치", f"{fmt_억(eq_val)}억원")
        with kpi_cols[3]:
            if price_per_share is not None:
                st.metric("주당가치", f"{price_per_share:,.0f}원")
            else:
                st.metric("주당가치", "N/A (주식수 미입력)")

        st.divider()

        # FCFF Table
        st.subheader("FCFF 추정 (단위: 억원)")
        display_rows = []
        for i, row_data in fcff_df.iterrows():
            pv_row = pv_fcff_df.iloc[i]
            display_rows.append({
                '연도': str(int(row_data['year'])),
                '매출액': fmt_억(row_data['revenue']),
                '영업이익': fmt_억(row_data['ebit']),
                'EBIT(1-t)': fmt_억(row_data['nopat']),
                'D&A': fmt_억(row_data['da']),
                'CapEx': fmt_억(row_data['capex']),
                'ΔNWC': fmt_억(row_data['delta_nwc']),
                'FCFF': fmt_억(row_data['fcff']),
                '할인계수': f"{pv_row['discount_factor']:.4f}",
                'PV(FCFF)': fmt_억(pv_row['pv_fcff']),
            })
        st.dataframe(pd.DataFrame(display_rows).set_index('연도'), use_container_width=True)

        st.divider()

        # EV Bridge
        st.subheader("EV → 주당가치")
        bridge_data = {
            '항목': ['PV(FCFF) 합계', 'PV(Terminal Value)', '기업가치 (EV)', '(-) 순차입금', '자기자본가치', '÷ 발행주식수', '주당가치'],
            '금액': [
                f"{fmt_억(total_pv_fcff)}억원",
                f"{fmt_억(pv_tv)}억원",
                f"{fmt_억(ev)}억원",
                f"({fmt_억(net_debt)}억원)",
                f"{fmt_억(eq_val)}억원",
                f"{shares:,.0f}주" if shares > 0 else "N/A",
                f"{price_per_share:,.0f}원" if price_per_share else "N/A"
            ]
        }
        st.table(pd.DataFrame(bridge_data).set_index('항목'))

        col1, col2, col3 = st.columns(3)
        with col1:
            st.info(f"WACC: **{wacc*100:.2f}%**")
        with col2:
            st.info(f"TGR: **{asmp.get('terminal_growth_rate', 1.5):.1f}%**")
        with col3:
            tv_share = safe_div(pv_tv, ev, 0) * 100
            st.info(f"TV 비중: **{tv_share:.1f}%**")

        st.divider()

        # Waterfall
        st.subheader("EV 구성 폭포 차트")
        years_proj = fcff_df['year'].tolist()
        pv_fcff_list = pv_fcff_df['pv_fcff'].tolist()
        fig_wf = go.Figure(go.Waterfall(
            orientation="v",
            measure=["relative"] * len(years_proj) + ["relative", "total"],
            x=[f"PV(FCFF {int(yr)})" for yr in years_proj] + ["PV(TV)", "EV"],
            y=[v / 1e8 for v in pv_fcff_list] + [pv_tv / 1e8, None],
            connector={"line": {"color": "rgb(63,63,63)"}},
            increasing={"marker": {"color": "steelblue"}},
            totals={"marker": {"color": "orange"}},
        ))
        fig_wf.update_layout(title="기업가치 구성 (억원)", yaxis_title="억원", height=400)
        st.plotly_chart(fig_wf, use_container_width=True)

        st.divider()

        # Sensitivity
        st.subheader("민감도 분석 (WACC vs TGR)")
        wacc_c = wacc * 100
        tgr_c = float(asmp.get('terminal_growth_rate', 1.5))
        wacc_range = [round(wacc_c + d, 2) for d in [-2, -1, 0, 1, 2]]
        tgr_range = [max(0.1, round(tgr_c + d, 2)) for d in [-1, -0.5, 0, 0.5, 1]]

        with st.spinner("민감도 계산 중..."):
            sens_metric = 'price_per_share' if shares > 0 else 'ev'
            sens_df = model.sensitivity_analysis(
                [w/100 for w in wacc_range],
                [t/100 for t in tgr_range],
                metric=sens_metric
            )

        sens_df.index = [f"{w:.1f}%" for w in wacc_range]
        sens_df.columns = [f"{t:.1f}%" for t in tgr_range]
        sens_df.index.name = "WACC \\ TGR"

        label = "주당가치 (원)" if shares > 0 else "EV (억원)"
        st.write(f"**{label}**")
        sens_display = sens_df.map(lambda x: f"{x:,.0f}" if x is not None and not np.isnan(x) else "N/A")
        st.dataframe(sens_display, use_container_width=True)

        fig_heat = px.imshow(
            sens_df.map(lambda x: x if not np.isnan(x) else 0).values,
            x=list(sens_df.columns),
            y=list(sens_df.index),
            color_continuous_scale='RdYlGn',
            title=f'{label} 민감도 히트맵',
            labels={'x': 'TGR', 'y': 'WACC'},
            text_auto='.0f'
        )
        fig_heat.update_layout(height=400)
        st.plotly_chart(fig_heat, use_container_width=True)

        st.divider()

        # Excel Export
        st.subheader("📥 결과 내보내기")

        def create_excel_export():
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                (fcff_df / 1e8).to_excel(writer, sheet_name='FCFF 추정', float_format='%.1f')
                pd.DataFrame({
                    '항목': ['EV', '순차입금', '자기자본가치', '주당가치', 'WACC', 'TGR', 'TV PV'],
                    '값': [ev/1e8, net_debt/1e8, eq_val/1e8, price_per_share or 0, wacc*100, tgr_c, pv_tv/1e8],
                    '단위': ['억원','억원','억원','원','%','%','억원']
                }).to_excel(writer, sheet_name='요약', index=False)
                sens_df.to_excel(writer, sheet_name='민감도 분석')
                hist_rows = {m: {str(k): v/1e8 for k,v in d.items()} for m,d in hist.items()}
                pd.DataFrame(hist_rows).T.to_excel(writer, sheet_name='역사적 데이터', float_format='%.1f')
            return output.getvalue()

        company_name = st.session_state.selected_company['corp_name'] if st.session_state.selected_company else 'company'
        st.download_button(
            label="📊 Excel 다운로드",
            data=create_excel_export(),
            file_name=f"{company_name}_DCF_{base_year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        st.error(f"DCF 계산 오류: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
