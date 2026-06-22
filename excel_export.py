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

# 참고 양식의 실제 스타일 카탈로그(테마 색상 theme=3 navy 계열)를 RGB로
# 근사한 값들. theme=3,tint=0.0 은 진한 네이비 헤더, tint=0.0999 는 약간
# 밝은 네이비(서브헤더 띠), tint=0.2499 는 중간 톤(강조 라벨 띠),
# tint=0.8999 는 거의 흰색에 가까운 옅은 네이비(소계/하이라이트 행).
NAVY = "1F3864"
HEADER_FILL = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")            # theme3 tint0.0
SUBHEADER_FILL = PatternFill(start_color="2E5395", end_color="2E5395", fill_type="solid")  # theme3 tint0.0999
ACCENT_FILL = PatternFill(start_color="4472A8", end_color="4472A8", fill_type="solid")     # theme3 tint0.2499
LIGHT_NAVY_FILL = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")  # theme3 tint0.8999 (소계행)
LIGHT_GRAY_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")   # theme0 tint-0.0499
INPUT_FILL = PatternFill(start_color="FFFFCC", end_color="FFFFCC", fill_type="solid")
RED_INPUT_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
GRAY_FILL = PatternFill(start_color="DCDCDC", end_color="DCDCDC", fill_type="solid")
HEADER_FONT = Font(name="맑은 고딕", color="FFFFFF", bold=True, size=10)
SUBHEADER_FONT = Font(name="맑은 고딕", color="FFFFFF", bold=False, size=10)
BOLD = Font(name="맑은 고딕", bold=True, size=10)
ITALIC_GRAY = Font(name="맑은 고딕", italic=True, color="808080", size=10)
NORMAL = Font(name="맑은 고딕", size=10)
RED_BOLD = Font(name="맑은 고딕", bold=True, color="FF0000", size=10)
THIN = Side(style="thin", color="BFBFBF")
THIN_DARK = Side(style="thin", color="808080")
THICK = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
ROW_BORDER = Border(left=THIN_DARK, right=THIN_DARK, top=THIN_DARK, bottom=THIN_DARK)
TOTAL_BORDER = Border(left=THIN_DARK, right=THIN_DARK, top=THIN_DARK, bottom=THICK)
SECTION_BORDER = Border(left=THIN_DARK, right=THIN_DARK, top=THIN_DARK, bottom=THIN_DARK)
NUM_FMT = '#,##0.0'
PCT_FMT = '0.0%'
ACC_FMT = '#,##0_);(#,##0);-_) '
ACTUAL_FMT = '#"A"'
ESTIMATE_FMT = '#"E"'
INTERIM_LABEL_FMT = 'General'  # 예: '2025.1H' 같은 반기 실적 텍스트 열
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
    """참고 양식 Row2 '(단위: 백만원)' 헤더 띠 — 진한 네이비 배경, 흰 굵은 글씨."""
    cell = ws.cell(row=row, column=col, value=text)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    for c in range(col, col + span):
        c_ = ws.cell(row=row, column=c)
        c_.fill = HEADER_FILL
        if c_.value is None:
            c_.font = HEADER_FONT


def _sheet_header_band(ws, label, col0, n_hist, n_proj, interim_label=None):
    """참고 양식 공통 패턴: Row2 = '(단위: 백만원)' + 연도 헤더(A/E 표기),
    Row4 = 시트 제목 띠(살짝 밝은 네이비). col0부터 과거(historical) n_hist개
    열 + (선택)interim 1개 열 + 추정(projection) n_proj개 열을 배치한다.
    반환값: {'hist_col0':.., 'interim_col':.., 'proj_col0':..}
    """
    unit = ws.cell(row=2, column=2, value="(단위: 백만원)")
    unit.font = HEADER_FONT
    unit.fill = HEADER_FILL
    for c in range(2, col0):
        cc = ws.cell(row=2, column=c)
        cc.fill = HEADER_FILL
        if cc.value is None:
            cc.font = HEADER_FONT

    title = ws.cell(row=4, column=2, value=label)
    title.font = BOLD
    title.fill = SUBHEADER_FILL
    for c in range(2, col0):
        cc = ws.cell(row=4, column=c)
        cc.fill = SUBHEADER_FILL
        if cc.value is None:
            cc.font = SUBHEADER_FONT
    return {'hist_col0': col0, 'interim_col': col0 + n_hist if interim_label else None,
            'proj_col0': col0 + n_hist + (1 if interim_label else 0)}


def _year_header(ws, row, col0, years, fill=SUBHEADER_FILL):
    for i, yr in enumerate(years):
        cell = ws.cell(row=row, column=col0 + i, value=yr)
        cell.font = BOLD
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = BORDER


def _full_year_header(ws, row, col0, hist_years, proj_years, interim_label=None):
    """과거(Actual, '#"A"') + (선택)반기 interim 텍스트열 + 추정(Estimate, '#"E"')
    연도 헤더를 한 줄에 그린다. 참고 양식 DCF/Revenue/... Row2 패턴과 동일.
    반환값: (hist_col0, interim_col_or_None, proj_col0)
    """
    col = col0
    hist_col0 = col
    for i, yr in enumerate(hist_years):
        c = ws.cell(row=row, column=col, value=yr)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.number_format = ACTUAL_FMT
        c.alignment = Alignment(horizontal="center", vertical="center")
        col += 1
    interim_col = None
    if interim_label is not None:
        interim_col = col
        c = ws.cell(row=row, column=col, value=interim_label)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")
        col += 1
    proj_col0 = col
    for i, yr in enumerate(proj_years):
        if i == 0:
            c = ws.cell(row=row, column=col, value=yr)
        else:
            prev = get_column_letter(col - 1)
            c = ws.cell(row=row, column=col, value=f"=+{prev}{row}+1")
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.number_format = ESTIMATE_FMT
        c.alignment = Alignment(horizontal="center", vertical="center")
        col += 1
    return hist_col0, interim_col, proj_col0


def _label(ws, row, col, text, bold=False):
    cell = ws.cell(row=row, column=col, value=text)
    cell.font = BOLD if bold else NORMAL
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


def _apply_sheet_chrome(ws, ncols_label=2, last_data_col=None):
    """참고 양식 공통: 그리드라인 숨김, A열 좁게(2.625), 라벨열 넓게."""
    ws.sheet_view.showGridLines = False
    ws.column_dimensions['A'].width = 2.625
    ws.column_dimensions['B'].width = 30


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
    _apply_sheet_chrome(ctrl)
    _title(ctrl, f"{company_name} — Control Panel (가정 입력)", span=2 + n)
    # Control 시트는 참고 양식에서도 연도별 시계열이 아니라 가정값 입력
    # 전용(point-in-time) 시트이므로 historical Actual 열을 추가하지 않는다.
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

    # ────────────────────────────────────────────────────────────────
    # 과거(Actual)+추정(Estimate) 공통 열 배치: 과거 H개 열(3..3+H-1) 다음에
    # 바로 추정 n개 열(3+H..3+H+n-1)이 이어진다. (참고 양식의 I~M=Actual,
    # O~T=Estimate 패턴을 그대로 일반화 — 반기 interim N열은 우리는 반기
    # 데이터가 없으므로 생략한다.)
    # ────────────────────────────────────────────────────────────────
    H = len(hist_years)
    proj_col0 = 3 + H

    def hist_col(i):
        return get_column_letter(3 + i)

    def proj_col(i):
        return get_column_letter(proj_col0 + i)

    def _style_header_row(ws, row=2):
        _full_year_header(ws, row, 3, hist_years, proj_years)

    def _hist_link_row(ws, row, sheet_name, src_row, sign="+", bold=False, fmt=NUM_FMT):
        """과거 열에는 BS/IS/CS 시트의 해당 행을 그대로 수식으로 참조한다."""
        for i in range(H):
            c = ws.cell(row=row, column=3 + i,
                        value=f"={sign}{sheet_name}!{hist_col(i)}{src_row}")
            c.number_format = fmt
            if bold:
                c.font = BOLD

    # ════════════════════════════════════════════════════════════════
    # 2. Revenue
    # ════════════════════════════════════════════════════════════════
    ws_rev = wb.create_sheet("Revenue")
    _apply_sheet_chrome(ws_rev)
    _title(ws_rev, "매출액 (단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_rev)
    _label(ws_rev, 4, 2, "매출액", bold=True)

    rev_row = 6
    _label(ws_rev, rev_row, 2, "매출액", bold=True)
    if is_row.get('revenue'):
        _hist_link_row(ws_rev, rev_row, "IS", is_row['revenue'], bold=True)

    _label(ws_rev, 8, 2, "YoY 성장률(%)")
    for i in range(n):
        ws_rev.cell(row=8, column=proj_col0 + i, value=f"={C(cr['rev_growth'], i)}").number_format = PCT_FMT

    _label(ws_rev, 10, 2, "매출액(추정)", bold=True)
    for i in range(n):
        col = proj_col(i)
        prev = f"{hist_col(H-1)}{rev_row}" if i == 0 else f"{proj_col(i-1)}10"
        ws_rev.cell(row=10, column=proj_col0 + i, value=f"={prev}*(1+{col}8)").number_format = NUM_FMT
        ws_rev.cell(row=10, column=proj_col0 + i).font = BOLD
    # rev_row(=6)이 과거 매출액, 10행이 추정 매출액 — DCF 등 다른 시트는
    # 추정 구간만 필요하므로 rev_row 변수는 "추정" 행(10)을 가리키게 유지.
    rev_row = 10
    _col_widths(ws_rev, H + n + 1)

    # ════════════════════════════════════════════════════════════════
    # 3. COGS
    # ════════════════════════════════════════════════════════════════
    ws_cogs = wb.create_sheet("COGS")
    _apply_sheet_chrome(ws_cogs)
    _title(ws_cogs, "매출원가 (단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_cogs)

    _label(ws_cogs, 6, 2, "매출원가", bold=True)
    if is_row.get('cogs'):
        _hist_link_row(ws_cogs, 6, "IS", is_row['cogs'], bold=True)

    _label(ws_cogs, 7, 2, "매출원가/매출(%)" if cogs_method == 'pct_revenue' else "YoY 성장률(%)")
    for i in range(n):
        ws_cogs.cell(row=7, column=proj_col0 + i, value=f"={C(cr['cogs'], i)}").number_format = PCT_FMT
    _label(ws_cogs, 8, 2, "매출원가(추정)", bold=True)
    for i in range(n):
        col = proj_col(i)
        if cogs_method == 'pct_revenue':
            ws_cogs.cell(row=8, column=proj_col0 + i, value=f"=Revenue!{col}{rev_row}*{col}7").number_format = NUM_FMT
        else:
            prev = f"IS!{hist_col(H-1)}{is_row['cogs']}" if i == 0 else f"{proj_col(i-1)}8"
            ws_cogs.cell(row=8, column=proj_col0 + i, value=f"={prev}*(1+{col}7)").number_format = NUM_FMT
    _col_widths(ws_cogs, H + n + 1)
    cogs_row_out = 8

    # ════════════════════════════════════════════════════════════════
    # 4. SG&A
    # ════════════════════════════════════════════════════════════════
    ws_sga = wb.create_sheet("SG&A")
    _apply_sheet_chrome(ws_sga)
    _title(ws_sga, "판매비와관리비 (단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_sga)

    _label(ws_sga, 6, 2, "판매비와관리비", bold=True)
    if is_row.get('sga'):
        _hist_link_row(ws_sga, 6, "IS", is_row['sga'], bold=True)

    _label(ws_sga, 7, 2, "판관비/매출(%)" if sga_method == 'pct_revenue' else "YoY 성장률(%)")
    for i in range(n):
        ws_sga.cell(row=7, column=proj_col0 + i, value=f"={C(cr['sga'], i)}").number_format = PCT_FMT
    _label(ws_sga, 8, 2, "판관비(추정)", bold=True)
    for i in range(n):
        col = proj_col(i)
        if sga_method == 'pct_revenue':
            ws_sga.cell(row=8, column=proj_col0 + i, value=f"=Revenue!{col}{rev_row}*{col}7").number_format = NUM_FMT
        else:
            prev = f"IS!{hist_col(H-1)}{is_row['sga']}" if i == 0 else f"{proj_col(i-1)}8"
            ws_sga.cell(row=8, column=proj_col0 + i, value=f"={prev}*(1+{col}7)").number_format = NUM_FMT
    _col_widths(ws_sga, H + n + 1)
    sga_row_out = 8

    # ════════════════════════════════════════════════════════════════
    # 5. D&A CAPEX
    # ════════════════════════════════════════════════════════════════
    ws_dc = wb.create_sheet("D&A CAPEX")
    _apply_sheet_chrome(ws_dc)
    _title(ws_dc, "D&A · CapEx (단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_dc)

    _label(ws_dc, 6, 2, "D&A (과거, CS 감가상각비)", bold=True)
    if cs_row.get('da'):
        _hist_link_row(ws_dc, 6, "CS", cs_row['da'], bold=True)

    _label(ws_dc, 7, 2, f"D&A 가정값 ({da_method})")
    for i in range(n):
        ws_dc.cell(row=7, column=proj_col0 + i, value=f"={C(cr['da'], i)}").number_format = da_fmt
    _label(ws_dc, 8, 2, "D&A(추정)", bold=True)
    for i in range(n):
        col = proj_col(i)
        if da_method == 'pct_revenue':
            ws_dc.cell(row=8, column=proj_col0 + i, value=f"=Revenue!{col}{rev_row}*{col}7").number_format = NUM_FMT
        elif da_method == 'fixed':
            ws_dc.cell(row=8, column=proj_col0 + i, value=f"={col}7").number_format = NUM_FMT
        else:
            prev = f"CS!{hist_col(H-1)}{cs_row['da']}" if i == 0 else f"{proj_col(i-1)}8"
            ws_dc.cell(row=8, column=proj_col0 + i, value=f"={prev}*(1+{col}7)").number_format = NUM_FMT
    da_row_out = 8

    _label(ws_dc, 11, 2, f"유지보수 CapEx 가정값 ({capex_method})")
    for i in range(n):
        ws_dc.cell(row=11, column=proj_col0 + i, value=f"={C(cr['capex'], i)}").number_format = (
            PCT_FMT if capex_method == 'pct_revenue' else NUM_FMT)
    _label(ws_dc, 12, 2, "유지보수 CapEx", bold=True)
    for i in range(n):
        col = proj_col(i)
        if capex_method == 'equal_da':
            ws_dc.cell(row=12, column=proj_col0 + i, value=f"={col}8").number_format = NUM_FMT
        elif capex_method == 'pct_revenue':
            ws_dc.cell(row=12, column=proj_col0 + i, value=f"=Revenue!{col}{rev_row}*{col}11").number_format = NUM_FMT
        else:
            ws_dc.cell(row=12, column=proj_col0 + i, value=f"={col}11").number_format = NUM_FMT

    _label(ws_dc, 14, 2, "신규투자 CapEx")
    for i in range(n):
        ws_dc.cell(row=14, column=proj_col0 + i, value=f"={C(cr['new_invest'], i)}").number_format = NUM_FMT
    _label(ws_dc, 15, 2, "총 CapEx(추정)", bold=True)
    for i in range(n):
        col = proj_col(i)
        ws_dc.cell(row=15, column=proj_col0 + i, value=f"={col}12+{col}14").number_format = NUM_FMT
    # 과거 CapEx: CS 현금흐름표의 "유형자산의취득"(투자활동현금흐름 내)을
    # 찾아 연결한다. 정확히 일치하는 계정이 없으면 빈칸으로 두고 주석만 남김.
    capex_hist_row = _find_row(cs_raw_rows, cs_names, ['유형자산의취득', '유형자산 취득'])
    if capex_hist_row:
        for i in range(H):
            c = ws_dc.cell(row=15, column=3 + i, value=f"=-CS!{hist_col(i)}{capex_hist_row}")
            c.number_format = NUM_FMT
            c.font = BOLD
    else:
        ws_dc.cell(row=15, column=2).comment = None  # TODO: CAPEX 과거값 — CS에서 유형자산취득 계정을 찾지 못함
    _col_widths(ws_dc, H + n + 1)
    capex_row_out = 15

    # ════════════════════════════════════════════════════════════════
    # 6. NWC
    # ════════════════════════════════════════════════════════════════
    ws_nwc = wb.create_sheet("NWC")
    _apply_sheet_chrome(ws_nwc)
    _title(ws_nwc, "순운전자본(NWC) (단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_nwc)

    # 과거 NWC = (재고자산+매출채권) - 매입채무, BS 원본 계정에서 직접 합산.
    inv_row = _find_row(bs_raw_rows, bs_names, ['재고자산'])
    ar_row = _find_row(bs_raw_rows, bs_names, ['매출채권'])
    ap_row = _find_row(bs_raw_rows, bs_names, ['매입채무'])
    _label(ws_nwc, 6, 2, "NWC (과거, BS: 재고+매출채권-매입채무)", bold=True)
    if inv_row and ar_row and ap_row:
        for i in range(H):
            col = hist_col(i)
            c = ws_nwc.cell(row=6, column=3 + i,
                             value=f"=BS!{col}{inv_row}+BS!{col}{ar_row}-BS!{col}{ap_row}")
            c.number_format = NUM_FMT
            c.font = BOLD

    _label(ws_nwc, 7, 2, "NWC 가정값" + (" (매출대비%)" if nwc_method == 'pct_revenue' else " (고정액)"))
    for i in range(n):
        ws_nwc.cell(row=7, column=proj_col0 + i, value=f"={C(cr['nwc'], i)}").number_format = (
            PCT_FMT if nwc_method == 'pct_revenue' else NUM_FMT)

    base_nwc_val = (hist['nwc'].get(base_year, 0)) / 1e6
    if nwc_method == 'pct_revenue':
        _label(ws_nwc, 9, 2, "NWC 잔액(추정)")
        for i in range(n):
            col = proj_col(i)
            ws_nwc.cell(row=9, column=proj_col0 + i, value=f"=Revenue!{col}{rev_row}*{col}7").number_format = NUM_FMT
        _label(ws_nwc, 19, 2, "ΔNWC(현금유출, +)", bold=True)
        for i in range(n):
            col = proj_col(i)
            if i == 0:
                base_ref = f"{hist_col(H-1)}6" if (inv_row and ar_row and ap_row) else str(base_nwc_val)
                ws_nwc.cell(row=19, column=proj_col0 + i, value=f"={col}9-{base_ref}").number_format = NUM_FMT
            else:
                ws_nwc.cell(row=19, column=proj_col0 + i, value=f"={col}9-{proj_col(i-1)}9").number_format = NUM_FMT
    else:
        _label(ws_nwc, 19, 2, "ΔNWC(현금유출, +)", bold=True)
        for i in range(n):
            col = proj_col(i)
            ws_nwc.cell(row=19, column=proj_col0 + i, value=f"={col}7").number_format = NUM_FMT
    _col_widths(ws_nwc, H + n + 1)
    dnwc_row_out = 19
    nwc_balance_row = 9 if nwc_method == 'pct_revenue' else None

    # ════════════════════════════════════════════════════════════════
    # 7. Debt
    # ════════════════════════════════════════════════════════════════
    ws_debt = wb.create_sheet("Debt")
    _apply_sheet_chrome(ws_debt)
    _title(ws_debt, "차입금 · 이자 (단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_debt)

    _label(ws_debt, 6, 2, "차입금(과거, BS IBD)", bold=True)
    if bs_row.get('ibd'):
        _hist_link_row(ws_debt, 6, "BS", bs_row['ibd'], bold=True)

    _label(ws_debt, 7, 2, "기초 차입금")
    for i in range(n):
        col = proj_col(i)
        prev = f"BS!{hist_col(H-1)}{bs_row['ibd']}" if i == 0 else f"{proj_col(i-1)}8"
        ws_debt.cell(row=7, column=proj_col0 + i, value=f"={prev}").number_format = NUM_FMT
    _label(ws_debt, 8, 2, "기말 차입금", bold=True)
    for i in range(n):
        ws_debt.cell(row=8, column=proj_col0 + i, value=f"={C(cr['debt_balance'], i)}").number_format = NUM_FMT
    _label(ws_debt, 9, 2, "차입금 변동(+조달/-상환)")
    for i in range(n):
        col = proj_col(i)
        ws_debt.cell(row=9, column=proj_col0 + i, value=f"={col}8-{col}7").number_format = NUM_FMT
    _label(ws_debt, 10, 2, "적용 이자율(%)")
    for i in range(n):
        ws_debt.cell(row=10, column=proj_col0 + i, value=f"={C(cr['interest_rate'], i)}").number_format = PCT_FMT
    _label(ws_debt, 11, 2, "이자비용 (평균차입금 기준)", bold=True)
    for i in range(n):
        col = proj_col(i)
        ws_debt.cell(row=11, column=proj_col0 + i, value=f"=AVERAGE({col}7:{col}8)*{col}10").number_format = NUM_FMT
    _col_widths(ws_debt, H + n + 1)
    debt_end_row, debt_delta_row, interest_row = 8, 9, 11

    # ════════════════════════════════════════════════════════════════
    # 8. Tax
    # ════════════════════════════════════════════════════════════════
    ws_tax = wb.create_sheet("Tax")
    _apply_sheet_chrome(ws_tax)
    _title(ws_tax, "법인세", span=2 + n)
    # 참고 양식에서도 Tax 시트는 연도별 시계열이 아니라 단일 가정값(법인세율)만
    # 갖는 시트이므로 historical Actual 열을 추가하지 않는다.
    _year_header(ws_tax, 4, 3, proj_years)
    _label(ws_tax, 6, 2, "법인세율(%)", bold=True)
    for i in range(n):
        ws_tax.cell(row=6, column=3 + i, value=f"={C(cr['tax_rate'])}").number_format = PCT_FMT
    _col_widths(ws_tax, n + 1)
    tax_rate_row = 6

    # ════════════════════════════════════════════════════════════════
    # 9. WACC
    # ════════════════════════════════════════════════════════════════
    # 참고 양식 WACC 시트도 연도 시계열이 아니라 단일 시점(point-in-time)
    # Peer Beta/CAPM/WACC 산출 시트이므로 historical Actual 연도 열은
    # 추가하지 않는다 (회사 전용 Peer_RAW 비교기업 분기는 의도적으로
    # 제외하고, Control 패널 직접입력 Rf/Beta/ERP/Kd 방식만 유지).
    ws_w = wb.create_sheet("WACC")
    _apply_sheet_chrome(ws_w)
    _title(ws_w, "WACC 산출 (단위: %)", span=4)
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
    ws_w.cell(row=10, column=3).fill = LIGHT_NAVY_FILL
    _label(ws_w, 12, 2, "총차입금 (BS 과거 IBD, 기준연도말)")
    ws_w.cell(row=12, column=3, value=f"=BS!{hist_col(H-1)}{bs_row['ibd']}").number_format = NUM_FMT
    _label(ws_w, 13, 2, "총자본 (BS 과거 자본총계, 기준연도말)")
    ws_w.cell(row=13, column=3, value=f"=BS!{hist_col(H-1)}{bs_row['total_equity']}").number_format = NUM_FMT
    _label(ws_w, 14, 2, "타인자본비중 직접입력(%, 0=BS기준)")
    ws_w.cell(row=14, column=3, value=f"={C(cr['dw_direct'][0])}").number_format = PCT_FMT
    _label(ws_w, 15, 2, "타인자본비중(Dw)", bold=True)
    ws_w.cell(row=15, column=3, value="=IF(C14>0,C14,C12/(C12+C13))").number_format = PCT_FMT
    _label(ws_w, 16, 2, "자기자본비중(Ew)", bold=True)
    ws_w.cell(row=16, column=3, value="=1-C15").number_format = PCT_FMT
    _label(ws_w, 18, 2, "WACC", bold=True)
    ws_w.cell(row=18, column=3, value=f"=C16*C10+C15*(C7*(1-Tax!C{tax_rate_row}))").number_format = PCT_FMT
    ws_w.cell(row=18, column=3).fill = LIGHT_NAVY_FILL
    ws_w.cell(row=18, column=3).font = BOLD
    _label(ws_w, 20, 2, "영구성장률(TGR, %)", bold=True)
    ws_w.cell(row=20, column=3, value=f"={C(cr['tgr'][0])}").number_format = PCT_FMT
    for rr_ in range(4, 21):
        ws_w.cell(row=rr_, column=2).font = BOLD if rr_ in (8, 10, 15, 16, 18, 20) else NORMAL
    ws_w.column_dimensions['B'].width = 38
    ws_w.column_dimensions['C'].width = 14
    wacc_cell, tgr_cell = "WACC!$C$18", "WACC!$C$20"

    # ════════════════════════════════════════════════════════════════
    # 10. FS — 추정 IS / BS / CS (3대 재무제표 통합)
    # ════════════════════════════════════════════════════════════════
    ws_fs = wb.create_sheet("FS")
    _apply_sheet_chrome(ws_fs)
    _title(ws_fs, "재무제표 (과거 Actual + 추정 Estimate, 단위: 백만원)", span=2 + H + n)
    _style_header_row(ws_fs)

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

    # 과거 H개 열: IS/BS/CS 원본 그대로 링크 (가공 없는 실제 보고 항목).
    for i in range(H):
        col = hist_col(i)
        if is_row.get('revenue'):
            ws_fs.cell(row=fs['revenue'], column=3+i, value=f"=IS!{col}{is_row['revenue']}").number_format = NUM_FMT
        if is_row.get('cogs'):
            ws_fs.cell(row=fs['cogs'], column=3+i, value=f"=-IS!{col}{is_row['cogs']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['gp'], column=3+i, value=f"={col}{fs['revenue']}+{col}{fs['cogs']}").number_format = NUM_FMT
        if is_row.get('sga'):
            ws_fs.cell(row=fs['sga'], column=3+i, value=f"=-IS!{col}{is_row['sga']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ebit'], column=3+i, value=f"={col}{fs['gp']}+{col}{fs['sga']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ebit'], column=3+i).font = BOLD
        if bs_row.get('total_equity'):
            ws_fs.cell(row=fs['equity'], column=3+i, value=f"=BS!{col}{bs_row['total_equity']}").number_format = NUM_FMT
            ws_fs.cell(row=fs['equity'], column=3+i).font = BOLD
        if bs_row.get('ibd'):
            ws_fs.cell(row=fs['debt'], column=3+i, value=f"=BS!{col}{bs_row['ibd']}").number_format = NUM_FMT
        if bs_row.get('cash'):
            ws_fs.cell(row=fs['cash_end'], column=3+i, value=f"=BS!{col}{bs_row['cash']}").number_format = NUM_FMT
            ws_fs.cell(row=fs['cash_end'], column=3+i).font = BOLD

    for i in range(n):
        col = proj_col(i)
        prevcol = hist_col(H-1) if i == 0 else proj_col(i-1)
        ws_fs.cell(row=fs['revenue'], column=proj_col0+i, value=f"=Revenue!{col}{rev_row}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cogs'], column=proj_col0+i, value=f"=-COGS!{col}{cogs_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['gp'], column=proj_col0+i, value=f"={col}{fs['revenue']}+{col}{fs['cogs']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['sga'], column=proj_col0+i, value=f"=-'SG&A'!{col}{sga_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ebit'], column=proj_col0+i, value=f"={col}{fs['gp']}+{col}{fs['sga']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['interest'], column=proj_col0+i, value=f"=-Debt!{col}{interest_row}").number_format = NUM_FMT
        ws_fs.cell(row=fs['pretax'], column=proj_col0+i, value=f"={col}{fs['ebit']}+{col}{fs['interest']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['tax'], column=proj_col0+i, value=f"=-MAX({col}{fs['pretax']},0)*Tax!{col}{tax_rate_row}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ni'], column=proj_col0+i, value=f"={col}{fs['pretax']}+{col}{fs['tax']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['ni'], column=proj_col0+i).font = BOLD

        ws_fs.cell(row=fs['ar'], column=proj_col0+i, value=f"=NWC!{col}{nwc_balance_row}" if nwc_balance_row else 0).number_format = NUM_FMT
        ws_fs.cell(row=fs['ppe_delta'], column=proj_col0+i, value=f"='D&A CAPEX'!{col}{capex_row_out}-'D&A CAPEX'!{col}{da_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['debt'], column=proj_col0+i, value=f"=Debt!{col}{debt_end_row}").number_format = NUM_FMT

        if div_method == 'payout_ratio':
            ws_fs.cell(row=fs['div'], column=proj_col0+i, value=f"=MAX({col}{fs['ni']},0)*{C(cr['dividend'], i)}").number_format = NUM_FMT
        else:
            shares_ref = C(cr['shares'][0])
            ws_fs.cell(row=fs['div'], column=proj_col0+i, value=f"={C(cr['dividend'], i)}*{shares_ref}/1000000").number_format = NUM_FMT

        prev_equity = f"BS!{hist_col(H-1)}{bs_row['total_equity']}" if i == 0 else f"{prevcol}{fs['equity']}"
        ws_fs.cell(row=fs['equity'], column=proj_col0+i, value=f"={prev_equity}+{col}{fs['ni']}-{col}{fs['div']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['equity'], column=proj_col0+i).font = BOLD

        ws_fs.cell(row=fs['cfo'], column=proj_col0+i, value=f"={col}{fs['ni']}+'D&A CAPEX'!{col}{da_row_out}-NWC!{col}{dnwc_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cfi'], column=proj_col0+i, value=f"=-'D&A CAPEX'!{col}{capex_row_out}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cff'], column=proj_col0+i, value=f"=Debt!{col}{debt_delta_row}-{col}{fs['div']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cf_net'], column=proj_col0+i, value=f"={col}{fs['cfo']}+{col}{fs['cfi']}+{col}{fs['cff']}").number_format = NUM_FMT
        ws_fs.cell(row=fs['cf_net'], column=proj_col0+i).font = BOLD

        prev_cash = f"BS!{hist_col(H-1)}{bs_row['cash']}" if i == 0 else f"{prevcol}{fs['cash_end']}"
        ws_fs.cell(row=fs['cash_end'], column=proj_col0+i, value=f"={prev_cash}+{col}{fs['cf_net']}").number_format = NUM_FMT

    _col_widths(ws_fs, H + n + 1, 16)

    # ════════════════════════════════════════════════════════════════
    # 11. DCF
    # ════════════════════════════════════════════════════════════════
    ws_d = wb.create_sheet("DCF")
    _apply_sheet_chrome(ws_d)
    _title(ws_d, f"{company_name} — DCF (단위: 백만원, 과거 Actual + 추정 Estimate)", span=2 + H + n)
    _style_header_row(ws_d)

    line_rows = {
        '매출액': 6, '매출원가': 7, '매출총이익': 8, '판관비': 9, '영업이익(EBIT)': 10,
        '세후영업이익(NOPAT)': 11, 'D&A(+)': 12, 'CapEx(-)': 13, 'ΔNWC(-)': 14,
        'FCFF': 15, '할인계수': 16, 'PV(FCFF)': 17,
    }
    for label, row in line_rows.items():
        _label(ws_d, row, 2, label, bold=label in ('영업이익(EBIT)', 'FCFF', 'PV(FCFF)'))
        if label in ('영업이익(EBIT)', 'FCFF', 'PV(FCFF)'):
            for c in range(2, 3 + H + n):
                ws_d.cell(row=row, column=c).fill = LIGHT_NAVY_FILL

    # 과거(Actual) 구간: 참고 양식과 동일하게 IS 원본 매출액/매출원가/판관비를
    # 그대로 끌어오고, NOPAT 이하(D&A/CapEx/NWC/FCFF/할인 등 "현금흐름 추정"
    # 항목)은 과거 구간에서는 비워 둔다 — 과거 실적은 실측 손익만 보여주고
    # FCFF 계산은 추정구간에서만 의미를 갖기 때문(참고 양식 Row14 이하도
    # O열부터 비로소 값이 채워짐).
    for i in range(H):
        col = hist_col(i)
        if is_row.get('revenue'):
            ws_d.cell(row=6, column=3+i, value=f"=+IS!{col}{is_row['revenue']}").number_format = NUM_FMT
        if is_row.get('cogs'):
            ws_d.cell(row=7, column=3+i, value=f"=-IS!{col}{is_row['cogs']}").number_format = NUM_FMT
        ws_d.cell(row=8, column=3+i, value=f"={col}6+{col}7").number_format = NUM_FMT
        if is_row.get('sga'):
            ws_d.cell(row=9, column=3+i, value=f"=-IS!{col}{is_row['sga']}").number_format = NUM_FMT
        ws_d.cell(row=10, column=3+i, value=f"={col}8+{col}9").number_format = NUM_FMT
        ws_d.cell(row=10, column=3+i).font = BOLD
        if cs_row.get('da'):
            ws_d.cell(row=12, column=3+i, value=f"=+CS!{col}{cs_row['da']}").number_format = NUM_FMT

    for i in range(n):
        col = proj_col(i)
        ws_d.cell(row=6, column=proj_col0+i, value=f"=Revenue!{col}{rev_row}").number_format = NUM_FMT
        ws_d.cell(row=7, column=proj_col0+i, value=f"=-COGS!{col}{cogs_row_out}").number_format = NUM_FMT
        ws_d.cell(row=8, column=proj_col0+i, value=f"={col}6+{col}7").number_format = NUM_FMT
        ws_d.cell(row=9, column=proj_col0+i, value=f"=-'SG&A'!{col}{sga_row_out}").number_format = NUM_FMT
        ws_d.cell(row=10, column=proj_col0+i, value=f"={col}8+{col}9").number_format = NUM_FMT
        ws_d.cell(row=10, column=proj_col0+i).font = BOLD
        ws_d.cell(row=11, column=proj_col0+i, value=f"={col}10*(1-Tax!{col}{tax_rate_row})").number_format = NUM_FMT
        ws_d.cell(row=12, column=proj_col0+i, value=f"='D&A CAPEX'!{col}{da_row_out}").number_format = NUM_FMT
        ws_d.cell(row=13, column=proj_col0+i, value=f"=-'D&A CAPEX'!{col}{capex_row_out}").number_format = NUM_FMT
        ws_d.cell(row=14, column=proj_col0+i, value=f"=-NWC!{col}{dnwc_row_out}").number_format = NUM_FMT
        ws_d.cell(row=15, column=proj_col0+i, value=f"=SUM({col}11:{col}14)").number_format = NUM_FMT
        ws_d.cell(row=15, column=proj_col0+i).font = BOLD
        ws_d.cell(row=16, column=proj_col0+i, value=f"=1/(1+{wacc_cell})^{i+1}").number_format = '0.0000'
        ws_d.cell(row=17, column=proj_col0+i, value=f"={col}15*{col}16").number_format = NUM_FMT

    last_col = proj_col(n - 1)
    first_proj_col = proj_col(0)
    _label(ws_d, 20, 2, "PV(FCFF) 합계", bold=True)
    ws_d.cell(row=20, column=3, value=f"=SUM({first_proj_col}17:{last_col}17)").number_format = NUM_FMT

    # Terminal Value: 마지막 연도의 "할인 전" FCFF를 기준으로 영구가치를 구하고,
    # 마지막 연도의 할인계수를 한 번만 곱해서 PV(TV)를 구한다 (참고 양식에 있던
    # "PV(FCFF)에 할인계수를 중복 적용"하는 오류를 여기서는 처음부터 피함).
    _label(ws_d, 21, 2, "Terminal Value(할인 전)", bold=True)
    ws_d.cell(row=21, column=3, value=f"={last_col}15*(1+{tgr_cell})/({wacc_cell}-{tgr_cell})").number_format = NUM_FMT
    _label(ws_d, 22, 2, "PV(Terminal Value)", bold=True)
    ws_d.cell(row=22, column=3, value=f"=C21*{last_col}16").number_format = NUM_FMT

    _label(ws_d, 24, 2, "기업가치(EV)", bold=True)
    ws_d.cell(row=24, column=3, value="=C20+C22").number_format = NUM_FMT
    ws_d.cell(row=24, column=3).fill = LIGHT_NAVY_FILL
    _label(ws_d, 25, 2, "(-) 순차입금")
    ws_d.cell(row=25, column=3, value=net_debt / 1e6).number_format = NUM_FMT
    ws_d.cell(row=25, column=3).fill = INPUT_FILL
    _label(ws_d, 26, 2, "자기자본가치", bold=True)
    ws_d.cell(row=26, column=3, value="=C24-C25").number_format = NUM_FMT
    ws_d.cell(row=26, column=3).fill = LIGHT_NAVY_FILL
    _label(ws_d, 27, 2, "발행주식수")
    ws_d.cell(row=27, column=3, value=f"={C(cr['shares'][0])}").number_format = '#,##0'
    _label(ws_d, 28, 2, "주당가치(원)", bold=True)
    ws_d.cell(row=28, column=3, value="=IF(C27>0,C26*1000000/C27,\"N/A\")").number_format = '#,##0'
    ws_d.cell(row=28, column=3).fill = LIGHT_NAVY_FILL

    _col_widths(ws_d, H + n + 1)

    # ────────────────────────────────────────────────────────────────
    # 시트 순서: Control, DCF, WACC, FS, Revenue, COGS, SG&A, D&A CAPEX, NWC, Debt, Tax, BS, IS, CS
    # ────────────────────────────────────────────────────────────────
    order = ["Control", "DCF", "WACC", "FS", "Revenue", "COGS", "SG&A",
             "D&A CAPEX", "NWC", "Debt", "Tax", "BS", "IS", "CS"]
    wb._sheets = [wb[name] for name in order]

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
