import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import os
import io
import datetime
from dotenv import load_dotenv

load_dotenv()

# API 키를 로컬 파일에 저장해두면 매번 입력할 필요 없이 다음 실행 시 자동으로 불러옵니다.
# (실행 위치에 따라 프로젝트 폴더에 쓰기 권한이 없을 수 있어 홈 디렉터리에 저장)
_API_KEY_DIR = os.path.join(os.path.expanduser('~'), '.dart_dcf')
_API_KEY_FILE = os.path.join(_API_KEY_DIR, 'api_key.txt')

def _load_saved_api_key():
    if os.path.exists(_API_KEY_FILE):
        try:
            with open(_API_KEY_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except OSError:
            return ''
    return ''

def _save_api_key(key):
    try:
        os.makedirs(_API_KEY_DIR, exist_ok=True)
        with open(_API_KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(key.strip())
        return True
    except OSError:
        return False

from dart_api import (
    search_company, get_company_info, get_financial_statements,
    get_business_segments, get_corp_code_list
)
from dcf import DCFModel
from excel_export import build_excel_workbook

st.set_page_config(
    page_title="DART DCF 분석",
    page_icon="📊",
    layout="wide"
)

# ─── Session State Defaults ───────────────────────────────────────────────────
def init_session_state():
    cur_year = datetime.datetime.now().year
    defaults = {
        'api_key': os.getenv('DART_API_KEY', '') or _load_saved_api_key(),
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
        'amount_unit': '억원',
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session_state()

# ─── Utility ──────────────────────────────────────────────────────────────────
# 기업검색 탭에서 선택한 표시 단위(천원/백만원/억원)에 맞춰 모든 금액 표시를 변환
UNIT_DIVISORS = {'천원': 1e3, '백만원': 1e6, '억원': 1e8}

def current_unit():
    return st.session_state.get('amount_unit', '억원')

def fmt_억(value):
    """원(KRW) 단위 값을 선택된 표시 단위로 변환 후 포맷 (함수명은 호환을 위해 유지)"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    divisor = UNIT_DIVISORS[current_unit()]
    converted = value / divisor
    return f"{converted:,.1f}"

def safe_div(a, b, default=0.0):
    try:
        if b == 0 or b is None:
            return default
        return a / b
    except Exception:
        return default

def fmt_df(df):
    """DataFrame의 숫자 셀을 선택된 표시 단위로 포맷 (pandas 2.1+ 호환)"""
    return df.map(lambda x: fmt_억(x) if isinstance(x, (int, float)) and x != 0 else ('-' if x == 0 else x))

def calc_korea_corp_tax_rate(taxable_income_억: float) -> float:
    """
    2026년 이후 한국 법인세 누진세율 (국세청 기준, 영리법인).
    구간별 누적 계산:
      ≤2억: 10%, 2~200억: 20%, 200~3000억: 22%, >3000억: 25%
    """
    if taxable_income_억 <= 0:
        return 0.25
    if taxable_income_억 <= 2:
        tax = taxable_income_억 * 0.10
    elif taxable_income_억 <= 200:
        tax = 2 * 0.10 + (taxable_income_억 - 2) * 0.20
    elif taxable_income_억 <= 3000:
        tax = 2 * 0.10 + 198 * 0.20 + (taxable_income_억 - 200) * 0.22
    else:
        tax = 2 * 0.10 + 198 * 0.20 + 2800 * 0.22 + (taxable_income_억 - 3000) * 0.25
    return tax / taxable_income_억

def extract_historical_summary(financial_data: dict, years: list) -> dict:
    """
    연도별 재무데이터(financial_data)에서 주요 지표를 추출.
    financial_data 구조: {str(year): {'income_statement': {...}, 'balance_sheet': {...}, 'cash_flow': {...}}}
    """
    summary = {
        'revenue': {}, 'cogs': {}, 'gross_profit': {}, 'sga': {},
        'ebit': {}, 'net_income': {}, 'da': {}, 'da_debug': {}, 'capex': {},
        'total_assets': {}, 'total_debt': {}, 'total_equity': {}, 'cash': {},
        'operating_cf': {}, 'investing_cf': {}, 'nwc': {},
        # 운전자본 세부 항목 (DSO/DIO/DPO 계산용)
        'accounts_receivable': {}, 'inventory': {}, 'accounts_payable': {},
        'dso': {}, 'dio': {}, 'dpo': {},
        # 비용 세부 항목
        'selling_expense': {}, 'admin_expense': {},
        # 주식수
        'shares_outstanding': {},
        # 차입금/이자/배당
        'interest_expense': {}, 'dividends_paid': {}, 'implied_interest_rate': {},
        # 순차입금 (IBD - 현금성자산)
        'ibd': {}, 'cash_equivalents': {}, 'net_debt': {},
        # D&A를 XBRL 주석(유형자산 노트)에서 찾아낸 연도 표시용
        'da_source': {}, 'xbrl_debug': {},
    }
    for yr in years:
        yr_data = financial_data.get(str(yr), {})
        if not yr_data:
            continue
        is_data = yr_data.get('income_statement', {})
        bs_data = yr_data.get('balance_sheet', {})
        cf_data = yr_data.get('cash_flow', {})
        summary['da_source'][yr] = yr_data.get('_da_source')
        summary['xbrl_debug'][yr] = yr_data.get('_xbrl_debug')

        rev = is_data.get('revenue', 0)
        cogs = is_data.get('cogs', 0)
        summary['revenue'][yr] = rev
        summary['cogs'][yr] = cogs
        summary['gross_profit'][yr] = rev - cogs
        summary['sga'][yr] = is_data.get('sga', 0)
        summary['ebit'][yr] = is_data.get('ebit', 0)
        summary['net_income'][yr] = is_data.get('net_income', 0)
        summary['da'][yr] = cf_data.get('da', is_data.get('da', 0))
        summary['da_debug'][yr] = cf_data.get('_da_debug_items')
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

        # 운전자본 세부 항목
        ar = bs_data.get('accounts_receivable', 0)
        inv = bs_data.get('inventory', 0)
        ap = bs_data.get('accounts_payable', 0)
        summary['accounts_receivable'][yr] = ar
        summary['inventory'][yr] = inv
        summary['accounts_payable'][yr] = ap

        # DSO = 매출채권 / 매출액 × 365 (매출채권 회수 평균 일수)
        summary['dso'][yr] = safe_div(ar, rev, 0) * 365 if rev > 0 else 0
        # DIO = 재고자산 / 매출원가 × 365 (재고 보유 평균 일수)
        summary['dio'][yr] = safe_div(inv, cogs if cogs > 0 else rev, 0) * 365
        # DPO = 매입채무 / 매출원가 × 365 (매입채무 지급 평균 일수)
        summary['dpo'][yr] = safe_div(ap, cogs if cogs > 0 else rev, 0) * 365

        # 비용 세부 항목
        summary['selling_expense'][yr] = is_data.get('selling_expense', 0)
        summary['admin_expense'][yr] = is_data.get('admin_expense', 0)

        # 주식수
        summary['shares_outstanding'][yr] = bs_data.get('shares_outstanding', 0)

        # 차입금/이자/배당: 평균 차입금 대비 이자비용으로 implied 이자율 산출
        interest_exp = is_data.get('interest_expense', 0)
        total_debt_yr = bs_data.get('total_debt', 0)
        summary['interest_expense'][yr] = interest_exp
        summary['dividends_paid'][yr] = cf_data.get('dividends_paid', 0)
        summary['implied_interest_rate'][yr] = safe_div(interest_exp, total_debt_yr, 0) * 100 if total_debt_yr > 0 else 0

        # 순차입금 = IBD(단기차입금+유동성장기부채+장기차입금+리스부채+CB/EB/BW/사채 등)
        # - 현금성자산(현금및현금성자산+단기금융상품)
        ibd_yr = bs_data.get('ibd', 0)
        cash_eq_yr = bs_data.get('cash_equivalents', 0)
        summary['ibd'][yr] = ibd_yr
        summary['cash_equivalents'][yr] = cash_eq_yr
        summary['net_debt'][yr] = bs_data.get('net_debt', ibd_yr - cash_eq_yr)

    return summary

def make_hist_pct_table(hist, metric, years, rev_key='revenue'):
    """
    과거 3개년 지표를 '금액(억원) | 매출 대비 비율(%)' 형태의 가로 테이블로 반환.
    years를 열로 배치 (가로 방향).
    """
    rows = {}
    sorted_yrs = sorted(years)
    for yr in sorted_yrs:
        val = hist[metric].get(yr, 0)
        rev = hist[rev_key].get(yr, 1) or 1
        rows[str(yr)] = {
            '금액(억원)': f"{val/1e8:,.1f}",
            '매출 대비(%)': f"{val/rev*100:.1f}%",
        }
    return pd.DataFrame(rows)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 DART DCF 분석")
    st.divider()

    api_key_input = st.text_input(
        "DART API Key",
        value=st.session_state.api_key,
        type="password",
        help="dart.fss.or.kr에서 발급받은 API 키를 입력하세요. 한 번 입력하면 로컬에 저장되어 다음 실행부터는 다시 입력할 필요가 없습니다."
    )
    if api_key_input and api_key_input != st.session_state.api_key:
        st.session_state.api_key = api_key_input
        if not _save_api_key(api_key_input):
            st.warning(f"API 키 자동저장 실패 (쓰기 권한 확인 필요: {_API_KEY_FILE})")
    elif api_key_input:
        st.session_state.api_key = api_key_input

    if st.session_state.api_key:
        st.caption(f"✅ 저장된 API 키 사용 중 (저장 위치: {_API_KEY_FILE})")

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

    st.selectbox(
        "금액 표시 단위", list(UNIT_DIVISORS.keys()),
        key='amount_unit',
        help="이후 모든 화면(재무제표 확인, DCF 가정 입력, DCF 결과)의 금액 표시에 적용됩니다.",
    )

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
                        # 기준연도 포함 과거 3개년 연간 데이터 로드
                        years = [base_year, base_year - 1, base_year - 2]

                        financial_data = {}
                        no_data_years = []
                        for yr in years:
                            try:
                                # 역사적 비교는 항상 연간(사업보고서) 기준
                                fs = get_financial_statements(corp_code, yr, api_key,
                                                              report_type='11011',
                                                              fs_div=fs_div)
                                financial_data[str(yr)] = fs
                                if fs.get('_no_data'):
                                    no_data_years.append(yr)
                            except Exception as e:
                                st.warning(f"{yr}년 데이터 로드 실패: {str(e)}")
                                financial_data[str(yr)] = {}
                                no_data_years.append(yr)

                        if len(no_data_years) == len(years):
                            st.error(
                                f"❌ {selected_co['corp_name']}의 재무제표를 찾을 수 없습니다. "
                                "감사보고서만 제출하는 비상장 외부감사대상 법인은 DART Open API의 "
                                "표준 재무제표(전체계정) 조회 대상이 아니어서 이 화면에서는 불러올 수 없습니다. "
                                "감사보고서 원문은 DART 웹사이트(dart.fss.or.kr)에서 직접 확인해주세요."
                            )
                            st.session_state.financial_data = financial_data
                            st.stop()
                        elif no_data_years:
                            st.warning(f"{', '.join(str(y)+'년' for y in no_data_years)} 데이터를 찾을 수 없어 제외되었습니다.")

                        # 분기 보고서를 선택한 경우 기준연도 분기 데이터 별도 보관
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

                        # DCF 기본값 자동 설정 (재무제표 기반)
                        latest_rev = hist['revenue'].get(base_year, 0)
                        latest_da = hist['da'].get(base_year, 0)
                        latest_capex = hist['capex'].get(base_year, 0)
                        latest_nwc = hist['nwc'].get(base_year, 0)
                        latest_tax_expense = financial_data.get(str(base_year), {}).get('income_statement', {}).get('tax_expense', 0)
                        pretax_income = financial_data.get(str(base_year), {}).get('income_statement', {}).get('pretax_income', 0)

                        cogs = hist['cogs'].get(base_year, 0)
                        sga = hist['sga'].get(base_year, 0)
                        cogs_pct = safe_div(cogs, latest_rev, 0.6) * 100
                        sga_pct = safe_div(sga, latest_rev, 0.2) * 100
                        da_pct = safe_div(latest_da, latest_rev, 0.03) * 100
                        capex_pct = safe_div(latest_capex, latest_rev, 0.03) * 100
                        nwc_pct = safe_div(latest_nwc, latest_rev, 0.1) * 100

                        total_debt = hist['total_debt'].get(base_year, 0)
                        total_equity = hist['total_equity'].get(base_year, 0)
                        total_cap = total_debt + total_equity
                        debt_weight = safe_div(total_debt, total_cap, 0.3) * 100

                        ebit_억 = hist['ebit'].get(base_year, 0) / 1e8
                        prog_tax_rate = calc_korea_corp_tax_rate(ebit_억) * 100

                        # DSO/DIO/DPO: 과거 3년 평균값으로 초기화
                        valid_dso = [v for v in hist['dso'].values() if v > 0]
                        valid_dio = [v for v in hist['dio'].values() if v > 0]
                        valid_dpo = [v for v in hist['dpo'].values() if v > 0]
                        avg_dso = np.mean(valid_dso) if valid_dso else 60.0
                        avg_dio = np.mean(valid_dio) if valid_dio else 45.0
                        avg_dpo = np.mean(valid_dpo) if valid_dpo else 30.0

                        # Try to get shares from financial data
                        shares_from_fs = 0
                        for yr in [base_year, base_year - 1, base_year - 2]:
                            s = financial_data.get(str(yr), {}).get('balance_sheet', {}).get('shares_outstanding', 0)
                            if s and s > 0:
                                shares_from_fs = s
                                break

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
                            'capex_method': 'equal_da',
                            'capex_pct': round(capex_pct, 1),
                            'new_invest_capex_fixed': 0.0,
                            'new_invest_capex_list': [0.0] * 5,
                            'nwc_method': 'turnover',
                            'nwc_dso': round(avg_dso, 1),
                            'nwc_dio': round(avg_dio, 1),
                            'nwc_dpo': round(avg_dpo, 1),
                            'nwc_other_pct': 0.0,
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
                            'shares_outstanding': float(shares_from_fs),
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
    years = sorted([k for k in hist['revenue'].keys()])  # ascending

    st.subheader(f"📊 {st.session_state.selected_company['corp_name']} 재무 현황")
    st.caption(f"재무제표 구분: {st.session_state.fs_div} | 기준연도: {base_year}")

    # 손익계산서 (연도를 열로 표시, 오름차순) — 이익률 지표를 해당 항목 바로 아래에 함께 표시
    u = current_unit()
    st.markdown(f"#### 손익계산서 (단위: {u})")
    is_display = {}
    for yr in years:
        rev = hist['revenue'].get(yr, 1) or 1
        cogs_v = hist['cogs'].get(yr, 0)
        ebit_v = hist['ebit'].get(yr, 0)
        da_v = hist['da'].get(yr, 0)
        ebitda_v = ebit_v + da_v
        ni = hist['net_income'].get(yr, 0)
        eq = hist['total_equity'].get(yr, 1) or 1
        assets = hist['total_assets'].get(yr, 1) or 1
        is_display[str(yr)] = {
            f'매출액({u})': fmt_억(rev),
            f'매출원가({u})': fmt_억(cogs_v),
            '매출원가율': f"{safe_div(cogs_v, rev)*100:.1f}%",
            f'매출총이익({u})': fmt_억(hist['gross_profit'].get(yr,0)),
            f'판관비({u})': fmt_억(hist['sga'].get(yr,0)),
            f'감가상각비(D&A)({u})': fmt_억(da_v),
            f'영업이익(EBIT)({u})': fmt_억(ebit_v),
            '영업이익률': f"{safe_div(ebit_v, rev)*100:.1f}%",
            f'EBITDA({u})': fmt_억(ebitda_v),
            'EBITDA마진율': f"{safe_div(ebitda_v, rev)*100:.1f}%",
            f'순이익({u})': fmt_억(ni),
            '순이익률': f"{safe_div(ni, rev)*100:.1f}%",
            'ROE': f"{safe_div(ni, eq)*100:.1f}%",
            'ROA': f"{safe_div(ni, assets)*100:.1f}%",
        }
    st.dataframe(pd.DataFrame(is_display), use_container_width=True)

    # 재무상태표 (연도를 열로 표시, 오름차순)
    st.markdown(f"#### 재무상태 (단위: {u})")
    bs_rows = {
        '총자산': hist['total_assets'],
        '총부채': {yr: hist['total_assets'].get(yr,0) - hist['total_equity'].get(yr,0) for yr in years},
        '자기자본': hist['total_equity'],
        '차입금': hist['total_debt'],
        '현금': hist['cash'],
        '순차입금': {yr: hist['total_debt'].get(yr,0) - hist['cash'].get(yr,0) for yr in years},
    }
    bs_df = pd.DataFrame({str(yr): {k: v.get(yr, 0) for k, v in bs_rows.items()} for yr in years})
    st.dataframe(fmt_df(bs_df), use_container_width=True)

    # 현금흐름 (연도를 열로 표시, 오름차순)
    st.markdown(f"#### 현금흐름 (단위: {u})")
    cf_rows = {
        '영업활동CF': hist['operating_cf'],
        '투자활동CF': hist['investing_cf'],
        'D&A': hist['da'],
        'CapEx': hist['capex'],
        '잉여현금흐름(FCF)': {yr: hist['operating_cf'].get(yr,0) - hist['capex'].get(yr,0) for yr in years},
    }
    cf_df = pd.DataFrame({str(yr): {k: v.get(yr, 0) for k, v in cf_rows.items()} for yr in years})
    st.dataframe(fmt_df(cf_df), use_container_width=True)

    # 운전자본 현황
    if any(hist['accounts_receivable'].get(yr, 0) > 0 for yr in years):
        st.markdown("#### 운전자본 현황 (DSO/DIO/DPO)")
        nwc_detail = {}
        for yr in years:
            ar = hist['accounts_receivable'].get(yr, 0)
            inv = hist['inventory'].get(yr, 0)
            ap = hist['accounts_payable'].get(yr, 0)
            rev_yr = hist['revenue'].get(yr, 0)
            cogs_yr = hist['cogs'].get(yr, 0)
            nwc_detail[str(yr)] = {
                f'매출액({u})': fmt_억(rev_yr),
                f'매출원가({u})': fmt_억(cogs_yr),
                f'매출채권({u})': fmt_억(ar),
                f'재고자산({u})': fmt_억(inv),
                f'매입채무({u})': fmt_억(ap),
                'DSO(일)': f"{hist['dso'].get(yr,0):.1f}",
                'DIO(일)': f"{hist['dio'].get(yr,0):.1f}",
                'DPO(일)': f"{hist['dpo'].get(yr,0):.1f}",
            }
        st.dataframe(pd.DataFrame(nwc_detail), use_container_width=True)
        st.caption("DSO=매출채권/매출액×365 | DIO=재고/매출원가×365 | DPO=매입채무/매출원가×365")

    # 추이 차트
    st.markdown("#### 📈 추이 차트")
    col1, col2 = st.columns(2)
    yrs_sorted = sorted(years)

    with col1:
        fig_rev = go.Figure()
        divisor = UNIT_DIVISORS[u]
        rev_vals = [hist['revenue'].get(yr, 0) / divisor for yr in yrs_sorted]
        ebit_vals = [hist['ebit'].get(yr, 0) / divisor for yr in yrs_sorted]
        fig_rev.add_trace(go.Bar(name='매출액', x=[str(y) for y in yrs_sorted], y=rev_vals, marker_color='steelblue'))
        fig_rev.add_trace(go.Bar(name='영업이익', x=[str(y) for y in yrs_sorted], y=ebit_vals, marker_color='orange'))
        fig_rev.update_layout(title='매출액 및 영업이익 추이', yaxis_title=u, barmode='group', height=350)
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
    hist_years = sorted([yr for yr in hist['revenue'].keys()])  # 과거 3개년, 오름차순
    proj_years = [base_year + i + 1 for i in range(n)]          # 예측 연도 (실제 연도)

    # ── 1. 매출액 ────────────────────────────────────────────────────────────
    st.subheader("1. 매출액 가정")

    # 과거 3개년 매출 현황
    st.markdown("**📊 과거 실적 (억원)**")
    hist_rev_data = {}
    prev_rev = None
    for yr in hist_years:
        rev = hist['revenue'].get(yr, 0)
        yoy = safe_div(rev - prev_rev, prev_rev, 0) * 100 if prev_rev else None
        hist_rev_data[str(yr)] = {
            '매출액(억원)': f"{rev/1e8:,.1f}",
            'YoY성장률': f"{yoy:.1f}%" if yoy is not None else "-",
        }
        prev_rev = rev
    st.dataframe(pd.DataFrame(hist_rev_data), use_container_width=True)

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
                # 입력 레이블에 실제 연도 표시
                gr = col.number_input(f"{proj_years[i]}년 성장률(%)", value=float(prev_growths[i] if i < len(prev_growths) else 3.0), step=0.5, key=f"rev_gr_{i}")
                growth_rates.append(gr)
        asmp['revenue_total'] = {'base': base_rev, 'growth_rates': growth_rates}

        # 미리보기: 연도를 열로 배치
        st.markdown("**📊 매출액 추정 미리보기 (억원)**")
        rev_preview = {}
        v = base_rev
        for i, g in enumerate(growth_rates):
            v = v * (1 + g / 100)
            rev_preview[str(proj_years[i])] = {
                '매출액(억원)': f"{v/1e8:,.1f}",
                'YoY성장률': f"{g:.1f}%",
            }
        st.dataframe(pd.DataFrame(rev_preview), use_container_width=True)

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
                        seg['growth_rates'][i] = col.number_input(f"{proj_years[i]}(%)", value=float(seg['growth_rates'][i] if i < len(seg['growth_rates']) else 3.0), step=0.5, key=f"seg_gr_{j}_{i}")
        asmp['revenue_segments'] = segments
        base_rev = sum(s['base'] for s in segments)
        growth_rates = [(sum(s['base'] * s['growth_rates'][i] for s in segments) / max(base_rev,1)) for i in range(n)]

    st.divider()

    # ── 2. 비용 가정 ─────────────────────────────────────────────────────────
    st.subheader("2. 비용 가정")
    st.caption("📌 과거 재무제표 기반 매출 대비 비율을 참고하여 예측값을 입력하세요.")

    base_rev_val = asmp.get('revenue_total', {}).get('base', hist['revenue'].get(base_year, 0))
    rev_growths = asmp.get('revenue_total', {}).get('growth_rates', [5.0]*n)

    # 과거 비용 현황 표
    cost_hist = {}
    for yr in hist_years:
        rev = hist['revenue'].get(yr, 1) or 1
        cogs_v = hist['cogs'].get(yr, 0)
        sga_v = hist['sga'].get(yr, 0)
        ebit_v = hist['ebit'].get(yr, 0)
        cost_hist[str(yr)] = {
            '매출원가(억원)': f"{cogs_v/1e8:,.1f}",
            '매출원가율(%)': f"{cogs_v/rev*100:.1f}%",
            '판관비(억원)': f"{sga_v/1e8:,.1f}",
            '판관비율(%)': f"{sga_v/rev*100:.1f}%",
            '영업이익(억원)': f"{ebit_v/1e8:,.1f}",
            '영업이익률(%)': f"{ebit_v/rev*100:.1f}%",
        }
    st.markdown("**과거 비용 실적**")
    st.dataframe(pd.DataFrame(cost_hist), use_container_width=True)

    # ── (A) 매출원가 (COGS) ──
    st.markdown("---")
    st.markdown("#### (A) 매출원가 (COGS)")
    st.caption("매출원가 = 제조원가, 상품원가 등 매출에 직접 대응되는 비용")

    cogs_method = st.radio("COGS 방법", ['매출 대비 비율 (%)', '전년 대비 성장률'], horizontal=True,
                           key='cogs_method_radio',
                           index=0 if asmp.get('cogs_method','pct_revenue')=='pct_revenue' else 1)
    asmp['cogs_method'] = 'pct_revenue' if cogs_method == '매출 대비 비율 (%)' else 'growth'
    cogs_growth_vals = asmp.get('cogs_growth', [3.0]*n)

    if asmp['cogs_method'] == 'pct_revenue':
        asmp['cogs_pct'] = st.number_input(
            "COGS/매출 (%)",
            value=float(asmp.get('cogs_pct', 60.0)),
            min_value=0.0, max_value=100.0, step=0.1,
            help="기준연도 실적 참고 후 향후 예상 비율 입력"
        )
    else:
        cogs_growth_vals = []
        prev_cg = asmp.get('cogs_growth', [3.0]*n)
        cols = st.columns(n)
        for i, col in enumerate(cols):
            with col:
                cg = col.number_input(f"{proj_years[i]}년 성장률(%)", value=float(prev_cg[i] if i < len(prev_cg) else 3.0), step=0.5, key=f"cogs_gr_{i}")
                cogs_growth_vals.append(cg)
        asmp['cogs_growth'] = cogs_growth_vals

    # ── (B) 판매비와관리비 (SGA) ── COGS와 동일한 구조 (매출 대비 비율 / 전년 대비 성장률)
    st.markdown("---")
    st.markdown("#### (B) 판매비와관리비 (SGA)")
    st.caption("판관비 = 재무제표상 판매비와관리비 합계")

    sga_method = st.radio("SGA 방법", ['매출 대비 비율 (%)', '전년 대비 성장률'], horizontal=True,
                          key='sga_method_radio',
                          index=0 if asmp.get('sga_method','pct_revenue')=='pct_revenue' else 1)
    asmp['sga_method'] = 'pct_revenue' if sga_method == '매출 대비 비율 (%)' else 'growth'
    sga_growth_vals = asmp.get('sga_growth', [3.0]*n)

    if asmp['sga_method'] == 'pct_revenue':
        asmp['sga_pct'] = st.number_input(
            "SGA/매출 (%)",
            value=float(asmp.get('sga_pct', safe_div(hist['sga'].get(base_year, 0), hist['revenue'].get(base_year, 1), 10.0) * 100 if hist['sga'].get(base_year, 0) else 10.0)),
            min_value=0.0, max_value=100.0, step=0.1,
            help="기준연도 실적 참고 후 향후 예상 비율 입력"
        )
    else:
        sga_growth_vals = []
        prev_sg = asmp.get('sga_growth', [3.0]*n)
        cols = st.columns(n)
        for i, col in enumerate(cols):
            with col:
                sg = col.number_input(f"{proj_years[i]}년 성장률(%)", value=float(prev_sg[i] if i < len(prev_sg) else 3.0), step=0.5, key=f"sga_gr_{i}")
                sga_growth_vals.append(sg)
        asmp['sga_growth'] = sga_growth_vals

    # 비용 미리보기 (연도를 열로 배치)
    st.markdown("**📊 비용 추정 미리보기 (억원)**")
    cost_preview = {}
    rev_v = base_rev_val
    cogs_v = hist['cogs'].get(base_year, 0)
    sga_v = hist['sga'].get(base_year, 0)
    for i, g in enumerate(rev_growths):
        yr_label = str(proj_years[i])
        rev_v = rev_v * (1 + g / 100)
        if asmp['cogs_method'] == 'pct_revenue':
            cogs_v = rev_v * asmp.get('cogs_pct', 60.0) / 100
        else:
            cg = cogs_growth_vals[i] if i < len(cogs_growth_vals) else (cogs_growth_vals[-1] if cogs_growth_vals else 3.0)
            cogs_v = cogs_v * (1 + cg / 100)
        if asmp['sga_method'] == 'pct_revenue':
            sga_v = rev_v * asmp.get('sga_pct', 10.0) / 100
        else:
            sg = sga_growth_vals[i] if i < len(sga_growth_vals) else (sga_growth_vals[-1] if sga_growth_vals else 3.0)
            sga_v = sga_v * (1 + sg / 100)
        ebit_v = rev_v - cogs_v - sga_v
        cost_preview[yr_label] = {
            '매출액(억원)': f"{rev_v/1e8:,.1f}",
            '매출원가(억원)': f"{cogs_v/1e8:,.1f}",
            'COGS%': f"{safe_div(cogs_v,rev_v)*100:.1f}%",
            '판관비(억원)': f"{sga_v/1e8:,.1f}",
            'SGA%': f"{safe_div(sga_v,rev_v)*100:.1f}%",
            '영업이익(억원)': f"{ebit_v/1e8:,.1f}",
            'EBIT%': f"{safe_div(ebit_v,rev_v)*100:.1f}%",
        }
    st.dataframe(pd.DataFrame(cost_preview), use_container_width=True)

    st.divider()

    # ── 3. D&A 및 CapEx ──────────────────────────────────────────────────────
    st.subheader("3. D&A 및 CapEx 가정")

    # 과거 D&A / CapEx 현황
    da_capex_hist = {}
    for yr in hist_years:
        rev = hist['revenue'].get(yr, 1) or 1
        da_v = hist['da'].get(yr, 0)
        cap_v = hist['capex'].get(yr, 0)
        da_capex_hist[str(yr)] = {
            'D&A(억원)': f"{da_v/1e8:,.1f}",
            'D&A/매출(%)': f"{da_v/rev*100:.1f}%",
            'CapEx(억원)': f"{cap_v/1e8:,.1f}",
            'CapEx/매출(%)': f"{cap_v/rev*100:.1f}%",
            'CapEx/D&A': f"{safe_div(cap_v,da_v,0):.2f}x",
        }
    st.markdown("**과거 D&A / CapEx 실적**")
    st.dataframe(pd.DataFrame(da_capex_hist), use_container_width=True)

    # D&A가 0으로 잡힌 연도가 있으면, DART가 실제로 내려준 CF/IS 계정명을
    # 그대로 보여줘서 어떤 명칭으로 들어오는지 직접 확인할 수 있게 한다.
    # XBRL 주석 폴백이 값을 찾아낸 연도는 D&A가 0이 아니게 되므로 자동으로
    # 아래 경고 expander 대상에서 빠진다. 다만 da==0이 아니더라도 출처가
    # xbrl_note인 연도가 있으면 참고용으로 안내한다.
    xbrl_note_years = [yr for yr in hist_years if hist.get('da_source', {}).get(yr) == 'xbrl_note']
    if xbrl_note_years:
        st.caption(
            f"ℹ️ {', '.join(str(y) for y in xbrl_note_years)}년 D&A는 현금흐름표 본문에 "
            "없어 XBRL 유형자산 주석(당기증가(상각) 등)에서 추출한 값입니다."
        )

    zero_da_years = [yr for yr in hist_years if hist['da'].get(yr, 0) == 0]
    if zero_da_years:
        with st.expander(f"⚠️ {', '.join(str(y) for y in zero_da_years)}년 D&A가 0으로 조회됨 — DART 원본 계정명 확인"):
            for yr in zero_da_years:
                debug_items = hist.get('da_debug', {}).get(yr)
                st.markdown(f"**{yr}년**")
                if not debug_items:
                    st.caption("해당 연도의 현금흐름표/손익계산서 원본 항목 자체가 비어있습니다 (데이터 조회 실패 가능성).")
                else:
                    st.dataframe(pd.DataFrame(debug_items), use_container_width=True)

                xbrl_dbg = hist.get('xbrl_debug', {}).get(yr)
                if xbrl_dbg:
                    st.caption(f"XBRL 주석 폴백 진단 — 단계: `{xbrl_dbg.get('stage')}`")
                    if xbrl_dbg.get('rcept_no'):
                        st.caption(f"접수번호(rcept_no): {xbrl_dbg['rcept_no']}")
                    if xbrl_dbg.get('da_breakdown'):
                        b = xbrl_dbg['da_breakdown']
                        st.caption(
                            f"D&A 구성: 감가상각비(유형자산) {b.get('ppe', 0):,.0f} + "
                            f"무형자산상각비 {b.get('intangible', 0):,.0f} + "
                            f"사용권자산상각비 {b.get('rou', 0):,.0f}"
                        )
                    if xbrl_dbg.get('detail'):
                        st.code(str(xbrl_dbg['detail'])[:1000])
                    if xbrl_dbg.get('tag_sample'):
                        st.caption("XBRL 내 감가상각/상각/유형자산 관련 태그명 후보:")
                        st.code("\n".join(xbrl_dbg['tag_sample']))
                    elif xbrl_dbg.get('stage') == 'no_matching_tags':
                        st.caption("XBRL 문서 자체에 depreciation/amortisation/propertyplant 패턴을 포함한 태그가 전혀 없습니다 — 이 회사 필링은 주석을 XBRL로 태깅하지 않았을 가능성이 높습니다.")

    st.markdown("---")

    # (1) D&A
    st.markdown("#### (1) 감가상각비 (D&A)")
    st.caption("D&A = 재무제표 현금흐름표의 감가상각비·무형자산상각비 합계")
    da_method = st.radio("D&A 방법", ['매출 대비 비율', '고정금액', '전년 대비 성장'], horizontal=True,
                         key='da_method_radio',
                         index=['pct_revenue','fixed','growth'].index(asmp.get('da_method','pct_revenue')))
    method_map_da = {'매출 대비 비율': 'pct_revenue', '고정금액': 'fixed', '전년 대비 성장': 'growth'}
    asmp['da_method'] = method_map_da[da_method]
    da_growth_vals = asmp.get('da_growth', [3.0]*n)

    if asmp['da_method'] == 'pct_revenue':
        asmp['da_pct'] = st.number_input("D&A/매출 (%)", value=float(asmp.get('da_pct', 3.0)), min_value=0.0, step=0.1)
    elif asmp['da_method'] == 'fixed':
        asmp['da_fixed'] = st.number_input("고정 D&A (원)", value=float(asmp.get('da_fixed', hist['da'].get(base_year, 0))), step=1e8)
    else:
        da_growth_vals = []
        prev_dag = asmp.get('da_growth', [3.0]*n)
        cols = st.columns(n)
        for i, col in enumerate(cols):
            with col:
                dg = col.number_input(f"{proj_years[i]}년(%)", value=float(prev_dag[i] if i < len(prev_dag) else 3.0), step=0.5, key=f"da_gr_{i}")
                da_growth_vals.append(dg)
        asmp['da_growth'] = da_growth_vals

    st.markdown("---")

    # (2) 유지보수 CapEx
    st.markdown("#### (2) 유지보수 CapEx (Maintenance CapEx)")
    st.caption("기존 자산의 기능 유지에 필요한 최소 투자. 일반적으로 D&A 수준 또는 그 이상.")
    capex_method = st.radio("유지보수 CapEx 방법", ['D&A만큼 재투자 (보수적)', '매출 대비 비율', '고정금액'], horizontal=True,
                            key='capex_method_radio',
                            index=['equal_da','pct_revenue','fixed'].index(asmp.get('capex_method','equal_da')))
    capex_map = {'D&A만큼 재투자 (보수적)': 'equal_da', '매출 대비 비율': 'pct_revenue', '고정금액': 'fixed'}
    asmp['capex_method'] = capex_map[capex_method]

    if asmp['capex_method'] == 'pct_revenue':
        asmp['capex_pct'] = st.number_input("유지보수 CapEx/매출 (%)", value=float(asmp.get('capex_pct', 3.0)), min_value=0.0, step=0.1)
    elif asmp['capex_method'] == 'fixed':
        asmp['capex_fixed'] = st.number_input("유지보수 CapEx 고정액 (원)", value=float(asmp.get('capex_fixed', hist['capex'].get(base_year, 0))), step=1e8)

    st.markdown("---")

    # (3) 신규투자 CapEx (연도별 입력)
    st.markdown("#### (3) 신규투자 CapEx (연도별 입력)")
    st.caption("성장을 위한 신규 설비·투자. 유지보수 CapEx에 추가로 반영. 연도별로 입력하세요.")
    new_invest_list = asmp.get('new_invest_capex_list', [0.0]*n)
    cols = st.columns(n)
    new_invest_vals = []
    for i, col in enumerate(cols):
        with col:
            v = col.number_input(
                f"{proj_years[i]}년(억원)",
                value=float(new_invest_list[i] if i < len(new_invest_list) else 0.0),
                min_value=0.0, step=10.0, key=f"new_invest_{i}"
            )
            new_invest_vals.append(v * 1e8)  # store in 원
    asmp['new_invest_capex_list'] = [v / 1e8 for v in new_invest_vals]  # store as 억원 for display
    asmp['new_invest_capex_fixed'] = new_invest_vals[0] if new_invest_vals else 0  # backward compat

    # D&A / CapEx 통합 미리보기 (연도를 열로)
    st.markdown("**📊 D&A / CapEx 추정 미리보기 (억원)**")
    base_da = hist['da'].get(base_year, 0)
    base_capex = hist['capex'].get(base_year, 0)
    da_capex_preview = {}
    rev_v2 = base_rev_val
    da_v2 = base_da
    for i, g in enumerate(rev_growths):
        yr_label = str(proj_years[i])
        rev_v2 = rev_v2 * (1 + g / 100)
        # D&A 계산
        if asmp['da_method'] == 'pct_revenue':
            da_v2 = rev_v2 * asmp.get('da_pct', 3.0) / 100
        elif asmp['da_method'] == 'fixed':
            da_v2 = asmp.get('da_fixed', base_da)
        else:
            dg = da_growth_vals[i] if i < len(da_growth_vals) else (da_growth_vals[-1] if da_growth_vals else 3.0)
            da_v2 = da_v2 * (1 + dg / 100)
        # 유지보수 CapEx 계산
        if asmp['capex_method'] == 'equal_da':
            maint_capex = da_v2
        elif asmp['capex_method'] == 'pct_revenue':
            maint_capex = rev_v2 * asmp.get('capex_pct', 3.0) / 100
        else:
            maint_capex = asmp.get('capex_fixed', base_capex)
        new_cap = new_invest_vals[i] if i < len(new_invest_vals) else 0.0
        total_capex = maint_capex + new_cap
        da_capex_preview[yr_label] = {
            'D&A(억원)': f"{da_v2/1e8:,.1f}",
            'D&A/매출(%)': f"{safe_div(da_v2,rev_v2)*100:.1f}%",
            '유지보수CapEx(억원)': f"{maint_capex/1e8:,.1f}",
            '신규투자CapEx(억원)': f"{new_cap/1e8:,.1f}",
            '총CapEx(억원)': f"{total_capex/1e8:,.1f}",
            'CapEx/D&A': f"{safe_div(total_capex,da_v2):.2f}x",
        }
    st.dataframe(pd.DataFrame(da_capex_preview), use_container_width=True)

    st.divider()

    # ── 4. NWC ───────────────────────────────────────────────────────────────
    st.subheader("4. 순운전자본 (NWC) 변동")

    # 재무제표 기반 DSO/DIO/DPO 자동 계산 현황 표시
    st.markdown("**📊 과거 운전자본 회전율 (재무제표 자동 계산)**")
    has_nwc_data = any(hist['accounts_receivable'].get(yr, 0) > 0 for yr in hist_years)

    if has_nwc_data:
        nwc_hist_data = {}
        for yr in hist_years:
            rev = hist['revenue'].get(yr, 1) or 1
            cogs_yr = hist['cogs'].get(yr, rev)
            ar = hist['accounts_receivable'].get(yr, 0)
            inv = hist['inventory'].get(yr, 0)
            ap = hist['accounts_payable'].get(yr, 0)
            dso = hist['dso'].get(yr, 0)
            dio = hist['dio'].get(yr, 0)
            dpo = hist['dpo'].get(yr, 0)
            nwc_hist_data[str(yr)] = {
                '매출액(억원)': f"{rev/1e8:,.1f}",
                '매출원가(억원)': f"{cogs_yr/1e8:,.1f}",
                '매출채권(억원)': f"{ar/1e8:,.1f}",
                '재고자산(억원)': f"{inv/1e8:,.1f}",
                '매입채무(억원)': f"{ap/1e8:,.1f}",
                'DSO(일)': f"{dso:.1f}",
                'DIO(일)': f"{dio:.1f}",
                'DPO(일)': f"{dpo:.1f}",
                'CCC(일)': f"{dso+dio-dpo:.1f}",
            }
        st.dataframe(pd.DataFrame(nwc_hist_data), use_container_width=True)
        st.caption(
            "DSO=매출채권/매출액×365 | DIO=재고자산/매출원가×365 | DPO=매입채무/매출원가×365 | "
            "CCC(현금전환주기)=DSO+DIO-DPO"
        )
        valid_dso = [hist['dso'].get(yr,0) for yr in hist_years if hist['dso'].get(yr,0) > 0]
        valid_dio = [hist['dio'].get(yr,0) for yr in hist_years if hist['dio'].get(yr,0) > 0]
        valid_dpo = [hist['dpo'].get(yr,0) for yr in hist_years if hist['dpo'].get(yr,0) > 0]
        if valid_dso:
            st.info(f"3개년 평균 — DSO: **{np.mean(valid_dso):.1f}일** | DIO: **{np.mean(valid_dio):.1f}일** | DPO: **{np.mean(valid_dpo):.1f}일**")
    else:
        st.warning("재무제표에서 매출채권/재고자산/매입채무 데이터를 찾을 수 없습니다. DSO/DIO/DPO를 직접 입력해주세요.")

    # 회전율 기준 선택
    turnover_basis = st.selectbox(
        "과거 회전율 기준",
        options=['직전연도', '과거 2개년 평균', '과거 3개년 평균'],
        index=1,
        key='turnover_basis'
    )
    asmp['turnover_basis'] = turnover_basis

    # Compute DSO/DIO/DPO based on selection
    all_years_sorted = sorted(hist_years)
    if turnover_basis == '직전연도' and all_years_sorted:
        ref_years = [all_years_sorted[-1]]
    elif turnover_basis == '과거 2개년 평균' and len(all_years_sorted) >= 2:
        ref_years = all_years_sorted[-2:]
    else:
        ref_years = all_years_sorted

    calc_dso = np.mean([hist['dso'].get(yr, 0) for yr in ref_years if hist['dso'].get(yr, 0) > 0]) if any(hist['dso'].get(yr, 0) > 0 for yr in ref_years) else 60.0
    calc_dio = np.mean([hist['dio'].get(yr, 0) for yr in ref_years if hist['dio'].get(yr, 0) > 0]) if any(hist['dio'].get(yr, 0) > 0 for yr in ref_years) else 45.0
    calc_dpo = np.mean([hist['dpo'].get(yr, 0) for yr in ref_years if hist['dpo'].get(yr, 0) > 0]) if any(hist['dpo'].get(yr, 0) > 0 for yr in ref_years) else 30.0

    st.caption(f"선택 기준 자동계산: DSO={calc_dso:.1f}일, DIO={calc_dio:.1f}일, DPO={calc_dpo:.1f}일")

    # 회전율 기준이 바뀌면 향후 DSO/DIO/DPO 가정 입력값도 자동으로 갱신
    # (위젯에 key가 지정되어 있으면 value= 인자는 무시되므로, session_state를
    # 직접 덮어써서 갱신해야 위젯에 반영됨)
    if st.session_state.get('_prev_turnover_basis') != turnover_basis:
        asmp['nwc_dso'] = round(calc_dso, 1)
        asmp['nwc_dio'] = round(calc_dio, 1)
        asmp['nwc_dpo'] = round(calc_dpo, 1)
        st.session_state['nwc_dso_input'] = round(calc_dso, 1)
        st.session_state['nwc_dio_input'] = round(calc_dio, 1)
        st.session_state['nwc_dpo_input'] = round(calc_dpo, 1)
        st.session_state['_prev_turnover_basis'] = turnover_basis

    nwc_method = st.radio("NWC 예측 방법", ['Turnover 기반 (DSO/DIO/DPO)', '매출 대비 비율', '연간 고정 변동액'], horizontal=True,
                          index=['turnover','pct_revenue','fixed'].index(asmp.get('nwc_method','turnover')))
    nwc_method_map = {'Turnover 기반 (DSO/DIO/DPO)': 'turnover', '매출 대비 비율': 'pct_revenue', '연간 고정 변동액': 'fixed'}
    asmp['nwc_method'] = nwc_method_map[nwc_method]

    if asmp['nwc_method'] == 'turnover':
        st.markdown("**향후 DSO/DIO/DPO 가정 (과거 기준에서 자동 설정, 수정 가능)**")
        col1, col2, col3 = st.columns(3)
        if 'nwc_dso_input' not in st.session_state:
            st.session_state['nwc_dso_input'] = float(asmp.get('nwc_dso', round(calc_dso, 1)))
            st.session_state['nwc_dio_input'] = float(asmp.get('nwc_dio', round(calc_dio, 1)))
            st.session_state['nwc_dpo_input'] = float(asmp.get('nwc_dpo', round(calc_dpo, 1)))
        with col1:
            asmp['nwc_dso'] = st.number_input("DSO (매출채권 회수일수)", step=1.0, min_value=0.0, help="매출채권 / 매출액 × 365", key='nwc_dso_input')
        with col2:
            asmp['nwc_dio'] = st.number_input("DIO (재고자산 보유일수)", step=1.0, min_value=0.0, help="재고자산 / 매출원가 × 365", key='nwc_dio_input')
        with col3:
            asmp['nwc_dpo'] = st.number_input("DPO (매입채무 지급일수)", step=1.0, min_value=0.0, help="매입채무 / 매출원가 × 365", key='nwc_dpo_input')

        # NWC/매출 환산: (DSO + DIO - DPO) / 365
        nwc_days = asmp['nwc_dso'] + asmp['nwc_dio'] - asmp['nwc_dpo']
        eff_nwc_pct = nwc_days / 365 * 100
        asmp['nwc_pct'] = eff_nwc_pct
        st.info(f"유효 NWC/매출 = ({asmp['nwc_dso']} + {asmp['nwc_dio']} - {asmp['nwc_dpo']}) / 365 × 100 = **{eff_nwc_pct:.1f}%**")

        # NWC 미리보기 (연도를 열로) — AR/Inventory/AP → DSO/DIO/DPO → NWC 전체 계산 체인 표시
        nwc_preview = {}
        rev_v3 = base_rev_val
        cogs_pct_of_rev = safe_div(hist['cogs'].get(base_year, 0), hist['revenue'].get(base_year, 1) or 1, asmp.get('cogs_pct', 60.0) / 100)
        prev_nwc = base_rev_val * eff_nwc_pct / 100
        for i, g in enumerate(rev_growths):
            yr_label = str(proj_years[i])
            rev_v3 = rev_v3 * (1 + g / 100)
            cogs_v3 = rev_v3 * cogs_pct_of_rev
            ar_v3 = asmp['nwc_dso'] / 365 * rev_v3
            inv_v3 = asmp['nwc_dio'] / 365 * cogs_v3
            ap_v3 = asmp['nwc_dpo'] / 365 * cogs_v3
            nwc_level = rev_v3 * eff_nwc_pct / 100
            delta = nwc_level - prev_nwc
            prev_nwc = nwc_level
            nwc_preview[yr_label] = {
                '매출채권(억원)': f"{ar_v3/1e8:,.1f}",
                '재고자산(억원)': f"{inv_v3/1e8:,.1f}",
                '매입채무(억원)': f"{ap_v3/1e8:,.1f}",
                'DSO(일)': f"{asmp['nwc_dso']:.1f}",
                'DIO(일)': f"{asmp['nwc_dio']:.1f}",
                'DPO(일)': f"{asmp['nwc_dpo']:.1f}",
                'NWC잔액(억원)': f"{nwc_level/1e8:,.1f}",
                'ΔNWC(억원)': f"{delta/1e8:,.1f}",
                'NWC/매출(%)': f"{eff_nwc_pct:.1f}%",
            }
        st.markdown("**📊 NWC 추정 미리보기 — AR/재고/AP → DSO·DIO·DPO → NWC (억원)**")
        st.dataframe(pd.DataFrame(nwc_preview), use_container_width=True)

    elif asmp['nwc_method'] == 'pct_revenue':
        asmp['nwc_pct'] = st.number_input("NWC/매출 (%)", value=float(asmp.get('nwc_pct', 10.0)), step=0.5)
        nwc_preview = {}
        rev_v3 = base_rev_val
        prev_nwc = base_rev_val * asmp['nwc_pct'] / 100
        for i, g in enumerate(rev_growths):
            yr_label = str(proj_years[i])
            rev_v3 = rev_v3 * (1 + g / 100)
            nwc_level = rev_v3 * asmp['nwc_pct'] / 100
            delta = nwc_level - prev_nwc
            prev_nwc = nwc_level
            nwc_preview[yr_label] = {'NWC잔액(억원)': f"{nwc_level/1e8:,.1f}", 'ΔNWC(억원)': f"{delta/1e8:,.1f}"}
        st.dataframe(pd.DataFrame(nwc_preview), use_container_width=True)
    else:
        asmp['nwc_fixed'] = st.number_input("연간 NWC 변동액 (원)", value=float(asmp.get('nwc_fixed', 0)), step=1e8)

    st.divider()

    # ── 5. 차입금 / 이자 / 배당 ─────────────────────────────────────────────────
    st.subheader("5. 차입금 / 이자 / 배당 가정")
    st.caption("추정 재무상태표(BS)·현금흐름표(CS) 작성을 위한 가정입니다 (FCFF/DCF 결과 자체에는 영향 없음).")

    st.markdown("**차입금 (연도별 기말 잔액, 억원)**")
    base_debt_억 = hist['total_debt'].get(base_year, 0) / 1e8
    st.write(f"기준연도({base_year}) 차입금: **{base_debt_억:,.1f}억원**")
    prev_debt_list = asmp.get('debt_balance_list', [round(base_debt_억, 1)] * n)
    cols_debt = st.columns(n)
    debt_list = []
    for i, col in enumerate(cols_debt):
        with col:
            dv = col.number_input(f"{proj_years[i]}년", value=float(prev_debt_list[i] if i < len(prev_debt_list) else base_debt_억),
                                   step=1.0, key=f"debt_bal_{i}")
            debt_list.append(dv)
    asmp['debt_balance_list'] = debt_list

    st.markdown("**이자율**")
    implied_rates = [hist['implied_interest_rate'].get(yr, 0) for yr in hist_years if hist['implied_interest_rate'].get(yr, 0) > 0]
    default_rate = round(sum(implied_rates) / len(implied_rates), 2) if implied_rates else 4.0
    st.caption(f"과거 평균 implied 이자율(이자비용/차입금) = {default_rate:.2f}% 를 기본값으로 사용합니다. 필요시 직접 수정하세요.")
    asmp['interest_rate'] = st.number_input("적용 이자율 (%)", value=float(asmp.get('interest_rate', default_rate)),
                                              min_value=0.0, max_value=30.0, step=0.1)

    st.markdown("**배당**")
    div_method = st.radio("배당 방법", ['배당성향 (순이익 대비 %)', '주당 고정 배당금'], horizontal=True,
                          index=0 if asmp.get('dividend_method', 'payout_ratio') == 'payout_ratio' else 1)
    asmp['dividend_method'] = 'payout_ratio' if div_method == '배당성향 (순이익 대비 %)' else 'fixed_dps'
    if asmp['dividend_method'] == 'payout_ratio':
        asmp['dividend_payout_pct'] = st.number_input("배당성향 (%)", value=float(asmp.get('dividend_payout_pct', 0.0)),
                                                         min_value=0.0, max_value=100.0, step=1.0)
    else:
        asmp['dividend_per_share'] = st.number_input("주당 배당금 (원)", value=float(asmp.get('dividend_per_share', 0.0)), step=10.0)

    st.divider()

    # ── 6. 세율 ──────────────────────────────────────────────────────────────
    st.subheader("6. 세율")
    tax_method = st.radio("세율 방법", ['누진세율 자동계산 (2026 한국)', '직접 입력'], horizontal=True,
                          index=0 if asmp.get('tax_method','progressive')=='progressive' else 1)
    asmp['tax_method'] = 'progressive' if tax_method == '누진세율 자동계산 (2026 한국)' else 'direct'

    if asmp['tax_method'] == 'progressive':
        st.markdown("""
| 과세표준 | 세율 | 누진공제 |
|---|---|---|
| 2억원 이하 | 10% | - |
| 2억 ~ 200억 | 20% | 2,000만원 |
| 200억 ~ 3,000억 | 22% | 42,000만원 |
| 3,000억 초과 | 25% | 942,000만원 |

*2026년 이후 적용 세율 (국세청)*
""")
        ebit_억 = hist['ebit'].get(base_year, 0) / 1e8
        taxable_income = st.number_input("과세표준 (억원, 기준연도 영업이익 기준)", value=round(ebit_억, 1), step=1.0)
        auto_rate = calc_korea_corp_tax_rate(taxable_income) * 100
        asmp['tax_rate'] = round(auto_rate, 2)
        st.success(f"유효 법인세율 = **{auto_rate:.2f}%** (과세표준 {taxable_income:.1f}억원 기준)")
    else:
        asmp['tax_rate'] = st.number_input("유효 법인세율 (%)", value=float(asmp.get('tax_rate', 22.0)),
                                            min_value=0.0, max_value=50.0, step=0.5)

    st.divider()

    # ── 6. WACC ──────────────────────────────────────────────────────────────
    st.subheader("7. WACC 입력")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**자기자본비용 (Ke)**")
        ke_method = st.radio("Ke 방법", ['CAPM', '직접 입력'], horizontal=True,
                             index=0 if asmp.get('ke_method','capm')=='capm' else 1)
        asmp['ke_method'] = 'capm' if ke_method == 'CAPM' else 'direct'
        if asmp['ke_method'] == 'capm':
            # CAPM: Ke = Rf + β × ERP
            asmp['risk_free_rate'] = st.number_input("무위험수익률 Rf (%)", value=float(asmp.get('risk_free_rate', 3.5)), step=0.1)
            asmp['beta'] = st.number_input("베타 (β)", value=float(asmp.get('beta', 1.0)), step=0.05)
            asmp['equity_risk_premium'] = st.number_input("주식위험프리미엄 ERP (%)", value=float(asmp.get('equity_risk_premium', 5.0)), step=0.1)
            ke = asmp['risk_free_rate'] + asmp['beta'] * asmp['equity_risk_premium']
            st.info(f"Ke = {asmp['risk_free_rate']}% + {asmp['beta']}×{asmp['equity_risk_premium']}% = **{ke:.2f}%**")
        else:
            asmp['cost_of_equity'] = st.number_input("자기자본비용 (%)", value=float(asmp.get('cost_of_equity', 10.0)), step=0.1)

    with col2:
        st.markdown("**타인자본비용 (Kd) 및 자본구조**")
        asmp['cost_of_debt'] = st.number_input("세전 타인자본비용 (%)", value=float(asmp.get('cost_of_debt', 4.5)), step=0.1)
        use_auto_weight = st.checkbox("재무상태표 기준 자본구조 자동계산", value=True)
        if not use_auto_weight:
            asmp['debt_weight'] = st.number_input("부채 비중 (%)", value=float(asmp.get('debt_weight', 30.0)), min_value=0.0, max_value=100.0, step=1.0)
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
            st.caption(f"세후 Kd = {kd_after:.2f}%  |  WACC = Ke×We + Kd(1-t)×Wd")
        except Exception as e:
            st.warning(f"WACC 미리보기 오류: {str(e)}")

    st.divider()

    # ── 7. 터미널 밸류 ────────────────────────────────────────────────────────
    st.subheader("8. 터미널 밸류 (Terminal Value)")
    st.caption("Gordon Growth Model: TV = FCFF_n × (1+g) / (WACC - g)")
    asmp['terminal_growth_rate'] = st.number_input("영구성장률 TGR (%)",
                                                    value=float(asmp.get('terminal_growth_rate', 1.5)),
                                                    min_value=0.0, max_value=5.0, step=0.1,
                                                    help="한국 장기 GDP 성장률 이하 권장 (1~2%)")

    st.divider()

    # ── 8. 기타 ──────────────────────────────────────────────────────────────
    st.subheader("9. 기타")
    col1, col2 = st.columns(2)
    with col1:
        asmp['shares_outstanding'] = st.number_input("발행주식수 (주)",
                                                       value=float(asmp.get('shares_outstanding', 0)),
                                                       step=1000.0,
                                                       help="총 발행주식수 입력 시 주당가치 자동 계산")
    with col2:
        net_debt_auto = hist['net_debt'].get(base_year, 0)
        st.metric("자동 산출 순차입금 (IBD-현금성자산)", f"{fmt_억(net_debt_auto)}억원",
                  help=f"기준연도({base_year}) IBD(단기차입금/유동성장기부채/장기차입금/리스부채/CB·EB·BW/사채 등) "
                       "- 현금성자산(현금및현금성자산+단기금융상품). EV - 순차입금 = 자기자본가치")

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
        'ibd': hist['ibd'].get(base_year, 0),
        'cash_equivalents': hist['cash_equivalents'].get(base_year, 0),
        'net_debt': hist['net_debt'].get(base_year, 0),
    }

    try:
        model_asmp = asmp.copy()

        # UI에서 % 단위로 입력된 값들을 DCFModel이 요구하는 소수로 변환
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
        # NWC turnover 방식은 pct_revenue로 변환 (nwc_pct는 이미 위에서 소수로 변환됨)
        if model_asmp.get('nwc_method') == 'turnover':
            model_asmp['nwc_method'] = 'pct_revenue'

        model_asmp['tgr'] = model_asmp.pop('terminal_growth_rate', 0.015)
        # 순차입금 = IBD(단기차입금/유동성장기부채/장기차입금/리스부채/CB·EB·BW/사채 등)
        # - 현금성자산(현금및현금성자산+단기금융상품), 기준연도(base_year) 기준
        model_asmp['net_debt'] = hist_data['net_debt']

        with st.spinner("DCF 계산 중..."):
            model = DCFModel(hist_data, model_asmp)
            results = model.calculate_ev()

        fcff_df = results['fcff_df']
        wacc = results['wacc']
        pv_fcff_df = results['pv_fcff_by_year']
        pv_tv = results['pv_tv']
        ev = results['ev']
        total_pv_fcff = float(np.sum(results['pv_fcffs']))

        # Apply per-year new investment capex
        n_proj = len(fcff_df)
        new_invest_list_억 = asmp.get('new_invest_capex_list', [0.0] * n_proj)
        has_new_invest = any((new_invest_list_억[i] if i < len(new_invest_list_억) else 0) > 0 for i in range(n_proj))
        if has_new_invest:
            fcff_df = fcff_df.copy()
            for i in range(n_proj):
                new_inv = (new_invest_list_억[i] if i < len(new_invest_list_억) else 0) * 1e8
                fcff_df.loc[i, 'new_invest_capex'] = new_inv
                fcff_df.loc[i, 'capex'] = fcff_df.loc[i, 'capex'] + new_inv
                fcff_df.loc[i, 'fcff'] = (fcff_df.loc[i, 'nopat'] + fcff_df.loc[i, 'da']
                                           - fcff_df.loc[i, 'capex'] - fcff_df.loc[i, 'delta_nwc'])
            # Recalculate PV of FCFFs and EV manually
            pv_fcff_df = pv_fcff_df.copy()
            for i in range(n_proj):
                pv_fcff_df.loc[i, 'pv_fcff'] = fcff_df.loc[i, 'fcff'] * pv_fcff_df.loc[i, 'discount_factor']
            total_pv_fcff = float(pv_fcff_df['pv_fcff'].sum())
            ev = total_pv_fcff + pv_tv

        net_debt = hist_data['net_debt']
        eq_val = model.equity_value(ev)
        shares = hist_data['shares_outstanding']
        price_per_share = model.price_per_share(eq_val, shares) if shares > 0 else None

        # KPI 요약
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

        col1, col2, col3 = st.columns(3)
        with col1:
            st.info(f"WACC: **{wacc*100:.2f}%**")
        with col2:
            st.info(f"TGR: **{asmp.get('terminal_growth_rate', 1.5):.1f}%**")
        with col3:
            tv_share = safe_div(pv_tv, ev, 0) * 100
            st.info(f"TV 비중: **{tv_share:.1f}%**")

        st.divider()

        # FCFF 추정 테이블 (연도를 열로 배치)
        st.subheader("FCFF 추정 (단위: 억원)")
        st.caption(
            "FCFF = EBIT×(1-t) + D&A - 총CapEx - ΔNWC  |  "
            "EBIT(1-t)=NOPAT(세후영업이익)  |  ΔNWC=NWC잔액 전년 대비 증가분(현금유출)"
        )
        fcff_cols = {
            '매출액': [],
            '매출총이익': [],
            '영업이익(EBIT)': [],
            'NOPAT=EBIT×(1-t)': [],
            'D&A(+)': [],
            '총CapEx(-)': [],
            'ΔNWC(-)': [],
            'FCFF': [],
            '할인계수': [],
            'PV(FCFF)': [],
        }
        for i, row_data in fcff_df.iterrows():
            pv_row = pv_fcff_df.iloc[i]
            yr = str(int(row_data['year']))
            fcff_cols['매출액'].append((yr, fmt_억(row_data['revenue'])))
            fcff_cols['매출총이익'].append((yr, fmt_억(row_data['gross_profit'])))
            fcff_cols['영업이익(EBIT)'].append((yr, fmt_억(row_data['ebit'])))
            fcff_cols['NOPAT=EBIT×(1-t)'].append((yr, fmt_억(row_data['nopat'])))
            fcff_cols['D&A(+)'].append((yr, fmt_억(row_data['da'])))
            fcff_cols['총CapEx(-)'].append((yr, fmt_억(row_data['capex'])))
            fcff_cols['ΔNWC(-)'].append((yr, fmt_억(row_data['delta_nwc'])))
            fcff_cols['FCFF'].append((yr, fmt_억(row_data['fcff'])))
            fcff_cols['할인계수'].append((yr, f"{pv_row['discount_factor']:.4f}"))
            fcff_cols['PV(FCFF)'].append((yr, fmt_억(pv_row['pv_fcff'])))

        fcff_display = {}
        for metric, yr_vals in fcff_cols.items():
            fcff_display[metric] = {yr: v for yr, v in yr_vals}
        st.dataframe(pd.DataFrame(fcff_display).T, use_container_width=True)

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

        st.divider()

        # 폭포 차트
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

        # 민감도 분석 (WACC vs TGR) — 테이블: WACC를 열로
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

        # TGR을 행으로, WACC를 열로 전치
        sens_df_T = sens_df.T
        sens_df_T.index = [f"TGR {t:.1f}%" for t in tgr_range]
        sens_df_T.columns = [f"WACC {w:.1f}%" for w in wacc_range]

        label = "주당가치 (원)" if shares > 0 else "EV (억원)"
        st.write(f"**{label}** (행: TGR, 열: WACC)")
        sens_display = sens_df_T.map(lambda x: f"{x:,.0f}" if x is not None and not np.isnan(x) else "N/A")
        st.dataframe(sens_display, use_container_width=True)

        fig_heat = px.imshow(
            sens_df.map(lambda x: x if not np.isnan(x) else 0).values,
            x=[f"{t:.1f}%" for t in tgr_range],
            y=[f"{w:.1f}%" for w in wacc_range],
            color_continuous_scale='RdYlGn',
            title=f'{label} 민감도 히트맵 (행: WACC, 열: TGR)',
            labels={'x': 'TGR', 'y': 'WACC'},
            text_auto='.0f'
        )
        fig_heat.update_layout(height=400)
        st.plotly_chart(fig_heat, use_container_width=True)

        st.divider()

        # Excel 내보내기
        st.subheader("📥 결과 내보내기")

        def create_excel_export():
            return build_excel_workbook(
                company_name=company_name,
                hist=hist, asmp=asmp,
                fcff_df=fcff_df, pv_fcff_df=pv_fcff_df,
                wacc=wacc, tgr=tgr_c, pv_tv=pv_tv, ev=ev,
                net_debt=net_debt, eq_val=eq_val, shares=shares,
                price_per_share=price_per_share, base_year=base_year,
                financial_data=st.session_state.financial_data,
            )

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
