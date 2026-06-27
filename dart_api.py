import os
import io
import zipfile
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://opendart.fss.or.kr/api"
CACHE_DIR = Path(__file__).parent
CORP_CODE_CACHE = CACHE_DIR / "corp_codes.parquet"


def get_dart_api_key(api_key=None):
    if api_key:
        return api_key
    return os.getenv("DART_API_KEY", "")


def get_corp_code_list(api_key=None, force_refresh=False):
    """Download and cache DART corp code list"""
    if CORP_CODE_CACHE.exists() and not force_refresh:
        try:
            return pd.read_parquet(CORP_CODE_CACHE)
        except Exception:
            pass

    key = get_dart_api_key(api_key)
    if not key:
        raise ValueError("DART API Key가 필요합니다.")

    url = f"{BASE_URL}/corpCode.xml"
    resp = requests.get(url, params={"crtfc_key": key}, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_filename = [n for n in zf.namelist() if n.endswith('.xml')][0]
        xml_content = zf.read(xml_filename)

    root = ET.fromstring(xml_content)
    records = []
    for item in root.findall('.//list'):
        corp_code = item.findtext('corp_code', '').strip()
        corp_name = item.findtext('corp_name', '').strip()
        stock_code = item.findtext('stock_code', '').strip()
        modify_date = item.findtext('modify_date', '').strip()
        records.append({
            'corp_code': corp_code,
            'corp_name': corp_name,
            'stock_code': stock_code,
            'modify_date': modify_date,
        })

    df = pd.DataFrame(records)
    df.to_parquet(CORP_CODE_CACHE)
    return df


def search_company(keyword, api_key=None):
    """Search for companies by name or stock code"""
    df = get_corp_code_list(api_key)
    keyword = str(keyword).strip()

    mask = (
        df['corp_name'].str.contains(keyword, na=False, case=False) |
        df['stock_code'].str.contains(keyword, na=False, case=False)
    )
    results = df[mask].copy()
    # Prioritize listed companies (have stock_code)
    results = results.sort_values('stock_code', ascending=False)

    return results[['corp_name', 'corp_code', 'stock_code']].head(50).to_dict('records')


def get_company_info(corp_code, api_key=None):
    """Get company metadata from DART"""
    key = get_dart_api_key(api_key)
    url = f"{BASE_URL}/company.json"
    params = {"crtfc_key": key, "corp_code": corp_code}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get('status') != '000':
        raise ValueError(f"DART API Error: {data.get('message', 'Unknown error')}")
    return data


def _safe_amount(val):
    """Safely parse Korean number string to float"""
    if val is None or val == '' or val == '-':
        return 0.0
    try:
        cleaned = str(val).replace(',', '').replace(' ', '').strip()
        if cleaned in ('', '-', 'N/A'):
            return 0.0
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


# Account code mappings
ACCOUNT_MAP = {
    # Income Statement
    'revenue': [
        'ifrs-full_Revenue',
        'dart_Revenue',
        'ifrs_Revenue',
        'Revenue',
    ],
    'cogs': [
        'ifrs-full_CostOfSales',
        'dart_CostOfSales',
        'CostOfSales',
    ],
    'gross_profit': [
        'ifrs-full_GrossProfit',
        'dart_GrossProfit',
        'GrossProfit',
    ],
    'sga': [
        'ifrs-full_SellingGeneralAndAdministrativeExpense',
        'dart_SellingGeneralAndAdministrativeExpense',
        'ifrs-full_AdministrativeExpense',
        'SGA',
    ],
    'ebit': [
        'ifrs-full_ProfitLossFromOperatingActivities',
        'dart_OperatingIncomeLoss',
        'ifrs-full_OperatingIncome',
        'dart_ProfitLossFromOperatingActivities',
        'OperatingIncome',
    ],
    'pretax_income': [
        'ifrs-full_ProfitLossBeforeTax',
        'dart_ProfitLossBeforeTax',
        'ProfitLossBeforeTax',
    ],
    'tax_expense': [
        'ifrs-full_IncomeTaxExpense',
        'dart_IncomeTaxExpense',
        'IncomeTaxExpense',
    ],
    'net_income': [
        'ifrs-full_ProfitLoss',
        'dart_ProfitLoss',
        'ifrs-full_NetIncome',
        'ProfitLoss',
        'NetIncome',
    ],
    'da': [
        'ifrs-full_DepreciationAndAmortisationExpense',
        'dart_DepreciationAndAmortisation',
        'ifrs-full_DepreciationAmortisationAndImpairmentLoss',
        'DepreciationAndAmortisation',
    ],
    'interest_expense': [
        'ifrs-full_InterestExpense',
        'dart_InterestExpense',
        'InterestExpense',
        'ifrs-full_FinanceCosts',
        'dart_FinanceCosts',
    ],
    'dividends_paid': [
        'ifrs-full_DividendsPaid',
        'dart_DividendsPaid',
        'DividendsPaidClassifiedAsFinancingActivities',
    ],
    # Balance Sheet
    'total_assets': [
        'ifrs-full_Assets',
        'dart_Assets',
        'Assets',
    ],
    'current_assets': [
        'ifrs-full_CurrentAssets',
        'dart_CurrentAssets',
        'CurrentAssets',
    ],
    'current_liabilities': [
        'ifrs-full_CurrentLiabilities',
        'dart_CurrentLiabilities',
        'CurrentLiabilities',
    ],
    'cash': [
        'ifrs-full_CashAndCashEquivalents',
        'dart_CashAndCashEquivalents',
        'CashAndCashEquivalents',
    ],
    # DSO/DIO/DPO 계산에 필요한 운전자본 항목
    'accounts_receivable': [
        'ifrs-full_TradeAndOtherCurrentReceivables',
        'ifrs-full_TradeAndOtherReceivables',
        'dart_TradeReceivables',
        'TradeAndOtherCurrentReceivables',
        'TradeReceivables',
    ],
    'inventory': [
        'ifrs-full_Inventories',
        'dart_Inventories',
        'Inventories',
    ],
    'accounts_payable': [
        'ifrs-full_TradeAndOtherCurrentPayables',
        'ifrs-full_TradeAndOtherPayables',
        'dart_TradePayables',
        'TradeAndOtherCurrentPayables',
        'TradePayables',
    ],
    'total_equity': [
        'ifrs-full_Equity',
        'dart_Equity',
        'Equity',
        'ifrs-full_EquityAttributableToOwnersOfParent',
    ],
    'short_term_borrowings': [
        'ifrs-full_ShorttermBorrowings',
        'dart_ShorttermBorrowings',
        'ShorttermBorrowings',
        'ifrs-full_CurrentBorrowings',
    ],
    'long_term_borrowings': [
        'ifrs-full_NoncurrentPortionOfLongtermBorrowings',
        'dart_NoncurrentPortionOfLongtermBorrowings',
        'ifrs-full_LongtermBorrowings',
        'dart_LongtermBorrowings',
        'LongtermBorrowings',
    ],
    'total_borrowings': [
        'ifrs-full_Borrowings',
        'dart_Borrowings',
        'Borrowings',
    ],
    # Cash Flow
    'operating_cf': [
        'ifrs-full_CashFlowsFromUsedInOperatingActivities',
        'dart_CashFlowsFromOperatingActivities',
        'CashFlowsFromOperatingActivities',
        'ifrs-full_CashFlowsFromOperations',
    ],
    'investing_cf': [
        'ifrs-full_CashFlowsFromUsedInInvestingActivities',
        'dart_CashFlowsFromInvestingActivities',
        'CashFlowsFromInvestingActivities',
    ],
    'financing_cf': [
        'ifrs-full_CashFlowsFromUsedInFinancingActivities',
        'dart_CashFlowsFromFinancingActivities',
        'CashFlowsFromFinancingActivities',
    ],
    'capex': [
        'ifrs-full_PurchaseOfPropertyPlantAndEquipment',
        'dart_PurchaseOfPropertyPlantAndEquipment',
        'PurchaseOfPropertyPlantAndEquipment',
        'ifrs-full_AcquisitionOfPropertyPlantAndEquipment',
    ],
    # Individual D&A components
    'da_ppe': [
        'ifrs-full_DepreciationOfPropertyPlantAndEquipment',
        'dart_DepreciationOfPropertyPlantAndEquipment',
        'DepreciationOfPropertyPlantAndEquipment',
    ],
    'da_intangible': [
        'ifrs-full_AmortisationOfIntangibleAssets',
        'dart_AmortisationOfIntangibleAssets',
        'AmortisationOfIntangibleAssets',
    ],
    'da_rou': [
        'ifrs-full_DepreciationRightOfUseAssets',
        'dart_DepreciationRightOfUseAssets',
        'ifrs-full_DepreciationOfRightOfUseAssets',
        'DepreciationRightOfUseAssets',
    ],
    # SGA breakdown
    'selling_expense': [
        'ifrs-full_SellingExpense',
        'dart_SellingExpense',
        'SellingExpense',
    ],
    'admin_expense': [
        'ifrs-full_AdministrativeExpense',
        'dart_AdministrativeExpense',
        'AdministrativeExpense',
    ],
    # Shares outstanding (in balance sheet / equity items)
    'shares_outstanding': [
        'ifrs-full_NumberOfSharesOutstanding',
        'dart_NumberOfSharesOutstanding',
        'NumberOfSharesOutstanding',
        'dart_CommonStockSharesIssued',
        'ifrs-full_NumberOfSharesIssuedAndFullyPaid',
    ],
}


# Korean account_nm substring keywords used as a fallback when the XBRL
# account_id tag is missing or non-standard (common for many DART filers).
KOREAN_NAME_KEYWORDS = {
    'sga': ['판매비와관리비', '판매비및관리비', '판매비와 관리비', '판관비'],
    'selling_expense': ['판매비'],
    'admin_expense': ['관리비'],
    'da': ['감가상각비와무형자산상각비', '감가상각비및무형자산상각비'],
    'da_ppe': ['유형자산상각비', '감가상각비'],
    'da_intangible': ['무형자산상각비', '개발비상각'],
    'da_rou': ['사용권자산상각비', '리스자산상각비', '사용권자산감가상각비'],
    'accounts_receivable': ['매출채권'],
    'inventory': ['재고자산'],
    'accounts_payable': ['매입채무'],
    'shares_outstanding': ['발행주식수', '유통주식수', '보통주식수'],
    'interest_expense': ['이자비용', '금융원가', '금융비용'],
    'dividends_paid': ['배당금의지급', '배당금지급'],
    # 순차입금(IBD-Cash) 계산용
    'ibd': ['단기차입금', '유동성장기부채', '유동성장기차입금', '장기차입금',
            '리스부채', '금융리스부채', '전환사채', '신주인수권부사채', '교환사채', '사채'],
    'cash_equivalents': ['현금및현금성자산', '단기금융상품', '단기예금'],
    'capex': ['유형자산의취득', '유형자산취득', '유형자산의 취득', '유형자산의증가',
              '무형자산의취득', '무형자산취득', '무형자산의 취득'],
}

# CapEx 키워드 매칭 시 "처분"(자산 매각, 현금 유입)이나 투자자산/관계기업
# 관련 취득(설비투자가 아닌 지분투자)이 잘못 섞이는 것을 막기 위한 제외 키워드
CAPEX_EXCLUDE_KEYWORDS = ['처분', '관계기업', '종속기업', '투자자산', '리스']

# 감가상각비/상각비와 무관하게 "상각"이라는 단어가 들어가는 회계/금융 계정
# (사채할인발행차금상각, 상각후원가 등) — D&A 키워드 매칭 시 오염을 막기 위해 제외
DA_EXCLUDE_KEYWORDS = ['사채', '차입금', '할인발행차금', '상각후원가', '리스부채이자']

# 순차입금(IBD) 합산 시 사채/차입금의 contra계정(할인발행차금, 전환권조정 등)이나
# 이자/평가손익 계정이 오매칭되는 것을 막기 위한 제외 키워드
IBD_EXCLUDE_KEYWORDS = ['할인발행차금', '전환권조정', '신주인수권조정', '상각후원가측정',
                        '이자', '평가손익', '리스채권']
# 장기금융상품(비유동)이 단기 현금성자산에 섞이지 않도록 제외
CASH_EQUIV_EXCLUDE_KEYWORDS = ['장기']



def _normalize_account_nm(name):
    return name.replace(' ', '').replace('\xa0', '').replace('　', '')


def _lookup_account(account_map_by_id, category_keys, keywords=None, exclude_keywords=None):
    """Look up account value by trying multiple possible account IDs first,
    then falling back to a Korean account_nm substring match if provided."""
    for key in category_keys:
        if key in account_map_by_id:
            val = _safe_amount(account_map_by_id[key])
            if val != 0:
                return val
    if keywords:
        for name, raw_val in account_map_by_id.items():
            if not isinstance(name, str):
                continue
            norm = _normalize_account_nm(name)
            if exclude_keywords and any(kw in norm for kw in exclude_keywords):
                continue
            if any(kw in norm for kw in keywords):
                val = _safe_amount(raw_val)
                if val != 0:
                    return val
    return 0.0


def _sum_by_keyword(items, sj_div_filter, keywords, exclude_keywords=None):
    """Sum thstrm_amount across all distinct line items whose account_nm
    contains any of the given keywords. Used as a last-resort fallback for
    D&A, which is frequently broken out across several note line items
    (e.g. 유형자산 감가상각비, 무형자산상각비, 사용권자산 감가상각비) with no
    standard XBRL account_id."""
    seen = set()
    total = 0.0
    for item in items:
        if sj_div_filter and item.get('sj_div') != sj_div_filter:
            continue
        name = item.get('account_nm', '')
        if not name:
            continue
        norm = _normalize_account_nm(name)
        if exclude_keywords and any(kw in norm for kw in exclude_keywords):
            continue
        if not any(kw in norm for kw in keywords):
            continue
        dedup_key = (item.get('account_id', ''), name, item.get('thstrm_amount', ''))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        total += _safe_amount(item.get('thstrm_amount', '0'))
    return total


def _parse_statement(items, sj_div_filter=None):
    """Parse a list of financial statement items into a dict keyed by account_id"""
    result = {}
    for item in items:
        if sj_div_filter and item.get('sj_div') != sj_div_filter:
            continue
        account_id = item.get('account_id', '')
        if not account_id:
            account_id = item.get('account_nm', '')
        amount = _safe_amount(item.get('thstrm_amount', '0'))
        result[account_id] = amount
        # Also store by account_nm for fallback
        account_nm = item.get('account_nm', '')
        if account_nm and account_nm not in result:
            result[account_nm] = amount
    return result


def _extract_financials_from_items(items):
    """Extract standardized financial metrics from raw DART items"""
    # Separate by statement type
    is_items = [x for x in items if x.get('sj_div') in ('IS', 'CIS')]
    bs_items = [x for x in items if x.get('sj_div') == 'BS']
    cf_items = [x for x in items if x.get('sj_div') in ('CF', 'CFS')]

    is_map = _parse_statement(is_items)
    bs_map = _parse_statement(bs_items)
    cf_map = _parse_statement(cf_items)

    # Extract IS metrics
    revenue = _lookup_account(is_map, ACCOUNT_MAP['revenue'])
    cogs = _lookup_account(is_map, ACCOUNT_MAP['cogs'])
    gross_profit = _lookup_account(is_map, ACCOUNT_MAP['gross_profit'])
    if gross_profit == 0 and revenue > 0 and cogs > 0:
        gross_profit = revenue - cogs
    sga = _lookup_account(is_map, ACCOUNT_MAP['sga'], KOREAN_NAME_KEYWORDS['sga'])
    ebit = _lookup_account(is_map, ACCOUNT_MAP['ebit'])
    pretax_income = _lookup_account(is_map, ACCOUNT_MAP['pretax_income'])
    tax_expense = _lookup_account(is_map, ACCOUNT_MAP['tax_expense'])
    net_income = _lookup_account(is_map, ACCOUNT_MAP['net_income'])

    # SGA breakdown from IS
    selling_expense = _lookup_account(is_map, ACCOUNT_MAP['selling_expense'], KOREAN_NAME_KEYWORDS['selling_expense'])
    admin_expense = _lookup_account(is_map, ACCOUNT_MAP['admin_expense'], KOREAN_NAME_KEYWORDS['admin_expense'])
    # If SGA is 0 but selling+admin exist, compute SGA
    if sga == 0 and (selling_expense + admin_expense) > 0:
        sga = selling_expense + admin_expense

    # D&A는 현금흐름표의 "영업활동현금흐름 - 현금의 유출이 없는 비용 등의 가산"
    # 항목에서 가져오는 것이 가장 정확함. 유형자산감가상각비, 개발비상각,
    # 기타무형자산상각비, 사용권자산상각비를 모두 더해서 구한다.
    da_ppe = _lookup_account(cf_map, ACCOUNT_MAP['da_ppe'], KOREAN_NAME_KEYWORDS['da_ppe'], DA_EXCLUDE_KEYWORDS)
    da_intangible = _lookup_account(cf_map, ACCOUNT_MAP['da_intangible'], KOREAN_NAME_KEYWORDS['da_intangible'], DA_EXCLUDE_KEYWORDS)
    da_rou = _lookup_account(cf_map, ACCOUNT_MAP['da_rou'], KOREAN_NAME_KEYWORDS['da_rou'], DA_EXCLUDE_KEYWORDS)
    da_components = da_ppe + da_intangible + da_rou

    # 결합형 계정("감가상각비와무형자산상각비" 등 하나로 합쳐진 계정)도 시도
    da_cf = _lookup_account(cf_map, ACCOUNT_MAP['da'], KOREAN_NAME_KEYWORDS['da'], DA_EXCLUDE_KEYWORDS)

    # 손익계산서 측 D&A (제조원가/판관비 내 감가상각비 주석 등에서 잡힐 수 있음)
    da_is = _lookup_account(is_map, ACCOUNT_MAP['da'], KOREAN_NAME_KEYWORDS['da'], DA_EXCLUDE_KEYWORDS)

    # 우선순위: CF 개별 구성요소 합 > CF 결합계정 > IS > CF/IS 전체에서
    # "상각비"/"상각"이 포함된 모든 항목을 합산하는 최종 폴백 (사채 관련
    # 상각 계정은 DA_EXCLUDE_KEYWORDS로 제외)
    if da_components > 0:
        da = da_components
    elif da_cf > 0:
        da = da_cf
    elif da_is > 0:
        da = da_is
    else:
        da = _sum_by_keyword(cf_items, None, ['상각비', '개발비상각'], DA_EXCLUDE_KEYWORDS)
        if da == 0:
            da = _sum_by_keyword(is_items, None, ['상각비', '개발비상각'], DA_EXCLUDE_KEYWORDS)

    # D&A가 0일 경우 진단용으로 CF/IS 원본 계정명을 남겨, 어떤 계정명으로
    # 들어오는지 화면에서 직접 확인할 수 있도록 한다 (account_id가 비표준이라
    # 키워드 매칭에 실패하는 경우를 찾기 위함).
    da_debug_items = None
    if da == 0:
        da_debug_items = [
            {'sj_div': it.get('sj_div'), 'account_nm': it.get('account_nm'),
             'thstrm_amount': it.get('thstrm_amount')}
            for it in (cf_items + is_items)
            if it.get('account_nm')
        ]

    interest_expense = _lookup_account(is_map, ACCOUNT_MAP['interest_expense'], KOREAN_NAME_KEYWORDS['interest_expense'])
    dividends_paid = abs(_lookup_account(cf_map, ACCOUNT_MAP['dividends_paid'], KOREAN_NAME_KEYWORDS['dividends_paid']))

    # Extract BS metrics
    total_assets = _lookup_account(bs_map, ACCOUNT_MAP['total_assets'])
    current_assets = _lookup_account(bs_map, ACCOUNT_MAP['current_assets'])
    current_liabilities = _lookup_account(bs_map, ACCOUNT_MAP['current_liabilities'])
    cash = _lookup_account(bs_map, ACCOUNT_MAP['cash'])
    total_equity = _lookup_account(bs_map, ACCOUNT_MAP['total_equity'])
    accounts_receivable = _lookup_account(bs_map, ACCOUNT_MAP['accounts_receivable'], KOREAN_NAME_KEYWORDS['accounts_receivable'])
    inventory = _lookup_account(bs_map, ACCOUNT_MAP['inventory'], KOREAN_NAME_KEYWORDS['inventory'])
    accounts_payable = _lookup_account(bs_map, ACCOUNT_MAP['accounts_payable'], KOREAN_NAME_KEYWORDS['accounts_payable'])
    shares_outstanding = _lookup_account(bs_map, ACCOUNT_MAP['shares_outstanding'], KOREAN_NAME_KEYWORDS['shares_outstanding'])

    # Total debt: try borrowings first, then sum short+long term
    total_debt = _lookup_account(bs_map, ACCOUNT_MAP['total_borrowings'])
    if total_debt == 0:
        short_term = _lookup_account(bs_map, ACCOUNT_MAP['short_term_borrowings'])
        long_term = _lookup_account(bs_map, ACCOUNT_MAP['long_term_borrowings'])
        total_debt = short_term + long_term

    # 순차입금(Net Debt) = IBD(이자부부채) - 현금성자산
    # IBD: 단기차입금, 유동성장기부채, 장기차입금, 금융리스부채, 전환사채(CB),
    #      교환사채(EB), 신주인수권부사채(BW), 일반 사채 등을 모두 합산 (서로
    #      다른 계정이 동시에 존재할 수 있으므로 _sum_by_keyword로 전부 더함)
    ibd = _sum_by_keyword(bs_items, 'BS', KOREAN_NAME_KEYWORDS['ibd'], IBD_EXCLUDE_KEYWORDS)
    # 현금성자산: 현금및현금성자산 + 단기금융상품(단기예금 등)
    cash_equivalents = _sum_by_keyword(bs_items, 'BS', KOREAN_NAME_KEYWORDS['cash_equivalents'], CASH_EQUIV_EXCLUDE_KEYWORDS)
    if cash_equivalents == 0:
        cash_equivalents = cash

    # Extract CF metrics
    operating_cf = _lookup_account(cf_map, ACCOUNT_MAP['operating_cf'])
    investing_cf = _lookup_account(cf_map, ACCOUNT_MAP['investing_cf'])
    financing_cf = _lookup_account(cf_map, ACCOUNT_MAP['financing_cf'])
    # CapEx: XBRL account_id 매칭 우선, 실패 시 한국어 계정명 키워드로 폴백.
    # CF의 "유형자산의취득"/"무형자산의취득" 등은 회사마다 표준 XBRL 태그
    # 대신 자체 dart_ 태그를 쓰는 경우가 많아 account_id만으로는 자주 0이 됨
    # (D&A와 동일한 문제) — KOREAN_NAME_KEYWORDS 폴백과 _sum_by_keyword
    # 최종 폴백(투자활동현금흐름 내 여러 유형/무형자산 취득 계정을 모두 합산)을
    # 추가해 실제로 값을 잡아내도록 한다.
    capex_raw = _lookup_account(cf_map, ACCOUNT_MAP['capex'],
                                 KOREAN_NAME_KEYWORDS['capex'], CAPEX_EXCLUDE_KEYWORDS)
    if capex_raw == 0:
        capex_raw = _sum_by_keyword(cf_items, None, KOREAN_NAME_KEYWORDS['capex'],
                                     CAPEX_EXCLUDE_KEYWORDS)
    capex = abs(capex_raw)  # CapEx is typically negative in CF

    def _raw_rows(raw_items):
        """엑셀 원본 재무제표 시트용 — DART 보고서에 표시되는 줄 순서(ord)
        그대로, 계정명/금액만 추출한다 (가공/요약 없이 raw 그대로).

        주의: DART의 'ord' 필드는 문자열이며 동일 sj_div(BS/IS/CIS/CF) +
        동일 fs_div 내에서만 의미가 있는 상대 순서값이다. 파싱에 실패하는
        경우(빈 값/비숫자) 전부 0으로 깔아버리면 원본 리스트 순서가
        뒤섞여 버리므로, 실패 시에는 원본 리스트에서의 위치(인덱스)를
        안정적인 폴백으로 사용해 최소한 DART가 보낸 원래 순서를 보존한다.
        """
        seen = set()
        rows = []
        for idx, it in enumerate(raw_items):
            name = it.get('account_nm', '')
            if not name:
                continue
            norm_name = _normalize_account_nm(name).strip() or name.strip()
            try:
                ordv = int(str(it.get('ord', '')).strip())
            except (TypeError, ValueError):
                # ord를 파싱할 수 없으면 원본 리스트상의 위치를 폴백으로 사용
                # (0으로 통일하면 여러 항목이 맨 앞으로 몰려 순서가 깨짐).
                ordv = idx
            account_id = it.get('account_id', '') or ''
            key = (account_id, norm_name, ordv)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'account_nm': name,
                'account_nm_norm': norm_name,
                'account_id': account_id,
                'amount': _safe_amount(it.get('thstrm_amount', '0')),
                'ord': ordv,
            })
        rows.sort(key=lambda r: r['ord'])
        return rows

    return {
        'income_statement': {
            'revenue': revenue,
            'cogs': cogs,
            'gross_profit': gross_profit,
            'sga': sga,
            'ebit': ebit,
            'pretax_income': pretax_income,
            'tax_expense': tax_expense,
            'net_income': net_income,
            'da': da,
            'selling_expense': selling_expense,
            'admin_expense': admin_expense,
            'da_ppe': da_ppe,
            'da_intangible': da_intangible,
            'da_rou': da_rou,
            'interest_expense': interest_expense,
        },
        'balance_sheet': {
            'total_assets': total_assets,
            'current_assets': current_assets,
            'current_liabilities': current_liabilities,
            'cash': cash,
            'total_equity': total_equity,
            'total_debt': total_debt,
            'ibd': ibd,
            'cash_equivalents': cash_equivalents,
            'net_debt': ibd - cash_equivalents,
            'accounts_receivable': accounts_receivable,
            'inventory': inventory,
            'accounts_payable': accounts_payable,
            'shares_outstanding': shares_outstanding,
        },
        'cash_flow': {
            'operating_cf': operating_cf,
            'investing_cf': investing_cf,
            'financing_cf': financing_cf,
            'capex': capex,
            'da': da_cf if da_cf > 0 else da,
            'dividends_paid': dividends_paid,
            '_da_debug_items': da_debug_items,
        },
        'raw': {
            'bs': _raw_rows(bs_items),
            'is': _raw_rows(is_items),
            'cf': _raw_rows(cf_items),
        },
    }


def get_financial_statements(corp_code, year, api_key, report_type='11011', fs_div='OFS'):
    """
    Fetch financial statements from DART API.
    report_type: 11011=사업보고서, 11012=반기보고서, 11013=1분기, 11014=3분기
    fs_div: CFS=연결, OFS=개별

    fnlttSinglAcntAll은 정기보고서(사업/반기/분기보고서)를 제출한 법인에 대해서만
    데이터를 제공합니다. 감사보고서만 제출하는 비상장 외부감사대상 법인의 경우,
    같은 연도에 다른 보고서 종류로 데이터가 잡힐 수도 있으므로 요청한
    report_type이 비어있으면 다른 report_type도 순차적으로 시도합니다.

    fs_div 우선순위: 여러 연도를 모아 BS/IS/CS 원본 시트를 만들 때, 연도별로
    CFS(연결)/OFS(개별)가 섞이면 같은 계정명이라도 금액 기준(자회사 포함
    여부)이 달라져 시트가 "이상하게" 보인다. 따라서 호출자가 명시적으로
    요청한 fs_div를 최우선으로 시도하되, 그것이 없을 때만 다른 구분으로
    넘어가도록 순서를 고정한다 — 모든 연도 호출에서 동일한 우선순위
    (요청값 -> 대안값)를 적용해야 연도 간 일관성이 유지된다. 기본값은
    'OFS'(개별/별도) — 참고 양식("해농_BS/IS/CS")이 단일 회사 별도재무제표
    기준이었기 때문.
    """
    key = get_dart_api_key(api_key)
    url = f"{BASE_URL}/fnlttSinglAcntAll.json"

    def _fetch(rprt_code, div):
        params = {
            "crtfc_key": key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": rprt_code,
            "fs_div": div,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    tried = []
    report_candidates = [report_type] + [r for r in ('11011', '11014', '11012', '11013') if r != report_type]
    # fs_div 우선순위는 report_type 후보와 무관하게 고정한다: 요청된 fs_div를
    # 먼저 모든 report_type에 대해 시도하고, 그래도 못 찾으면 대안 fs_div로
    # 전체를 다시 시도한다. (이전에는 report_type마다 매번 fs_div 순서를
    # 새로 돌았기 때문에, 연도별로 우연히 다른 fs_div가 채택되어 raw 시트에
    # CFS/OFS가 섞이는 문제가 있었다 — 우선순위 자체는 고정되어 있었지만
    # report_type 루프 안쪽에 있어 실질적으로 연도마다 결과가 달라졌다.)
    fs_div_candidates = [fs_div, 'OFS' if fs_div == 'CFS' else 'CFS']
    data = None
    for div in fs_div_candidates:
        for rprt_code in report_candidates:
            data = _fetch(rprt_code, div)
            tried.append((rprt_code, div, data.get('status')))
            if data.get('status') == '000' and data.get('list'):
                items = data.get('list', [])
                result = _extract_financials_from_items(items)
                result['_report_type_used'] = rprt_code
                result['_fs_div_used'] = div

                # D&A/CapEx가 0이면(주석에만 있는 경우, 예: 삼성전자 별도
                # 재무제표) XBRL 주석(유형자산 노트) 파싱을 추가로 시도한다.
                # 이 경로가 실패해도(None 반환) 기존 디버그 expander 폴백은
                # 그대로 살아있으므로 안전하다.
                cf = result.get('cash_flow', {})
                is_ = result.get('income_statement', {})
                need_da = cf.get('da', 0) == 0
                need_capex = cf.get('capex', 0) == 0
                if need_da or need_capex:
                    try:
                        xbrl_result = get_da_capex_from_xbrl_notes(corp_code, year, key, rprt_code)
                    except Exception:
                        xbrl_result = None
                    if xbrl_result:
                        # 성공/실패 여부와 무관하게 진단 정보는 항상 남겨서
                        # app.py 디버그 expander에서 어느 단계에서 막혔는지
                        # (rcept_no 못 찾음/zip 아님/태그 없음 등) 바로 보이게 함
                        result['_xbrl_debug'] = xbrl_result.get('_debug')
                        if need_da and xbrl_result.get('da'):
                            cf['da'] = xbrl_result['da']
                            is_['da'] = is_.get('da') or xbrl_result['da']
                            cf['_da_debug_items'] = None  # 주석에서 찾았으므로 디버그 표시 불필요
                            result['_da_source'] = 'xbrl_note'
                        if need_capex and xbrl_result.get('capex'):
                            cf['capex'] = xbrl_result['capex']
                            result['_capex_source'] = 'xbrl_note'
                return result
            if data.get('status') not in ('000', '013'):
                raise ValueError(f"DART API Error [{data.get('status')}]: {data.get('message', 'Unknown')}")

    # 모든 보고서 종류/구분에서 데이터를 찾지 못함 — 정기보고서를 제출하지 않는
    # (감사보고서만 제출하는) 법인일 가능성이 높음
    return {
        'income_statement': {}, 'balance_sheet': {}, 'cash_flow': {},
        '_no_data': True,
        '_message': (
            f"{year}년 정기보고서(사업/반기/분기보고서) 데이터를 찾을 수 없습니다. "
            "감사보고서만 제출하는 비상장 외부감사대상 법인은 DART Open API의 "
            "표준 재무제표 조회 대상이 아닙니다 (감사보고서 원문은 DART 웹사이트에서 확인 가능)."
        ),
    }


def _find_rcept_no(corp_code, year, api_key, report_type='11011'):
    """주어진 연도/보고서 종류에 해당하는 정기보고서의 접수번호(rcept_no)를
    DART 공시검색(list.json) API로 조회한다.

    fnlttSinglAcntAll.json은 rcept_no를 직접 돌려주지 않으므로, XBRL
    원문(fnlttXbrl.xml)을 받으려면 이 단계가 별도로 필요하다. 보고서 종류별로
    공시 제목에 포함되는 키워드가 다르므로(사업보고서/반기보고서/분기보고서),
    report_type -> 제목 키워드로 매핑해 후보를 좁힌다.
    """
    key = get_dart_api_key(api_key)
    report_keyword_map = {
        '11011': '사업보고서',
        '11012': '반기보고서',
        '11013': '1분기보고서',
        '11014': '3분기보고서',
    }
    keyword = report_keyword_map.get(report_type, '사업보고서')

    # 정기보고서는 보통 회계연도 종료 후 다음해 초~3월 사이에 제출되므로
    # 검색 구간을 회계연도 1/1 ~ 익년 6/30로 넉넉하게 잡는다.
    bgn_de = f"{year}0101"
    end_de = f"{year + 1}0630"

    try:
        url = f"{BASE_URL}/list.json"
        params = {
            "crtfc_key": key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "pblntp_ty": "A",  # 정기공시
            "page_count": 100,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') != '000':
            return None
        candidates = data.get('list', []) or []
        for item in candidates:
            report_nm = item.get('report_nm', '')
            if keyword in report_nm and str(year) in report_nm:
                return item.get('rcept_no')
        # 연도 표시가 report_nm에 없는 경우도 있으므로 키워드만으로 재시도
        for item in candidates:
            if keyword in item.get('report_nm', ''):
                return item.get('rcept_no')
    except Exception:
        return None
    return None


# D&A는 영업활동현금흐름의 "현금의 유출이 없는 비용 등의 가산" 항목 기준으로
# 감가상각비(유형자산) + 무형자산상각비 + 사용권자산상각비 세 가지를 각각
# 찾아서 더한다 (메인 현금흐름표 키워드 매칭과 동일한 분류를, 메인 표에 없고
# XBRL 주석에만 태깅된 경우를 위해 그대로 적용). 예전에는 'depreciation'/
# 'amortisation' 부분일치로 모든 태그를 뭉쳐서 합산했는데, 그러면 유형자산
# 주석의 "기초/기말 누계", "처분", "손상" 같은 무관한 항목까지 섞여 들어가
# 숫자가 틀어졌다 — 세 카테고리로 분리하고 각 카테고리에 맞는 표준
# ifrs-full 태그명만 정확히 매칭해 그 문제를 막는다.
_XBRL_DA_PPE_TAG_PATTERNS = [
    'depreciationpropertyplantandequipment',
    'depreciationofpropertyplantandequipment',
]
_XBRL_DA_INTANGIBLE_TAG_PATTERNS = [
    'amortisationintangibleassets',
    'amortisationofintangibleassets',
    'amortizationintangibleassets',
    'amortizationofintangibleassets',
]
_XBRL_DA_ROU_TAG_PATTERNS = [
    'depreciationrightofuseassets',
    'depreciationofrightofuseassets',
]
# 위 세 카테고리에 매칭되더라도 "감가상각비"가 아닌 누계/처분/손상 관련
# 태그는 제외 (예: AccumulatedDepreciationPropertyPlantAndEquipment,
# DepreciationPropertyPlantAndEquipmentDisposals, ImpairmentLoss...)
_XBRL_DA_EXCLUDE_TAG_PATTERNS = [
    'accumulated', 'disposal', 'impairment', 'revaluation', 'increasedecrease',
]
_XBRL_CAPEX_TAG_PATTERNS = [
    'purchaseofpropertyplantandequipment',
    'acquisitionofpropertyplantandequipment',
    'paymentsforpropertyplantandequipment',
]


def _local_tag(tag):
    """'{namespace}LocalName' 형태의 ElementTree 태그에서 LocalName만 추출"""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _xbrl_build_duration_contexts(root):
    """<context> 엘리먼트 중 기간(duration)을 나타내는 contextRef id 집합을 만든다.
    instant(시점) 컨텍스트는 재무상태표용이라 D&A/CapEx(둘 다 기간 항목)에는
    해당하지 않으므로 제외한다.

    동시에 각 duration context의 (startDate, endDate)를 보관해, 사업연도
    전체(약 365일)를 커버하는 컨텍스트만 추리는 데 사용한다 (분기/반기
    누적이 아닌 값이 섞여 들어오는 것을 막기 위함 — 다만 1차 구현에서는
    "duration이면서 dimension(segment)이 없는 것"까지만 걸러내고, 정밀한
    날짜 길이 검증은 보수적으로 통과시킨다. 길이 미달이어도 완전히 버리기보다
    참고용으로 남겨, 상위 호출자가 결과를 받고 _sum 정도의 안전장치만 적용).
    """
    duration_ctx = {}
    for ctx in root.iter():
        if _local_tag(ctx.tag) != 'context':
            continue
        ctx_id = ctx.get('id')
        if not ctx_id:
            continue
        period = None
        has_segment = False
        for child in ctx:
            if _local_tag(child.tag) == 'period':
                start_el = None
                end_el = None
                for p in child:
                    lt = _local_tag(p.tag)
                    if lt == 'startDate':
                        start_el = p
                    elif lt == 'endDate':
                        end_el = p
                if start_el is not None and end_el is not None:
                    period = (start_el.text, end_el.text)
            elif _local_tag(child.tag) == 'entity':
                for seg in child.iter():
                    if _local_tag(seg.tag) == 'segment':
                        # segment 자식이 실제로 있는지(차원 멤버) 확인
                        if list(seg):
                            has_segment = True
        if period is not None:
            duration_ctx[ctx_id] = {'period': period, 'has_segment': has_segment}
    return duration_ctx


def _xbrl_extract_facts(root, duration_ctx, tag_patterns, exclude_patterns=None, prefer_no_segment=True):
    """XBRL instance에서 로컬 태그명이 tag_patterns 중 하나를 포함하는 모든
    fact를 찾아, duration 컨텍스트(기간성)인 것만 골라 금액을 모은다.

    동일 contextRef + 태그 조합은 한 번만 카운트해 중복 합산을 막는다
    (DART XBRL은 동일 사실이 여러 단위/소수점 정밀도로 중복 표기되는 경우가
    있음 - 기존 _sum_by_keyword의 dedup 패턴과 동일한 접근).

    prefer_no_segment=True이면 비차원(segment 없음) 컨텍스트의 facts를 우선
    사용하고, 그것이 전혀 없을 때만 차원(주석 PP&E 테이블 등) 컨텍스트의
    facts를 합산한다 — 비차원 합계 vs 차원별 세부내역을 이중으로 더해버리는
    것을 막기 위함.
    """
    exclude_patterns = exclude_patterns or []
    seen = set()
    no_seg_total = 0.0
    seg_total = 0.0
    no_seg_found = False
    seg_found = False

    for el in root.iter():
        local = _local_tag(el.tag).lower()
        if not any(p in local for p in tag_patterns):
            continue
        if any(p in local for p in exclude_patterns):
            continue
        ctx_ref = el.get('contextRef')
        if not ctx_ref or ctx_ref not in duration_ctx:
            continue  # instant context 등 기간 항목이 아닌 것은 제외
        text = (el.text or '').strip()
        if not text:
            continue
        try:
            val = abs(float(text.replace(',', '')))
        except ValueError:
            continue
        if val == 0:
            continue
        dedup_key = (_local_tag(el.tag), ctx_ref)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if duration_ctx[ctx_ref]['has_segment']:
            seg_total += val
            seg_found = True
        else:
            no_seg_total += val
            no_seg_found = True

    if prefer_no_segment and no_seg_found:
        return no_seg_total
    if seg_found:
        return seg_total
    return no_seg_total if no_seg_found else 0.0


def get_da_capex_from_xbrl_notes(corp_code, year, api_key, report_type='11011'):
    """fnlttSinglAcntAll.json 본문(4대 재무제표)에는 D&A/CapEx가 0으로
    잡히는 경우(예: 삼성전자 별도재무제표처럼 현금흐름표 본문에 감가상각비
    줄 자체가 없고, 유형자산 주석의 "당기증가(상각)" 컬럼에만 있는 경우)를
    위한 추가 폴백.

    절차:
      1) list.json으로 해당 연도/보고서종류의 rcept_no(접수번호)를 찾는다.
      2) fnlttXbrl.xml로 XBRL 원문 zip을 받아 메모리에서 풀고 .xbrl(인스턴스
         문서)을 ElementTree로 파싱한다.
      3) 태그 로컬명에 depreciation/amortisation이 들어간 모든 duration
         fact, 그리고 capex 관련 표준 태그를 찾아 합산한다.

    실패 시(rcept_no 없음/zip 깨짐/파싱 실패/매칭 실패 등) None을 반환해
    기존 폴백 흐름(디버그 expander 표시)이 그대로 동작하게 한다. 다만 *어느
    단계에서* 실패했는지는 '_debug' 키에 남겨, 화면(app.py)에서 사용자가
    바로 확인할 수 있게 한다 — 실 운영 중 원인 진단이 막혀 있던 문제를
    풀기 위함 (rcept_no 못 찾음 / zip 아님 / xbrl 안에 태그 없음 등 구분).
    """
    debug = {'rcept_no': None, 'xbrl_filenames': [], 'stage': None, 'detail': None}
    try:
        key = get_dart_api_key(api_key)
        if not key:
            debug['stage'] = 'no_api_key'
            return {'da': None, 'capex': None, 'source': None, '_debug': debug}

        rcept_no = _find_rcept_no(corp_code, year, key, report_type)
        debug['rcept_no'] = rcept_no
        if not rcept_no:
            debug['stage'] = 'rcept_no_not_found'
            return {'da': None, 'capex': None, 'source': None, '_debug': debug}

        url = f"{BASE_URL}/fnlttXbrl.xml"
        params = {
            "crtfc_key": key,
            "rcept_no": rcept_no,
            "reprt_code": report_type,
        }
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()

        # DART는 오류 시에도 200 + zip이 아닌 XML 오류 메시지를 줄 수 있으므로
        # zip으로 열 수 없으면 그 자체로 실패 처리. 이 경우 응답 본문(에러
        # 메시지)을 같이 남겨 어떤 오류인지 바로 보이게 한다.
        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            debug['stage'] = 'not_a_zip'
            debug['detail'] = resp.content[:500].decode('utf-8', errors='replace')
            return {'da': None, 'capex': None, 'source': None, '_debug': debug}

        xbrl_filenames = [n for n in zf.namelist() if n.lower().endswith(('.xbrl', '.xml'))]
        debug['xbrl_filenames'] = xbrl_filenames
        if not xbrl_filenames:
            debug['stage'] = 'zip_has_no_xbrl_xml'
            debug['detail'] = zf.namelist()
            return {'da': None, 'capex': None, 'source': None, '_debug': debug}

        da_total = 0.0
        capex_total = 0.0
        found_any = False
        tag_samples = []

        for fname in xbrl_filenames:
            try:
                content = zf.read(fname)
                root = ET.fromstring(content)
            except (ET.ParseError, KeyError) as e:
                debug.setdefault('parse_errors', []).append(f"{fname}: {e}")
                continue

            # 진단용: 이 인스턴스 문서에 실제로 어떤 로컬 태그들이 있는지
            # 일부 샘플을 남겨, depreciation/amortisation/capex 패턴과 전혀
            # 다른 명명을 쓰는 필러(예: dart: 확장 태그)를 식별할 수 있게 함
            for el in root.iter():
                lt = _local_tag(el.tag)
                if lt not in ('context', 'unit') and lt not in tag_samples:
                    tag_samples.append(lt)

            duration_ctx = _xbrl_build_duration_contexts(root)
            if not duration_ctx:
                debug.setdefault('no_duration_ctx_files', []).append(fname)
                continue

            # 감가상각비(유형자산) + 무형자산상각비 + 사용권자산상각비를 각각
            # 정확한 태그명으로 따로 찾아 합산 (영업활동현금흐름 조정항목 기준)
            da_ppe_val = _xbrl_extract_facts(
                root, duration_ctx, _XBRL_DA_PPE_TAG_PATTERNS, _XBRL_DA_EXCLUDE_TAG_PATTERNS,
            )
            da_intangible_val = _xbrl_extract_facts(
                root, duration_ctx, _XBRL_DA_INTANGIBLE_TAG_PATTERNS, _XBRL_DA_EXCLUDE_TAG_PATTERNS,
            )
            da_rou_val = _xbrl_extract_facts(
                root, duration_ctx, _XBRL_DA_ROU_TAG_PATTERNS, _XBRL_DA_EXCLUDE_TAG_PATTERNS,
            )
            da_val = da_ppe_val + da_intangible_val + da_rou_val
            capex_val = _xbrl_extract_facts(
                root, duration_ctx, _XBRL_CAPEX_TAG_PATTERNS,
            )
            if da_val > da_total:
                da_total = da_val
                found_any = True
                debug['da_breakdown'] = {
                    'ppe': da_ppe_val, 'intangible': da_intangible_val, 'rou': da_rou_val,
                }
            if capex_val > capex_total:
                capex_total = capex_val
                found_any = True

        debug['tag_sample'] = [t for t in tag_samples
                                if any(k in t.lower() for k in
                                       ('depreciat', 'amortis', 'amortiz', 'propertyplant'))][:30]

        if not found_any:
            debug['stage'] = 'no_matching_tags'
            return {'da': None, 'capex': None, 'source': None, '_debug': debug}

        return {
            'da': da_total if da_total > 0 else None,
            'capex': capex_total if capex_total > 0 else None,
            'source': 'xbrl_note',
            '_debug': debug,
        }
    except Exception as e:
        # XBRL 조회/파싱은 실패 경로가 매우 다양함 (네트워크, 잘못된 rcept_no,
        # 예상과 다른 XML 구조 등) — 호출자에게 예외를 전달하지 않고 어떤
        # 예외였는지만 _debug에 남긴다.
        debug['stage'] = 'exception'
        debug['detail'] = f"{type(e).__name__}: {e}"
        return {'da': None, 'capex': None, 'source': None, '_debug': debug}


def get_business_segments(corp_code, year, api_key=None):
    """Attempt to get business segment data - returns empty list if not available"""
    try:
        # DART doesn't have a direct segment API endpoint
        # This would require parsing XBRL or notes data
        return []
    except Exception:
        return []


def parse_financial_data(raw_data):
    """Parse raw DART API response data"""
    if not raw_data or 'list' not in raw_data:
        return {}
    return _extract_financials_from_items(raw_data['list'])


def get_financial_statements_for_year(corp_code: str, year: int, api_key=None, report_type: str = '11011') -> dict:
    """Alias that passes api_key through."""
    return get_financial_statements(corp_code, year, api_key, report_type)


def build_historical_summary(corp_code: str, years: list, api_key=None, report_type: str = '11011') -> pd.DataFrame:
    """
    Fetch financial statements for multiple years and return a tidy summary DataFrame.

    Columns: year, revenue, cogs, gross_profit, sga, ebit, ebitda, net_income,
             tax_expense, tax_rate, da, capex, total_assets, current_assets,
             current_liabilities, cash, total_debt, total_equity, nwc
    """
    rows = []
    for yr in sorted(years):
        try:
            fs = get_financial_statements(corp_code, yr, api_key, report_type)
        except Exception:
            continue

        is_ = fs.get('income_statement', {})
        bs = fs.get('balance_sheet', {})
        cf = fs.get('cash_flow', {})

        revenue = float(is_.get('revenue') or 0)
        cogs = float(is_.get('cogs') or 0)
        sga = float(is_.get('sga') or 0)
        ebit = float(is_.get('ebit') or 0)
        net_income = float(is_.get('net_income') or 0)
        tax_expense = float(is_.get('tax_expense') or 0)
        da = float(cf.get('da') or is_.get('da') or 0)
        capex = abs(float(cf.get('capex') or 0))
        total_assets = float(bs.get('total_assets') or 0)
        current_assets = float(bs.get('current_assets') or 0)
        current_liabilities = float(bs.get('current_liabilities') or 0)
        cash = float(bs.get('cash') or 0)
        total_equity = float(bs.get('total_equity') or 0)
        total_debt = float(bs.get('total_debt') or
                           (bs.get('short_term_borrowings', 0) or 0) +
                           (bs.get('long_term_borrowings', 0) or 0) +
                           (bs.get('total_borrowings', 0) or 0))

        gross_profit = revenue - cogs
        ebitda = ebit + da
        nwc = current_assets - current_liabilities
        pretax = net_income + tax_expense
        tax_rate = (tax_expense / pretax) if pretax else 0.22

        rows.append({
            'year': yr,
            'revenue': revenue,
            'cogs': cogs,
            'gross_profit': gross_profit,
            'sga': sga,
            'ebit': ebit,
            'ebitda': ebitda,
            'net_income': net_income,
            'tax_expense': tax_expense,
            'tax_rate': tax_rate,
            'da': da,
            'capex': capex,
            'total_assets': total_assets,
            'current_assets': current_assets,
            'current_liabilities': current_liabilities,
            'cash': cash,
            'total_debt': total_debt,
            'total_equity': total_equity,
            'nwc': nwc,
        })

    return pd.DataFrame(rows)
