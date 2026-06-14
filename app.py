import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import os
import io
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
    defaults = {
        'api_key': os.getenv('DART_API_KEY', ''),
        'selected_company': None,
        'financial_data': None,
        'historical_summary': None,
        'dcf_assumptions': {},
        'dcf_results': None,
        'search_results': [],
        'base_year': 2023,
        'page': '기업 검색',
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session_state()

# ─── Utility ──────────────────────────────────────────────────────────────────
def fmt_억(value):
    """Format KRW to 억원"""
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

def extract_historical_summary(financial_data: dict, years: list) -> dict:
    """Extract key metrics from financial data for multiple years"""
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

        col1, col2 = st.columns([1, 2])
        with col1:
            base_year = st.selectbox("기준연도", [2023, 2022, 2021, 2020], index=0)
            st.session_state.base_year = base_year

        with col2:
            st.write("")
            st.write("")
            fetch_btn = st.button("📥 재무제표 불러오기", use_container_width=True)

        if fetch_btn:
            selected = st.session_state.search_results[selected_idx]
            st.session_state.selected_company = selected

            if not st.session_state.api_key:
                st.error("API Key가 필요합니다.")
            else:
                with st.spinner(f"{selected['corp_name']}의 재무제표를 불러오는 중..."):
                    try:
                        corp_code = selected['corp_code']
                        api_key = st.session_state.api_key
                        years = [base_year, base_year - 1, base_year - 2]

                        financial_data = {}
                        for yr in years:
                            try:
                                fs = get_financial_statements(corp_code, yr, api_key)
                                financial_data[str(yr)] = fs
                            except Exception as e:
                                st.warning(f"{yr}년 데이터 로드 실패: {str(e)}")
                                financial_data[str(yr)] = {}

                        st.session_state.financial_data = financial_data

                        hist = extract_historical_summary(financial_data, years)
                        st.session_state.historical_summary = hist
                        st.session_state.base_year = base_year

                        # Auto-fill DCF defaults
                        latest_rev = hist['revenue'].get(base_year, 0)
                        latest_ebit = hist['ebit'].get(base_year, 0)
                        latest_da = hist['da'].get(base_year, 0)
                        latest_capex = hist['capex'].get(base_year, 0)
                        latest_nwc = hist['nwc'].get(base_year, 0)
                        latest_net_income = hist['net_income'].get(base_year, 0)
                        latest_tax_expense = financial_data.get(str(base_year), {}).get('income_statement', {}).get('tax_expense', 0)
                        pretax_income = financial_data.get(str(base_year), {}).get('income_statement', {}).get('pretax_income', 0)

                        eff_tax = safe_div(latest_tax_expense, pretax_income, 0.25) * 100 if pretax_income > 0 else 25.0
                        cogs = hist['cogs'].get(base_year, 0)
                        sga = hist['sga'].get(base_year, 0)
                        cogs_pct = safe_div(cogs, latest_rev, 0.6) * 100
                        sga_pct = safe_div(sga, latest_rev, 0.2) * 100
                        da_pct = safe_div(latest_da, latest_rev, 0.03) * 100
                        capex_pct = safe_div(latest_capex, latest_rev, 0.03) * 100
                        nwc_pct = safe_div(latest_nwc, latest_rev, 0.1) * 100

                        total_debt = hist['total_debt'].get(base_year, 0)
                        total_equity = hist['total_equity'].get(base_year, 0)
                        cash = hist['cash'].get(base_year, 0)
                        total_cap = total_debt + total_equity
                        debt_weight = safe_div(total_debt, total_cap, 0.3) * 100

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
                            'sga_method': 'pct_revenue',
                            'sga_pct': round(sga_pct, 1),
                            'da_method': 'pct_revenue',
                            'da_pct': round(da_pct, 1),
                            'capex_method': 'pct_revenue',
                            'capex_pct': round(capex_pct, 1),
                            'nwc_method': 'pct_revenue',
                            'nwc_pct': round(nwc_pct, 1),
                            'tax_rate': round(min(max(eff_tax, 0), 40), 1),
                            'ke_method': 'capm',
                            'risk_free_rate': 3.5,
                            'beta': 1.0,
                            'equity_risk_premium': 5.0,
                            'cost_of_debt': 4.5,
                            'debt_weight': round(debt_weight, 1),
                            'terminal_growth_rate': 1.5,
                            'shares_outstanding': financial_data.get(str(base_year), {}).get('shares_outstanding', 0),
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

    # Income Statement
    st.markdown("#### 손익계산서 (단위: 억원)")
    is_rows = {
        '매출액': hist['revenue'],
        '매출원가': hist['cogs'],
        '매출총이익': hist['gross_profit'],
        '판관비': hist['sga'],
        '영업이익': hist['ebit'],
        '순이익': hist['net_income'],
    }

    ebitda = {}
    for yr in years:
        ebitda[yr] = hist['ebit'].get(yr, 0) + hist['da'].get(yr, 0)
    is_rows['EBITDA'] = ebitda

    is_df = pd.DataFrame(is_rows).T
    is_df.columns = [str(yr) for yr in is_df.columns]
    is_df = is_df.applymap(lambda x: fmt_억(x) if x != 0 else '-')
    st.dataframe(is_df, use_container_width=True)

    # Ratios
    st.markdown("#### 주요 재무비율 (%)")
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
    ratio_df = pd.DataFrame(ratio_rows).T
    st.dataframe(ratio_df, use_container_width=True)

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
    bs_df = bs_df.applymap(lambda x: fmt_억(x) if x != 0 else '-')
    st.dataframe(bs_df, use_container_width=True)

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
    cf_df = cf_df.applymap(lambda x: fmt_억(x) if x != 0 else '-')
    st.dataframe(cf_df, use_container_width=True)

    # Charts
    st.markdown("#### 📈 추이 차트")
    col1, col2 = st.columns(2)

    with col1:
        fig_rev = go.Figure()
        yrs_sorted = sorted(years)
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

    # ── Revenue Assumptions ─────────────────────────────────────────────────
    st.subheader("1. 매출액 가정")
    rev_method = st.radio("매출액 방법", ['전체 매출 기준', '사업부문별'], horizontal=True,
                          index=0 if asmp.get('revenue_method','total') == 'total' else 1)
    asmp['revenue_method'] = 'total' if rev_method == '전체 매출 기준' else 'segment'

    if asmp['revenue_method'] == 'total':
        base_rev = asmp.get('revenue_total', {}).get('base', hist['revenue'].get(base_year, 0))
        st.write(f"기준연도 매출: **{fmt_억(base_rev)}억원** ({base_year})")

        prev_growths = asmp.get('revenue_total', {}).get('growth_rates', [5.0]*5)
        cols = st.columns(5)
        growth_rates = []
        for i, col in enumerate(cols):
            with col:
                gr = col.number_input(f"Y{i+1} 성장률(%)", value=float(prev_growths[i]), step=0.5, key=f"rev_gr_{i}")
                growth_rates.append(gr)

        asmp['revenue_total'] = {'base': base_rev, 'growth_rates': growth_rates}

    else:  # segment
        segments = asmp.get('revenue_segments', [])
        if not segments:
            segments = [{'name': '세그먼트1', 'base': hist['revenue'].get(base_year, 0), 'growth_rates': [5.0]*5}]

        st.write("사업부문 추가/수정:")
        n_seg = st.number_input("사업부문 수", min_value=1, max_value=10, value=len(segments))

        while len(segments) < n_seg:
            segments.append({'name': f'세그먼트{len(segments)+1}', 'base': 0, 'growth_rates': [5.0]*5})
        segments = segments[:n_seg]

        for j, seg in enumerate(segments):
            with st.expander(f"사업부문 {j+1}", expanded=True):
                seg['name'] = st.text_input("부문명", value=seg['name'], key=f"seg_name_{j}")
                seg['base'] = st.number_input("기준 매출(원)", value=float(seg['base']), step=1e8, key=f"seg_base_{j}")
                cols = st.columns(5)
                for i, col in enumerate(cols):
                    with col:
                        seg['growth_rates'][i] = col.number_input(f"Y{i+1}(%)", value=float(seg['growth_rates'][i]), step=0.5, key=f"seg_gr_{j}_{i}")

        asmp['revenue_segments'] = segments

    st.divider()

    # ── Cost Assumptions ───────────────────────────────────────────────────
    st.subheader("2. 비용 가정")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**매출원가 (COGS)**")
        cogs_method = st.radio("COGS 방법", ['매출 대비 비율', '성장률'], horizontal=True,
                               key='cogs_method_radio',
                               index=0 if asmp.get('cogs_method','pct_revenue')=='pct_revenue' else 1)
        asmp['cogs_method'] = 'pct_revenue' if cogs_method == '매출 대비 비율' else 'growth'

        if asmp['cogs_method'] == 'pct_revenue':
            asmp['cogs_pct'] = st.slider("COGS/매출 (%)", 0.0, 100.0, float(asmp.get('cogs_pct', 60.0)), 0.5)
        else:
            prev_cg = asmp.get('cogs_growth', [3.0]*5)
            cogs_growths = []
            for i in range(5):
                cg = st.number_input(f"COGS Y{i+1} 성장률(%)", value=float(prev_cg[i]), step=0.5, key=f"cogs_gr_{i}")
                cogs_growths.append(cg)
            asmp['cogs_growth'] = cogs_growths

    with col2:
        st.markdown("**판관비 (SGA)**")
        sga_method = st.radio("SGA 방법", ['매출 대비 비율', '성장률'], horizontal=True,
                              key='sga_method_radio',
                              index=0 if asmp.get('sga_method','pct_revenue')=='pct_revenue' else 1)
        asmp['sga_method'] = 'pct_revenue' if sga_method == '매출 대비 비율' else 'growth'

        if asmp['sga_method'] == 'pct_revenue':
            asmp['sga_pct'] = st.slider("SGA/매출 (%)", 0.0, 50.0, float(asmp.get('sga_pct', 20.0)), 0.5)
        else:
            prev_sg = asmp.get('sga_growth', [3.0]*5)
            sga_growths = []
            for i in range(5):
                sg = st.number_input(f"SGA Y{i+1} 성장률(%)", value=float(prev_sg[i]), step=0.5, key=f"sga_gr_{i}")
                sga_growths.append(sg)
            asmp['sga_growth'] = sga_growths

    ebit_override = st.checkbox("영업이익률 직접 입력 (COGS/SGA 대신)", value=asmp.get('ebit_margin_override', False))
    asmp['ebit_margin_override'] = ebit_override
    if ebit_override:
        prev_margins = asmp.get('ebit_margins', [10.0]*5)
        cols = st.columns(5)
        ebit_margins = []
        for i, col in enumerate(cols):
            with col:
                m = col.number_input(f"Y{i+1} EBIT마진(%)", value=float(prev_margins[i]), step=0.5, key=f"ebit_m_{i}")
                ebit_margins.append(m)
        asmp['ebit_margins'] = ebit_margins

    st.divider()

    # ── D&A and CapEx ──────────────────────────────────────────────────────
    st.subheader("3. D&A 및 CapEx 가정")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**감가상각비 (D&A)**")
        da_method = st.radio("D&A 방법", ['매출 대비 비율', '고정금액', '전년 대비 성장'], horizontal=True,
                             key='da_method_radio',
                             index=['pct_revenue','fixed','growth'].index(asmp.get('da_method','pct_revenue')))
        method_map = {'매출 대비 비율': 'pct_revenue', '고정금액': 'fixed', '전년 대비 성장': 'growth'}
        asmp['da_method'] = method_map[da_method]

        if asmp['da_method'] == 'pct_revenue':
            asmp['da_pct'] = st.number_input("D&A/매출 (%)", value=float(asmp.get('da_pct', 3.0)), step=0.1)
        elif asmp['da_method'] == 'fixed':
            asmp['da_fixed'] = st.number_input("고정 D&A (원)", value=float(asmp.get('da_fixed', hist['da'].get(base_year, 0))), step=1e8)
        else:
            prev_dag = asmp.get('da_growth', [3.0]*5)
            da_growths = []
            for i in range(5):
                dg = st.number_input(f"D&A Y{i+1} 성장률(%)", value=float(prev_dag[i]), step=0.5, key=f"da_gr_{i}")
                da_growths.append(dg)
            asmp['da_growth'] = da_growths

    with col2:
        st.markdown("**설비투자 (CapEx)**")
        capex_method = st.radio("CapEx 방법", ['감가상각만큼 재투자', '매출 대비 비율', '고정금액'], horizontal=True,
                                key='capex_method_radio',
                                index=['equal_da','pct_revenue','fixed'].index(asmp.get('capex_method','pct_revenue')))
        capex_map = {'감가상각만큼 재투자': 'equal_da', '매출 대비 비율': 'pct_revenue', '고정금액': 'fixed'}
        asmp['capex_method'] = capex_map[capex_method]

        if asmp['capex_method'] == 'pct_revenue':
            asmp['capex_pct'] = st.number_input("CapEx/매출 (%)", value=float(asmp.get('capex_pct', 3.0)), step=0.1)
        elif asmp['capex_method'] == 'fixed':
            asmp['capex_fixed'] = st.number_input("고정 CapEx (원)", value=float(asmp.get('capex_fixed', hist['capex'].get(base_year, 0))), step=1e8)

    st.divider()

    # ── NWC ───────────────────────────────────────────────────────────────
    st.subheader("4. 순운전자본 (NWC) 변동")
    nwc_method = st.radio("NWC 방법", ['매출 대비 비율 유지', '고정 변동액'], horizontal=True,
                          index=0 if asmp.get('nwc_method','pct_revenue')=='pct_revenue' else 1)
    asmp['nwc_method'] = 'pct_revenue' if nwc_method == '매출 대비 비율 유지' else 'fixed'

    if asmp['nwc_method'] == 'pct_revenue':
        asmp['nwc_pct'] = st.number_input("NWC/매출 (%)", value=float(asmp.get('nwc_pct', 10.0)), step=0.5,
                                           help="NWC = 유동자산 - 유동부채. 이 비율을 매출 대비 일정하게 유지.")
    else:
        asmp['nwc_fixed_change'] = st.number_input("연간 NWC 변동액 (원)", value=float(asmp.get('nwc_fixed_change', 0)), step=1e8)

    st.divider()

    # ── Tax Rate ──────────────────────────────────────────────────────────
    st.subheader("5. 세율")
    asmp['tax_rate'] = st.number_input("유효 법인세율 (%)", value=float(asmp.get('tax_rate', 25.0)),
                                        min_value=0.0, max_value=50.0, step=0.5)

    st.divider()

    # ── WACC ──────────────────────────────────────────────────────────────
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
        else:
            asmp['use_market_weights'] = False

        # Preview WACC
        try:
            hist_data_for_wacc = {
                'total_debt': hist['total_debt'].get(base_year, 0),
                'total_equity': hist['total_equity'].get(base_year, 0),
            }
            temp_model = DCFModel(hist_data_for_wacc, asmp)
            preview_wacc = temp_model.calculate_wacc() * 100
            kd_after = asmp['cost_of_debt'] * (1 - asmp['tax_rate']/100)
            st.success(f"계산된 WACC = **{preview_wacc:.2f}%**")
            st.caption(f"세후 Kd = {kd_after:.2f}%")
        except Exception as e:
            st.warning(f"WACC 계산 미리보기 오류: {str(e)}")

    st.divider()

    # ── Terminal Value ─────────────────────────────────────────────────────
    st.subheader("7. 터미널 밸류")
    asmp['terminal_growth_rate'] = st.number_input("영구성장률 TGR (%)",
                                                    value=float(asmp.get('terminal_growth_rate', 1.5)),
                                                    min_value=0.0, max_value=5.0, step=0.1,
                                                    help="장기 영구성장률. 일반적으로 GDP 성장률 이하.")

    st.divider()

    # ── Shares & Net Debt ──────────────────────────────────────────────────
    st.subheader("8. 기타")
    col1, col2 = st.columns(2)
    with col1:
        shares = asmp.get('shares_outstanding', 0)
        asmp['shares_outstanding'] = st.number_input("발행주식수 (주)",
                                                       value=float(shares) if shares else 0.0,
                                                       step=1000.0,
                                                       help="총 발행주식수")
    with col2:
        net_debt_auto = hist['total_debt'].get(base_year, 0) - hist['cash'].get(base_year, 0)
        st.write(f"자동 산출 순차입금: {fmt_억(net_debt_auto)}억원")

    # Save assumptions
    st.session_state.dcf_assumptions = asmp

    st.divider()
    if st.button("💾 가정 저장 및 DCF 계산", use_container_width=True, type='primary'):
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
        with st.spinner("DCF 계산 중..."):
            model = DCFModel(hist_data, asmp)
            results = model.calculate_ev()

        fcff_df = results['fcff_df']
        wacc = results['wacc']
        pv_fcff_by_year = results['pv_fcff_by_year']
        pv_tv = results['pv_tv']
        tv = results['tv']
        ev = results['ev']
        total_pv_fcff = results['total_pv_fcff']

        net_debt = hist_data['total_debt'] - hist_data['cash']
        eq_val = model.equity_value(ev)
        shares = hist_data['shares_outstanding']
        price_per_share = model.price_per_share(eq_val, shares) if shares > 0 else None

        # ── Summary KPIs ───────────────────────────────────────────────────
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

        # ── FCFF Projection Table ─────────────────────────────────────────
        st.subheader("FCFF 추정 (단위: 억원)")
        display_rows = []
        for yr in fcff_df.index:
            df_factor = results['discount_factors'][yr]
            row = {
                '연도': str(yr),
                '매출액': fmt_억(fcff_df.loc[yr, 'Revenue']),
                '영업이익': fmt_억(fcff_df.loc[yr, 'EBIT']),
                'EBIT(1-t)': fmt_억(fcff_df.loc[yr, 'EBIT_after_tax']),
                'D&A': fmt_억(fcff_df.loc[yr, 'DA']),
                'CapEx': fmt_억(fcff_df.loc[yr, 'CapEx']),
                'ΔNWC': fmt_억(fcff_df.loc[yr, 'Delta_NWC']),
                'FCFF': fmt_억(fcff_df.loc[yr, 'FCFF']),
                '할인계수': f"{df_factor:.4f}",
                'PV(FCFF)': fmt_억(pv_fcff_by_year[yr]),
            }
            display_rows.append(row)

        fcff_display = pd.DataFrame(display_rows).set_index('연도')
        st.dataframe(fcff_display, use_container_width=True)

        st.divider()

        # ── EV Bridge ─────────────────────────────────────────────────────
        st.subheader("EV → 주당가치 계산")
        bridge_data = {
            '항목': ['FCF PV 합계', '터미널 밸류 PV', '기업가치 (EV)', '(-) 순차입금', '자기자본가치', '÷ 발행주식수', '주당가치'],
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
        bridge_df = pd.DataFrame(bridge_data)
        st.table(bridge_df.set_index('항목'))

        # WACC & TGR info
        col1, col2, col3 = st.columns(3)
        with col1:
            st.info(f"WACC: **{wacc*100:.2f}%**")
        with col2:
            st.info(f"TGR: **{asmp.get('terminal_growth_rate', 1.5):.1f}%**")
        with col3:
            tv_share = safe_div(pv_tv, ev, 0) * 100
            st.info(f"TV 비중: **{tv_share:.1f}%**")

        st.divider()

        # ── Waterfall Chart ────────────────────────────────────────────────
        st.subheader("EV 구성 폭포 차트")
        years_proj = list(fcff_df.index)
        waterfall_labels = [f"PV(FCFF {yr})" for yr in years_proj] + ["PV(TV)", "EV"]
        waterfall_values = [pv_fcff_by_year[yr] / 1e8 for yr in years_proj] + [pv_tv / 1e8, None]
        waterfall_measures = ["relative"] * len(years_proj) + ["relative", "total"]

        fig_wf = go.Figure(go.Waterfall(
            name="EV 구성",
            orientation="v",
            measure=waterfall_measures,
            x=waterfall_labels,
            y=waterfall_values,
            connector={"line": {"color": "rgb(63, 63, 63)"}},
            increasing={"marker": {"color": "steelblue"}},
            totals={"marker": {"color": "orange"}},
        ))
        fig_wf.update_layout(title="기업가치 구성 (억원)", yaxis_title="억원", height=400)
        st.plotly_chart(fig_wf, use_container_width=True)

        st.divider()

        # ── Sensitivity Analysis ───────────────────────────────────────────
        st.subheader("민감도 분석 (WACC vs TGR)")

        wacc_center = wacc * 100
        tgr_center = float(asmp.get('terminal_growth_rate', 1.5))

        wacc_range = [wacc_center - 2, wacc_center - 1, wacc_center, wacc_center + 1, wacc_center + 2]
        tgr_range = [tgr_center - 1, tgr_center - 0.5, tgr_center, tgr_center + 0.5, tgr_center + 1]
        tgr_range = [max(0.1, t) for t in tgr_range]

        wacc_range_dec = [w/100 for w in wacc_range]
        tgr_range_dec = [t/100 for t in tgr_range]

        with st.spinner("민감도 분석 계산 중..."):
            sens_df = model.sensitivity_analysis(wacc_range_dec, tgr_range_dec)

        st.write("**주당가치 민감도 (원)**")

        # Format as integer for display
        sens_display = sens_df.applymap(lambda x: f"{x:,.0f}" if x is not None and not np.isnan(x) else "N/A")
        st.dataframe(sens_display, use_container_width=True)

        # Heatmap
        sens_numeric = sens_df.copy()
        for col in sens_numeric.columns:
            sens_numeric[col] = pd.to_numeric(sens_numeric[col], errors='coerce')

        fig_heat = px.imshow(
            sens_numeric.values / 1 if shares > 0 else sens_numeric.values,
            x=[c for c in sens_df.columns],
            y=[str(idx) for idx in sens_df.index],
            color_continuous_scale='RdYlGn',
            title='주당가치 민감도 히트맵 (WACC vs TGR)',
            labels={'x': 'TGR', 'y': 'WACC', 'color': '주당가치(원)'},
            text_auto='.0f'
        )
        fig_heat.update_layout(height=400)
        st.plotly_chart(fig_heat, use_container_width=True)

        st.divider()

        # ── Export ────────────────────────────────────────────────────────
        st.subheader("📥 결과 내보내기")

        def create_excel_export():
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                # FCFF sheet
                fcff_export = fcff_df.copy()
                fcff_export = fcff_export / 1e8  # convert to 억원
                fcff_export.to_excel(writer, sheet_name='FCFF 추정', float_format='%.1f')

                # Summary sheet
                summary_data = {
                    '항목': ['EV', '순차입금', '자기자본가치', '주당가치', 'WACC', 'TGR', 'TV PV'],
                    '값': [ev/1e8, net_debt/1e8, eq_val/1e8, price_per_share or 0, wacc*100, float(asmp.get('terminal_growth_rate',1.5)), pv_tv/1e8],
                    '단위': ['억원', '억원', '억원', '원', '%', '%', '억원']
                }
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='요약', index=False)

                # Sensitivity sheet
                sens_df.to_excel(writer, sheet_name='민감도 분석')

                # Historical sheet
                hist_export_rows = {}
                for metric_name, metric_data in hist.items():
                    hist_export_rows[metric_name] = {str(k): v/1e8 for k, v in metric_data.items()}
                pd.DataFrame(hist_export_rows).T.to_excel(writer, sheet_name='역사적 데이터', float_format='%.1f')

            return output.getvalue()

        excel_data = create_excel_export()
        company_name = st.session_state.selected_company['corp_name'] if st.session_state.selected_company else 'company'
        st.download_button(
            label="📊 Excel 파일 다운로드",
            data=excel_data,
            file_name=f"{company_name}_DCF분석_{base_year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        st.error(f"DCF 계산 오류: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
