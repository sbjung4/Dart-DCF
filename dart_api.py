"""
DART OpenAPI client for Korean listed company financial data.
Base URL: https://opendart.fss.or.kr/api/
"""

import os
import io
import zipfile
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DART_BASE_URL = "https://opendart.fss.or.kr/api"
CORP_CODE_CACHE = Path("corp_codes.parquet")

# ---------------------------------------------------------------------------
# Account-name → standard-category mapping
# ---------------------------------------------------------------------------
ACCOUNT_MAP = {
    # Revenue
    "ifrs-full_Revenue": "revenue",
    "dart_Revenue": "revenue",
    "ifrs_Revenue": "revenue",
    # COGS
    "ifrs-full_CostOfSales": "cogs",
    "dart_CostOfSales": "cogs",
    # SGA
    "ifrs-full_SellingGeneralAndAdministrativeExpense": "sga",
    "dart_SellingGeneralAndAdministrativeExpense": "sga",
    # EBIT / Operating income
    "ifrs-full_ProfitLossFromOperatingActivities": "ebit",
    "dart_OperatingIncomeLoss": "ebit",
    "ifrs-full_OperatingIncome": "ebit",
    # Tax
    "ifrs-full_IncomeTaxExpense": "tax_expense",
    "dart_IncomeTaxExpenseContinuingOperations": "tax_expense",
    # Net income
    "ifrs-full_ProfitLoss": "net_income",
    "dart_ProfitLoss": "net_income",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income",
    # D&A (often in cash flow statement)
    "ifrs-full_AdjustmentsForDepreciationAndAmortisationExpense": "da",
    "dart_DepreciationAndAmortization": "da",
    "ifrs-full_DepreciationAndAmortisationExpense": "da",
    # CapEx
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment": "capex",
    "dart_AcquisitionOfPropertyPlantAndEquipment": "capex",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsAndOtherLonglivedAssets": "capex",
    # Balance sheet
    "ifrs-full_Assets": "total_assets",
    "dart_Assets": "total_assets",
    "ifrs-full_CurrentAssets": "current_assets",
    "dart_CurrentAssets": "current_assets",
    "ifrs-full_CurrentLiabilities": "current_liabilities",
    "dart_CurrentLiabilities": "current_liabilities",
    "ifrs-full_CashAndCashEquivalents": "cash",
    "dart_CashAndCashEquivalents": "cash",
    "ifrs-full_Equity": "total_equity",
    "dart_Equity": "total_equity",
    "ifrs-full_EquityAttributableToOwnersOfParent": "total_equity",
    # Debt
    "ifrs-full_ShorttermBorrowings": "short_term_debt",
    "dart_ShorttermBorrowings": "short_term_debt",
    "ifrs-full_CurrentPortionOfLongtermBorrowings": "short_term_debt",
    "ifrs-full_LongtermBorrowings": "long_term_debt",
    "dart_LongtermBorrowings": "long_term_debt",
    "ifrs-full_BondsIssued": "long_term_debt",
}

# Korean label fallbacks (partial match)
KR_LABEL_MAP = {
    "매출액": "revenue",
    "매출": "revenue",
    "매출원가": "cogs",
    "판매비와관리비": "sga",
    "판관비": "sga",
    "영업이익": "ebit",
    "법인세비용": "tax_expense",
    "당기순이익": "net_income",
    "감가상각비": "da",
    "감가상각": "da",
    "자산총계": "total_assets",
    "유동자산": "current_assets",
    "유동부채": "current_liabilities",
    "현금및현금성자산": "cash",
    "자본총계": "total_equity",
    "단기차입금": "short_term_debt",
    "장기차입금": "long_term_debt",
    "사채": "long_term_debt",
    "유형자산의취득": "capex",
    "유형자산취득": "capex",
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key:
        raise ValueError("DART_API_KEY가 설정되지 않았습니다.")
    return key


def _get(endpoint: str, params: dict) -> dict:
    """Execute a GET request and return parsed JSON."""
    params["crtfc_key"] = _get_api_key()
    url = f"{DART_BASE_URL}/{endpoint}"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") not in ("000", "013"):  # 013 = no data
        msg = data.get("message", "알 수 없는 오류")
        raise RuntimeError(f"DART API 오류 ({data.get('status')}): {msg}")
    return data


def _safe_float(val) -> Optional[float]:
    """Convert a string amount (possibly with commas / minus sign) to float."""
    if val is None or str(val).strip() in ("", "-", "N/A"):
        return None
    try:
        return float(str(val).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _map_account(account_id: str, account_nm: str) -> Optional[str]:
    """Return standard category for an account, or None if unknown."""
    # Try exact account_id match first
    if account_id in ACCOUNT_MAP:
        return ACCOUNT_MAP[account_id]
    # Try Korean label match (exact)
    clean_nm = account_nm.strip().replace(" ", "")
    if clean_nm in KR_LABEL_MAP:
        return KR_LABEL_MAP[clean_nm]
    # Try partial Korean label match
    for kr, cat in KR_LABEL_MAP.items():
        if kr in clean_nm:
            return cat
    return None


# ---------------------------------------------------------------------------
# Corp code list
# ---------------------------------------------------------------------------

def get_corp_code_list(force_refresh: bool = False) -> pd.DataFrame:
    """
    Download and cache the full corporation code list from DART.
    Returns DataFrame with columns: corp_code, corp_name, stock_code, modify_date.
    Cached locally as corp_codes.parquet.
    """
    cache_path = CORP_CODE_CACHE
    if not force_refresh and cache_path.exists():
        try:
            df = pd.read_parquet(cache_path)
            logger.info("Corp codes loaded from cache (%d rows)", len(df))
            return df
        except Exception:
            pass  # re-download on corrupt cache

    api_key = _get_api_key()
    url = f"{DART_BASE_URL}/corpCode.xml"
    resp = requests.get(url, params={"crtfc_key": api_key}, timeout=60)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_filename = [n for n in zf.namelist() if n.endswith(".xml")][0]
        xml_bytes = zf.read(xml_filename)

    root = ET.fromstring(xml_bytes)
    records = []
    for item in root.findall(".//list"):
        records.append(
            {
                "corp_code": (item.findtext("corp_code") or "").strip(),
                "corp_name": (item.findtext("corp_name") or "").strip(),
                "stock_code": (item.findtext("stock_code") or "").strip(),
                "modify_date": (item.findtext("modify_date") or "").strip(),
            }
        )
    df = pd.DataFrame(records)
    df.to_parquet(cache_path, index=False)
    logger.info("Corp codes downloaded and cached (%d rows)", len(df))
    return df


# ---------------------------------------------------------------------------
# Company search and info
# ---------------------------------------------------------------------------

def search_company(keyword: str) -> list[dict]:
    """
    Search for companies matching keyword (name or stock code).
    Returns list of {corp_name, corp_code, stock_code}.
    """
    try:
        df = get_corp_code_list()
    except Exception as e:
        raise RuntimeError(f"기업 목록 로드 실패: {e}") from e

    keyword = keyword.strip()
    if not keyword:
        return []

    # Filter by name or stock code
    mask_name = df["corp_name"].str.contains(keyword, case=False, na=False)
    mask_code = df["stock_code"].str.strip() == keyword
    result = df[mask_name | mask_code].copy()

    # Only listed companies (stock_code not empty)
    result = result[result["stock_code"].str.strip() != ""]
    result = result.sort_values("corp_name").head(50)

    return result[["corp_name", "corp_code", "stock_code"]].to_dict("records")


def get_company_info(corp_code: str) -> dict:
    """
    Fetch company metadata from DART.
    Returns dict with corp metadata fields.
    """
    data = _get("company.json", {"corp_code": corp_code})
    return data


# ---------------------------------------------------------------------------
# Financial statement parsing
# ---------------------------------------------------------------------------

def _parse_fs_items(items: list[dict], fs_div: str) -> dict:
    """
    Parse a list of financial statement line items into a dict of
    {category: amount} for the given fs_div ('IS', 'BS', 'CF').
    Returns amounts in KRW (원).
    """
    result: dict[str, float] = {}

    for item in items:
        if item.get("fs_div") != fs_div:
            continue
        account_id = str(item.get("account_id") or "").strip()
        account_nm = str(item.get("account_nm") or "").strip()
        category = _map_account(account_id, account_nm)
        if category is None:
            # Check for D&A and CapEx via Korean label even in CF
            clean = account_nm.replace(" ", "")
            if "감가상각" in clean:
                category = "da"
            elif "유형자산" in clean and "취득" in clean:
                category = "capex"
            else:
                continue

        # Prefer thstrm (current year) amount
        amt = _safe_float(item.get("thstrm_amount"))
        if amt is None:
            amt = _safe_float(item.get("thstrm_add_amount"))
        if amt is None:
            continue

        # Some categories accumulate (e.g. multiple debt lines)
        if category in ("short_term_debt", "long_term_debt"):
            result[category] = result.get(category, 0.0) + abs(amt)
        elif category not in result:
            result[category] = amt

    # Derive total_debt
    st = result.get("short_term_debt", 0.0)
    lt = result.get("long_term_debt", 0.0)
    if st or lt:
        result["total_debt"] = st + lt

    return result


def _fetch_single_fs(corp_code: str, year: str, reprt_code: str) -> list[dict]:
    """Fetch single-company all-account financial statement items."""
    data = _get(
        "fnlttSinglAcntAll.json",
        {
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": reprt_code,
            "fs_div": "OFS",  # individual; try CFS (consolidated) if empty
        },
    )
    items = data.get("list", [])
    if not items:
        # Try consolidated
        data2 = _get(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": year,
                "reprt_code": reprt_code,
                "fs_div": "CFS",
            },
        )
        items = data2.get("list", [])
    return items


def get_financial_statements(
    corp_code: str, year: int, report_type: str = "11011"
) -> dict:
    """
    Fetch and parse financial statements for a given corp and year.

    report_type:
        11011 = 사업보고서 (annual)
        11012 = 반기보고서 (semi-annual)
        11013 = 1분기 (Q1)
        11014 = 3분기 (Q3)

    Returns dict:
        {
          'income_statement': dict of {category: amount},
          'balance_sheet':    dict of {category: amount},
          'cash_flow':        dict of {category: amount},
          'raw_items':        list of raw API dicts,
          'year': year,
        }
    """
    items = _fetch_single_fs(corp_code, str(year), report_type)
    if not items:
        raise RuntimeError(
            f"{year}년 재무제표 데이터를 찾을 수 없습니다. (corp_code={corp_code})"
        )

    is_data = _parse_fs_items(items, "IS")
    bs_data = _parse_fs_items(items, "BS")
    cf_data = _parse_fs_items(items, "CF")

    # If D&A not in CF, try IS items (some companies put it there)
    if "da" not in cf_data:
        if "da" in is_data:
            cf_data["da"] = is_data.pop("da")

    # CapEx is typically negative in CF; store as positive
    if "capex" in cf_data:
        cf_data["capex"] = abs(cf_data["capex"])

    return {
        "income_statement": is_data,
        "balance_sheet": bs_data,
        "cash_flow": cf_data,
        "raw_items": items,
        "year": year,
    }


def get_multi_year_financials(
    corp_code: str, years: list[int], report_type: str = "11011"
) -> dict[int, dict]:
    """
    Fetch financial statements for multiple years.
    Returns {year: fs_dict}.
    """
    result = {}
    for yr in years:
        try:
            result[yr] = get_financial_statements(corp_code, yr, report_type)
        except Exception as e:
            logger.warning("연도 %s 재무제표 로드 실패: %s", yr, e)
    return result


# ---------------------------------------------------------------------------
# Business segments
# ---------------------------------------------------------------------------

def get_business_segments(corp_code: str, year: int) -> list[dict]:
    """
    Attempt to retrieve segment revenue data.
    DART does not have a dedicated segment endpoint; we query the note disclosures
    via fnlttSinglAcntAll and look for segment-related accounts.
    Returns list of {segment_name, revenue}.
    """
    try:
        items = _fetch_single_fs(corp_code, str(year), "11011")
    except Exception:
        return []

    segments = []
    for item in items:
        nm = str(item.get("account_nm") or "")
        if "부문" in nm and "매출" in nm:
            amt = _safe_float(item.get("thstrm_amount"))
            if amt and amt > 0:
                segments.append({"segment_name": nm.strip(), "revenue": amt})
    return segments


# ---------------------------------------------------------------------------
# Convenience: build historical summary DataFrame
# ---------------------------------------------------------------------------

def build_historical_summary(
    corp_code: str, years: list[int]
) -> pd.DataFrame:
    """
    Return a tidy DataFrame with key financials for each requested year.
    Columns: year, revenue, cogs, gross_profit, sga, ebit, net_income,
             da, capex, total_assets, current_assets, current_liabilities,
             cash, total_debt, total_equity, ebitda, nwc
    """
    multi = get_multi_year_financials(corp_code, years)
    rows = []
    for yr, fs in sorted(multi.items()):
        is_ = fs["income_statement"]
        bs = fs["balance_sheet"]
        cf = fs["cash_flow"]

        revenue = is_.get("revenue", 0) or 0
        cogs = is_.get("cogs", 0) or 0
        sga = is_.get("sga", 0) or 0
        ebit = is_.get("ebit", 0) or 0
        net_income = is_.get("net_income", 0) or 0
        tax_expense = is_.get("tax_expense", 0) or 0
        da = cf.get("da", 0) or 0
        capex = cf.get("capex", 0) or 0
        total_assets = bs.get("total_assets", 0) or 0
        current_assets = bs.get("current_assets", 0) or 0
        current_liabilities = bs.get("current_liabilities", 0) or 0
        cash = bs.get("cash", 0) or 0
        total_debt = bs.get("total_debt", 0) or 0
        total_equity = bs.get("total_equity", 0) or 0

        gross_profit = revenue - cogs if revenue else 0
        ebitda = ebit + da
        nwc = current_assets - current_liabilities
        pretax_income = net_income + tax_expense
        tax_rate = (tax_expense / pretax_income) if pretax_income else 0.0

        rows.append(
            {
                "year": yr,
                "revenue": revenue,
                "cogs": cogs,
                "gross_profit": gross_profit,
                "sga": sga,
                "ebit": ebit,
                "ebitda": ebitda,
                "net_income": net_income,
                "tax_expense": tax_expense,
                "tax_rate": tax_rate,
                "da": da,
                "capex": capex,
                "total_assets": total_assets,
                "current_assets": current_assets,
                "current_liabilities": current_liabilities,
                "cash": cash,
                "total_debt": total_debt,
                "total_equity": total_equity,
                "nwc": nwc,
            }
        )
    return pd.DataFrame(rows)
