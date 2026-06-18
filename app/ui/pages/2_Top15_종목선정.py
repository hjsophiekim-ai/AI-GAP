"""
2_Top15_종목선정.py

네이버 갭상승 수집 → ETF/거래정지/위험종목 제외 → 미국장 섹터 가점 → MA 가점 → Top 15 선정
"""

import io
from datetime import datetime

import pandas as pd
import streamlit as st

try:
    from app.strategy.candidate_generator import CandidateGenerator
    from app.strategy.top15_selector import Top15Selector
    from app.config import get_config
except Exception as e:
    st.error(f"모듈 로드 오류: {e}")

st.title("Top 15 종목 선정")

# ---------------------------------------------------------------------------
# 미국장 섹터 설정
# ---------------------------------------------------------------------------

US_SECTOR_MAP: dict[str, list[str]] = {
    "IT/반도체": ["반도체", "디스플레이", "전자", "IT", "소프트웨어", "AI", "칩"],
    "바이오/헬스케어": ["바이오", "제약", "의료", "헬스", "신약", "항체", "유전"],
    "금융": ["금융", "은행", "보험", "증권", "투자", "캐피탈"],
    "소비재/유통": ["유통", "소비", "식품", "패션", "의류", "화장품", "뷰티", "리테일"],
    "에너지/화학": ["에너지", "석유", "화학", "정유", "가스", "태양광", "수소"],
    "자동차/2차전지": ["자동차", "배터리", "2차전지", "전기차", "부품", "리튬", "양극재"],
    "방산/항공": ["방산", "방위", "항공", "무기", "군수"],
    "엔터/미디어/게임": ["엔터", "콘텐츠", "미디어", "게임", "방송", "드라마", "K-pop"],
}

US_SECTOR_BONUS = 8.0   # 가점


def _sector_bonus(name: str, strong_sectors: list[str]) -> float:
    if not strong_sectors:
        return 0.0
    name_lower = name.lower()
    for sector in strong_sectors:
        keywords = US_SECTOR_MAP.get(sector, [])
        for kw in keywords:
            if kw.lower() in name_lower:
                return US_SECTOR_BONUS
    return 0.0


# ---------------------------------------------------------------------------
# MA 가점 계산
# ---------------------------------------------------------------------------

MA_BONUS = 5.0  # 이동평균 우상향 가점


def _calc_ma_bonus(symbol: str, kis_client) -> float:
    if kis_client is None:
        return 0.0
    try:
        prices = kis_client.get_daily_prices(symbol, days=65)
        if len(prices) < 20:
            return 0.0
        closes = [p["close"] for p in prices]  # 최근이 [0]
        ma5 = sum(closes[:5]) / 5
        ma20 = sum(closes[:20]) / 20
        if len(closes) >= 60:
            ma60 = sum(closes[:60]) / 60
        else:
            ma60 = ma20

        ma5_prev = sum(closes[1:6]) / 5
        ma20_prev = sum(closes[1:21]) / 20

        ma5_up = ma5 > ma5_prev
        ma20_up = ma20 > ma20_prev
        ma5_above_ma20 = ma5 > ma20
        ma20_above_ma60 = ma20 > ma60

        if ma5_up and ma20_up and ma5_above_ma20 and ma20_above_ma60:
            return MA_BONUS
        elif ma5_up and ma5_above_ma20:
            return MA_BONUS * 0.5
        return 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# ETF/위험 종목 필터
# ---------------------------------------------------------------------------

ETF_KEYWORDS = ["ETF", "TIGER", "KODEX", "KBSTAR", "ARIRANG", "HANARO",
                 "KOSEF", "ACE", "SOL", "RISE", "파킹", "레버리지", "인버스",
                 "선물", "리츠", "REITS"]

RISK_KEYWORDS = ["관리", "거래정지", "상장폐지", "불성실", "정리매매", "투자주의"]


def _is_excluded(stock) -> tuple[bool, str]:
    """StockData 객체 또는 dict 모두 지원."""
    if hasattr(stock, "name"):
        name = stock.name or ""
        symbol = stock.symbol or ""
        is_etf_flag = getattr(stock, "is_etf", False)
        is_etn_flag = getattr(stock, "is_etn", False)
        is_spac_flag = getattr(stock, "is_spac", False)
    else:
        name = stock.get("name", "")
        symbol = stock.get("symbol", "")
        is_etf_flag = stock.get("is_etf", False)
        is_etn_flag = stock.get("is_etn", False)
        is_spac_flag = stock.get("is_spac", False)

    for kw in ETF_KEYWORDS:
        if kw.upper() in name.upper():
            return True, f"ETF/파생 제외: {kw}"

    if is_etf_flag or is_etn_flag:
        return True, "ETF/ETN 플래그"

    if is_spac_flag:
        return True, "SPAC 제외"

    if len(symbol) != 6 or not symbol.isdigit():
        return True, f"종목코드 이상: {symbol}"

    for kw in RISK_KEYWORDS:
        if kw in name:
            return True, f"위험종목 제외: {kw}"

    return False, ""


# ---------------------------------------------------------------------------
# formatter helpers
# ---------------------------------------------------------------------------

def _fmt_trade_value(val: float) -> str:
    if val >= 1_000_000_000_000:
        return f"{val / 1_000_000_000_000:.1f}조"
    elif val >= 100_000_000:
        return f"{val / 100_000_000:.0f}억"
    else:
        return f"{val / 100_000_000:.1f}억"


def _style_score(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= 0.7:
        return "background-color:#d4edda;color:#155724"
    elif v >= 0.5:
        return "background-color:#fff3cd;color:#856404"
    return "background-color:#fde8d8;color:#7d3c00"


# ---------------------------------------------------------------------------
# Section 1 — 미국장 강한 섹터 선택
# ---------------------------------------------------------------------------

st.subheader("어제 미국장 강한 섹터 선택 (선택 사항)")
st.caption("선택한 섹터와 관련된 한국 종목에 가점이 부여됩니다.")

strong_sectors = st.multiselect(
    "강세 섹터 선택",
    options=list(US_SECTOR_MAP.keys()),
    default=[],
    placeholder="섹터를 선택하세요 (복수 선택 가능)",
)

# MA 분석 옵션
use_ma = st.checkbox(
    "이동평균선(MA5/MA20/MA60) 우상향 분석 포함",
    value=False,
    help="KIS API로 60일 가격 데이터를 가져옵니다. 종목당 1회 API 호출 — 시간이 더 걸릴 수 있습니다.",
)

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — Top15 종목선정하기 (원클릭)
# ---------------------------------------------------------------------------

st.subheader("Top15 종목 선정")

if st.button("Top15 종목선정하기", type="primary", use_container_width=True):
    progress = st.progress(0, text="시작 중...")
    log_area = st.empty()
    log_msgs: list[str] = []

    def _log(msg: str) -> None:
        log_msgs.append(msg)
        log_area.markdown("\n".join(f"- {m}" for m in log_msgs))

    try:
        cfg = get_config()

        # ── STEP 1: 네이버 갭상승 수집 ─────────────────────────────────
        progress.progress(10, text="STEP 1: 네이버 갭상승 종목 수집 중...")
        _log("STEP 1: 네이버 갭상승 데이터 수집")

        raw_stocks: list[dict] = []
        try:
            from app.data.data_collector import DataCollector
            collector = DataCollector(cfg)
            result = collector.collect_gap_candidates()
            raw_stocks = result.get("candidates", [])
            source = result.get("source", "unknown")
            is_sample = result.get("is_sample", False)
            _log(f"  수집 완료: {len(raw_stocks)}개 (출처: {source})")
            if is_sample:
                _log("  샘플 데이터 사용 중 (실제 API 응답 없음)")
        except Exception as ex:
            _log(f"  DataCollector 실패 → StockCollector 시도: {ex}")
            try:
                from app.data.stock_collector import StockCollector
                collector2 = StockCollector(cfg=cfg)
                raw_stocks = collector2.collect()
                _log(f"  StockCollector 수집: {len(raw_stocks)}개")
            except Exception as ex2:
                _log(f"  StockCollector도 실패: {ex2}")

        if not raw_stocks:
            st.error("종목 수집 실패. 네트워크 연결 및 네이버 갭상승 페이지를 확인하세요.")
            progress.empty()
            st.stop()

        # ── STEP 2: ETF/거래정지/위험종목 제외 ─────────────────────────
        progress.progress(25, text="STEP 2: ETF/거래정지/위험종목 제외 중...")
        _log("STEP 2: ETF / 거래정지 / 위험종목 제외")

        valid_stocks = []
        excluded_stocks: list[dict] = []
        for s in raw_stocks:
            excl, reason = _is_excluded(s)
            if excl:
                # StockData 객체는 mapping이 아니므로 직접 dict 구성
                sym  = s.symbol if hasattr(s, "symbol") else s.get("symbol", "")
                name = s.name   if hasattr(s, "name")   else s.get("name", "")
                excluded_stocks.append({"symbol": sym, "name": name, "reason": reason})
            else:
                valid_stocks.append(s)

        _log(f"  유효 종목: {len(valid_stocks)}개 | 제외: {len(excluded_stocks)}개")
        st.session_state["excluded_stocks"] = excluded_stocks

        # ── STEP 3: 미국장 섹터 가점 ────────────────────────────────────
        progress.progress(40, text="STEP 3: 미국장 섹터 가점 계산 중...")
        _log(f"STEP 3: 미국장 섹터 가점 ({', '.join(strong_sectors) or '없음'})")

        sym_extra: dict[str, float] = {}
        sym_reasons: dict[str, list] = {}
        for s in valid_stocks:
            sym = s.symbol if hasattr(s, "symbol") else s.get("symbol", "")
            name = s.name if hasattr(s, "name") else s.get("name", "")
            bonus = _sector_bonus(name, strong_sectors)
            sym_extra[sym] = sym_extra.get(sym, 0.0) + bonus
            if bonus > 0:
                sym_reasons.setdefault(sym, []).append("미국섹터")

        sector_bonus_cnt = sum(1 for v in sym_extra.values() if v > 0)
        _log(f"  섹터 가점 종목: {sector_bonus_cnt}개")

        # ── STEP 4: MA 이동평균선 가점 ──────────────────────────────────
        progress.progress(55, text="STEP 4: 이동평균선 분석 중...")
        _log("STEP 4: 이동평균선(MA5/MA20/MA60) 우상향 가점")

        if use_ma:
            try:
                from app.trading.kis_client import create_kis_client
                kis = create_kis_client("mock")
                ma_bonus_cnt = 0
                for i, s in enumerate(valid_stocks):
                    progress.progress(
                        55 + int(15 * i / max(len(valid_stocks), 1)),
                        text=f"MA 분석 중... {i+1}/{len(valid_stocks)}",
                    )
                    sym = s.symbol if hasattr(s, "symbol") else s.get("symbol", "")
                    bonus = _calc_ma_bonus(sym, kis)
                    sym_extra[sym] = sym_extra.get(sym, 0.0) + bonus
                    if bonus > 0:
                        sym_reasons.setdefault(sym, []).append("MA우상향")
                        ma_bonus_cnt += 1
                _log(f"  MA 우상향 가점 종목: {ma_bonus_cnt}개")
            except Exception as ex:
                _log(f"  MA 분석 실패 (건너뜀): {ex}")
        else:
            _log("  MA 분석 건너뜀 (옵션 미선택)")

        # ── STEP 5: extra_score를 raw_stocks에 반영하고 후보 생성 ────────
        progress.progress(75, text="STEP 5: 후보 50 생성 및 Top15 선정 중...")
        _log("STEP 5: 후보 생성 및 Top15 선정")

        generator = CandidateGenerator(cfg=cfg)
        predictions = None
        try:
            from app.ml.predict_model import ModelPredictor
            from app.features.feature_builder import FeatureBuilder
            predictor = ModelPredictor(cfg=cfg)
            fb = FeatureBuilder()
            features = fb.build_features(valid_stocks)
            if features:
                predictions = predictor.predict(features)
        except Exception:
            pass

        candidates = generator.generate(valid_stocks, predictions=predictions)

        # sym_extra 가점 적용 (섹터+MA)
        for c in candidates:
            bonus = sym_extra.get(c.symbol, 0.0)
            if bonus > 0:
                c.final_score = round(c.final_score + bonus / 100.0, 4)
                reasons = sym_reasons.get(c.symbol, [])
                if reasons:
                    c.selected_reason = (c.selected_reason or "") + f" [{', '.join(reasons)}]"

        # 점수 내림차순 재정렬
        candidates.sort(key=lambda c: c.final_score, reverse=True)
        for i, c in enumerate(candidates, 1):
            c.rank = i

        st.session_state["candidates"] = candidates
        _log(f"  후보 {len(candidates)}개 생성")

        selector = Top15Selector(cfg=cfg)
        top15 = selector.select(candidates)

        date_str = datetime.now().strftime("%Y%m%d")
        saved_path = selector.save_top15(top15, date_str=date_str)

        st.session_state["top15"] = top15
        st.session_state["top15_csv_path"] = saved_path
        st.session_state["top15_selected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        progress.progress(100, text="완료!")
        _log(f"  Top {len(top15)}개 선정 완료! (저장: {saved_path})")
        st.success(f"Top {len(top15)}개 종목 선정 완료!")

    except Exception as ex:
        st.error(f"선정 중 오류 발생: {ex}")
        progress.empty()

# ---------------------------------------------------------------------------
# 결과 표시
# ---------------------------------------------------------------------------


def _risk_label(c) -> str:
    """risk_comment + fallback 여부를 합쳐 표시용 문자열 반환."""
    parts = []
    rc = (c.risk_comment or "").strip()
    if rc:
        parts.append(rc)
    if getattr(c, "fallback_included", False):
        parts.append("완화기준포함")
    return ", ".join(parts)


if st.session_state.get("top15"):
    top15 = st.session_state["top15"]
    selected_at = st.session_state.get("top15_selected_at", "")
    if selected_at:
        st.caption(f"선정 시각: {selected_at}")

    n_risky = sum(1 for c in top15 if _risk_label(c))
    n_safe = len(top15) - n_risky

    # 요약 카드
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("선정 종목", f"{len(top15)}개")
    with col2:
        st.metric("정상", f"{n_safe}개")
    with col3:
        st.metric("위험신호 🔴", f"{n_risky}개")

    if n_risky > 0:
        st.caption(
            "🔴 위험신호 종목은 조건 완화로 포함된 종목입니다. "
            "표 하단 '위험신호 상세'에서 사유를 확인하세요."
        )

    rows = []
    for c in top15:
        medal = {1: "1", 2: "2", 3: "3"}.get(c.rank, str(c.rank))
        risk = _risk_label(c)
        rows.append({
            "순위": medal,
            "위험": "🔴" if risk else "⚪",
            "종목코드": c.symbol,
            "종목명": c.name,
            "현재가": f"{int(c.current_price):,}",
            "갭률(%)": round(c.gap_rate, 2),
            "거래대금": _fmt_trade_value(c.trade_value),
            "ML점수": round(c.ml_score, 4),
            "룰점수": round(c.rule_score, 4),
            "최종점수": round(c.final_score, 4),
            "선정이유": c.selected_reason,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.map(_style_score, subset=["최종점수"]),
        use_container_width=True,
        hide_index=True,
        column_config={"위험": st.column_config.TextColumn("위험", width=50)},
    )

    # 위험신호 종목 상세
    risky_items = [c for c in top15 if _risk_label(c)]
    if risky_items:
        with st.expander(f"🔴 위험신호 종목 상세 ({len(risky_items)}개)"):
            detail_rows = []
            for c in risky_items:
                detail_rows.append({
                    "종목코드": c.symbol,
                    "종목명": c.name,
                    "위험사유": _risk_label(c),
                    "갭률(%)": round(c.gap_rate, 2),
                    "시가대비(%)": round(c.open_to_current_rate, 2),
                    "거래대금": _fmt_trade_value(c.trade_value),
                })
            st.dataframe(
                pd.DataFrame(detail_rows),
                use_container_width=True,
                hide_index=True,
            )

    # 제외 종목
    excluded = st.session_state.get("excluded_stocks", [])
    if excluded:
        with st.expander(f"제외된 종목 ({len(excluded)}개)"):
            exc_rows = [
                {"종목코드": s.get("symbol", ""), "종목명": s.get("name", ""), "제외사유": s.get("reason", "")}
                for s in excluded
            ]
            st.dataframe(pd.DataFrame(exc_rows), use_container_width=True, hide_index=True)

    # CSV 다운로드
    st.divider()
    csv_buf = io.StringIO()
    df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
    st.download_button(
        label="CSV 다운로드",
        data=csv_buf.getvalue().encode("utf-8-sig"),
        file_name=f"{datetime.now().strftime('%Y%m%d')}_top15.csv",
        mime="text/csv",
    )

    st.page_link(
        "pages/3_예산배분_및_매수.py",
        label="→ 예산배분 및 매수로 이동",
        icon="💰",
    )

elif st.session_state.get("candidates"):
    st.info(f"후보 {len(st.session_state['candidates'])}개가 있습니다. 'Top15 종목선정하기'를 클릭하세요.")
