"""
Streamlit DCF Analysis Application for Korean Listed Companies (DART OpenAPI)
Run: streamlit run app.py
"""

from __future__ import annotations

import io
import os
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Page config (must be first Streamlit call) ──────────────────────────────
st.set_page_config(
    page_title="DART DCF 분석",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Lazy imports from project modules ───────────────────────────────────────
import dart_api
from dcf import DCFModel

# ── Session-state initialisation ────────────────────────────────────────────
DEFAULTS = {
    "api_key": os.getenv("DART_API_KEY", ""),
    "search_results": [],
    "selected_corp": None,      # {corp_name, corp_code, stock_code}
    "selected_year": 2023,
    "hist_df": None,            # pd.DataFrame from build_historical_summary
    "company_info": None,
    "assumptions": {},
    "dcf_result": None,
    "page": "기업 검색",
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Utility helpers ──────────────────────────────────────────────────────────

def _억원(val: Optional[float], decimals: int = 1) -> str:
    """Format KRW amount as 억원 with comma separators."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "-"
    v = val / 1e8
    return f"{v:,.{decimals}f}"


def _pct(val: Optional[float], decimals: int = 1) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "-"
    return f"{val * 100:.{decimals}f}%"


def _set_api_key(key: str):
    st.session_state["api_key"] = key.strip()
    os.environ["DART_API_KEY"] = key.strip()


def _apply_api_key():
    """Push the session API key into the environment for dart_api calls."""
    os.environ["DART_API_KEY"] = st.session_state.get("api_key", "")


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📊 DART DCF 분석")
    st.divider()

    # API key
    api_key_input = st.text_input(
        "DART API 키",
        value=st.session_state["api_key"],
        type="password",
        placeholder="DART OpenAPI 키를 입력하세요",
        help="dart.fss.or.kr에서 발급받은 API 키를 입력합니다.",
    )
    if api_key_input != st.session_state["api_key"]:
        _set_api_key(api_key_input)

    st.divider()

    # Navigation
    st.subheader("메뉴")
    pages = ["기업 검색", "재무제표 확인", "DCF 가정 입력", "DCF 결과"]
    page = st.radio("이동", pages, index=pages.index(st.session_state["page"]))
    st.session_state["page"] = page

    st.divider()

    # Current company info pill
    if st.session_state["selected_corp"]:
        corp = st.session_state["selected_corp"]
        st.success(f"선택된 기업: **{corp['corp_name']}**\n\n종목코드: {corp['stock_code']}")

_apply_api_key()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — 기업 검색
# ══════════════════════════════════════════════════════════════════════════════

if page == "기업 검색":
    st.header("🔍 기업 검색")

    col1, col2 = st.columns([3, 1])
    with col1:
        keyword = st.text_input(
            "기업명 또는 종목코드",
            placeholder="예) 삼성전자 또는 005930",
        )
    with col2:
        st.write("")
        st.write("")
        search_btn = st.button("검색", use_container_width=True)

    if search_btn:
        if not st.session_state["api_key"]:
            st.error("사이드바에서 DART API 키를 먼저 입력해 주세요.")
        elif not keyword.strip():
            st.warning("검색어를 입력해 주세요.")
        else:
            with st.spinner("기업 목록 검색 중..."):
                try:
                    results = dart_api.search_company(keyword.strip())
                    st.session_state["search_results"] = results
                    if not results:
                        st.info("검색 결과가 없습니다.")
                except Exception as e:
                    st.error(f"검색 오류: {e}")
                    st.session_state["search_results"] = []

    results = st.session_state["search_results"]
    if results:
        st.subheader(f"검색 결과 ({len(results)}건)")
        df_results = pd.DataFrame(results)[["corp_name", "stock_code", "corp_code"]]
        df_results.columns = ["기업명", "종목코드", "고유번호"]
        st.dataframe(df_results, use_container_width=True, hide_index=True)

        st.subheader("기업 선택")
        corp_names = [r["corp_name"] for r in results]
        selected_name = st.selectbox("기업 선택", corp_names)
        selected = next((r for r in results if r["corp_name"] == selected_name), None)

        col_a, col_b = st.columns([2, 1])
        with col_a:
            year_options = list(range(2021, 2025))
            sel_year = st.selectbox("기준 연도 (재무제표 조회)", year_options, index=year_options.index(2023))
        with col_b:
            st.write("")
            st.write("")
            load_btn = st.button("재무제표 불러오기", use_container_width=True)

        if load_btn and selected:
            if not st.session_state["api_key"]:
                st.error("DART API 키를 먼저 입력해 주세요.")
            else:
                with st.spinner(f"{selected_name} 재무제표 불러오는 중 (최근 3년)..."):
                    try:
                        years = [sel_year - 2, sel_year - 1, sel_year]
                        hist_df = dart_api.build_historical_summary(
                            selected["corp_code"], years
                        )
                        company_info = dart_api.get_company_info(selected["corp_code"])

                        st.session_state["selected_corp"] = selected
                        st.session_state["selected_year"] = sel_year
                        st.session_state["hist_df"] = hist_df
                        st.session_state["company_info"] = company_info
                        st.session_state["assumptions"] = {}
                        st.session_state["dcf_result"] = None
                        st.success(f"{selected_name} 재무제표를 불러왔습니다. '재무제표 확인' 탭으로 이동하세요.")
                    except Exception as e:
                        st.error(f"재무제표 로드 오류: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — 재무제표 확인
# ══════════════════════════════════════════════════════════════════════════════

elif page == "재무제표 확인":
    st.header("📋 재무제표 확인")

    hist_df: Optional[pd.DataFrame] = st.session_state.get("hist_df")
    corp = st.session_state.get("selected_corp")
    company_info = st.session_state.get("company_info")

    if hist_df is None or hist_df.empty:
        st.info("먼저 '기업 검색' 페이지에서 기업을 선택하고 재무제표를 불러오세요.")
        st.stop()

    corp_name = corp["corp_name"] if corp else "선택된 기업"
    st.subheader(f"{corp_name} 재무 현황")

    if company_info:
        with st.expander("기업 기본정보", expanded=False):
            info_cols = {
                "corp_name": "기업명", "stock_code": "종목코드",
                "ceo_nm": "대표이사", "adres": "주소",
                "ind_tp": "업종", "est_dt": "설립일",
                "acc_mt": "결산월",
            }
            for k, label in info_cols.items():
                val = company_info.get(k, "-")
                if val:
                    st.write(f"**{label}:** {val}")

    years = sorted(hist_df["year"].tolist())
    yr_strs = [str(y) for y in years]

    # ── 손익계산서 ─────────────────────────────────────────────────────────
    st.subheader("손익계산서 (단위: 억원)")
    is_rows = {
        "매출액": "revenue",
        "매출원가": "cogs",
        "매출총이익": "gross_profit",
        "판관비": "sga",
        "영업이익": "ebit",
        "EBITDA": "ebitda",
        "순이익": "net_income",
    }
    is_data = {}
    for label, col in is_rows.items():
        if col in hist_df.columns:
            is_data[label] = [_억원(hist_df[hist_df["year"] == y][col].values[0] if len(hist_df[hist_df["year"] == y]) > 0 else None) for y in years]
        else:
            is_data[label] = ["-"] * len(years)
    is_table = pd.DataFrame(is_data, index=yr_strs).T
    st.dataframe(is_table, use_container_width=True)

    # ── 수익성 지표 ──────────────────────────────────────────────────────────
    st.subheader("주요 수익성 지표")
    ratio_data = {}
    for yr in years:
        row = hist_df[hist_df["year"] == yr]
        if len(row) == 0:
            ratio_data[str(yr)] = ["-"] * 4
            continue
        r = row.iloc[0]
        rev = r.get("revenue") or 0
        eq = r.get("total_equity") or 0
        assets = r.get("total_assets") or 0
        ratio_data[str(yr)] = [
            _pct(r.get("ebit") / rev if rev else None),
            _pct(r.get("net_income") / rev if rev else None),
            _pct(r.get("net_income") / eq if eq else None),
            _pct(r.get("net_income") / assets if assets else None),
        ]
    ratio_idx = ["영업이익률", "순이익률", "ROE", "ROA"]
    ratio_table = pd.DataFrame(ratio_data, index=ratio_idx)
    st.dataframe(ratio_table, use_container_width=True)

    # ── 재무상태표 ──────────────────────────────────────────────────────────
    st.subheader("재무상태 (단위: 억원)")
    bs_rows = {
        "총자산": "total_assets",
        "유동자산": "current_assets",
        "유동부채": "current_liabilities",
        "자기자본": "total_equity",
        "총차입금": "total_debt",
        "현금성자산": "cash",
        "순운전자본": "nwc",
    }
    bs_data = {}
    for label, col in bs_rows.items():
        if col in hist_df.columns:
            bs_data[label] = [
                _억원(hist_df[hist_df["year"] == y][col].values[0] if len(hist_df[hist_df["year"] == y]) > 0 else None)
                for y in years
            ]
        else:
            bs_data[label] = ["-"] * len(years)

    # 순부채 = total_debt - cash
    net_debt_vals = []
    for yr in years:
        row = hist_df[hist_df["year"] == yr]
        if len(row) == 0:
            net_debt_vals.append("-")
            continue
        r = row.iloc[0]
        nd = (r.get("total_debt") or 0) - (r.get("cash") or 0)
        net_debt_vals.append(_억원(nd))
    bs_data["순부채"] = net_debt_vals

    bs_table = pd.DataFrame(bs_data, index=yr_strs).T
    st.dataframe(bs_table, use_container_width=True)

    # ── 현금흐름 ────────────────────────────────────────────────────────────
    st.subheader("현금흐름 (단위: 억원)")
    cf_rows = {"감가상각(D&A)": "da", "자본적지출(CapEx)": "capex"}
    cf_data = {}
    for label, col in cf_rows.items():
        if col in hist_df.columns:
            cf_data[label] = [
                _억원(hist_df[hist_df["year"] == y][col].values[0] if len(hist_df[hist_df["year"] == y]) > 0 else None)
                for y in years
            ]
        else:
            cf_data[label] = ["-"] * len(years)

    # FCFF 추정 (ebit*(1-t) + da - capex)
    fcff_vals = []
    for yr in years:
        row = hist_df[hist_df["year"] == yr]
        if len(row) == 0:
            fcff_vals.append("-")
            continue
        r = row.iloc[0]
        ebit = r.get("ebit") or 0
        da = r.get("da") or 0
        capex = r.get("capex") or 0
        tr = r.get("tax_rate") or 0.22
        fcff_est = ebit * (1 - tr) + da - capex
        fcff_vals.append(_억원(fcff_est))
    cf_data["추정 FCFF"] = fcff_vals

    cf_table = pd.DataFrame(cf_data, index=yr_strs).T
    st.dataframe(cf_table, use_container_width=True)

    # ── 차트 ────────────────────────────────────────────────────────────────
    st.subheader("매출 및 이익 추이")
    chart_cols = ["revenue", "ebit", "net_income"]
    chart_labels = {"revenue": "매출액", "ebit": "영업이익", "net_income": "순이익"}

    chart_data = []
    for col in chart_cols:
        if col not in hist_df.columns:
            continue
        for yr in years:
            row = hist_df[hist_df["year"] == yr]
            if len(row) == 0:
                continue
            val = row.iloc[0][col]
            if val is not None:
                chart_data.append({"연도": str(yr), "항목": chart_labels[col], "금액(억원)": val / 1e8})

    if chart_data:
        fig_rev = px.bar(
            pd.DataFrame(chart_data),
            x="연도", y="금액(억원)", color="항목", barmode="group",
            title="매출액·영업이익·순이익 추이 (억원)",
        )
        st.plotly_chart(fig_rev, use_container_width=True)

    # Margin trend
    margin_data = []
    for yr in years:
        row = hist_df[hist_df["year"] == yr]
        if len(row) == 0:
            continue
        r = row.iloc[0]
        rev = r.get("revenue") or 0
        if rev == 0:
            continue
        margin_data.append({
            "연도": str(yr),
            "영업이익률(%)": (r.get("ebit") or 0) / rev * 100,
            "순이익률(%)": (r.get("net_income") or 0) / rev * 100,
        })

    if margin_data:
        df_margin = pd.DataFrame(margin_data).melt(id_vars="연도", var_name="지표", value_name="(%)")
        fig_margin = px.line(df_margin, x="연도", y="(%)", color="지표", markers=True, title="이익률 추이")
        st.plotly_chart(fig_margin, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — DCF 가정 입력
# ══════════════════════════════════════════════════════════════════════════════

elif page == "DCF 가정 입력":
    st.header("⚙️ DCF 가정 입력")

    hist_df: Optional[pd.DataFrame] = st.session_state.get("hist_df")
    corp = st.session_state.get("selected_corp")

    if hist_df is None or hist_df.empty:
        st.info("먼저 '기업 검색' 페이지에서 재무제표를 불러오세요.")
        st.stop()

    # Latest historical row
    latest = hist_df.sort_values("year").iloc[-1]
    base_year = int(latest["year"])
    base_rev = float(latest.get("revenue") or 0)
    base_ebit = float(latest.get("ebit") or 0)
    base_da = float(latest.get("da") or 0)
    base_capex = float(latest.get("capex") or 0)
    base_nwc = float(latest.get("nwc") or 0)
    base_tr = float(latest.get("tax_rate") or 0.22)
    base_total_debt = float(latest.get("total_debt") or 0)
    base_cash = float(latest.get("cash") or 0)
    base_total_equity = float(latest.get("total_equity") or 0)

    asmp = st.session_state.get("assumptions", {})

    # ── 1. 매출액 가정 ───────────────────────────────────────────────────────
    with st.expander("1️⃣ 매출액 가정", expanded=True):
        rev_method = st.radio(
            "매출 예측 방식",
            ["전체 매출 기준", "사업부문별"],
            horizontal=True,
            key="rev_method_radio",
        )
        asmp["revenue_method"] = "total" if rev_method == "전체 매출 기준" else "segment"

        if asmp["revenue_method"] == "total":
            st.write(f"기준연도 매출액: **{_억원(base_rev)} 억원** ({base_year}년)")
            cols = st.columns(5)
            growth_rates = []
            for i, col in enumerate(cols):
                with col:
                    g = col.number_input(
                        f"Y{i+1} 성장률(%)",
                        value=float(asmp.get("revenue_total", {}).get("growth_rates", [5.0]*5)[i] if asmp.get("revenue_total") else 5.0),
                        min_value=-50.0, max_value=100.0, step=0.5,
                        key=f"rev_g_{i}",
                    )
                    growth_rates.append(g / 100)
            asmp["revenue_total"] = {"base": base_rev, "growth_rates": growth_rates}
            asmp.pop("revenue_segments", None)

        else:  # segment
            st.info("DART 사업부문 데이터 또는 직접 입력")
            corp_code = corp["corp_code"] if corp else ""
            if st.button("DART에서 부문 데이터 가져오기"):
                with st.spinner("사업부문 조회 중..."):
                    try:
                        segs = dart_api.get_business_segments(corp_code, base_year)
                        if segs:
                            st.session_state["dart_segments"] = segs
                            st.success(f"{len(segs)}개 부문을 찾았습니다.")
                        else:
                            st.warning("DART에서 부문 데이터를 찾지 못했습니다. 직접 입력하세요.")
                    except Exception as e:
                        st.error(f"부문 조회 오류: {e}")

            dart_segs = st.session_state.get("dart_segments", [])
            n_segs = st.number_input("부문 수", 1, 10, value=max(len(dart_segs), 2), key="n_segs")
            segments = []
            for i in range(int(n_segs)):
                st.markdown(f"**부문 {i+1}**")
                default_name = dart_segs[i]["segment_name"] if i < len(dart_segs) else f"부문{i+1}"
                default_base = float(dart_segs[i]["revenue"]) if i < len(dart_segs) else base_rev / int(n_segs)
                s_cols = st.columns([2, 2, 1, 1, 1, 1, 1])
                seg_name = s_cols[0].text_input("부문명", value=default_name, key=f"seg_nm_{i}")
                seg_base = s_cols[1].number_input("기준 매출(억원)", value=default_base/1e8, step=1.0, key=f"seg_base_{i}") * 1e8
                seg_rates = []
                for j in range(5):
                    g = s_cols[j+2].number_input(f"Y{j+1}(%)", value=5.0, step=0.5, key=f"seg_g_{i}_{j}")
                    seg_rates.append(g / 100)
                segments.append({"name": seg_name, "base": seg_base, "growth_rates": seg_rates})
            asmp["revenue_segments"] = segments

    # ── 2. 비용 가정 ─────────────────────────────────────────────────────────
    with st.expander("2️⃣ 비용 가정", expanded=True):
        col_c1, col_c2 = st.columns(2)

        with col_c1:
            st.markdown("**매출원가 (COGS)**")
            cogs_method = st.radio("방식", ["매출 대비 비율", "성장률"], key="cogs_method_radio", horizontal=True)
            asmp["cogs_method"] = "pct_revenue" if cogs_method == "매출 대비 비율" else "growth"
            if asmp["cogs_method"] == "pct_revenue":
                default_cogs_pct = (latest.get("cogs") or 0) / base_rev * 100 if base_rev else 60.0
                cogs_pct = st.slider("COGS / 매출 (%)", 0.0, 100.0, float(asmp.get("cogs_pct", default_cogs_pct) * 100 if "cogs_pct" in asmp else default_cogs_pct), 0.5)
                asmp["cogs_pct"] = cogs_pct / 100
            else:
                cogs_gs = []
                for i in range(5):
                    g = st.number_input(f"COGS Y{i+1} 성장률(%)", value=3.0, step=0.5, key=f"cogs_g_{i}")
                    cogs_gs.append(g / 100)
                asmp["cogs_growth"] = cogs_gs

        with col_c2:
            st.markdown("**판관비 (SGA)**")
            sga_method = st.radio("방식", ["매출 대비 비율", "성장률"], key="sga_method_radio", horizontal=True)
            asmp["sga_method"] = "pct_revenue" if sga_method == "매출 대비 비율" else "growth"
            if asmp["sga_method"] == "pct_revenue":
                default_sga_pct = (latest.get("sga") or 0) / base_rev * 100 if base_rev else 20.0
                sga_pct = st.slider("SGA / 매출 (%)", 0.0, 100.0, float(asmp.get("sga_pct", default_sga_pct) * 100 if "sga_pct" in asmp else default_sga_pct), 0.5)
                asmp["sga_pct"] = sga_pct / 100
            else:
                sga_gs = []
                for i in range(5):
                    g = st.number_input(f"SGA Y{i+1} 성장률(%)", value=3.0, step=0.5, key=f"sga_g_{i}")
                    sga_gs.append(g / 100)
                asmp["sga_growth"] = sga_gs

    # ── 3. D&A 및 CapEx ──────────────────────────────────────────────────────
    with st.expander("3️⃣ D&A 및 CapEx", expanded=True):
        col_d1, col_d2 = st.columns(2)

        with col_d1:
            st.markdown("**감가상각 (D&A)**")
            da_method = st.radio("방식", ["매출 대비 비율", "고정금액", "전년 대비 성장"], key="da_method_radio", horizontal=True)
            if da_method == "매출 대비 비율":
                asmp["da_method"] = "pct_revenue"
                default_da_pct = base_da / base_rev * 100 if base_rev else 3.0
                da_pct = st.number_input("D&A / 매출 (%)", value=float(asmp.get("da_pct", default_da_pct / 100) * 100 if "da_pct" in asmp else default_da_pct), min_value=0.0, step=0.1)
                asmp["da_pct"] = da_pct / 100
            elif da_method == "고정금액":
                asmp["da_method"] = "fixed"
                da_fixed = st.number_input("D&A 고정액(억원)", value=float(asmp.get("da_fixed", base_da / 1e8) if "da_fixed" in asmp else base_da / 1e8), min_value=0.0, step=1.0)
                asmp["da_fixed"] = da_fixed * 1e8
            else:
                asmp["da_method"] = "growth"
                da_gs = []
                for i in range(5):
                    g = st.number_input(f"D&A Y{i+1} 성장률(%)", value=3.0, step=0.5, key=f"da_g_{i}")
                    da_gs.append(g / 100)
                asmp["da_growth"] = da_gs

        with col_d2:
            st.markdown("**자본적 지출 (CapEx)**")
            capex_method = st.radio("방식", ["감가상각만큼 재투자", "매출 대비 비율", "고정금액"], key="capex_method_radio", horizontal=True)
            if capex_method == "감가상각만큼 재투자":
                asmp["capex_method"] = "equal_da"
                st.info("CapEx = D&A (유지보수 수준)")
            elif capex_method == "매출 대비 비율":
                asmp["capex_method"] = "pct_revenue"
                default_capex_pct = base_capex / base_rev * 100 if base_rev else 4.0
                capex_pct = st.number_input("CapEx / 매출 (%)", value=float(asmp.get("capex_pct", default_capex_pct / 100) * 100 if "capex_pct" in asmp else default_capex_pct), min_value=0.0, step=0.1)
                asmp["capex_pct"] = capex_pct / 100
            else:
                asmp["capex_method"] = "fixed"
                capex_fixed = st.number_input("CapEx 고정액(억원)", value=float(asmp.get("capex_fixed", base_capex / 1e8) if "capex_fixed" in asmp else base_capex / 1e8), min_value=0.0, step=1.0)
                asmp["capex_fixed"] = capex_fixed * 1e8

    # ── 4. 순운전자본 ────────────────────────────────────────────────────────
    with st.expander("4️⃣ 순운전자본 (NWC) 변동", expanded=True):
        nwc_method = st.radio("방식", ["매출 대비 비율 유지", "고정 변동액"], key="nwc_method_radio", horizontal=True)
        if nwc_method == "매출 대비 비율 유지":
            asmp["nwc_method"] = "pct_revenue"
            default_nwc_pct = base_nwc / base_rev * 100 if base_rev else 10.0
            nwc_pct = st.number_input("NWC / 매출 (%)", value=float(asmp.get("nwc_pct", default_nwc_pct / 100) * 100 if "nwc_pct" in asmp else default_nwc_pct), step=0.5)
            asmp["nwc_pct"] = nwc_pct / 100
        else:
            asmp["nwc_method"] = "fixed"
            nwc_fixed = st.number_input("연간 NWC 변동액(억원)", value=float(asmp.get("nwc_fixed", 0) / 1e8 if "nwc_fixed" in asmp else 0), step=1.0)
            asmp["nwc_fixed"] = nwc_fixed * 1e8

    # ── 5. 세율 ─────────────────────────────────────────────────────────────
    with st.expander("5️⃣ 세율", expanded=True):
        tax_rate = st.number_input(
            "유효 법인세율 (%)",
            value=float(asmp.get("tax_rate", base_tr) * 100 if "tax_rate" in asmp else base_tr * 100),
            min_value=0.0, max_value=60.0, step=0.5,
        )
        asmp["tax_rate"] = tax_rate / 100

    # ── 6. WACC ──────────────────────────────────────────────────────────────
    with st.expander("6️⃣ WACC 입력", expanded=True):
        wacc_input_method = st.radio("자기자본비용 산출 방식", ["직접 입력", "CAPM"], horizontal=True, key="ke_method")

        if wacc_input_method == "직접 입력":
            ke = st.number_input("자기자본비용 Ke (%)", value=float(asmp.get("cost_of_equity", 0.10) * 100 if "cost_of_equity" in asmp else 10.0), min_value=0.0, step=0.1)
            asmp["cost_of_equity"] = ke / 100
            asmp.pop("risk_free_rate", None)
            asmp.pop("beta", None)
            asmp.pop("equity_risk_premium", None)
        else:  # CAPM
            c1, c2, c3 = st.columns(3)
            rf = c1.number_input("무위험수익률 Rf (%)", value=float(asmp.get("risk_free_rate", 0.035) * 100 if "risk_free_rate" in asmp else 3.5), min_value=0.0, step=0.1)
            beta = c2.number_input("베타 (β)", value=float(asmp.get("beta", 1.0) if "beta" in asmp else 1.0), min_value=0.0, step=0.05)
            erp = c3.number_input("주식위험프리미엄 ERP (%)", value=float(asmp.get("equity_risk_premium", 0.055) * 100 if "equity_risk_premium" in asmp else 5.5), min_value=0.0, step=0.1)
            asmp["risk_free_rate"] = rf / 100
            asmp["beta"] = beta
            asmp["equity_risk_premium"] = erp / 100
            asmp.pop("cost_of_equity", None)
            ke_capm = rf / 100 + beta * erp / 100
            st.info(f"CAPM Ke = {ke_capm*100:.2f}%  (= {rf:.1f}% + {beta:.2f} × {erp:.1f}%)")

        c4, c5 = st.columns(2)
        kd = c4.number_input("세전 타인자본비용 Kd (%)", value=float(asmp.get("cost_of_debt", 0.05) * 100 if "cost_of_debt" in asmp else 5.0), min_value=0.0, step=0.1)
        asmp["cost_of_debt"] = kd / 100

        # Capital structure
        st.markdown("**자본구조**")
        cap_struct = st.radio("자본구조 결정 방식", ["재무제표 자동 계산", "직접 입력"], horizontal=True, key="cap_struct")
        total_capital = base_total_debt + base_total_equity
        if cap_struct == "재무제표 자동 계산":
            auto_dw = base_total_debt / total_capital if total_capital > 0 else 0.3
            auto_ew = base_total_equity / total_capital if total_capital > 0 else 0.7
            st.write(f"부채 비중: **{auto_dw*100:.1f}%**, 자기자본 비중: **{auto_ew*100:.1f}%**")
            asmp["debt_weight"] = auto_dw
            asmp["equity_weight"] = auto_ew
        else:
            dw = st.slider("부채 비중 (%)", 0, 100, int(asmp.get("debt_weight", 0.3) * 100 if "debt_weight" in asmp else 30))
            asmp["debt_weight"] = dw / 100
            asmp["equity_weight"] = 1 - dw / 100

        # Preview WACC
        try:
            preview_model = DCFModel(hist_df, asmp)
            preview_wacc = preview_model.calculate_wacc()
            st.success(f"산출된 WACC: **{preview_wacc*100:.2f}%**")
        except Exception as e:
            st.warning(f"WACC 계산 미리보기 오류: {e}")

    # ── 7. Terminal Value ────────────────────────────────────────────────────
    with st.expander("7️⃣ 터미널 밸류", expanded=True):
        tgr = st.number_input(
            "영구 성장률 TGR (%)",
            value=float(asmp.get("tgr", 0.015) * 100 if "tgr" in asmp else 1.5),
            min_value=0.0, max_value=10.0, step=0.1,
        )
        asmp["tgr"] = tgr / 100

    # ── 8. 기타 ─────────────────────────────────────────────────────────────
    with st.expander("8️⃣ 주식수 및 순부채", expanded=True):
        company_info = st.session_state.get("company_info", {}) or {}
        shares_default = float(asmp.get("shares_outstanding", 0)) if "shares_outstanding" in asmp else 0.0
        shares_k = st.number_input(
            "발행주식수 (천주)",
            value=shares_default / 1000 if shares_default else 0.0,
            min_value=0.0, step=100.0,
            help="천주 단위. 0 입력 시 주당가치 계산 생략.",
        )
        asmp["shares_outstanding"] = shares_k * 1000

        net_debt_default = (base_total_debt - base_cash) / 1e8
        nd = st.number_input(
            "순부채 (억원) [총차입금 - 현금]",
            value=float(asmp.get("net_debt", base_total_debt - base_cash) / 1e8 if "net_debt" in asmp else net_debt_default),
            step=10.0,
        )
        asmp["net_debt"] = nd * 1e8

    asmp["base_year"] = base_year
    st.session_state["assumptions"] = asmp

    st.divider()
    if st.button("🚀 DCF 분석 실행", use_container_width=True, type="primary"):
        with st.spinner("DCF 계산 중..."):
            try:
                model = DCFModel(hist_df, asmp)
                result = model.calculate_ev()
                result["model"] = model
                st.session_state["dcf_result"] = result
                st.session_state["page"] = "DCF 결과"
                st.success("DCF 분석 완료! 'DCF 결과' 탭으로 이동합니다.")
                st.rerun()
            except Exception as e:
                st.error(f"DCF 계산 오류: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — DCF 결과
# ══════════════════════════════════════════════════════════════════════════════

elif page == "DCF 결과":
    st.header("📈 DCF 분석 결과")

    result = st.session_state.get("dcf_result")
    if result is None:
        st.info("먼저 'DCF 가정 입력' 페이지에서 분석을 실행하세요.")
        st.stop()

    asmp = st.session_state.get("assumptions", {})
    hist_df = st.session_state.get("hist_df")
    corp = st.session_state.get("selected_corp")
    model: DCFModel = result["model"]

    corp_name = corp["corp_name"] if corp else "분석 기업"
    st.subheader(f"{corp_name} DCF 분석 결과")

    ev = result["ev"]
    wacc = result["wacc"]
    tgr = result["tgr"]
    pv_tv = result["pv_tv"]
    fcff_df = result["fcff_df"]
    pv_fcffs = result["pv_fcffs"]
    pv_fcff_by_year = result["pv_fcff_by_year"]
    terminal_value = result["terminal_value"]
    terminal_fcff = result["terminal_fcff"]

    shares = float(asmp.get("shares_outstanding", 0))
    net_debt = float(asmp.get("net_debt", 0))
    eq_val = model.equity_value(ev)

    # ── Key metrics ──────────────────────────────────────────────────────────
    st.subheader("핵심 지표")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("기업가치 (EV)", f"{_억원(ev)} 억원")
    m2.metric("WACC", f"{wacc*100:.2f}%")
    m3.metric("TGR", f"{tgr*100:.1f}%")
    m4.metric("자기자본가치", f"{_억원(eq_val)} 억원")

    if shares > 0:
        price = model.price_per_share(eq_val, shares)
        m_p1, m_p2, m_p3 = st.columns(3)
        m_p1.metric("주당 내재가치", f"₩{price:,.0f}")
        m_p2.metric("순부채", f"{_억원(net_debt)} 억원")
        m_p3.metric("발행주식수", f"{shares/1000:,.0f} 천주")

    # ── FCFF 상세 테이블 ─────────────────────────────────────────────────────
    st.subheader("FCFF 예측 (단위: 억원)")
    fcff_display = fcff_df.copy()
    display_cols = ["year", "revenue", "ebit", "nopat", "da", "capex", "delta_nwc", "fcff"]
    col_labels = {
        "year": "연도", "revenue": "매출액", "ebit": "영업이익",
        "nopat": "EBIT(1-t)", "da": "D&A", "capex": "CapEx",
        "delta_nwc": "ΔNWC", "fcff": "FCFF",
    }
    fcff_display = fcff_display[[c for c in display_cols if c in fcff_display.columns]].copy()
    fcff_display.rename(columns=col_labels, inplace=True)

    # Add discount factor and PV
    fcff_display["할인계수"] = result["discount_factors"]
    fcff_display["PV(FCFF)"] = pv_fcffs

    # Format numbers
    money_cols = ["매출액", "영업이익", "EBIT(1-t)", "D&A", "CapEx", "ΔNWC", "FCFF", "PV(FCFF)"]
    fmt_df = fcff_display.copy()
    for col in money_cols:
        if col in fmt_df.columns:
            fmt_df[col] = fmt_df[col].apply(lambda x: _억원(x))
    fmt_df["할인계수"] = fmt_df["할인계수"].apply(lambda x: f"{x:.4f}")
    fmt_df["연도"] = fmt_df["연도"].astype(int).astype(str)
    st.dataframe(fmt_df.set_index("연도"), use_container_width=True)

    # ── Terminal Value 계산 ───────────────────────────────────────────────────
    st.subheader("터미널 밸류 계산")
    tv_cols = st.columns(4)
    tv_cols[0].metric("터미널 FCFF", f"{_억원(terminal_fcff)} 억원")
    tv_cols[1].metric("터미널 밸류 (TV)", f"{_억원(terminal_value)} 억원")
    tv_cols[2].metric("TV 현재가치", f"{_억원(pv_tv)} 억원")
    tv_pct = pv_tv / ev * 100 if ev else 0
    tv_cols[3].metric("TV / EV 비중", f"{tv_pct:.1f}%")

    tv_formula = f"TV = {_억원(terminal_fcff)} × (1+{tgr*100:.1f}%) / ({wacc*100:.2f}% - {tgr*100:.1f}%) = {_억원(terminal_value)} 억원"
    st.caption(tv_formula)

    # ── EV Bridge ─────────────────────────────────────────────────────────────
    st.subheader("EV → 주당가치 브릿지")
    pv_fcff_total = float(np.sum(pv_fcffs))
    bridge_items = ["PV of FCFFs", "PV of TV", "Enterprise Value (EV)", "(-) 순부채", "자기자본가치 (Equity Value)"]
    bridge_vals = [pv_fcff_total, pv_tv, ev, -net_debt, eq_val]
    bridge_measures = ["relative", "relative", "total", "relative", "total"]

    fig_bridge = go.Figure(go.Waterfall(
        name="EV Bridge",
        orientation="v",
        measure=bridge_measures,
        x=bridge_items,
        y=[v / 1e8 for v in bridge_vals],
        text=[f"{_억원(v)} 억원" for v in bridge_vals],
        textposition="outside",
        connector={"line": {"color": "rgb(63, 63, 63)"}},
    ))
    fig_bridge.update_layout(title="EV Bridge (억원)", showlegend=False, height=400)
    st.plotly_chart(fig_bridge, use_container_width=True)

    if shares > 0:
        price = model.price_per_share(eq_val, shares)
        bridge_share = pd.DataFrame({
            "항목": ["자기자본가치", "÷ 발행주식수", "주당 내재가치"],
            "값": [f"{_억원(eq_val)} 억원", f"{shares/1000:,.0f} 천주", f"₩{price:,.0f}"],
        })
        st.dataframe(bridge_share, use_container_width=True, hide_index=True)

    # ── FCF Contribution waterfall ────────────────────────────────────────────
    st.subheader("FCF 기여도 (Waterfall)")
    wf_years = [str(int(y)) for y in fcff_df["year"].values]
    wf_vals = list(pv_fcffs)
    fig_wf = go.Figure(go.Waterfall(
        orientation="v",
        measure=["relative"] * len(wf_vals) + ["relative", "total"],
        x=wf_years + ["PV TV", "EV 합계"],
        y=[v / 1e8 for v in wf_vals] + [pv_tv / 1e8, 0],
        text=[f"{_억원(v)}" for v in wf_vals] + [f"{_억원(pv_tv)}", f"{_억원(ev)}"],
        textposition="outside",
    ))
    fig_wf.update_layout(title="FCF + TV → EV 기여도 (억원)", height=400)
    st.plotly_chart(fig_wf, use_container_width=True)

    # ── Sensitivity Analysis ──────────────────────────────────────────────────
    st.subheader("민감도 분석 — WACC × TGR")

    sens_metric = st.radio("민감도 지표", ["EV (억원)", "주당가치 (원)"], horizontal=True, key="sens_metric")
    use_price = sens_metric == "주당가치 (원)" and shares > 0

    wacc_step = st.number_input("WACC 간격 (%p)", value=0.5, min_value=0.1, step=0.1)
    tgr_step = st.number_input("TGR 간격 (%p)", value=0.25, min_value=0.05, step=0.05)

    wacc_range = [wacc + (i - 2) * wacc_step / 100 for i in range(5)]
    tgr_range = [tgr + (j - 2) * tgr_step / 100 for j in range(5)]
    wacc_range = [max(0.01, w) for w in wacc_range]
    tgr_range = [max(0.0, g) for g in tgr_range]

    with st.spinner("민감도 분석 중..."):
        try:
            metric_key = "price_per_share" if use_price else "ev"
            sens_df = model.sensitivity_analysis(wacc_range, tgr_range, metric=metric_key)
            sens_df.index = [f"{w*100:.2f}%" for w in sens_df.index]
            sens_df.columns = [f"{g*100:.2f}%" for g in sens_df.columns]

            if use_price:
                display_sens = sens_df.applymap(lambda x: f"₩{x:,.0f}" if not np.isnan(x) else "-")
            else:
                display_sens = sens_df.applymap(lambda x: f"{x/1e8:,.0f}" if not np.isnan(x) else "-")

            # Heatmap
            z_vals = sens_df.values.astype(float)
            if use_price:
                z_text = [[f"₩{v:,.0f}" if not np.isnan(v) else "-" for v in row] for row in z_vals]
            else:
                z_text = [[f"{v/1e8:,.0f}억" if not np.isnan(v) else "-" for v in row] for row in z_vals]

            fig_heat = go.Figure(go.Heatmap(
                z=z_vals / (1 if use_price else 1e8),
                x=sens_df.columns.tolist(),
                y=sens_df.index.tolist(),
                text=z_text,
                texttemplate="%{text}",
                colorscale="RdYlGn",
                showscale=True,
            ))
            fig_heat.update_layout(
                title=f"민감도 분석: {'주당가치 (원)' if use_price else 'EV (억원)'}",
                xaxis_title="TGR",
                yaxis_title="WACC",
                height=350,
            )
            st.plotly_chart(fig_heat, use_container_width=True)
            st.dataframe(display_sens, use_container_width=True)

        except Exception as e:
            st.error(f"민감도 분석 오류: {e}")

    # ── Excel Export ──────────────────────────────────────────────────────────
    st.subheader("결과 다운로드")

    def _build_excel() -> bytes:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            # Sheet 1: FCFF
            fcff_export = fcff_df.copy()
            for col in ["revenue", "ebit", "nopat", "da", "capex", "delta_nwc", "fcff"]:
                if col in fcff_export.columns:
                    fcff_export[col] = fcff_export[col] / 1e8
            fcff_export["discount_factor"] = result["discount_factors"]
            fcff_export["pv_fcff"] = pv_fcffs / 1e8
            fcff_export.to_excel(writer, sheet_name="FCFF 예측", index=False)

            # Sheet 2: Summary
            summary = pd.DataFrame({
                "항목": ["EV (억원)", "PV FCFFs (억원)", "PV TV (억원)", "TV 비중(%)",
                          "자기자본가치(억원)", "WACC(%)", "TGR(%)", "순부채(억원)"],
                "값": [ev/1e8, pv_fcff_total/1e8, pv_tv/1e8, tv_pct,
                        eq_val/1e8, wacc*100, tgr*100, net_debt/1e8],
            })
            if shares > 0:
                price = model.price_per_share(eq_val, shares)
                summary = pd.concat([summary, pd.DataFrame({"항목": ["주당가치(원)"], "값": [price]})], ignore_index=True)
            summary.to_excel(writer, sheet_name="DCF 요약", index=False)

            # Sheet 3: Sensitivity
            try:
                sens_export = model.sensitivity_analysis(wacc_range, tgr_range, metric="ev")
                sens_export.index = [f"{w*100:.2f}%" for w in sens_export.index]
                sens_export.columns = [f"{g*100:.2f}%" for g in sens_export.columns]
                (sens_export / 1e8).to_excel(writer, sheet_name="민감도분석(EV억원)")
            except Exception:
                pass

            # Sheet 4: Historical
            if hist_df is not None:
                hist_export = hist_df.copy()
                money_cols_hist = ["revenue","cogs","gross_profit","sga","ebit","ebitda","net_income",
                                   "da","capex","total_assets","current_assets","current_liabilities",
                                   "cash","total_debt","total_equity","nwc"]
                for c in money_cols_hist:
                    if c in hist_export.columns:
                        hist_export[c] = hist_export[c] / 1e8
                hist_export.to_excel(writer, sheet_name="역사적재무(억원)", index=False)

        buf.seek(0)
        return buf.read()

    try:
        excel_bytes = _build_excel()
        file_label = f"{corp_name}_DCF분석_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx"
        st.download_button(
            "📥 Excel로 다운로드",
            data=excel_bytes,
            file_name=file_label,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"Excel 생성 오류: {e}")
