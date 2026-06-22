"""
DCF 모델 엑셀 내보내기.

사용자가 제공한 참고 양식(Control / DCF / WACC / FS / Revenue / COGS / SG&A /
D&A CAPEX / NWC / Debt / Tax 시트 구성)을 그대로 따라가되, CB(전환사채) 전환
케이스 분기와 회사 전용 raw 데이터 시트는 범용 양식이 아니므로 제외한다.

핵심 구조: 모든 가정 입력값은 "Control" 시트 한 곳에 모여 있고, 나머지 모든
시트(Revenue/COGS/SG&A/D&A CAPEX/NWC/Debt/WACC/Tax/FS/DCF)는 Control 시트의
셀을 수식으로 참조한다. 즉 Control 시트의 숫자만 바꾸면 뒤의 모든 시트와
DCF 결과가 자동으로 재계산된다.

과거 재무제표는 DART에서 받아온 그대로 BS/IS/CS 세 시트로 나눠 전부 싣는다
(참고 양식의 회사 전용 "해농_BS/IS/CS" 시트를 범용화한 것).

주의: 현재 계산 엔진(dcf.py)의 FCFF/DCF 자체는 무차입 기준(unlevered)이라
차입금/이자/배당 가정이 FCFF 값을 바꾸지는 않는다. 이 가정들은 추정
재무상태표(BS)·현금흐름표(CS)를 구성하기 위해서만 쓰인다.
"""

import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
SUBHEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
INPUT_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
GRAY_FILL = PatternFill(start_color="DCDCDC", end_color="DCDCDC", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
BOLD = Font(bold=True)
THIN = Side(style="thin", color="BFBFBF")
THIN_DARK = Side(style="thin", color="808080")
THICK = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
ROW_BORDER = Border(left=THIN_DARK, right=THIN_DARK, top=THIN_DARK, bottom=THIN_DARK)
TOTAL_BORDER = Border(left=THIN_DARK, right=THIN_DARK, top=THIN_DARK, bottom=THICK)
NUM_FMT = '#,##0.0'
PCT_FMT = '0.0%'
ACC_FMT = '#,##0_);(#,##0);-_) '
ACTUAL_FMT = '#"A"'
ESTIMATE_FMT = '#"E"'
SUBTOTAL_MARKS = ('Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ')
SUBTOTAL_SUFFIXES = ('총계', '총액')
SUBTOTAL_KEYWORDS = ('순이익', '순손실', '영업이익', '영업손실')


def _is_subtotal_label(name):
    if not name:
        return False
    if name.lstrip().startswith(SUBTOTAL_MARKS):
        return True
    if name.rstrip().endswith(SUBTOTAL_SUFFIXES):
        return True
    return any(kw in name for kw in SUBTOTAL_KEYWORDS)


def _title(ws, text, row=2, col=2, span=8):
    cell = ws.cell(row=row, column=col, value=text)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    for c in range(col, col + span):
        ws.cell(row=row, column=c).fill = HEADER_FILL


def _year_header(ws, row, col0, years, fill=SUBHEADER_FILL):
    for i, yr in enumerate(years):
        cell = ws.cell(row=row, column=col0 + i, value=yr)
        cell.font = BOLD
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = BORDER


def _label(ws, row, col, text, bold=False):
    cell = ws.cell(row=row, column=col, value=text)
    if bold:
        cell.font = BOLD
    return cell


def _input_row(ws, row, col0, n, values, fmt=NUM_FMT):
    for i in range(n):
        v = values[i] if i < len(values) else (values[-1] if values else 0)
        cell = ws.cell(row=row, column=col0 + i, value=v)
        cell.number_format = fmt
        cell.fill = INPUT_FILL


def _col_widths(ws, ncols, width=14, start=2):
    for c in range(start, start + ncols):
        ws.column_dimensions[get_column_letter(c)].width = width


def build_excel_workbook(company_name, hist, asmp, fcff_df, pv_fcff_df,
                          wacc, tgr, pv_tv, ev, net_debt, eq_val, shares,
                          price_per_share, base_year, financial_data=None):
    wb = Workbook()
    wb.remove(wb.active)

    n = int(asmp.get('projection_years', 5))
    proj_years = [base_year + i + 1 for i in range(n)]
    hist_years = sorted(hist['revenue'].keys())

    # ════════════════════════════════════════════════════════════════
    # 0. Control — 모든 가정 입력값이 모이는 시트
    # ════════════════════════════════════════════════════════════════
    ctrl = wb.create_sheet("Control")
    _title(ctrl, f"{company_name} — Control Panel (가정 입력)", span=2 + n)
    cr = {}  # control row registry

    r = 4
    _label(ctrl, r, 2, "1. 매출 YoY 성장률(%)", bold=True)
    _year_header(ctrl, r + 1, 3, proj_years)
    growth_rates = asmp.get('revenue_total', {}).get('growth_rates', [5.0] * n)
    _input_row(ctrl, r + 2, 3, n, [g / 100.0 for g in growth_rates], PCT_FMT)
    cr['rev_growth'] = r + 2
    r += 4

    cogs_method = asmp.get('cogs_method', 'pct_revenue')
    sga_method = asmp.get('sga_method', 'pct_revenue')
    _label(ctrl, r, 2, f"2. 매출원가 가정 ({'매출대비%' if cogs_method=='pct_revenue' else 'YoY성장률%'})", bold=True)
    if cogs_method == 'pct_revenue':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('cogs_pct', 0) / 100.0] * n, PCT_FMT)
    else:
        _input_row(ctrl, r + 1, 3, n, [g / 100.0 for g in asmp.get('cogs_growth', [3.0] * n)], PCT_FMT)
    cr['cogs'] = r + 1
    r += 3

    _label(ctrl, r, 2, f"3. 판관비 가정 ({'매출대비%' if sga_method=='pct_revenue' else 'YoY성장률%'})", bold=True)
    if sga_method == 'pct_revenue':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('sga_pct', 0) / 100.0] * n, PCT_FMT)
    else:
        _input_row(ctrl, r + 1, 3, n, [g / 100.0 for g in asmp.get('sga_growth', [3.0] * n)], PCT_FMT)
    cr['sga'] = r + 1
    r += 3

    da_method = asmp.get('da_method', 'pct_revenue')
    da_fmt = PCT_FMT if da_method == 'pct_revenue' else NUM_FMT
    _label(ctrl, r, 2, f"4. D&A 가정 ({da_method})", bold=True)
    if da_method == 'pct_revenue':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('da_pct', 0) / 100.0] * n, PCT_FMT)
    elif da_method == 'fixed':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('da_fixed', hist['da'].get(base_year, 0)) / 1e6] * n, NUM_FMT)
    else:
        _input_row(ctrl, r + 1, 3, n, [g / 100.0 for g in asmp.get('da_growth', [3.0] * n)], PCT_FMT)
    cr['da'] = r + 1
    r += 3

    capex_method = asmp.get('capex_method', 'equal_da')
    _label(ctrl, r, 2, f"5. CapEx 가정 ({capex_method})", bold=True)
    if capex_method == 'pct_revenue':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('capex_pct', 0) / 100.0] * n, PCT_FMT)
    elif capex_method == 'fixed':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('capex_fixed', hist['capex'].get(base_year, 0)) / 1e6] * n, NUM_FMT)
    else:
        _input_row(ctrl, r + 1, 3, n, [0.0] * n, NUM_FMT)  # equal_da: D&A row referenced directly downstream
    cr['capex'] = r + 1
    r += 2
    _label(ctrl, r, 2, "신규투자 CapEx (백만원)")
    new_invest_list = asmp.get('new_invest_capex_list', [0.0] * n)
    _input_row(ctrl, r, 3, n, [v * 100 for v in new_invest_list], NUM_FMT)
    cr['new_invest'] = r
    r += 2

    nwc_method = asmp.get('nwc_method', 'pct_revenue')
    if nwc_method == 'turnover':
        nwc_method = 'pct_revenue'
    _label(ctrl, r, 2, f"6. NWC 가정 ({nwc_method})", bold=True)
    if nwc_method == 'pct_revenue':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('nwc_pct', 0) / 100.0] * n, PCT_FMT)
    else:
        _input_row(ctrl, r + 1, 3, n, [asmp.get('nwc_fixed', 0) / 1e6] * n, NUM_FMT)
    cr['nwc'] = r + 1
    r += 3

    _label(ctrl, r, 2, "7. 차입금 기말잔액 (백만원)", bold=True)
    debt_list = asmp.get('debt_balance_list', [hist['total_debt'].get(base_year, 0) / 1e8] * n)
    _input_row(ctrl, r + 1, 3, n, [v * 100 for v in debt_list], NUM_FMT)
    cr['debt_balance'] = r + 1
    r += 3

    _label(ctrl, r, 2, "8. 적용 이자율(%)", bold=True)
    interest_rate = asmp.get('interest_rate', 4.0)
    _input_row(ctrl, r + 1, 3, n, [interest_rate / 100.0] * n, PCT_FMT)
    cr['interest_rate'] = r + 1
    r += 3

    div_method = asmp.get('dividend_method', 'payout_ratio')
    _label(ctrl, r, 2, f"9. 배당 가정 ({'배당성향%' if div_method=='payout_ratio' else '주당배당금'})", bold=True)
    if div_method == 'payout_ratio':
        _input_row(ctrl, r + 1, 3, n, [asmp.get('dividend_payout_pct', 0) / 100.0] * n, PCT_FMT)
    else:
        _input_row(ctrl, r + 1, 3, n, [asmp.get('dividend_per_share', 0)] * n, '#,##0')
    cr['dividend'] = r + 1
    r += 3

    _label(ctrl, r, 2, "10. 법인세율(%)")
    ctrl.cell(row=r, column=3, value=asmp.get('tax_rate', 22.0) / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['tax_rate'] = (r, 3)
    r += 1

    _label(ctrl, r, 2, "무위험수익률 Rf(%)")
    ctrl.cell(row=r, column=3, value=asmp.get('risk_free_rate', 3.5) / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['rf'] = (r, 3); r += 1

    _label(ctrl, r, 2, "베타(β)")
    ctrl.cell(row=r, column=3, value=asmp.get('beta', 1.0)).number_format = '0.00'
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['beta'] = (r, 3); r += 1

    _label(ctrl, r, 2, "시장위험프리미엄 ERP(%)")
    ctrl.cell(row=r, column=3, value=asmp.get('equity_risk_premium', 5.5) / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['erp'] = (r, 3); r += 1

    _label(ctrl, r, 2, "타인자본비용 세전 Kd(%)")
    ctrl.cell(row=r, column=3, value=asmp.get('cost_of_debt', 5.0) / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['kd'] = (r, 3); r += 1

    _label(ctrl, r, 2, "자기자본비용 직접입력(%, 0=CAPM 사용)")
    ctrl.cell(row=r, column=3, value=(asmp.get('cost_of_equity') or 0) / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['ke_direct'] = (r, 3); r += 1

    _label(ctrl, r, 2, "타인자본비중 직접입력(%, 0=BS기준 자동계산)")
    ctrl.cell(row=r, column=3, value=(asmp.get('debt_weight') or 0) / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['dw_direct'] = (r, 3); r += 1

    _label(ctrl, r, 2, "영구성장률 TGR(%)")
    ctrl.cell(row=r, column=3, value=tgr / 100.0).number_format = PCT_FMT
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['tgr'] = (r, 3); r += 1

    _label(ctrl, r, 2, "발행주식수")
    ctrl.cell(row=r, column=3, value=shares if shares else 0).number_format = '#,##0'
    ctrl.cell(row=r, column=3).fill = INPUT_FILL
    cr['shares'] = (r, 3); r += 1

    ctrl.column_dimensions['B'].width = 34
    _col_widths(ctrl, n, 14)

    def C(row, i=None):
        """Control 시트 참조 문자열. i가 있으면 연도별 열, 없으면 C열 단일 셀."""
        if i is None:
            return f"Control!$C${row}"
        return f"Control!{get_column_letter(3+i)}${row}"

    # ════════════════════════════════════════════════════════════════
    # 1. 과거 재무제표 — BS / IS / CS 세 시트 (DART 원본 그대로, 가공 없음)
    # ════════════════════════════════════════════════════════════════
    financial_data = financial_data or {}

    def _merged_raw_rows(stmt_key):
        """여러 연도의 DART 원본 항목을 계정명 기준으로 합치고, 각 계정이
        보고서에 나타나는 순서(ord)를 기준으로 정렬한다 (가공/요약 없음).

        병합 키: account_id가 있으면 (account_id 우선)을 키로 쓰고, 없거나
        연도별로 account_id가 비표준/누락인 경우 공백을 제거한 정규화된
        계정명(account_nm_norm)으로 fallback한다. account_id만 단독 키로
        쓰면 DART가 계정명 표기를 살짝 바꾼 해 사이에도 잘 합쳐지고,
        반대로 계정명만 쓰면 단순 공백/개행 차이로 같은 계정이 다른 행으로
        쪼개지는 문제(다트에서 받은 그대로인데 "이상하게" 보이는 원인 중
        하나)를 막을 수 있다. 화면에 표시되는 라벨은 가장 처음 등장한
        연도의 원본 account_nm(정규화 전)을 그대로 사용한다.
        """
        order_of = {}
        vals = {}
        display_name = {}
        for yr in hist_years:
            rows = financial_data.get(str(yr), {}).get('raw', {}).get(stmt_key, [])
            for r in rows:
                account_id = r.get('account_id') or ''
                norm_name = r.get('account_nm_norm') or r['account_nm'].replace(' ', '').strip()
                key = account_id if account_id else f"__name__:{norm_name}"
                vals.setdefault(key, {})[yr] = r['amount']
                if key not in display_name:
                    display_name[key] = r['account_nm']
                if key not in order_of or r['ord'] < order_of[key]:
                    order_of[key] = r['ord']
        keys_sorted = sorted(vals.keys(), key=lambda k: order_of.get(k, 0))
        # 반환값은 기존 인터페이스(이름->값/행번호)를 유지하기 위해 표시용
        # 이름을 키로 다시 매핑한다. 표시 이름이 같은 계정이 서로 다른
        # account_id로 두 번 잡히는 극히 드문 경우를 피하려고, 표시 이름이
        # 충돌하면 두 번째 항목에 계정ID를 덧붙여 구분한다.
        names_sorted = []
        vals_by_name = {}
        seen_names = set()
        for key in keys_sorted:
            name = display_name[key]
            if name in seen_names:
                name = f"{name} ({key})"
            seen_names.add(name)
            names_sorted.append(name)
            vals_by_name[name] = vals[key]
        return names_sorted, vals_by_name

    TITLES = {"BS": "재 무 상 태 표", "IS": "손 익 계 산 서", "CS": "현 금 흐 름 표"}

    def raw_hist_sheet(name, stmt_key, fallback_rows=None):
        ws = wb.create_sheet(name)
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = "C6"
        title = ws.cell(row=3, column=2, value=TITLES[name])
        title.font = Font(bold=True, size=13)
        title.alignment = Alignment(horizontal="center")
        ws.cell(row=4, column=2, value=company_name)
        unit = ws.cell(row=4, column=3, value="(단위: 백만원)")
        unit.alignment = Alignment(horizontal="right")

        hdr = ws.cell(row=5, column=2, value="과목")
        hdr.font = BOLD
        hdr.fill = GRAY_FILL
        hdr.alignment = Alignment(horizontal="center", vertical="center")
        hdr.border = ROW_BORDER
        for i, yr in enumerate(hist_years):
            c = ws.cell(row=5, column=3 + i, value=f"{yr}년")
            c.font = BOLD
            c.fill = GRAY_FILL
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = ROW_BORDER

        names_sorted, vals = _merged_raw_rows(stmt_key)
        row_of = {}
        rr = 6
        for name in names_sorted:
            is_sub = _is_subtotal_label(name)
            lbl = ws.cell(row=rr, column=2, value=name)
            lbl.border = ROW_BORDER
            lbl.alignment = Alignment(horizontal="left", vertical="top")
            if is_sub:
                lbl.font = BOLD
            for i, yr in enumerate(hist_years):
                v = vals[name].get(yr)
                c = ws.cell(row=rr, column=3 + i, value=(v / 1e6) if v is not None else None)
                c.number_format = ACC_FMT
                c.border = ROW_BORDER
                c.alignment = Alignment(horizontal="right", vertical="top")
                if is_sub:
                    c.font = BOLD
            row_of[name] = rr
            rr += 1

        # 마지막 줄(보통 자산총계/부채및자본총계/당기순이익/기말현금)에 굵은
        # 하단 테두리를 줘서 통계표를 닫는 느낌을 낸다.
        if rr > 6:
            for col in range(2, 3 + len(hist_years)):
                ws.cell(row=rr - 1, column=col).border = TOTAL_BORDER

        # DART 원본 항목명에서 못 찾은 핵심 지표는, 다른 시트의 수식이 항상
        # 유효한 셀을 참조할 수 있도록 보조행으로 보강한다 (요약 hist 기준).
        for label, key in (fallback_rows or []):
            if label in row_of:
                continue
            rr += 1
            lbl = ws.cell(row=rr, column=2, value=f"[보조] {label}")
            lbl.border = ROW_BORDER
            for i, yr in enumerate(hist_years):
                v = hist.get(key, {}).get(yr, 0) or 0
                c = ws.cell(row=rr, column=3 + i, value=v / 1e6)
                c.number_format = ACC_FMT
                c.border = ROW_BORDER
            row_of[label] = rr

        ws.column_dimensions['B'].width = 37
        for i in range(len(hist_years)):
            ws.column_dimensions[get_column_letter(3 + i)].width = 16
        return row_of, names_sorted

    def _find_row(row_map, names, keywords):
        """병합된 raw 항목 중 키워드를 포함하는 첫 계정명의 행 번호를 찾는다."""
        for nm in names:
            if any(kw in nm for kw in keywords):
                return row_map[nm]
        return None

    bs_raw_rows, bs_names = raw_hist_sheet("BS", "bs", fallback_rows=[
        ("총자산", "total_assets"), ("총자본", "total_equity"), ("현금", "cash"),
        ("IBD(이자부부채)", "ibd"), ("순차입금", "net_debt"),
    ])
    is_raw_rows, is_names = raw_hist_sheet("IS", "is", fallback_rows=[
        ("매출액", "revenue"), ("매출원가", "cogs"), ("판매비와관리비", "sga"),
    ])
    cs_raw_rows, cs_names = raw_hist_sheet("CS", "cs", fallback_rows=[
        ("D&A", "da"),
    ])

    # DCF/WACC/Debt 등 다른 시트에서 과거 실적을 참조할 때 쓸 행 번호.
    # raw 원본에서 우선 찾고, 없으면 위에서 보강한 [보조] 행을 사용한다.
    bs_row = {
        'total_assets': _find_row(bs_raw_rows, bs_names, ['자산총계']) or bs_raw_rows.get('총자산'),
        'total_equity': _find_row(bs_raw_rows, bs_names, ['자본총계']) or bs_raw_rows.get('총자본'),
        'cash': _find_row(bs_raw_rows, bs_names, ['현금및현금성자산']) or bs_raw_rows.get('현금'),
        'ibd': bs_raw_rows.get('IBD(이자부부채)') or _find_row(bs_raw_rows, bs_names, ['단기차입금']),
    }
    is_row = {
        'revenue': _find_row(is_raw_rows, is_names, ['매출액']) or is_raw_rows.get('매출액'),
        'cogs': _find_row(is_raw_rows, is_names, ['매출원가']) or is_raw_rows.get('매출원가'),
        'sga': _find_row(is_raw_rows, is_names, ['판매비와관리비']) or is_raw_rows.get('판매비와관리비'),
    }
    cs_row = {
        'da': _find_row(cs_raw_rows, cs_names, ['감가상각비']) or cs_raw_rows.get('D&A'),
    }

    # ════════════════════════════════════════════════════════════════
    # 2. Revenue
    # ════════════════════════════════════════════════════════════════
    ws_rev = wb.create_sheet("Revenue")
    _title(ws_rev, "매출액 (단위: 백만원)", span=2 + n)
    _label(ws_rev, 4, 2, "항목", bold=True)
    _year_header(ws_rev, 4, 3, proj_years)

    base_rev_col = get_column_letter(3 + len(hist_years) - 1)
    _label(ws_rev, 6, 2, "기준연도 매출액")
    ws_rev.cell(row=6, column=3, value=f"=IS!{base_rev_col}{is_row['revenue']}").number_format = NUM_FMT

    _label(ws_rev, 8, 2, "YoY 성장률(%)")
    for i in range(n):
        ws_rev.cell(row=8, column=3 + i, value=f"={C(cr['rev_growth'], i)}").number_format = PCT_FMT

    _label(ws_rev, 10, 2, "매출액(추정)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        prev = "$C$6" if i == 0 else f"{get_column_letter(3+i-1)}10"
        ws_rev.cell(row=10, column=3 + i, value=f"={prev}*(1+{col}8)").number_format = NUM_FMT
        ws_rev.cell(row=10, column=3 + i).font = BOLD
    _col_widths(ws_rev, n + 1)
    rev_row = 10

    # ════════════════════════════════════════════════════════════════
    # 3. COGS
    # ════════════════════════════════════════════════════════════════
    ws_cogs = wb.create_sheet("COGS")
    _title(ws_cogs, "매출원가 (단위: 백만원)", span=2 + n)
    _year_header(ws_cogs, 4, 3, proj_years)
    _label(ws_cogs, 6, 2, "매출원가/매출(%)" if cogs_method == 'pct_revenue' else "YoY 성장률(%)")
    for i in range(n):
        ws_cogs.cell(row=6, column=3 + i, value=f"={C(cr['cogs'], i)}").number_format = PCT_FMT
    _label(ws_cogs, 8, 2, "매출원가(추정)", bold=True)
    base_col = get_column_letter(3 + len(hist_years) - 1)
    for i in range(n):
        col = get_column_letter(3 + i)
        if cogs_method == 'pct_revenue':
            ws_cogs.cell(row=8, column=3 + i, value=f"=Revenue!{col}{rev_row}*{col}6").number_format = NUM_FMT
        else:
            prev = f"IS!{base_col}{is_row['cogs']}" if i == 0 else f"{get_column_letter(3+i-1)}8"
            ws_cogs.cell(row=8, column=3 + i, value=f"={prev}*(1+{col}6)").number_format = NUM_FMT
    _col_widths(ws_cogs, n + 1)
    cogs_row_out = 8

    # ════════════════════════════════════════════════════════════════
    # 4. SG&A
    # ════════════════════════════════════════════════════════════════
    ws_sga = wb.create_sheet("SG&A")
    _title(ws_sga, "판매비와관리비 (단위: 백만원)", span=2 + n)
    _year_header(ws_sga, 4, 3, proj_years)
    _label(ws_sga, 6, 2, "판관비/매출(%)" if sga_method == 'pct_revenue' else "YoY 성장률(%)")
    for i in range(n):
        ws_sga.cell(row=6, column=3 + i, value=f"={C(cr['sga'], i)}").number_format = PCT_FMT
    _label(ws_sga, 8, 2, "판관비(추정)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        if sga_method == 'pct_revenue':
            ws_sga.cell(row=8, column=3 + i, value=f"=Revenue!{col}{rev_row}*{col}6").number_format = NUM_FMT
        else:
            prev = f"IS!{base_col}{is_row['sga']}" if i == 0 else f"{get_column_letter(3+i-1)}8"
            ws_sga.cell(row=8, column=3 + i, value=f"={prev}*(1+{col}6)").number_format = NUM_FMT
    _col_widths(ws_sga, n + 1)
    sga_row_out = 8

    # ════════════════════════════════════════════════════════════════
    # 5. D&A CAPEX
    # ════════════════════════════════════════════════════════════════
    ws_dc = wb.create_sheet("D&A CAPEX")
    _title(ws_dc, "D&A · CapEx (단위: 백만원)", span=2 + n)
    _year_header(ws_dc, 4, 3, proj_years)

    _label(ws_dc, 6, 2, f"D&A 가정값 ({da_method})")
    for i in range(n):
        ws_dc.cell(row=6, column=3 + i, value=f"={C(cr['da'], i)}").number_format = da_fmt
    _label(ws_dc, 8, 2, "D&A(추정)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        if da_method == 'pct_revenue':
            ws_dc.cell(row=8, column=3 + i, value=f"=Revenue!{col}{rev_row}*{col}6").number_format = NUM_FMT
        elif da_method == 'fixed':
            ws_dc.cell(row=8, column=3 + i, value=f"={col}6").number_format = NUM_FMT
        else:
            prev = f"CS!{base_col}{cs_row['da']}" if i == 0 else f"{get_column_letter(3+i-1)}8"
            ws_dc.cell(row=8, column=3 + i, value=f"={prev}*(1+{col}6)").number_format = NUM_FMT
    da_row_out = 8

    _label(ws_dc, 11, 2, f"유지보수 CapEx 가정값 ({capex_method})")
    for i in range(n):
        ws_dc.cell(row=11, column=3 + i, value=f"={C(cr['capex'], i)}").number_format = (
            PCT_FMT if capex_method == 'pct_revenue' else NUM_FMT)
    _label(ws_dc, 12, 2, "유지보수 CapEx", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        if capex_method == 'equal_da':
            ws_dc.cell(row=12, column=3 + i, value=f"={col}8").number_format = NUM_FMT
        elif capex_method == 'pct_revenue':
            ws_dc.cell(row=12, column=3 + i, value=f"=Revenue!{col}{rev_row}*{col}11").number_format = NUM_FMT
        else:
            ws_dc.cell(row=12, column=3 + i, value=f"={col}11").number_format = NUM_FMT

    _label(ws_dc, 14, 2, "신규투자 CapEx")
    for i in range(n):
        ws_dc.cell(row=14, column=3 + i, value=f"={C(cr['new_invest'], i)}").number_format = NUM_FMT
    _label(ws_dc, 15, 2, "총 CapEx(추정)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        ws_dc.cell(row=15, column=3 + i, value=f"={col}12+{col}14").number_format = NUM_FMT
    _col_widths(ws_dc, n + 1)
    capex_row_out = 15

    # ════════════════════════════════════════════════════════════════
    # 6. NWC
    # ════════════════════════════════════════════════════════════════
    ws_nwc = wb.create_sheet("NWC")
    _title(ws_nwc, "순운전자본(NWC) (단위: 백만원)", span=2 + n)
    _year_header(ws_nwc, 4, 3, proj_years)
    _label(ws_nwc, 6, 2, "NWC 가정값" + (" (매출대비%)" if nwc_method == 'pct_revenue' else " (고정액)"))
    for i in range(n):
        ws_nwc.cell(row=6, column=3 + i, value=f"={C(cr['nwc'], i)}").number_format = (
            PCT_FMT if nwc_method == 'pct_revenue' else NUM_FMT)

    base_nwc_val = (hist['nwc'].get(base_year, 0)) / 1e6
    if nwc_method == 'pct_revenue':
        _label(ws_nwc, 7, 2, "NWC 잔액(추정)")
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_nwc.cell(row=7, column=3 + i, value=f"=Revenue!{col}{rev_row}*{col}6").number_format = NUM_FMT
        _label(ws_nwc, 8, 2, "ΔNWC(현금유출, +)", bold=True)
        for i in range(n):
            col = get_column_letter(3 + i)
            prev = base_nwc_val if i == 0 else f"{get_column_letter(3+i-1)}7"
            prevref = prev if i == 0 else prev
            if i == 0:
                ws_nwc.cell(row=8, column=3 + i, value=f"={col}7-{base_nwc_val}").number_format = NUM_FMT
            else:
                ws_nwc.cell(row=8, column=3 + i, value=f"={col}7-{get_column_letter(3+i-1)}7").number_format = NUM_FMT
    else:
        _label(ws_nwc, 8, 2, "ΔNWC(현금유출, +)", bold=True)
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_nwc.cell(row=8, column=3 + i, value=f"={col}6").number_format = NUM_FMT
    _col_widths(ws_nwc, n + 1)
    dnwc_row_out = 8
    nwc_balance_row = 7 if nwc_method == 'pct_revenue' else None

    # ════════════════════════════════════════════════════════════════
    # 7. Debt
    # ════════════════════════════════════════════════════════════════
    ws_debt = wb.create_sheet("Debt")
    _title(ws_debt, "차입금 · 이자 (단위: 백만원)", span=2 + n)
    _year_header(ws_debt, 4, 3, proj_years)

    _label(ws_debt, 6, 2, "기초 차입금")
    base_debt_col = get_column_letter(3 + len(hist_years) - 1)
    for i in range(n):
        col = get_column_letter(3 + i)
        prev = f"BS!{base_debt_col}{bs_row['ibd']}" if i == 0 else f"{get_column_letter(3+i-1)}7"
        ws_debt.cell(row=6, column=3 + i, value=f"={prev}").number_format = NUM_FMT
    _label(ws_debt, 7, 2, "기말 차입금", bold=True)
    for i in range(n):
        ws_debt.cell(row=7, column=3 + i, value=f"={C(cr['debt_balance'], i)}").number_format = NUM_FMT
    _label(ws_debt, 8, 2, "차입금 변동(+조달/-상환)")
    for i in range(n):
        col = get_column_letter(3 + i)
        ws_debt.cell(row=8, column=3 + i, value=f"={col}7-{col}6").number_format = NUM_FMT
    _label(ws_debt, 10, 2, "적용 이자율(%)")
    for i in range(n):
        ws_debt.cell(row=10, column=3 + i, value=f"={C(cr['interest_rate'], i)}").number_format = PCT_FMT
    _label(ws_debt, 11, 2, "이자비용 (평균차입금 기준)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        ws_debt.cell(row=11, column=3 + i, value=f"=AVERAGE({col}6:{col}7)*{col}10").number_format = NUM_FMT
    _col_widths(ws_debt, n + 1)
    debt_end_row, debt_delta_row, interest_row = 7, 8, 11

    # ════════════════════════════════════════════════════════════════
    # 8. Tax
    # ════════════════════════════════════════════════════════════════
    ws_tax = wb.create_sheet("Tax")
    _title(ws_tax, "법인세", span=2 + n)
    _year_header(ws_tax, 4, 3, proj_years)
    _label(ws_tax, 6, 2, "법인세율(%)", bold=True)
    for i in range(n):
        ws_tax.cell(row=6, column=3 + i, value=f"={C(cr['tax_rate'])}").number_format = PCT_FMT
    _col_widths(ws_tax, n + 1)
    tax_rate_row = 6

    # ════════════════════════════════════════════════════════════════
    # 9. WACC
    # ════════════════════════════════════════════════════════════════
    ws_w = wb.create_sheet("WACC")
    _title(ws_w, "WACC 산출", span=4)
    _label(ws_w, 4, 2, "무위험수익률 Rf(%)")
    ws_w.cell(row=4, column=3, value=f"={C(cr['rf'][0])}").number_format = PCT_FMT
    _label(ws_w, 5, 2, "베타(β)")
    ws_w.cell(row=5, column=3, value=f"={C(cr['beta'][0])}").number_format = '0.00'
    _label(ws_w, 6, 2, "시장위험프리미엄 ERP(%)")
    ws_w.cell(row=6, column=3, value=f"={C(cr['erp'][0])}").number_format = PCT_FMT
    _label(ws_w, 7, 2, "타인자본비용 세전 Kd(%)")
    ws_w.cell(row=7, column=3, value=f"={C(cr['kd'][0])}").number_format = PCT_FMT
    _label(ws_w, 8, 2, "자기자본비용 Ke (CAPM)", bold=True)
    ws_w.cell(row=8, column=3, value="=C4+C5*C6").number_format = PCT_FMT
    _label(ws_w, 9, 2, "자기자본비용 직접입력(%, 0=CAPM)")
    ws_w.cell(row=9, column=3, value=f"={C(cr['ke_direct'][0])}").number_format = PCT_FMT
    _label(ws_w, 10, 2, "최종 Ke", bold=True)
    ws_w.cell(row=10, column=3, value="=IF(C9>0,C9,C8)").number_format = PCT_FMT
    _label(ws_w, 12, 2, "총차입금")
    ws_w.cell(row=12, column=3, value=f"=BS!{base_debt_col}{bs_row['ibd']}").number_format = NUM_FMT
    _label(ws_w, 13, 2, "총자본")
    ws_w.cell(row=13, column=3, value=f"=BS!{base_debt_col}{bs_row['total_equity']}").number_format = NUM_FMT
    _label(ws_w, 14, 2, "타인자본비중 직접입력(%, 0=BS기준)")
    ws_w.cell(row=14, column=3, value=f"={C(cr['dw_direct'][0])}").number_format = PCT_FMT
    _label(ws_w, 15, 2, "타인자본비중(Dw)", bold=True)
    ws_w.cell(row=15, column=3, value="=IF(C14>0,C14,C12/(C12+C13))").number_format = PCT_FMT
    _label(ws_w, 16, 2, "자기자본비중(Ew)", bold=True)
    ws_w.cell(row=16, column=3, value="=1-C15").number_format = PCT_FMT
    _label(ws_w, 18, 2, "WACC", bold=True)
    ws_w.cell(row=18, column=3, value=f"=C16*C10+C15*(C7*(1-Tax!C{tax_rate_row}))").number_format = PCT_FMT
    _label(ws_w, 20, 2, "영구성장률(TGR, %)", bold=True)
    ws_w.cell(row=20, column=3, value=f"={C(cr['tgr'][0])}").number_format = PCT_FMT
    ws_w.column_dimensions['B'].width = 32
    ws_w.column_dimensions['C'].width = 14
    wacc_cell, tgr_cell = "WACC!$C$18", "WACC!$C$20"

    # ════════════════════════════════════════════════════════════════
    # 10. FS — 추정 IS / BS / CS (3대 재무제표 통합)
    # ════════════════════════════════════════════════════════════════
    ws_fs = wb.create_sheet("FS")
    _title(ws_fs, "추정 재무제표 (IS·BS·CS, 단위: 백만원)", span=2 + n)
    _year_header(ws_fs, 4, 3, proj_years)

    fs = {}
    rr = 6
    _label(ws_fs, rr, 2, "[추정 손익계산서]", bold=True); rr += 1
    fs['revenue'] = rr; _label(ws_fs, rr, 2, "매출액"); rr += 1
    fs['cogs'] = rr; _label(ws_fs, rr, 2, "(매출원가)"); rr += 1
    fs['gp'] = rr; _label(ws_fs, rr, 2, "매출총이익"); rr += 1
    fs['sga'] = rr; _label(ws_fs, rr, 2, "(판관비)"); rr += 1
    fs['ebit'] = rr; _label(ws_fs, rr, 2, "영업이익(EBIT)", bold=True); rr += 1
    fs['interest'] = rr; _label(ws_fs, rr, 2, "(이자비용)"); rr += 1
    fs['pretax'] = rr; _label(ws_fs, rr, 2, "세전이익"); rr += 1
    fs['tax'] = rr; _label(ws_fs, rr, 2, "(법인세)"); rr += 1
    fs['ni'] = rr; _label(ws_fs, rr, 2, "순이익", bold=True); rr += 2

    _label(ws_fs, rr, 2, "[추정 재무상태표]", bold=True); rr += 1
    fs['ar'] = rr; _label(ws_fs, rr, 2, "매출채권+재고-매입채무(NWC)"); rr += 1
    fs['ppe_delta'] = rr; _label(ws_fs, rr, 2, "CapEx - D&A (순고정자산 변동)"); rr += 1
    fs['debt'] = rr; _label(ws_fs, rr, 2, "차입금(기말)"); rr += 1
    fs['div'] = rr; _label(ws_fs, rr, 2, "배당금"); rr += 1
    fs['equity'] = rr; _label(ws_fs, rr, 2, "자본(기말) = 기초자본+순이익-배당", bold=True); rr += 1
    fs['cash_end'] = rr; _label(ws_fs, rr, 2, "현금(기말, plug)", bold=True); rr += 2

    _label(ws_fs, rr, 2, "[추정 현금흐름표]", bold=True); rr += 1
    fs['cfo'] = rr; _label(ws_fs, rr, 2, "영업활동현금흐름 = 순이익+D&A-ΔNWC"); rr += 1
    fs['cfi'] = rr; _label(ws_fs, rr, 2, "투자활동현금흐름 = -CapEx"); rr += 1
    fs['cff'] = rr; _label(ws_fs, rr, 2, "재무활동현금흐름 = ΔDebt-배당"); rr += 1
    fs['cf_net'] = rr; _label(ws_fs, rr, 2, "현금증감", bold=True); rr += 1

    base_equity_col = get_column_letter(3 + len(hist_years) - 1)
    base_cash_col = base_equity_col
    for i in range(n):
        col = get_column_letter(3 + i)
        prevcol = get_column_letter(3 + i - 1) if i > 0 else None
        ws_fs.cell(row=fs['revenue'], column=3+i, value=f"=Revenue!{col}{rev_row}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cogs'], column=3+i, value=f"=-COGS!{col}{cogs_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['gp'], column=3+i, value=f"={col}{fs['revenue']}+{col}{fs['cogs']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['sga'], column=3+i, value=f"=-'SG&A'!{col}{sga_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ebit'], column=3+i, value=f"={col}{fs['gp']}+{col}{fs['sga']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['interest'], column=3+i, value=f"=-Debt!{col}{interest_row}").number_format = NUM_FMT
        ws_fs.cell(row=fs['pretax'], column=3+i, value=f"={col}{fs['ebit']}+{col}{fs['interest']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['tax'], column=3+i, value=f"=-MAX({col}{fs['pretax']},0)*Tax!{col}{tax_rate_row}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ni'], column=3+i, value=f"={col}{fs['pretax']}+{col}{fs['tax']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ni'], column=3+i).font = BOLD

        ws_fs.cell(row=fs['ar'], column=3+i, value=f"=NWC!{col}{nwc_balance_row}" if nwc_balance_row else 0).number_format = NUM_FMT
        ws_fs.cell(row=fs['ppe_delta'], column=3+i, value=f"='D&A CAPEX'!{col}{capex_row_out}-'D&A CAPEX'!{col}{da_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['debt'], column=3+i, value=f"=Debt!{col}{debt_end_row}").number_format = NUM_FMT

        if div_method == 'payout_ratio':
            ws_fs.cell(row=fs['div'], column=3+i, value=f"=MAX({col}{fs['ni']},0)*{C(cr['dividend'], i)}").number_format = NUM_FMT
        else:
            shares_ref = C(cr['shares'][0])
            ws_fs.cell(row=fs['div'], column=3+i, value=f"={C(cr['dividend'], i)}*{shares_ref}/1000000").number_format = NUM_FMT

        prev_equity = f"BS!{base_equity_col}{bs_row['total_equity']}" if i == 0 else f"{prevcol}{fs['equity']}"
        ws_fs.cell(row=fs['equity'], column=3+i, value=f"={prev_equity}+{col}{fs['ni']}-{col}{fs['div']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['equity'], column=3+i).font = BOLD

        ws_fs.cell(row=fs['cfo'], column=3+i, value=f"={col}{fs['ni']}+'D&A CAPEX'!{col}{da_row_out}-NWC!{col}{dnwc_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cfi'], column=3+i, value=f"=-'D&A CAPEX'!{col}{capex_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cff'], column=3+i, value=f"=Debt!{col}{debt_delta_row}-{col}{fs['div']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cf_net'], column=3+i, value=f"={col}{fs['cfo']}+{col}{fs['cfi']}+{col}{fs['cff']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cf_net'], column=3+i).font = BOLD

        prev_cash = f"BS!{base_cash_col}{bs_row['cash']}" if i == 0 else f"{prevcol}{fs['cash_end']}"
        ws_fs.cell(row=fs['cash_end'], column=3+i, value=f"={prev_cash}+{col}{fs['cf_net']}").number_format = NUM_FMT

    _col_widths(ws_fs, n + 1, 16)

    # ════════════════════════════════════════════════════════════════
    # 11. DCF
    # ════════════════════════════════════════════════════════════════
    ws_d = wb.create_sheet("DCF")
    _title(ws_d, f"{company_name} — DCF (단위: 백만원)", span=2 + n)
    _year_header(ws_d, 4, 3, proj_years)

    line_rows = {
        '매출액': 6, '매출원가': 7, '매출총이익': 8, '판관비': 9, '영업이익(EBIT)': 10,
        '세후영업이익(NOPAT)': 11, 'D&A(+)': 12, 'CapEx(-)': 13, 'ΔNWC(-)': 14,
        'FCFF': 15, '할인계수': 16, 'PV(FCFF)': 17,
    }
    for label, row in line_rows.items():
        _label(ws_d, row, 2, label, bold=label in ('영업이익(EBIT)', 'FCFF', 'PV(FCFF)'))

    for i in range(n):
        col = get_column_letter(3 + i)
        ws_d.cell(row=6, column=3+i, value=f"=Revenue!{col}{rev_row}").number_format = NUM_FMT
        ws_d.cell(row=7, column=3+i, value=f"=-COGS!{col}{cogs_row_out}").number_format = NUM_FMT
        ws_d.cell(row=8, column=3+i, value=f"={col}6+{col}7").number_format = NUM_FMT
        ws_d.cell(row=9, column=3+i, value=f"=-'SG&A'!{col}{sga_row_out}").number_format = NUM_FMT
        ws_d.cell(row=10, column=3+i, value=f"={col}8+{col}9").number_format = NUM_FMT
        ws_d.cell(row=11, column=3+i, value=f"={col}10*(1-Tax!{col}{tax_rate_row})").number_format = NUM_FMT
        ws_d.cell(row=12, column=3+i, value=f"='D&A CAPEX'!{col}{da_row_out}").number_format = NUM_FMT
        ws_d.cell(row=13, column=3+i, value=f"=-'D&A CAPEX'!{col}{capex_row_out}").number_format = NUM_FMT
        ws_d.cell(row=14, column=3+i, value=f"=-NWC!{col}{dnwc_row_out}").number_format = NUM_FMT
        ws_d.cell(row=15, column=3+i, value=f"=SUM({col}11:{col}14)").number_format = NUM_FMT
        ws_d.cell(row=15, column=3+i).font = BOLD
        ws_d.cell(row=16, column=3+i, value=f"=1/(1+{wacc_cell})^{i+1}").number_format = '0.0000'
        ws_d.cell(row=17, column=3+i, value=f"={col}15*{col}16").number_format = NUM_FMT

    last_col = get_column_letter(2 + n)
    _label(ws_d, 20, 2, "PV(FCFF) 합계", bold=True)
    ws_d.cell(row=20, column=3, value=f"=SUM(C17:{last_col}17)").number_format = NUM_FMT

    # Terminal Value: 마지막 연도의 "할인 전" FCFF를 기준으로 영구가치를 구하고,
    # 마지막 연도의 할인계수를 한 번만 곱해서 PV(TV)를 구한다 (참고 양식에 있던
    # "PV(FCFF)에 할인계수를 중복 적용"하는 오류를 여기서는 처음부터 피함).
    _label(ws_d, 21, 2, "Terminal Value(할인 전)", bold=True)
    ws_d.cell(row=21, column=3, value=f"={last_col}15*(1+{tgr_cell})/({wacc_cell}-{tgr_cell})").number_format = NUM_FMT
    _label(ws_d, 22, 2, "PV(Terminal Value)", bold=True)
    ws_d.cell(row=22, column=3, value=f"=C21*{last_col}16").number_format = NUM_FMT

    _label(ws_d, 24, 2, "기업가치(EV)", bold=True)
    ws_d.cell(row=24, column=3, value="=C20+C22").number_format = NUM_FMT
    _label(ws_d, 25, 2, "(-) 순차입금")
    ws_d.cell(row=25, column=3, value=net_debt / 1e6).number_format = NUM_FMT
    ws_d.cell(row=25, column=3).fill = INPUT_FILL
    _label(ws_d, 26, 2, "자기자본가치", bold=True)
    ws_d.cell(row=26, column=3, value="=C24-C25").number_format = NUM_FMT
    _label(ws_d, 27, 2, "발행주식수")
    ws_d.cell(row=27, column=3, value=f"={C(cr['shares'][0])}").number_format = '#,##0'
    _label(ws_d, 28, 2, "주당가치(원)", bold=True)
    ws_d.cell(row=28, column=3, value="=IF(C27>0,C26*1000000/C27,\"N/A\")").number_format = '#,##0'

    _col_widths(ws_d, n + 1)

    # ────────────────────────────────────────────────────────────────
    # 시트 순서: Control, DCF, WACC, FS, Revenue, COGS, SG&A, D&A CAPEX, NWC, Debt, Tax, BS, IS, CS
    # ────────────────────────────────────────────────────────────────
    order = ["Control", "DCF", "WACC", "FS", "Revenue", "COGS", "SG&A",
             "D&A CAPEX", "NWC", "Debt", "Tax", "BS", "IS", "CS"]
    wb._sheets = [wb[name] for name in order]

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
