import os
import io
import re
import zipfile
import requests
from datetime import datetime
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
        'ifrs-full_PurchaseOfIntangibleAssets',
        'dart_PurchaseOfIntangibleAssets',
        'PurchaseOfIntangibleAssets',
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
# da_ppe 키워드 '감가상각비'는 "사용권자산감가상각비" 계정명의 부분문자열이기도
# 해서, ROU 전용 계정이 PPE 카테고리로 잘못 잡혀 da_rou와 중복 합산되는 문제가
# 있었다 (예: 삼성전자 D&A가 실제보다 부풀려짐) — da_ppe 매칭에서는 사용권 관련
# 계정을 명시적으로 제외한다.
DA_PPE_EXCLUDE_KEYWORDS = DA_EXCLUDE_KEYWORDS + ['사용권']

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


def _sum_by_keyword(items, sj_div_filter, keywords, exclude_keywords=None, return_detail=False):
    """Sum thstrm_amount across all distinct line items whose account_nm
    contains any of the given keywords. Used as a last-resort fallback for
    D&A, which is frequently broken out across several note line items
    (e.g. 유형자산 감가상각비, 무형자산상각비, 사용권자산 감가상각비) with no
    standard XBRL account_id.
    return_detail=True이면 (total, [(account_nm, amount), ...]) 반환."""
    seen = set()
    total = 0.0
    detail = []
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
        amt = _safe_amount(item.get('thstrm_amount', '0'))
        total += amt
        if return_detail:
            detail.append((name, amt))
    if return_detail:
        return total, detail
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
    da_ppe = _lookup_account(cf_map, ACCOUNT_MAP['da_ppe'], KOREAN_NAME_KEYWORDS['da_ppe'], DA_PPE_EXCLUDE_KEYWORDS)
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

    # D&A 진단용으로 CF/IS 원본 계정명을 항상 남겨, 어떤 계정명으로 들어오는지
    # 화면에서 직접 확인할 수 있도록 한다 (account_id가 비표준이라 키워드
    # 매칭에 실패하거나, 의도와 다른 계정이 잡히는 경우를 찾기 위함).
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

    # 순차입금(Net Debt) = IBD(이자부부채) - 현금성자산
    # IBD: 단기차입금, 유동성장기부채, 장기차입금, 금융리스부채, 전환사채(CB),
    #      교환사채(EB), 신주인수권부사채(BW), 일반 사채 등을 모두 합산 (서로
    #      다른 계정이 동시에 존재할 수 있으므로 _sum_by_keyword로 전부 더함)
    ibd, ibd_detail = _sum_by_keyword(bs_items, 'BS', KOREAN_NAME_KEYWORDS['ibd'], IBD_EXCLUDE_KEYWORDS, return_detail=True)

    # Total debt: XBRL Borrowings 태그는 사채(회사채)를 누락하는 경우가 많음.
    # ibd(단기차입금+유동성장기부채+장기차입금+사채 등 키워드 합산)와 비교해
    # 더 큰 값을 사용한다.
    total_debt = _lookup_account(bs_map, ACCOUNT_MAP['total_borrowings'])
    if total_debt == 0:
        short_term = _lookup_account(bs_map, ACCOUNT_MAP['short_term_borrowings'])
        long_term = _lookup_account(bs_map, ACCOUNT_MAP['long_term_borrowings'])
        total_debt = short_term + long_term
    # ibd가 더 크면 ibd 사용 (사채 등 XBRL 태그 누락 항목 보완)
    total_debt = max(total_debt, ibd)
    # 현금성자산: 현금및현금성자산 + 단기금융상품(단기예금 등)
    cash_equivalents, cash_eq_detail = _sum_by_keyword(bs_items, 'BS', KOREAN_NAME_KEYWORDS['cash_equivalents'], CASH_EQUIV_EXCLUDE_KEYWORDS, return_detail=True)
    if cash_equivalents == 0:
        cash_equivalents = cash

    # Extract CF metrics
    operating_cf = _lookup_account(cf_map, ACCOUNT_MAP['operating_cf'])
    investing_cf = _lookup_account(cf_map, ACCOUNT_MAP['investing_cf'])
    financing_cf = _lookup_account(cf_map, ACCOUNT_MAP['financing_cf'])
    # CapEx = 현금흐름표(투자활동) 내 "유형자산의 취득" + "무형자산의 취득"
    # 금액의 합. 두 항목이 별도 줄로 같이 잡히는 경우가 많으므로(예: 유형자산
    # 취득 + 무형자산 취득을 따로 공시), 첫 매칭 한 건만 반환하는
    # _lookup_account 대신 _sum_by_keyword로 두 계정을 전부 더한다.
    # account_id(XBRL 표준 태그) 매칭을 우선 시도하고, 0이면 한국어 계정명
    # 키워드 합산으로 폴백한다.
    capex_raw = _lookup_account(cf_map, ACCOUNT_MAP['capex'])
    capex_kw_sum = _sum_by_keyword(cf_items, None, KOREAN_NAME_KEYWORDS['capex'],
                                    CAPEX_EXCLUDE_KEYWORDS)
    if capex_kw_sum != 0:
        capex_raw = capex_kw_sum
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
            'da_combined_account': da_cf,
            'da_is_side': da_is,
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
            'ibd_detail': ibd_detail,
            'cash_equivalents': cash_equivalents,
            'cash_eq_detail': cash_eq_detail,
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
            'da': da,
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
                        xbrl_result = get_da_capex_from_xbrl_notes(corp_code, year, key, rprt_code, fs_div=div)
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

    # 모든 보고서 종류/구분에서 데이터를 찾지 못함
    # → 감사보고서 원문 직접 파싱으로 폴백 시도
    audit_result = _get_fs_from_audit_report(corp_code, year, key, fs_div)
    if audit_result:
        return audit_result

    return {
        'income_statement': {}, 'balance_sheet': {}, 'cash_flow': {},
        '_no_data': True,
        '_message': (
            f"{year}년 재무제표 데이터를 찾을 수 없습니다. "
            "정기보고서(사업/반기/분기) 및 감사보고서 원문 파싱을 모두 시도했으나 "
            "데이터를 추출하지 못했습니다."
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


def _find_audit_report_rcept_no(corp_code, year, api_key):
    """비상장 외부감사 대상 법인의 감사보고서 rcept_no를 DART list API로 찾는다.
    정기공시(pblntf_ty=A)에 없는 경우 외부감사(F) 카테고리도 시도한다."""
    key = get_dart_api_key(api_key)
    bgn_de = f"{year}0101"
    end_de = f"{year + 1}0930"
    for pblntf_ty in ('A', 'F'):
        try:
            params = {
                "crtfc_key": key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": pblntf_ty,
                "page_count": 100,
            }
            resp = requests.get(f"{BASE_URL}/list.json", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') != '000':
                continue
            candidates = data.get('list', []) or []
            # 감사보고서 or 재무제표 포함 제목 우선
            for keyword in ('감사보고서', '재무제표'):
                for item in candidates:
                    nm = item.get('report_nm', '')
                    if keyword in nm:
                        return item.get('rcept_no')
        except Exception:
            continue
    return None


# 재무제표 섹션 제목 키워드 → 추출할 계정 키워드 매핑
_FS_SECTION_KEYWORDS = {
    'bs':  ('재무상태표', '대차대조표'),
    'is':  ('손익계산서', '포괄손익계산서', '영업손익계산서'),
    'cf':  ('현금흐름표',),
}

# 계정명 키워드 → (섹션, 내부 키)
_FS_ACCOUNT_KEYWORDS = [
    # IS
    ('is', 'revenue',      ['매출액', '영업수익', '수익(매출액)', '매출']),
    ('is', 'cogs',         ['매출원가', '영업비용(매출원가)', '영업원가']),
    ('is', 'gross_profit', ['매출총이익', '매출총손익']),
    ('is', 'sga',          ['판매비와관리비', '판매비및관리비', '판관비']),
    ('is', 'ebit',         ['영업이익', '영업손익']),
    ('is', 'net_income',   ['당기순이익', '당기순손익', '분기순이익']),
    # BS
    ('bs', 'total_assets',      ['자산총계']),
    ('bs', 'total_liabilities', ['부채총계']),
    ('bs', 'total_equity',      ['자본총계']),
    ('bs', 'cash',              ['현금및현금성자산']),
    ('bs', 'accounts_receivable', ['매출채권']),
    ('bs', 'inventory',         ['재고자산']),
    # CF
    ('cf', 'operating_cf',  ['영업활동으로인한현금흐름', '영업활동현금흐름']),
    ('cf', 'investing_cf',  ['투자활동으로인한현금흐름', '투자활동현금흐름']),
    ('cf', 'financing_cf',  ['재무활동으로인한현금흐름', '재무활동현금흐름']),
    ('cf', 'da',            ['감가상각비', '유형자산감가상각비']),
    ('cf', 'capex',         ['유형자산의취득', '유형자산취득']),
]


def _parse_amount(text):
    """금액 문자열 → float. △/-는 음수. 파싱 불가면 None."""
    t = (text or '').replace(',', '').replace(' ', '').strip()
    neg = t.startswith('△') or t.startswith('-') or (t.startswith('(') and t.endswith(')'))
    t = t.lstrip('△-(').rstrip(')')
    if not t:
        return None
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return None


def _parse_fs_from_document_xml(root, fs_div='OFS'):
    """감사보고서/사업보고서 원문 XML에서 BS/IS/CF 주요 계정을 추출한다.

    전략: 제목 기반 탐지 대신 테이블 내용으로 종류 판별.
    문서 내 모든 테이블을 순회하며 셀 텍스트에 충분한 수의 시그니처 키워드가
    포함된 경우 해당 재무제표 테이블로 분류한다 (목차/기타 테이블 오탐 방지).
    """
    # 각 재무제표 종류를 판별하는 시그니처 키워드 (2개 이상 포함 시 해당 종류로 분류)
    _BS_SIG  = ['유동자산', '비유동자산', '자산총계', '부채총계', '자본총계', '유동부채', '비유동부채']
    _IS_SIG  = ['매출액', '영업이익', '당기순이익', '매출원가', '판매비', '영업수익', '법인세비용']
    _CF_SIG  = ['영업활동', '투자활동', '재무활동', '현금및현금성자산']

    # 모든 테이블 수집
    all_tables = [el for el in root.iter() if _local_tag(el.tag).lower() == 'table']
    print(f"[AUDIT_PARSE] total tables found: {len(all_tables)}")

    def _table_text(table):
        rows = _table_rows(table)
        return ' '.join(_cell_text(c) for tr in rows for c in _row_cells(tr))

    def _count_sig(text, sigs):
        return sum(1 for kw in sigs if kw in text)

    bs_table = is_table = cf_table = None
    for ti, table in enumerate(all_tables):
        txt = _table_text(table)
        bs_score = _count_sig(txt, _BS_SIG)
        is_score = _count_sig(txt, _IS_SIG)
        cf_score = _count_sig(txt, _CF_SIG)
        if bs_table is None and bs_score >= 3:
            print(f"[AUDIT_PARSE] table[{ti}] -> BS (score={bs_score}) snippet={txt[:100]!r}")
            bs_table = table
        if is_table is None and is_score >= 3:
            print(f"[AUDIT_PARSE] table[{ti}] -> IS (score={is_score}) snippet={txt[:100]!r}")
            is_table = table
        if cf_table is None and cf_score >= 2:
            print(f"[AUDIT_PARSE] table[{ti}] -> CF (score={cf_score}) snippet={txt[:100]!r}")
            cf_table = table

    section_tables = {}
    if bs_table is not None:
        section_tables['bs'] = bs_table
    if is_table is not None:
        section_tables['is'] = is_table
    if cf_table is not None:
        section_tables['cf'] = cf_table

    result = {'income_statement': {}, 'balance_sheet': {}, 'cash_flow': {}}
    section_map = {'bs': 'balance_sheet', 'is': 'income_statement', 'cf': 'cash_flow'}

    for sec, table in section_tables.items():
        rows = _table_rows(table)
        if not rows:
            continue
        # 헤더 행에서 "당기" 컬럼 위치 찾기
        amount_col = None
        data_start = 0
        for hi, tr in enumerate(rows[:4]):
            cells = _row_cells(tr)
            texts = [_cell_text(c) for c in cells]
            for ci, t in enumerate(texts):
                t_n = _normalize_ws(t)
                if '당기' in t_n and '전기' not in t_n:
                    amount_col = ci
                    data_start = hi + 1
                    break
            if amount_col is not None:
                break

        target_accounts = [(key, kws) for (s, key, kws) in _FS_ACCOUNT_KEYWORDS if s == sec]
        section_result = result[section_map[sec]]
        print(f"[AUDIT_PARSE] sec={sec} amount_col={amount_col} data_start={data_start} rows={len(rows)}")

        for tri, tr in enumerate(rows[data_start:data_start+5]):
            cells = _row_cells(tr)
            texts = [_cell_text(c) for c in cells]
            print(f"[AUDIT_PARSE]   row{tri}: {texts}")

        for tr in rows[data_start:]:
            cells = _row_cells(tr)
            texts = [_cell_text(c) for c in cells]
            if len(texts) < 2:
                continue
            acct_name = ''
            for t in texts:
                if t.strip():
                    acct_name = _normalize_ws(t.strip())
                    break
            if not acct_name:
                continue
            if amount_col is not None and amount_col < len(texts):
                amt = _parse_amount(texts[amount_col])
            else:
                amt = _parse_amount(texts[-1])
            if amt is None:
                continue
            for key, kws in target_accounts:
                if key not in section_result:
                    if any(kw in acct_name for kw in kws):
                        section_result[key] = amt
                        break

    return result


def _get_fs_from_audit_report(corp_code, year, api_key, fs_div='OFS'):
    """감사보고서 원문을 다운로드해 재무제표 주요 계정을 파싱한다.
    성공 시 get_financial_statements와 동일한 형태의 dict를 반환.
    실패 시 None을 반환한다."""
    key = get_dart_api_key(api_key)
    rcept_no = _find_audit_report_rcept_no(corp_code, year, key)
    print(f"[AUDIT] corp={corp_code} year={year} rcept_no={rcept_no}")
    if not rcept_no:
        return None

    try:
        resp = requests.get(f"{BASE_URL}/document.xml",
                            params={"crtfc_key": key, "rcept_no": rcept_no},
                            timeout=60)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except Exception as e:
        print(f"[AUDIT] ZIP download failed: {e}")
        return None

    # 연결/별도 구분에 맞는 파일 우선 시도 (파일명 힌트 없으면 모두 시도)
    xml_files = [n for n in zf.namelist() if n.lower().endswith('.xml')]
    print(f"[AUDIT] xml_files={xml_files}")
    best = None
    for fname in xml_files:
        try:
            root = _lenient_parse_xml(zf.read(fname))
        except Exception:
            continue
        # 태그 구조 덤프
        from collections import Counter as _Counter
        _tags = _Counter()
        for _el in root.iter():
            _tags[_local_tag(_el.tag).lower()] += 1
        print(f"[AUDIT] {fname} tag counts: {_tags.most_common(20)}")
        # 텍스트 샘플 덤프 (재무/매출 관련 키워드 포함 노드만)
        samples = []
        for el in root.iter():
            for t in ((el.text or '').strip(), (el.tail or '').strip()):
                if t and any(k in t for k in ('재무', '매출', '영업', '당기', '자산', '부채', '자본')):
                    samples.append(t[:80])
                    if len(samples) >= 15:
                        break
            if len(samples) >= 15:
                break
        print(f"[AUDIT] {fname} text samples: {samples}")
        parsed = _parse_fs_from_document_xml(root, fs_div)
        rev = parsed['income_statement'].get('revenue')
        print(f"[AUDIT] {fname}: revenue={rev}  is_keys={list(parsed['income_statement'].keys())}")
        # IS에 revenue가 있으면 채택
        if rev:
            if best is None:
                best = parsed
            # CFS 요청인데 연결 재무제표 파일인 경우 우선
            if fs_div == 'CFS' and '연결' in fname:
                best = parsed
                break
            elif fs_div == 'OFS' and '연결' not in fname:
                best = parsed
                break

    if not best or not best['income_statement'].get('revenue'):
        return None

    # get_financial_statements 반환 형태로 맞추기
    is_ = best['income_statement']
    bs_ = best['balance_sheet']
    cf_ = best['cash_flow']

    # 단위 추론: 감사보고서는 대부분 원 단위 or 천원 단위
    # revenue가 1조 이상이면 원 단위, 1억 이하면 천원 단위일 가능성
    # 여기서는 그냥 원 단위로 반환 (파서가 숫자 그대로 읽음)
    unit = 1  # 단위 변환은 하지 않음; 이미 원 단위

    result = {
        'income_statement': {
            'revenue': (is_.get('revenue') or 0) * unit,
            'cogs': (is_.get('cogs') or 0) * unit,
            'gross_profit': (is_.get('gross_profit') or 0) * unit,
            'sga': (is_.get('sga') or 0) * unit,
            'ebit': (is_.get('ebit') or 0) * unit,
            'net_income': (is_.get('net_income') or 0) * unit,
            'da': (cf_.get('da') or 0) * unit,
        },
        'balance_sheet': {
            'total_assets': (bs_.get('total_assets') or 0) * unit,
            'total_liabilities': (bs_.get('total_liabilities') or 0) * unit,
            'total_equity': (bs_.get('total_equity') or 0) * unit,
            'cash': (bs_.get('cash') or 0) * unit,
            'accounts_receivable': (bs_.get('accounts_receivable') or 0) * unit,
            'inventory': (bs_.get('inventory') or 0) * unit,
            'shares_outstanding': 0,
        },
        'cash_flow': {
            'operating_cf': (cf_.get('operating_cf') or 0) * unit,
            'investing_cf': (cf_.get('investing_cf') or 0) * unit,
            'financing_cf': (cf_.get('financing_cf') or 0) * unit,
            'da': (cf_.get('da') or 0) * unit,
            'capex': abs(cf_.get('capex') or 0) * unit,
        },
        '_report_type_used': 'audit_report',
        '_fs_div_used': fs_div,
        '_rcept_no': rcept_no,
    }
    return result


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
# CapEx = 유형자산 취득 + 무형자산 취득 (둘 다 매칭해 합산)
_XBRL_CAPEX_TAG_PATTERNS = [
    'purchaseofpropertyplantandequipment',
    'acquisitionofpropertyplantandequipment',
    'paymentsforpropertyplantandequipment',
    'purchaseofintangibleassets',
    'acquisitionofintangibleassets',
    'paymentsforintangibleassets',
]


def _local_tag(tag):
    """'{namespace}LocalName' 형태의 ElementTree 태그에서 LocalName만 추출"""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


_CONSOLIDATED_SCOPE_AXIS_SUFFIX = 'consolidatedandseparatefinancialstatementsaxis'
_CONSOLIDATED_SCOPE_MEMBERS = {
    'CFS': 'consolidatedmember',
    'OFS': 'separatemember',
}


def _xbrl_build_duration_contexts(root, target_year=None, fs_div=None):
    """<context> 엘리먼트 중 기간(duration)을 나타내는 contextRef id 집합을 만든다.
    instant(시점) 컨텍스트는 재무상태표용이라 D&A/CapEx(둘 다 기간 항목)에는
    해당하지 않으므로 제외한다.

    동시에 각 duration context의 (startDate, endDate)를 보관해, 사업연도
    전체(약 365일)를 커버하는 컨텍스트만 추리는 데 사용한다 (분기/반기
    누적이 아닌 값이 섞여 들어오는 것을 막기 위함).

    target_year가 주어지면, IFRS 연차 보고서 XBRL 인스턴스 문서 한 개에는
    당기뿐 아니라 비교공시되는 전기/전전기 duration context도 함께 들어있는
    것이 보통이므로, endDate의 연도가 target_year와 다르거나 기간 길이가
    약 330~380일(연간) 범위를 벗어나는 context는 제외한다 — 그러지 않으면
    같은 axis 아래 여러 회계연도 facts가 함께 합산되어 D&A가 몇 배로
    부풀려지는 문제가 생긴다.

    fs_div가 주어지면, ConsolidatedAndSeparateFinancialStatementsAxis로
    연결(Consolidated)/별도(Separate) 범위를 함께 태깅한 context 중
    요청한 범위(fs_div='CFS'->연결, 'OFS'->별도)와 다른 멤버를 가진 것은
    제외한다. 이 axis는 "분류 항목"이 아니라 "같은 회사를 보는 두 가지
    스코프"이므로, 둘을 같은 axis-group으로 묶어 더하면 연결+별도 금액이
    그대로 합산되어 실제 금액의 약 1.7~1.8배로 부풀려진다 (삼성전자 등에서
    확인됨).
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
        axes = set()
        members = []
        scope_member = None
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
                            for member in seg:
                                dim = member.get('dimension')
                                if dim:
                                    axes.add(dim)
                                    member_text = (member.text or '').strip()
                                    members.append(f"{dim}={member_text}")
                                    if dim.lower().endswith(_CONSOLIDATED_SCOPE_AXIS_SUFFIX):
                                        scope_member = member_text.lower()
        if period is not None:
            if fs_div is not None and scope_member is not None:
                wanted_member = _CONSOLIDATED_SCOPE_MEMBERS.get(fs_div)
                if wanted_member is not None and not scope_member.endswith(wanted_member):
                    continue
            if target_year is not None:
                start_str, end_str = period
                try:
                    start_d = datetime.strptime(start_str[:10], '%Y-%m-%d')
                    end_d = datetime.strptime(end_str[:10], '%Y-%m-%d')
                except ValueError:
                    continue
                if end_d.year != target_year:
                    continue
                if not (330 <= (end_d - start_d).days <= 380):
                    continue
            duration_ctx[ctx_id] = {
                'period': period,
                'has_segment': has_segment,
                'axes': frozenset(axes),
                'members': members,
            }
    return duration_ctx


def _xbrl_extract_facts(root, duration_ctx, tag_patterns, exclude_patterns=None, prefer_no_segment=True,
                         debug_collector=None):
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
    no_seg_found = False
    # 차원(segment)이 있는 fact는 어떤 axis 조합에 속하는지별로 따로 합산한다.
    # 동일 개념(예: 감가상각비)이 서로 무관한 여러 주석 테이블(예: 유형자산
    # 종류별 내역 axis, 사업부문별 내역 axis)에 동시에 태깅되어 있는 경우,
    # 모든 axis를 무조건 합치면 같은 금액이 여러 번 더해져 실제 총액보다
    # 훨씬 큰 값이 나온다. 따라서 axis 조합별로 분리 합산한 뒤, 그 중 하나의
    # axis 그룹(가장 큰 합계)만을 진짜 총액으로 사용한다.
    seg_totals_by_axes = {}
    seg_facts_by_axes = {}  # axes_key -> list of (tag, ctx_ref, members, val), for debugging

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

        ctx_info = duration_ctx[ctx_ref]
        if ctx_info['has_segment']:
            axes_key = ctx_info.get('axes', frozenset())
            seg_totals_by_axes[axes_key] = seg_totals_by_axes.get(axes_key, 0.0) + val
            if debug_collector is not None:
                seg_facts_by_axes.setdefault(axes_key, []).append(
                    (_local_tag(el.tag), ctx_ref, ctx_info.get('members', []), val)
                )
        else:
            no_seg_total += val
            no_seg_found = True

    if debug_collector is not None:
        debug_collector['no_seg_total'] = no_seg_total
        debug_collector['axis_groups'] = [
            {
                'axes': sorted(axes_key),
                'total': total,
                'facts': [
                    {'tag': tag, 'contextRef': cref, 'members': members, 'value': val}
                    for tag, cref, members, val in seg_facts_by_axes.get(axes_key, [])
                ],
            }
            for axes_key, total in seg_totals_by_axes.items()
        ]

    if prefer_no_segment and no_seg_found:
        return no_seg_total
    if seg_totals_by_axes:
        return max(seg_totals_by_axes.values())
    return no_seg_total if no_seg_found else 0.0


def get_da_capex_from_xbrl_notes(corp_code, year, api_key, report_type='11011', fs_div='CFS'):
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

            duration_ctx = _xbrl_build_duration_contexts(root, target_year=year, fs_div=fs_div)
            if not duration_ctx:
                debug.setdefault('no_duration_ctx_files', []).append(fname)
                continue

            # 감가상각비(유형자산) + 무형자산상각비 + 사용권자산상각비를 각각
            # 정확한 태그명으로 따로 찾아 합산 (영업활동현금흐름 조정항목 기준)
            ppe_debug = {}
            da_ppe_val = _xbrl_extract_facts(
                root, duration_ctx, _XBRL_DA_PPE_TAG_PATTERNS, _XBRL_DA_EXCLUDE_TAG_PATTERNS,
                debug_collector=ppe_debug,
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
                debug['da_ppe_axis_groups'] = ppe_debug.get('axis_groups')
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


_INVALID_XML_CHARS_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_BARE_AMP_RE = re.compile(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)')
# 한글 보고서 원문에는 "<주1>", "<비교표시>"처럼 '<' '>'를 괄호 문장부호(낫표)로
# 그대로 쓴 경우가 흔하다. 실제 XML 태그는 항상 '<' 바로 뒤에 영문자/'_'/'!'
# (주석,CDATA)/'?'(PI)/'/' (닫는 태그)가 오므로, 그 외의 경우(숫자/한글/공백 등이
# 바로 뒤따르는 '<')는 태그가 아니라 텍스트로 보고 escape한다.
_RAW_LT_RE = re.compile(r'<(?![A-Za-z_!?/])')


def _lenient_parse_xml(raw):
    """DART 사업보고서 원문(document.xml)은 잘못 escape된 '&'/'<' (예: "R&D",
    "<주1>"처럼 HWP/한글 문서에서 그대로 넘어온 텍스트)나 XML 1.0에서 허용하지
    않는 제어문자가 섞여 있어, 표준 인코딩(UTF-8)으로 디코딩해도 ET.fromstring이
    "not well-formed (invalid token)"으로 실패하는 경우가 매우 흔하다. 인코딩
    후보(UTF-8/CP949/EUC-KR)와 정제(제어문자 제거 + 잘못된 '&'/'<' escape 보정)
    조합을 순서대로 시도해 가장 먼저 성공하는 결과를 반환한다. 전부 실패하면
    마지막 ParseError를 그대로 올린다.
    """
    texts = []
    for enc in ('utf-8', 'cp949', 'euc-kr'):
        try:
            texts.append(raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    if not texts:
        texts.append(raw.decode('utf-8', errors='replace'))

    last_err = None
    for text in texts:
        cleaned = _INVALID_XML_CHARS_RE.sub('', text)
        cleaned = _RAW_LT_RE.sub('&lt;', cleaned)
        cleaned = _BARE_AMP_RE.sub('&amp;', cleaned)
        for candidate in (text, cleaned):
            try:
                return ET.fromstring(candidate)
            except ET.ParseError as e:
                last_err = e
    raise last_err


_UNIT_TEXT_RE = re.compile(r'단위\s*[:：]\s*([^()\[\]]+)')
_PERIOD_HEADER_RE = re.compile(r'제\s*\d+\s*기')
_SEGMENT_ROW_EXCLUDE = ('합계', '총계', '소계')


def _cell_text(el):
    return ''.join(el.itertext()).strip()


def _unit_text_to_multiplier(unit_text):
    t = (unit_text or '').replace(' ', '')
    if '억원' in t:
        return 1e8
    if '백만원' in t:
        return 1e6
    if '천원' in t:
        return 1e3
    if '원' in t:
        return 1.0
    return None


_REVENUE_SECTION_HEADING_HINTS = ('매출 및 수주상황', '매출실적', '매출 실적')


def _normalize_ws(s):
    return re.sub(r'\s+', '', s or '')


def _table_rows(table):
    """TABLE 엘리먼트에서 행들을 찾는다. DART 사업보고서 원문은 HTML 스타일
    (TABLE>TR>TD)뿐 아니라 CALS 표 모델(TABLE>TGROUP>THEAD|TBODY>ROW>ENTRY)로도
    나오므로, 직계 자식이 아니라 하위 전체를 훑어 'tr'/'row' 태그를 찾는다."""
    return [el for el in table.iter() if el is not table and _local_tag(el.tag).lower() in ('tr', 'row')]


def _row_cells(row):
    """행 엘리먼트에서 칸들을 찾는다. HTML 스타일 td/th와 CALS 스타일 entry를
    모두 인식한다."""
    return [c for c in row if _local_tag(c.tag).lower() in ('td', 'th', 'entry')]


def _sample_table_headers(root, limit=15):
    """진단용: 문서에서 발견된 표들의 헤더 텍스트를 일부 수집해 반환한다.
    매칭에 실패했을 때, 실제 문서의 표 헤더가 어떤 모양인지 확인하기 위함."""
    samples = []
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'table':
            continue
        rows = _table_rows(el)
        if not rows:
            continue
        header_texts = [_cell_text(td) for td in _row_cells(rows[0])]
        if any(t for t in header_texts):
            samples.append(header_texts)
        if len(samples) >= limit:
            break
    return samples


def _find_revenue_table_with_unit(root):
    """문서 트리를 순서대로 훑어, 사업보고서 "4. 매출 및 수주상황 > 가. 매출실적"의
    표를 찾는다. 표 직전에 등장한 "(단위 : 억원)" 같은 단위 표기도 함께
    추적해 반환한다.

    1차로는 헤더에 '매출유형'과 '품목'을 모두 포함하는 표준 서식을 찾는다
    (거의 모든 회사가 같은 서식을 씀). 헤더 셀 텍스트는 줄바꿈으로
    "매출\n유형"처럼 쪼개져 들어오는 경우가 있어, 공백/줄바꿈을 모두 제거한
    뒤 부분일치를 검사한다.

    표준 서식과 정확히 안 맞는 회사를 위해, "매출 및 수주상황"/"매출실적"
    제목이 먼저 나온 뒤 등장하는, "제NN기" 형태의 기수 컬럼을 가진 첫 번째
    표를 fallback으로 채택한다.
    """
    pending_unit = None
    after_heading = False
    fallback_table = None
    fallback_unit = None
    loose_fallback_table = None
    loose_fallback_unit = None

    heading_hints_norm = tuple(_normalize_ws(h) for h in _REVENUE_SECTION_HEADING_HINTS)

    for el in root.iter():
        local = _local_tag(el.tag).lower()
        if local == 'table':
            rows = _table_rows(el)
            if not rows:
                continue
            header_texts = [_cell_text(td) for td in _row_cells(rows[0])]
            header_norm = _normalize_ws(' '.join(header_texts))
            if '매출유형' in header_norm and '품목' in header_norm:
                return el, pending_unit
            has_period_col = any(_PERIOD_HEADER_RE.search(t) for t in header_texts)
            if after_heading and fallback_table is None and has_period_col:
                fallback_table = el
                fallback_unit = pending_unit
            if loose_fallback_table is None and has_period_col and '부문' in header_norm:
                loose_fallback_table = el
                loose_fallback_unit = pending_unit
        else:
            # 제목/단위 텍스트가 el.text가 아니라 el.tail(자식 태그 뒤 텍스트)에
            # 들어있는 경우도 있어 둘 다 검사한다.
            for txt in ((el.text or '').strip(), (el.tail or '').strip()):
                if not txt:
                    continue
                txt_norm = _normalize_ws(txt)
                if any(h in txt_norm for h in heading_hints_norm):
                    after_heading = True
                if '단위' in txt:
                    m = _UNIT_TEXT_RE.search(txt)
                    if m:
                        pending_unit = m.group(1)
    if fallback_table is not None:
        return fallback_table, fallback_unit
    return loose_fallback_table, loose_fallback_unit


def _table_diagnostic(table, max_rows=6):
    """진단용: 매칭된 표의 헤더/데이터 행 원문을 그대로 담아 반환한다.
    표는 찾았는데 _parse_revenue_table이 부문을 하나도 못 뽑아냈을 때,
    실제 표 모양(컬럼 구성·기수 표기 등)을 확인하기 위함."""
    rows = _table_rows(table)
    if not rows:
        return {'header': [], 'rows': [], 'period_col': None}
    all_row_texts = [[_cell_text(td) for td in _row_cells(tr)] for tr in rows[:1 + max_rows]]
    period_row_idx = next(
        (i for i, texts in enumerate(all_row_texts) if any(_PERIOD_HEADER_RE.search(t) for t in texts)),
        None,
    )
    header_texts = all_row_texts[period_row_idx] if period_row_idx is not None else (all_row_texts[0] if all_row_texts else [])
    period_col = (
        next((i for i, t in enumerate(header_texts) if _PERIOD_HEADER_RE.search(t)), None)
        if period_row_idx is not None else None
    )
    return {'header_rows': all_row_texts, 'header_row_idx': period_row_idx, 'period_col': period_col}


def _parse_revenue_table(table):
    """매출실적 표의 TABLE 엘리먼트에서 {부문명: 당기 매출액} 을 추출한다.

    부문 칸은 같은 부문에 속한 매출유형/품목 행들에 걸쳐 ROWSPAN으로 병합돼
    있는 경우가 많아, 해당 칸이 없는(병합된) 행은 ElementTree 상에서 그
    칸의 <TD> 자체가 통째로 빠져 있다. 따라서 행의 칸 수가 헤더보다 적으면
    그만큼 앞쪽(부문 쪽)에 빈 칸을 채워 넣어 칼럼 위치를 맞추고, 빈 부문
    칸은 바로 위 행에서 마지막으로 본 부문명을 그대로 사용한다(rowspan
    carry-forward). 같은 부문에 여러 행(품목별)이 있으면 합산한다.

    헤더의 "제NN기" 칼럼 중 가장 왼쪽(=당기, 가장 최근 연도) 칼럼 값만
    사용한다 — 비교공시 연도(전기/전전기)는 그 연도의 사업보고서를 따로
    조회할 때 해당 연도 문서의 당기 칼럼에서 가져오는 것이 더 정확하다.

    "기타(부문간 내부거래 제거 등)"처럼 음수(△ 표기)로 나오는 조정 행도
    그대로 부문에 포함시킨다 — 이를 빼고 합치면 실제 총매출(=각 부문
    합계 - 내부거래)보다 부풀려진 값이 나오므로, DCF에서 각 부문을 따로
    키워 합산할 때도 이 조정행이 같이 성장해야 총계가 어긋나지 않는다.
    합계/총계/소계처럼 이미 다른 행들의 합산인 행만 제외한다.
    """
    rows = _table_rows(table)
    if not rows:
        return {}

    # 헤더가 "사업부문/매출유형/품목"과 "제NN기" 기수 표기가 한 행에 같이
    # 있는 경우가 많지만, 회사에 따라 헤더가 두 행으로 나뉘어(예: 1행
    # "매출유형|품목", 2행 "제19기|제18기") "제NN기" 표기가 rows[0]이 아닌
    # 다음 행에 나오는 경우도 있다. rows[0]만 보면 이런 회사는 period_col을
    # 못 찾아 통째로 빈 결과가 나오므로, "제NN기" 패턴이 처음 등장하는 행을
    # 헤더로 채택한다.
    header_row_idx = None
    header_cells = None
    header_texts = None
    for i, tr in enumerate(rows):
        cells = _row_cells(tr)
        texts = [_cell_text(td) for td in cells]
        if any(_PERIOD_HEADER_RE.search(t) for t in texts):
            header_row_idx = i
            header_cells = cells
            header_texts = texts
            break
    if header_row_idx is None:
        return {}
    period_col = next(i for i, t in enumerate(header_texts) if _PERIOD_HEADER_RE.search(t))

    # 일부 회사(예: 서진시스템)는 "제NN기" 헤더 칸 하나가 실제로는 "매출액"/
    # "비중" 두 개의 실데이터 칼럼을 colspan으로 합친 표제일 뿐이고, 바로
    # 다음 행에 그 하위 칼럼명("매출액"/"비중")이 따로 나온다. 이 경우
    # period_col을 그대로 쓰면 데이터 행과 칼럼 수가 안 맞아(실제 칼럼이 더
    # 많음) 전부 스킵된다. 다음 행이 그런 하위 헤더로 보이면, 실데이터
    # 기준 칼럼 위치를 다시 계산한다.
    division_col = 0
    total_columns = len(header_cells)
    data_start_idx = header_row_idx + 1
    sub_row = rows[header_row_idx + 1] if header_row_idx + 1 < len(rows) else None
    if sub_row is not None:
        sub_texts = [_cell_text(td) for td in _row_cells(sub_row)]
        if any(t.strip() == '비중' for t in sub_texts):
            fixed_col_count = period_col
            rev_offset = next((i for i, t in enumerate(sub_texts) if '매출액' in t), 0)
            period_col = fixed_col_count + rev_offset
            total_columns = fixed_col_count + len(sub_texts)
            data_start_idx = header_row_idx + 2
            division_idx = next((i for i, t in enumerate(header_texts[:fixed_col_count]) if '품목' in t), None)
            if division_idx is not None:
                division_col = division_idx

    totals = {}
    last_division = None
    for tr in rows[data_start_idx:]:
        cells = _row_cells(tr)
        if not cells:
            continue
        texts = [_cell_text(td) for td in cells]
        n_missing = total_columns - len(cells)
        if n_missing > 0:
            # 칸이 모자란 이유는 두 가지다: (1) 부문 칸 자체가 ROWSPAN으로 위
            # 행과 병합돼 빠진 경우(부문명을 포함해 전부 모자람) -> 앞쪽에
            # 빈 칸을 채워 이전 행의 부문명을 그대로 carry-forward 한다.
            # (2) "기타"/"합계" 행처럼 부문명은 있지만 매출유형/품목 칸이
            # COLSPAN으로 합쳐지거나 비어서 없는 경우 -> 부문명 칸은 그대로
            # 두고 그 뒤(매출유형/품목 위치)에 빈 칸을 채워야 당기 매출액
            # 칸(period_col) 위치가 어긋나지 않는다.
            trailing_count = total_columns - period_col
            leading_actual = len(cells) - trailing_count
            if division_col == 0 and leading_actual >= 1:
                texts = texts[:1] + [''] * n_missing + texts[1:]
            else:
                texts = [''] * n_missing + texts
        elif n_missing < 0:
            continue

        division = texts[division_col].strip() or last_division
        if not division:
            continue
        last_division = division
        if period_col >= len(texts):
            continue

        val_text = texts[period_col].replace(',', '').replace(' ', '')
        is_negative = val_text.startswith('△') or val_text.startswith('-')
        val_text = val_text.lstrip('△-')
        if not val_text:
            continue
        try:
            val = float(val_text)
        except ValueError:
            continue
        if is_negative:
            val = -val
        if any(k in _normalize_ws(division) for k in _SEGMENT_ROW_EXCLUDE):
            continue
        totals[division] = totals.get(division, 0.0) + val
    return totals


def get_business_segments(corp_code, year, api_key=None, report_type='11011', fs_div='CFS', debug=None):
    """사업보고서 원문(document.xml)에서 "4. 매출 및 수주상황 > 가. 매출실적"
    표를 직접 파싱해 사업부문별 매출액을 추출한다.

    재무제표 XBRL 주석(IFRS 8 영업부문 정보)을 먼저 시도해봤지만, 그 주석은
    DX/DS 같은 사업부문이 아니라 종속회사 단위로 차원화된 경우가 많아(연결
    대상 법인별 매출 disclosure) 사용자가 원하는 "매출 및 수주상황"의 사업
    부문 구분과는 다른 결과를 준다는 것이 확인되어, 사업보고서 본문 표를
    직접 읽는 방식으로 변경했다. DART OpenAPI에는 이 표를 구조화된 형태로
    주는 엔드포인트가 없어, 원문 문서(document.xml)를 받아 표를 파싱한다.

    실패하거나 부문이 1개뿐이면(=의미있는 분할이 없으면) 빈 dict를 반환한다.
    성공 시 {부문명: 매출액(원)} 형태로 반환한다.

    debug에 dict를 넘기면 어느 단계에서 실패했는지를 채워준다.
    """
    if debug is None:
        debug = {}
    debug['stage'] = None
    try:
        key = get_dart_api_key(api_key)
        if not key:
            debug['stage'] = 'no_api_key'
            return {}

        rcept_no = _find_rcept_no(corp_code, year, key, report_type)
        debug['rcept_no'] = rcept_no
        if not rcept_no:
            debug['stage'] = 'rcept_no_not_found'
            return {}

        url = f"{BASE_URL}/document.xml"
        params = {"crtfc_key": key, "rcept_no": rcept_no}
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()

        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            debug['stage'] = 'not_a_zip'
            debug['detail'] = resp.content[:300].decode('utf-8', errors='replace')
            return {}

        xml_filenames = [n for n in zf.namelist() if n.lower().endswith('.xml')]
        debug['document_filenames'] = xml_filenames
        if not xml_filenames:
            debug['stage'] = 'zip_has_no_xml'
            return {}

        for fname in xml_filenames:
            raw = zf.read(fname)
            try:
                root = _lenient_parse_xml(raw)
            except ET.ParseError as e:
                debug.setdefault('parse_errors', []).append(f"{fname}: {e}")
                continue

            table, unit_text = _find_revenue_table_with_unit(root)
            if table is None:
                debug.setdefault('sample_headers', {})[fname] = _sample_table_headers(root)
                continue
            debug['unit_text'] = unit_text
            debug['source_file'] = fname
            multiplier = _unit_text_to_multiplier(unit_text) or 1e8  # 표기 못 찾으면 관행상 억원 단위로 간주
            segments = _parse_revenue_table(table)
            debug['raw_segments'] = segments
            debug['table_diagnostic'] = _table_diagnostic(table)
            if len(segments) >= 2:
                debug['stage'] = 'ok'
                return {name: val * multiplier for name, val in segments.items()}
            debug.setdefault('matched_table_diagnostics', {})[fname] = _table_diagnostic(table)

        debug['stage'] = 'no_matching_table_found'
        return {}
    except Exception as e:
        debug['stage'] = 'exception'
        debug['detail'] = f"{type(e).__name__}: {e}"
        return {}


_COST_NATURE_HEADING_KEYWORDS = ('비용의', '성격별', '분류')
_SGA_HEADING_KEYWORDS = ('판매비와관리비',)
_COST_NATURE_ROW_EXCLUDE = ('합계', '총계', '소계', '성격별비용')
_SGA_ROW_EXCLUDE = ('합계', '총계', '소계')


def _find_note_table_current_period(root, heading_keywords, period_marker='당기'):
    """주석 표("비용의 성격별 분류", "판매비와관리비" 등)에서 당기(현재 연도) 표를
    찾는다. 매출실적표처럼 기수가 한 표 안에 컬럼으로 나란히 있는 게 아니라,
    "당기"/"전기"/"전전기" 표가 각각 별도의 TABLE로 따로 나온다. 따라서 제목
    문단이 등장한 뒤 "당기" 문단이 나오고, 그 다음 처음 만나는 표를 채택한다
    (전기/전전기 표는 건너뛴다 -- 비교연도는 그 연도 사업보고서를 따로 조회할
    때 당기 표에서 가져오는 것이 더 정확하다).

    DART XML에서 제목이 여러 요소에 쪼개져 있는 경우("비용의"/<b>"성격별 분류"</b>)를
    처리하기 위해, 최근 N자 롤링 버퍼에 키워드가 모두 나타나는지 확인한다."""
    pending_unit = None
    after_heading = False
    seen_period_marker = False
    heading_keywords_norm = tuple(_normalize_ws(h) for h in heading_keywords)

    # 최근 텍스트를 누적해 단일 노드에 쪼개진 제목도 검출
    text_buf = ''
    BUF_LIMIT = 200  # 제목 키워드 탐색 윈도우 (문자 수)

    for el in root.iter():
        local = _local_tag(el.tag).lower()
        if local == 'table':
            if after_heading and seen_period_marker:
                return el, pending_unit
            # "당기" 마커를 찾지 못한 상태에서 표를 만나면:
            # 표 안에 "전기"만 있고 "당기"가 없는 경우 → 이 표는 전기 표일 수 있어 건너뜀.
            # 표 안에 아무 기수 구분이 없는 경우 → 첫 번째 표를 당기로 간주(폴백).
            # 판단 방법: 표 바로 앞 최근 버퍼에 "전기" 단독 언급이 있으면 전기표로 스킵,
            # 아무것도 없으면 폴백으로 채택.
            if after_heading and not seen_period_marker:
                if '전기' not in text_buf:
                    return el, pending_unit  # 폴백: 기수 표지 없는 첫 표 = 당기
            # 표 경계에서 버퍼 초기화 (다음 표의 제목이 이전 표 뒤의 텍스트와 섞이지 않도록)
            text_buf = ''
        else:
            for txt in ((el.text or '').strip(), (el.tail or '').strip()):
                if not txt:
                    continue
                txt_norm = _normalize_ws(txt)
                text_buf = (text_buf + ' ' + txt_norm)[-BUF_LIMIT:]

                # 제목 키워드 검사: 단일 노드 OR 누적 버퍼에서 모두 발견
                if all(h in txt_norm for h in heading_keywords_norm) or \
                        all(h in text_buf for h in heading_keywords_norm):
                    after_heading = True
                    seen_period_marker = False
                    text_buf = ''  # 제목 찾은 후 버퍼 초기화

                # "당기" 마커는 단독 텍스트(≤30자)에서만 인정
                # ("당기순이익", "당기법인세" 같은 본문 텍스트의 오탐 방지)
                if after_heading and not seen_period_marker and period_marker in txt_norm \
                        and len(txt_norm) <= 30:
                    seen_period_marker = True
                if '단위' in txt:
                    m = _UNIT_TEXT_RE.search(txt)
                    if m:
                        pending_unit = m.group(1)
    return None, None


def _parse_note_amount_table(table, exclude_keywords):
    """주석 표에서 {항목명: 금액}을 추출한다.

    두 가지 구조를 처리한다:
    A) 단일 금액 컬럼: [항목명, 금액] — 마지막 칸 = 금액
    B) 다중 기간 컬럼: [항목명, 당기, 전기, ...] — 헤더에서 "당기" 컬럼 위치 찾아 사용

    헤더 행을 먼저 스캔해 "당기" 키워드 컬럼이 있으면 B형으로,
    없으면 A형(마지막 칸)으로 처리한다.
    """
    rows = _table_rows(table)
    if not rows:
        return {}

    # 헤더 행 탐색: 첫 3행 중 "당기" 텍스트를 가진 칸의 컬럼 인덱스 찾기
    amount_col = None  # None → A형(마지막 칸), int → B형(특정 컬럼)
    data_start = 0
    for hi, tr in enumerate(rows[:3]):
        cells = _row_cells(tr)
        texts = [_cell_text(c) for c in cells]
        for ci, t in enumerate(texts):
            t_n = _normalize_ws(t)
            # "당기" 단독이거나 "당기" + 연도/기수 조합 (예: "당기(제55기)")
            if '당기' in t_n and '전기' not in t_n:
                amount_col = ci
                data_start = hi + 1
                break
        if amount_col is not None:
            break

    items = {}
    for tr in rows[data_start:]:
        cells = _row_cells(tr)
        texts = [_cell_text(c) for c in cells]
        if len(texts) < 2:
            continue

        if amount_col is not None and amount_col < len(texts):
            # B형: 당기 컬럼 위치 사용
            raw_amount = texts[amount_col]
            # 항목명: amount_col보다 앞에서 가장 가까운 비어있지 않은 칸
            label = None
            for t in reversed(texts[:amount_col]):
                if t.strip():
                    label = t.strip()
                    break
            if not label and texts[0].strip():
                label = texts[0].strip()
        else:
            # A형: 마지막 칸 = 금액, 그 앞에서 가장 가까운 비어있지 않은 칸 = 항목명
            raw_amount = texts[-1]
            label = None
            for t in reversed(texts[:-1]):
                if t.strip():
                    label = t.strip()
                    break

        if not label:
            continue

        amount_text = raw_amount.replace(',', '').replace(' ', '')
        is_negative = amount_text.startswith('△') or amount_text.startswith('-')
        amount_text = amount_text.lstrip('△-')
        if not amount_text:
            continue
        try:
            val = float(amount_text)
        except ValueError:
            continue
        if is_negative:
            val = -val

        if any(k in _normalize_ws(label) for k in exclude_keywords):
            continue
        items[label] = items.get(label, 0.0) + val
    return items


def get_cost_breakdown(corp_code, year, api_key=None, report_type='11011', fs_div='CFS', debug=None):
    """사업보고서 주석 "비용의 성격별 분류"와 "판매비와관리비" 표를 파싱해
    매출원가(COGS)/판매비와관리비(SG&A)를 항목별(인건비/감가상각비/지급수수료
    등)로 쪼갠 금액을 반환한다.

    "비용의 성격별 분류" 주석은 매출원가+판관비를 합친 전체 비용을 성격별로
    보여주고, "판매비와관리비" 주석은 그중 판관비에 해당하는 부분만 항목별로
    보여준다. 두 표의 항목 이름 체계가 회사마다 다를 수 있어, 우선은 각 표를
    그대로 파싱해 반환하고 COGS/SG&A 합산·비교는 호출 측(app.py)에서 한다.

    반환: {'expense_by_nature': {항목명: 금액(원)}, 'sga_detail': {항목명: 금액(원)}}
    실패하면 해당 항목은 빈 dict가 된다.
    """
    if debug is None:
        debug = {}
    result = {'expense_by_nature': {}, 'sga_detail': {}}
    try:
        key = get_dart_api_key(api_key)
        if not key:
            debug['stage'] = 'no_api_key'
            return result

        rcept_no = _find_rcept_no(corp_code, year, key, report_type)
        debug['rcept_no'] = rcept_no
        if not rcept_no:
            debug['stage'] = 'rcept_no_not_found'
            return result

        url = f"{BASE_URL}/document.xml"
        params = {"crtfc_key": key, "rcept_no": rcept_no}
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()

        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            debug['stage'] = 'not_a_zip'
            return result

        xml_filenames = [n for n in zf.namelist() if n.lower().endswith('.xml')]
        debug['document_filenames'] = xml_filenames

        text_samples = {}
        for fname in xml_filenames:
            raw = zf.read(fname)
            try:
                root = _lenient_parse_xml(raw)
            except ET.ParseError:
                continue

            # 파일 안의 텍스트 샘플 수집 (진단용)
            file_texts = []
            for el in root.iter():
                for t in ((el.text or '').strip(), (el.tail or '').strip()):
                    if t and len(t) > 2:
                        file_texts.append(t)
            # 비용/성격/판매비 관련 텍스트만 필터
            relevant = [t for t in file_texts if any(k in t for k in ('비용', '성격', '판매비', '당기', '전기'))]
            text_samples[fname] = relevant[:30]

            if not result['expense_by_nature']:
                table, unit_text = _find_note_table_current_period(root, _COST_NATURE_HEADING_KEYWORDS)
                if table is not None:
                    mult = _unit_text_to_multiplier(unit_text) or 1e6
                    items = _parse_note_amount_table(table, _COST_NATURE_ROW_EXCLUDE)
                    if items:
                        result['expense_by_nature'] = {k: v * mult for k, v in items.items()}
                        debug['expense_by_nature_unit'] = unit_text
                        debug['expense_by_nature_source_file'] = fname

            if not result['sga_detail']:
                table, unit_text = _find_note_table_current_period(root, _SGA_HEADING_KEYWORDS)
                if table is not None:
                    mult = _unit_text_to_multiplier(unit_text) or 1e6
                    items = _parse_note_amount_table(table, _SGA_ROW_EXCLUDE)
                    if items:
                        result['sga_detail'] = {k: v * mult for k, v in items.items()}
                        debug['sga_unit'] = unit_text
                        debug['sga_source_file'] = fname

            if result['expense_by_nature'] and result['sga_detail']:
                break

        debug['stage'] = 'ok' if (result['expense_by_nature'] or result['sga_detail']) else 'no_matching_table_found'
        if debug['stage'] == 'no_matching_table_found':
            debug['text_samples_per_file'] = text_samples
        return result
    except Exception as e:
        debug['stage'] = 'exception'
        debug['detail'] = f"{type(e).__name__}: {e}"
        return result


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


def get_shares_outstanding(corp_code, year, api_key=None):
    """연도별 발행주식수(보통주)를 반환한다.

    DART stockTotqySttus API → 사업보고서 연도 기준 발행주식총수(보통주)를 조회.
    API가 실패하면 None을 반환한다.

    DART API 응답 필드:
      se: 구분 ("보통주" / "우선주" 등)
      isu_stock_totqy: 발행주식총수 (쉼표 포함 문자열)
    """
    key = get_dart_api_key(api_key)
    if not key:
        return None
    try:
        url = f"{BASE_URL}/stockTotqySttus.json"
        params = {"crtfc_key": key, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == '000':
            items = data.get('list', [])
            # 보통주 행 우선
            for item in items:
                se = (item.get('se') or '').strip()
                if '보통주' in se or not se:
                    for field in ('isu_stock_totqy', 'istc_totqy', 'distb_stock_co'):
                        qty_str = (item.get(field) or '').replace(',', '').replace(' ', '')
                        try:
                            v = int(qty_str)
                            if v > 0:
                                return v
                        except ValueError:
                            pass
    except Exception:
        pass

    # 폴백: irdsSttus API
    try:
        url2 = f"{BASE_URL}/irdsSttus.json"
        params2 = {"crtfc_key": key, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}
        resp2 = requests.get(url2, params=params2, timeout=30)
        resp2.raise_for_status()
        data2 = resp2.json()
        if data2.get('status') == '000':
            for item in data2.get('list', []):
                se = (item.get('se') or '').strip()
                if '보통주' in se or not se:
                    for field in ('isu_stock_totqy', 'istc_totqy', 'distb_stock_co'):
                        qty_str = (item.get(field) or '').replace(',', '').replace(' ', '')
                        try:
                            v = int(qty_str)
                            if v > 0:
                                return v
                        except ValueError:
                            pass
    except Exception:
        pass
    return None
