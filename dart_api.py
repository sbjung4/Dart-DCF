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


def _lookup_account(account_map_by_id, category_keys):
    """Look up account value by trying multiple possible account IDs"""
    for key in category_keys:
        if key in account_map_by_id:
            return _safe_amount(account_map_by_id[key])
    return 0.0


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
    sga = _lookup_account(is_map, ACCOUNT_MAP['sga'])
    ebit = _lookup_account(is_map, ACCOUNT_MAP['ebit'])
    pretax_income = _lookup_account(is_map, ACCOUNT_MAP['pretax_income'])
    tax_expense = _lookup_account(is_map, ACCOUNT_MAP['tax_expense'])
    net_income = _lookup_account(is_map, ACCOUNT_MAP['net_income'])
    da_is = _lookup_account(is_map, ACCOUNT_MAP['da'])

    # SGA breakdown from IS
    selling_expense = _lookup_account(is_map, ACCOUNT_MAP['selling_expense'])
    admin_expense = _lookup_account(is_map, ACCOUNT_MAP['admin_expense'])
    # If SGA is 0 but selling+admin exist, compute SGA
    if sga == 0 and (selling_expense + admin_expense) > 0:
        sga = selling_expense + admin_expense

    # Try to find D&A in CF (often listed as adjustment item)
    da_cf = _lookup_account(cf_map, ACCOUNT_MAP['da'])

    # Try individual D&A components from CF statement
    da_ppe = _lookup_account(cf_map, ACCOUNT_MAP['da_ppe'])
    da_intangible = _lookup_account(cf_map, ACCOUNT_MAP['da_intangible'])
    da_rou = _lookup_account(cf_map, ACCOUNT_MAP['da_rou'])
    da_components = da_ppe + da_intangible + da_rou

    # Best estimate: components sum > combined CF > IS
    if da_components > 0:
        da = da_components
    elif da_cf > 0:
        da = da_cf
    else:
        da = da_is

    # Extract BS metrics
    total_assets = _lookup_account(bs_map, ACCOUNT_MAP['total_assets'])
    current_assets = _lookup_account(bs_map, ACCOUNT_MAP['current_assets'])
    current_liabilities = _lookup_account(bs_map, ACCOUNT_MAP['current_liabilities'])
    cash = _lookup_account(bs_map, ACCOUNT_MAP['cash'])
    total_equity = _lookup_account(bs_map, ACCOUNT_MAP['total_equity'])
    accounts_receivable = _lookup_account(bs_map, ACCOUNT_MAP['accounts_receivable'])
    inventory = _lookup_account(bs_map, ACCOUNT_MAP['inventory'])
    accounts_payable = _lookup_account(bs_map, ACCOUNT_MAP['accounts_payable'])
    shares_outstanding = _lookup_account(bs_map, ACCOUNT_MAP['shares_outstanding'])

    # Total debt: try borrowings first, then sum short+long term
    total_debt = _lookup_account(bs_map, ACCOUNT_MAP['total_borrowings'])
    if total_debt == 0:
        short_term = _lookup_account(bs_map, ACCOUNT_MAP['short_term_borrowings'])
        long_term = _lookup_account(bs_map, ACCOUNT_MAP['long_term_borrowings'])
        total_debt = short_term + long_term

    # Extract CF metrics
    operating_cf = _lookup_account(cf_map, ACCOUNT_MAP['operating_cf'])
    investing_cf = _lookup_account(cf_map, ACCOUNT_MAP['investing_cf'])
    financing_cf = _lookup_account(cf_map, ACCOUNT_MAP['financing_cf'])
    capex_raw = _lookup_account(cf_map, ACCOUNT_MAP['capex'])
    capex = abs(capex_raw)  # CapEx is typically negative in CF

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
        },
        'balance_sheet': {
            'total_assets': total_assets,
            'current_assets': current_assets,
            'current_liabilities': current_liabilities,
            'cash': cash,
            'total_equity': total_equity,
            'total_debt': total_debt,
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
        }
    }


def get_financial_statements(corp_code, year, api_key, report_type='11011', fs_div='CFS'):
    """
    Fetch financial statements from DART API.
    report_type: 11011=사업보고서, 11012=반기보고서, 11013=1분기, 11014=3분기
    fs_div: CFS=연결, OFS=개별
    """
    key = get_dart_api_key(api_key)
    url = f"{BASE_URL}/fnlttSinglAcntAll.json"

    params = {
        "crtfc_key": key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_type,
        "fs_div": fs_div,
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get('status') == '013':
        # No data for requested fs_div; try the other
        fallback = 'OFS' if fs_div == 'CFS' else 'CFS'
        params['fs_div'] = fallback
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

    if data.get('status') not in ('000', '013'):
        if data.get('status') != '000':
            raise ValueError(f"DART API Error [{data.get('status')}]: {data.get('message', 'Unknown')}")

    items = data.get('list', [])
    if not items:
        return {'income_statement': {}, 'balance_sheet': {}, 'cash_flow': {}}

    return _extract_financials_from_items(items)


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
