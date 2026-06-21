"""
DCF 모델 엑셀 내보내기.

Streamlit 앱(app.py)에서 입력한 가정값들을 시트별로(매출/COGS·SG&A/D&A·CapEx/
NWC/WACC·Tax) 분리하고, 그 시트들을 셀 수식으로 참조하는 FCFF/DCF 시트를
별도로 구성한다. 엑셀을 열어서 가정값을 바꾸면 DCF 결과가 자동으로
재계산되도록, 값이 아니라 수식을 셀에 직접 쓴다.

주의: 현재 계산 엔진(dcf.py)은 매출 -> COGS/SG&A -> EBIT -> FCFF 와 NWC 변동만
모델링하며, 추정 재무상태표/현금흐름표 풀 롤포워드는 계산하지 않는다. 따라서
이 모듈도 동일한 범위(가정 시트 + FCFF + DCF + 과거 재무제표)까지만 만든다.
"""

import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
SUBHEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
INPUT_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
BOLD = Font(bold=True)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NUM_FMT = '#,##0.0'
PCT_FMT = '0.0%'


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


def build_excel_workbook(company_name, hist, asmp, fcff_df, pv_fcff_df,
                          wacc, tgr, pv_tv, ev, net_debt, eq_val, shares,
                          price_per_share, base_year):
    """
    hist: extract_historical_summary() 결과 dict (metric -> {year: value})
    asmp: st.session_state.dcf_assumptions (UI 단위: %, 억원 등 원시 입력값)
    fcff_df, pv_fcff_df: dcf.py DCFModel.calculate_ev() 결과
    나머지: app.py Page 4에서 계산된 스칼라 값들
    """
    wb = Workbook()
    wb.remove(wb.active)

    n = int(asmp.get('projection_years', 5))
    proj_years = [base_year + i + 1 for i in range(n)]
    hist_years = sorted(hist['revenue'].keys())
    all_years_hdr = hist_years + proj_years

    # ────────────────────────────────────────────────────────────────
    # 1. 과거 재무제표
    # ────────────────────────────────────────────────────────────────
    ws = wb.create_sheet("재무제표(과거)")
    _title(ws, f"{company_name} — 과거 재무제표 (단위: 백만원)", span=2 + len(hist_years))
    _label(ws, 4, 2, "항목", bold=True)
    _year_header(ws, 4, 3, hist_years)

    rows = [
        ("매출액", "revenue"), ("매출원가", "cogs"), ("매출총이익", "gross_profit"),
        ("판매비와관리비", "sga"), ("영업이익(EBIT)", "ebit"), ("순이익", "net_income"),
        ("D&A", "da"), ("CapEx", "capex"),
        ("총자산", "total_assets"), ("총차입금", "total_debt"), ("총자본", "total_equity"),
        ("현금", "cash"), ("순운전자본(NWC)", "nwc"),
        ("매출채권", "accounts_receivable"), ("재고자산", "inventory"), ("매입채무", "accounts_payable"),
        ("발행주식수", "shares_outstanding"),
    ]
    r = 5
    for label, key in rows:
        _label(ws, r, 2, label)
        for i, yr in enumerate(hist_years):
            v = hist.get(key, {}).get(yr, 0) or 0
            unit = 1 if key == "shares_outstanding" else 1e6
            cell = ws.cell(row=r, column=3 + i, value=v / unit)
            cell.number_format = NUM_FMT
        r += 1
    for c in range(2, 3 + len(hist_years)):
        ws.column_dimensions[get_column_letter(c)].width = 16
    hist_sheet_first_row = 5
    hist_row_of = {key: hist_sheet_first_row + idx for idx, (_, key) in enumerate(rows)}

    # ────────────────────────────────────────────────────────────────
    # 2. Revenue 가정
    # ────────────────────────────────────────────────────────────────
    ws_rev = wb.create_sheet("Revenue")
    _title(ws_rev, "매출액 가정 (단위: 백만원)", span=2 + n)
    _label(ws_rev, 4, 2, "항목", bold=True)
    _year_header(ws_rev, 4, 3, proj_years)

    rev_method = asmp.get('revenue_method', 'total')
    base_rev_억 = asmp.get('revenue_total', {}).get('base', hist['revenue'].get(base_year, 0))

    _label(ws_rev, 6, 2, "기준연도 매출액")
    ws_rev.cell(row=6, column=3, value=f"='재무제표(과거)'!{get_column_letter(3 + len(hist_years) - 1)}{hist_row_of['revenue']}")
    ws_rev.cell(row=6, column=3).number_format = NUM_FMT

    _label(ws_rev, 8, 2, "YoY 성장률(%)", bold=True)
    growth_rates = asmp.get('revenue_total', {}).get('growth_rates', [5.0] * n)
    for i in range(n):
        cell = ws_rev.cell(row=8, column=3 + i, value=(growth_rates[i] if i < len(growth_rates) else 3.0) / 100.0)
        cell.number_format = PCT_FMT
        cell.fill = INPUT_FILL

    _label(ws_rev, 10, 2, "매출액(추정)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        prev = f"$C$6" if i == 0 else f"{get_column_letter(3 + i - 1)}10"
        cell = ws_rev.cell(row=10, column=3 + i, value=f"={prev}*(1+{col}8)")
        cell.number_format = NUM_FMT
        cell.font = BOLD
    for c in range(2, 3 + n):
        ws_rev.column_dimensions[get_column_letter(c)].width = 14

    # ────────────────────────────────────────────────────────────────
    # 3. COGS / SG&A 가정
    # ────────────────────────────────────────────────────────────────
    ws_cs = wb.create_sheet("COGS_SGA")
    _title(ws_cs, "매출원가 · 판관비 가정 (단위: 백만원)", span=2 + n)
    _year_header(ws_cs, 4, 3, proj_years)

    def cost_block(ws, row0, label, method_key, pct_key, growth_key, hist_key):
        method = asmp.get(method_key, 'pct_revenue')
        is_pct = method == 'pct_revenue'
        _label(ws, row0, 2, f"{label} 방법: {'매출 대비 비율' if is_pct else '전년 대비 성장률'}", bold=True)
        if is_pct:
            pct = asmp.get(pct_key, 0)
            for i in range(n):
                cell = ws.cell(row=row0 + 1, column=3 + i, value=pct / 100.0)
                cell.number_format = PCT_FMT
                cell.fill = INPUT_FILL
            _label(ws, row0 + 1, 2, f"{label}/매출(%)")
            for i in range(n):
                col = get_column_letter(3 + i)
                cell = ws.cell(row=row0 + 2, column=3 + i, value=f"=Revenue!{col}10*{col}{row0+1}")
                cell.number_format = NUM_FMT
        else:
            rates = asmp.get(growth_key, [3.0] * n)
            for i in range(n):
                cell = ws.cell(row=row0 + 1, column=3 + i, value=(rates[i] if i < len(rates) else 3.0) / 100.0)
                cell.number_format = PCT_FMT
                cell.fill = INPUT_FILL
            _label(ws, row0 + 1, 2, "YoY 성장률(%)")
            base_col = get_column_letter(3 + len(hist_years) - 1)
            for i in range(n):
                col = get_column_letter(3 + i)
                prev = f"'재무제표(과거)'!{base_col}{hist_row_of[hist_key]}" if i == 0 else f"{get_column_letter(3 + i - 1)}{row0+2}"
                cell = ws.cell(row=row0 + 2, column=3 + i, value=f"={prev}*(1+{col}{row0+1})")
                cell.number_format = NUM_FMT
        _label(ws, row0 + 2, 2, f"{label}(추정)", bold=True)
        return row0 + 2  # row containing projected values

    cogs_row = cost_block(ws_cs, 6, "매출원가", 'cogs_method', 'cogs_pct', 'cogs_growth', 'cogs')
    sga_row = cost_block(ws_cs, 11, "판관비", 'sga_method', 'sga_pct', 'sga_growth', 'sga')
    for c in range(2, 3 + n):
        ws_cs.column_dimensions[get_column_letter(c)].width = 14

    # ────────────────────────────────────────────────────────────────
    # 4. D&A / CapEx 가정
    # ────────────────────────────────────────────────────────────────
    ws_dc = wb.create_sheet("DA_CAPEX")
    _title(ws_dc, "D&A · CapEx 가정 (단위: 백만원)", span=2 + n)
    _year_header(ws_dc, 4, 3, proj_years)

    da_method = asmp.get('da_method', 'pct_revenue')
    _label(ws_dc, 6, 2, f"D&A 방법: {da_method}", bold=True)
    if da_method == 'pct_revenue':
        pct = asmp.get('da_pct', 3.0)
        for i in range(n):
            cell = ws_dc.cell(row=7, column=3 + i, value=pct / 100.0)
            cell.number_format = PCT_FMT
            cell.fill = INPUT_FILL
        _label(ws_dc, 7, 2, "D&A/매출(%)")
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_dc.cell(row=8, column=3 + i, value=f"=Revenue!{col}10*{col}7").number_format = NUM_FMT
    elif da_method == 'fixed':
        fixed = asmp.get('da_fixed', hist['da'].get(base_year, 0)) / 1e6
        for i in range(n):
            cell = ws_dc.cell(row=7, column=3 + i, value=fixed)
            cell.number_format = NUM_FMT
            cell.fill = INPUT_FILL
        _label(ws_dc, 7, 2, "고정 D&A")
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_dc.cell(row=8, column=3 + i, value=f"={col}7").number_format = NUM_FMT
    else:  # growth
        rates = asmp.get('da_growth', [3.0] * n)
        for i in range(n):
            cell = ws_dc.cell(row=7, column=3 + i, value=(rates[i] if i < len(rates) else 3.0) / 100.0)
            cell.number_format = PCT_FMT
            cell.fill = INPUT_FILL
        _label(ws_dc, 7, 2, "YoY 성장률(%)")
        base_col = get_column_letter(3 + len(hist_years) - 1)
        for i in range(n):
            col = get_column_letter(3 + i)
            prev = f"'재무제표(과거)'!{base_col}{hist_row_of['da']}" if i == 0 else f"{get_column_letter(3 + i - 1)}8"
            ws_dc.cell(row=8, column=3 + i, value=f"={prev}*(1+{col}7)").number_format = NUM_FMT
    _label(ws_dc, 8, 2, "D&A(추정)", bold=True)

    capex_method = asmp.get('capex_method', 'equal_da')
    _label(ws_dc, 11, 2, f"유지보수 CapEx 방법: {capex_method}", bold=True)
    if capex_method == 'equal_da':
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_dc.cell(row=12, column=3 + i, value=f"={col}8").number_format = NUM_FMT
    elif capex_method == 'pct_revenue':
        pct = asmp.get('capex_pct', 3.0)
        for i in range(n):
            cell = ws_dc.cell(row=11, column=3 + i, value=pct / 100.0)
            cell.number_format = PCT_FMT
            cell.fill = INPUT_FILL
            col = get_column_letter(3 + i)
            ws_dc.cell(row=12, column=3 + i, value=f"=Revenue!{col}10*{col}11").number_format = NUM_FMT
    else:  # fixed
        fixed = asmp.get('capex_fixed', hist['capex'].get(base_year, 0)) / 1e6
        for i in range(n):
            cell = ws_dc.cell(row=11, column=3 + i, value=fixed)
            cell.number_format = NUM_FMT
            cell.fill = INPUT_FILL
            col = get_column_letter(3 + i)
            ws_dc.cell(row=12, column=3 + i, value=f"={col}11").number_format = NUM_FMT
    _label(ws_dc, 12, 2, "유지보수 CapEx", bold=True)

    new_invest_list = asmp.get('new_invest_capex_list', [0.0] * n)
    _label(ws_dc, 14, 2, "신규투자 CapEx(억원 입력값)")
    for i in range(n):
        cell = ws_dc.cell(row=14, column=3 + i, value=(new_invest_list[i] if i < len(new_invest_list) else 0.0) * 100)
        cell.number_format = NUM_FMT
        cell.fill = INPUT_FILL

    _label(ws_dc, 15, 2, "총 CapEx(추정)", bold=True)
    for i in range(n):
        col = get_column_letter(3 + i)
        ws_dc.cell(row=15, column=3 + i, value=f"={col}12+{col}14").number_format = NUM_FMT
    for c in range(2, 3 + n):
        ws_dc.column_dimensions[get_column_letter(c)].width = 16
    da_row, capex_row = 8, 15

    # ────────────────────────────────────────────────────────────────
    # 5. NWC 가정
    # ────────────────────────────────────────────────────────────────
    ws_nwc = wb.create_sheet("NWC")
    _title(ws_nwc, "순운전자본(NWC) 가정 (단위: 백만원)", span=2 + n)
    _year_header(ws_nwc, 4, 3, proj_years)

    nwc_method = asmp.get('nwc_method', 'pct_revenue')
    if nwc_method == 'turnover':
        nwc_method = 'pct_revenue'
    if nwc_method == 'pct_revenue':
        pct = asmp.get('nwc_pct', 0)
        base_col = get_column_letter(3 + len(hist_years) - 1)
        _label(ws_nwc, 6, 2, "NWC/매출(%)", bold=True)
        for i in range(n):
            cell = ws_nwc.cell(row=6, column=3 + i, value=pct / 100.0)
            cell.number_format = PCT_FMT
            cell.fill = INPUT_FILL
        _label(ws_nwc, 7, 2, "NWC 잔액(추정)")
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_nwc.cell(row=7, column=3 + i, value=f"=Revenue!{col}10*{col}6").number_format = NUM_FMT
        _label(ws_nwc, 8, 2, "ΔNWC(현금유출, +)", bold=True)
        for i in range(n):
            col = get_column_letter(3 + i)
            prev = f"'재무제표(과거)'!{base_col}{hist_row_of['nwc']}" if i == 0 else f"{get_column_letter(3 + i - 1)}7"
            ws_nwc.cell(row=8, column=3 + i, value=f"={col}7-{prev}").number_format = NUM_FMT
    else:  # fixed
        fixed = asmp.get('nwc_fixed', 0) / 1e6
        _label(ws_nwc, 6, 2, "연간 ΔNWC 고정값(현금유출, +)", bold=True)
        for i in range(n):
            cell = ws_nwc.cell(row=6, column=3 + i, value=fixed)
            cell.number_format = NUM_FMT
            cell.fill = INPUT_FILL
        _label(ws_nwc, 8, 2, "ΔNWC(현금유출, +)", bold=True)
        for i in range(n):
            col = get_column_letter(3 + i)
            ws_nwc.cell(row=8, column=3 + i, value=f"={col}6").number_format = NUM_FMT
    for c in range(2, 3 + n):
        ws_nwc.column_dimensions[get_column_letter(c)].width = 14
    dnwc_row = 8

    # ────────────────────────────────────────────────────────────────
    # 6. WACC / Tax 가정
    # ────────────────────────────────────────────────────────────────
    ws_w = wb.create_sheet("WACC_Tax")
    _title(ws_w, "WACC · 세율 가정", span=4)

    tax_rate = asmp.get('tax_rate', 22.0)
    rf = asmp.get('risk_free_rate', 3.5)
    beta = asmp.get('beta', 1.0)
    erp = asmp.get('equity_risk_premium', 5.5)
    cost_of_equity_direct = asmp.get('cost_of_equity')
    kd_pre = asmp.get('cost_of_debt', 5.0)
    debt_weight = asmp.get('debt_weight')
    total_debt_억 = hist['total_debt'].get(base_year, 0) / 1e6
    total_equity_억 = hist['total_equity'].get(base_year, 0) / 1e6

    fields = [
        ("법인세율(%)", tax_rate / 100.0, PCT_FMT, 4),
        ("무위험수익률 Rf(%)", rf / 100.0, PCT_FMT, 5),
        ("베타(β)", beta, '0.00', 6),
        ("시장위험프리미엄 ERP(%)", erp / 100.0, PCT_FMT, 7),
        ("타인자본비용(세전 Kd, %)", kd_pre / 100.0, PCT_FMT, 8),
    ]
    for r0, (label, val, fmt, row) in enumerate([(l, v, f, rw) for (l, v, f, rw) in fields]):
        _label(ws_w, row, 2, label)
        cell = ws_w.cell(row=row, column=3, value=val)
        cell.number_format = fmt
        cell.fill = INPUT_FILL

    _label(ws_w, 9, 2, "자기자본비용 Ke (CAPM)", bold=True)
    ws_w.cell(row=9, column=3, value="=C5+C6*C7").number_format = PCT_FMT
    if cost_of_equity_direct is not None:
        _label(ws_w, 10, 2, "자기자본비용 직접입력(%) — 입력 시 CAPM 대체")
        ws_w.cell(row=10, column=3, value=cost_of_equity_direct / 100.0).number_format = PCT_FMT
        ws_w.cell(row=10, column=3).fill = INPUT_FILL
        ke_formula = "=IF(C10>0,C10,C9)"
    else:
        _label(ws_w, 10, 2, "자기자본비용 직접입력(%) — 입력 시 CAPM 대체")
        ws_w.cell(row=10, column=3, value=0).number_format = PCT_FMT
        ws_w.cell(row=10, column=3).fill = INPUT_FILL
        ke_formula = "=IF(C10>0,C10,C9)"
    _label(ws_w, 11, 2, "최종 Ke", bold=True)
    ws_w.cell(row=11, column=3, value=ke_formula).number_format = PCT_FMT

    _label(ws_w, 13, 2, "총차입금(백만원)")
    ws_w.cell(row=13, column=3, value=total_debt_억).number_format = NUM_FMT
    _label(ws_w, 14, 2, "총자본(백만원)")
    ws_w.cell(row=14, column=3, value=total_equity_억).number_format = NUM_FMT
    if debt_weight is not None:
        _label(ws_w, 15, 2, "타인자본 비중 직접입력(%)")
        ws_w.cell(row=15, column=3, value=debt_weight / 100.0).number_format = PCT_FMT
        ws_w.cell(row=15, column=3).fill = INPUT_FILL
        dw_formula = "=IF(C15>0,C15,C13/(C13+C14))"
    else:
        _label(ws_w, 15, 2, "타인자본 비중 직접입력(%)")
        ws_w.cell(row=15, column=3, value=0).number_format = PCT_FMT
        ws_w.cell(row=15, column=3).fill = INPUT_FILL
        dw_formula = "=IF(C15>0,C15,C13/(C13+C14))"
    _label(ws_w, 16, 2, "타인자본 비중(Dw)", bold=True)
    ws_w.cell(row=16, column=3, value=dw_formula).number_format = PCT_FMT
    _label(ws_w, 17, 2, "자기자본 비중(Ew)", bold=True)
    ws_w.cell(row=17, column=3, value="=1-C16").number_format = PCT_FMT

    _label(ws_w, 19, 2, "WACC", bold=True)
    ws_w.cell(row=19, column=3, value="=C17*C11+C16*(C8*(1-C4))").number_format = PCT_FMT

    _label(ws_w, 21, 2, "영구성장률(TGR, %)", bold=True)
    ws_w.cell(row=21, column=3, value=tgr / 100.0).number_format = PCT_FMT
    ws_w.cell(row=21, column=3).fill = INPUT_FILL

    ws_w.column_dimensions["B"].width = 30
    ws_w.column_dimensions["C"].width = 14

    # ────────────────────────────────────────────────────────────────
    # 7. DCF
    # ────────────────────────────────────────────────────────────────
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
        ws_d.cell(row=6, column=3 + i, value=f"=Revenue!{col}10").number_format = NUM_FMT
        ws_d.cell(row=7, column=3 + i, value=f"=-COGS_SGA!{col}{cogs_row}").number_format = NUM_FMT
        ws_d.cell(row=8, column=3 + i, value=f"={col}6+{col}7").number_format = NUM_FMT
        ws_d.cell(row=9, column=3 + i, value=f"=-COGS_SGA!{col}{sga_row}").number_format = NUM_FMT
        ws_d.cell(row=10, column=3 + i, value=f"={col}8+{col}9").number_format = NUM_FMT
        ws_d.cell(row=11, column=3 + i, value=f"={col}10*(1-WACC_Tax!$C$4)").number_format = NUM_FMT
        ws_d.cell(row=12, column=3 + i, value=f"=DA_CAPEX!{col}{da_row}").number_format = NUM_FMT
        ws_d.cell(row=13, column=3 + i, value=f"=-DA_CAPEX!{col}{capex_row}").number_format = NUM_FMT
        ws_d.cell(row=14, column=3 + i, value=f"=-NWC!{col}{dnwc_row}").number_format = NUM_FMT
        ws_d.cell(row=15, column=3 + i, value=f"=SUM({col}11:{col}14)").number_format = NUM_FMT
        ws_d.cell(row=15, column=3 + i).font = BOLD
        ws_d.cell(row=16, column=3 + i, value=f"=1/(1+WACC_Tax!$C$19)^{i+1}").number_format = '0.0000'
        ws_d.cell(row=17, column=3 + i, value=f"={col}15*{col}16").number_format = NUM_FMT

    last_col = get_column_letter(2 + n)
    _label(ws_d, 20, 2, "PV(FCFF) 합계", bold=True)
    ws_d.cell(row=20, column=3, value=f"=SUM(C17:{last_col}17)").number_format = NUM_FMT

    # Terminal Value — 할인 전 마지막해 FCFF * (1+g)/(WACC-g), 그 다음 마지막해
    # 할인계수를 한 번만 곱한다 (예전 템플릿의 N24 버그: PV(FCFF)에 할인계수를
    # 중복 적용했던 것을 여기서는 처음부터 올바르게 구현).
    _label(ws_d, 21, 2, "Terminal Value(할인 전)", bold=True)
    ws_d.cell(row=21, column=3, value=f"={last_col}15*(1+WACC_Tax!$C$21)/(WACC_Tax!$C$19-WACC_Tax!$C$21)").number_format = NUM_FMT
    _label(ws_d, 22, 2, "PV(Terminal Value)", bold=True)
    ws_d.cell(row=22, column=3, value=f"=C21*{last_col}16").number_format = NUM_FMT

    _label(ws_d, 24, 2, "기업가치(EV)", bold=True)
    ws_d.cell(row=24, column=3, value="=C20+C22").number_format = NUM_FMT
    _label(ws_d, 25, 2, "(-) 순차입금")
    net_debt_mm = net_debt / 1e6
    ws_d.cell(row=25, column=3, value=net_debt_mm).number_format = NUM_FMT
    ws_d.cell(row=25, column=3).fill = INPUT_FILL
    _label(ws_d, 26, 2, "자기자본가치", bold=True)
    ws_d.cell(row=26, column=3, value="=C24-C25").number_format = NUM_FMT
    _label(ws_d, 27, 2, "발행주식수")
    ws_d.cell(row=27, column=3, value=shares if shares else 0).number_format = '#,##0'
    ws_d.cell(row=27, column=3).fill = INPUT_FILL
    _label(ws_d, 28, 2, "주당가치(원)", bold=True)
    ws_d.cell(row=28, column=3, value="=IF(C27>0,C26*1000000/C27,\"N/A\")").number_format = '#,##0'

    for c in range(2, 3 + n):
        ws_d.column_dimensions[get_column_letter(c)].width = 14

    wb._sheets = [
        wb["DCF"], wb["재무제표(과거)"], wb["Revenue"], wb["COGS_SGA"],
        wb["DA_CAPEX"], wb["NWC"], wb["WACC_Tax"],
    ]

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
