"""
candidate_quality_filter.py

후보 종목에 대한 품질 필터 및 점수 보정 모듈.

처리 순서:
  1. 빠른 제외 필터 (ETF/ETN/우선주/스팩/리츠/동전주/거래대금/갭 과다)
  2. 점수 보정 (갭 모멘텀, MA 정배열, 과열, 장초반 약세, 테마 대장주)
  3. 테마 cap 적용 (동일 테마 최대 N개)
  4. 설명 CSV / 제외 CSV 저장

설계 원칙:
  - 이미 수집된 네이버 갭상승 데이터를 최대한 재사용; 추가 KIS API 호출 없음
  - MA 등 일봉 데이터가 없으면 해당 필터 스킵 (프로그램 중단 없음)
  - 실전 주문 모드에서만 KIS API로 최종 현재가/거래가능 여부 검증
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from app.logger import logger
from app.models import Candidate, StockData
from app.config import get_config

try:
    from app.services.us_theme_map import match_kr_stock_to_themes
except ImportError:
    def match_kr_stock_to_themes(name: str, sector: str = "") -> list[str]:  # type: ignore[misc]
        return []


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ETF_ETN_KEYWORDS = [
    "KODEX", "TIGER", "ACE", "SOL", "PLUS", "KBSTAR", "KOSEF",
    "HANARO", "ARIRANG", "ETN", "ETF", "레버리지", "인버스", "선물",
    "합성", "TR", "RISE", "FOCUS", "TREX", "TIMEFOLIO", "WOORI",
]

SPAC_KEYWORDS = ["스팩", "SPAC"]
REIT_KEYWORDS = ["리츠", "REIT", "REITS"]
RISK_KEYWORDS = ["관리", "거래정지", "상장폐지", "불성실", "정리매매", "투자주의"]

# 우선주 판별 패턴: 이름 끝이 '우', '1우B', '2우B' 등으로 끝나는 경우만 매칭
_PREFERRED_RE = re.compile(r'(?:우B?|\d+우B?)$')


# ---------------------------------------------------------------------------
# Main Class
# ---------------------------------------------------------------------------

class CandidateQualityFilter:
    """
    후보 종목에 빠른 제외 + 점수 보정 + 테마 cap을 적용한다.

    Parameters
    ----------
    cfg : Config | None
        앱 설정. None이면 get_config() 사용.
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or get_config()
        self._qcfg: dict = self.cfg._raw.get("candidate_quality_filters", {})

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def filter_and_score(
        self,
        candidates: list[Candidate],
        stock_data_by_symbol: Optional[dict[str, StockData]] = None,
        daily_prices_cache: Optional[dict[str, list[dict]]] = None,
    ) -> tuple[list[Candidate], list[dict]]:
        """
        후보 목록에 필터 및 점수 보정을 적용한다.

        Parameters
        ----------
        candidates : list[Candidate]
            CandidateGenerator가 생성한 후보 목록 (이미 정렬됨).
        stock_data_by_symbol : dict | None
            {symbol: StockData} — is_etf, is_preferred 등 플래그 참조용.
        daily_prices_cache : dict | None
            {symbol: [{date, close, open, high, low, volume}, ...]} (최신 순).
            없으면 MA/수익률 관련 필터 스킵.

        Returns
        -------
        (passed_candidates, excluded_list)
            passed_candidates : 점수 보정 + 테마 cap 적용 후 재정렬된 리스트.
            excluded_list : 제외된 종목 dict 목록.
        """
        if not self._qcfg.get("enabled", True):
            return candidates, []

        sdb = stock_data_by_symbol or {}
        dpc = daily_prices_cache or {}
        speed_mode: bool = self._qcfg.get("speed_mode", True)
        heavy_limit: int = self._qcfg.get("max_candidates_for_heavy_filters", 30)

        # ── Step 1: 빠른 제외 필터 ──────────────────────────────────────
        passed: list[Candidate] = []
        excluded: list[dict] = []

        for c in candidates:
            sd = sdb.get(c.symbol)
            reason = self._fast_exclude(c, sd)
            if reason:
                excluded.append({
                    "code": c.symbol,
                    "name": c.name,
                    "gap_rate": round(c.gap_rate, 2),
                    "current_price": c.current_price,
                    "trading_value": c.trade_value,
                    "excluded_reason": reason,
                    "warning_reason": "",
                })
                logger.debug(f"[QFilter] 제외: {c.symbol} {c.name} — {reason}")
            else:
                passed.append(c)

        # ── Step 2: 점수 보정 (무거운 필터는 상위 N개만) ─────────────
        for i, c in enumerate(passed):
            sd = sdb.get(c.symbol)
            is_heavy_target = (not speed_mode) or (i < heavy_limit)
            daily = dpc.get(c.symbol, []) if is_heavy_target else []
            self._apply_score_adjustments(c, sd, daily)

        # ── Step 3: 테마 대장주 bonus ──────────────────────────────────
        self._apply_theme_leader_bonus(passed)

        # ── Step 4: final_score 재계산 및 정렬 ────────────────────────
        for c in passed:
            adjusted = (
                c.rule_score
                + c.quality_bonus
                + c.momentum_bonus
                + c.ma_bonus
                + c.theme_leader_bonus
                - c.risk_penalty_q
                - c.liquidity_penalty
                - c.overheat_penalty
            )
            c.final_score = round(max(0.0, min(100.0, adjusted)), 4)

        passed.sort(key=lambda x: x.final_score, reverse=True)
        for idx, c in enumerate(passed, 1):
            c.rank = idx

        # ── Step 5: 테마 cap 적용 ──────────────────────────────────────
        max_theme = self._qcfg.get("max_same_theme_in_top15", 4)
        max_positions = self.cfg.trading.get("max_positions", 15)
        passed = self._apply_theme_cap(passed, max_theme, max_positions)

        return passed, excluded

    def save_explain_csv(
        self,
        candidates: list[Candidate],
        excluded: list[dict],
        date_str: str = None,
        time_str: str = None,
    ) -> tuple[str, str]:
        """
        Top15 explain CSV와 제외 종목 CSV를 저장한다.

        Returns
        -------
        (explain_path, excluded_path)
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        if time_str is None:
            time_str = datetime.now().strftime("%H%M")

        out_dir = Path("data") / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Explain CSV
        explain_rows = []
        for c in candidates:
            explain_rows.append({
                "rank": c.rank,
                "code": c.symbol,
                "name": c.name,
                "theme": c.theme,
                "current_price": c.current_price,
                "gap_rate": round(c.gap_rate, 2),
                "trading_value": c.trade_value,
                "existing_score": round(c.rule_score, 4),
                "quality_bonus": round(c.quality_bonus, 4),
                "momentum_bonus": round(c.momentum_bonus, 4),
                "ma_bonus": round(c.ma_bonus, 4),
                "theme_leader_bonus": round(c.theme_leader_bonus, 4),
                "risk_penalty": round(c.risk_penalty_q, 4),
                "liquidity_penalty": round(c.liquidity_penalty, 4),
                "overheat_penalty": round(c.overheat_penalty, 4),
                "final_score": round(c.final_score, 4),
                "warning_reason": c.warning_reason,
            })

        explain_path = out_dir / f"top15_explain_{date_str}_{time_str}.csv"
        pd.DataFrame(explain_rows).to_csv(explain_path, index=False, encoding="utf-8-sig")
        logger.info(f"[QFilter] explain CSV 저장: {explain_path}")

        # Excluded CSV
        excl_path = out_dir / f"excluded_candidates_{date_str}_{time_str}.csv"
        pd.DataFrame(excluded).to_csv(excl_path, index=False, encoding="utf-8-sig")
        logger.info(f"[QFilter] 제외 CSV 저장: {excl_path} ({len(excluded)}개)")

        return str(explain_path), str(excl_path)

    # ------------------------------------------------------------------ #
    # Fast Exclusion Filter                                                #
    # ------------------------------------------------------------------ #

    def _fast_exclude(self, c: Candidate, sd: Optional[StockData]) -> Optional[str]:
        """
        제외 사유가 있으면 사유 문자열을, 없으면 None을 반환.
        StockData가 없으면 이름/코드 기반으로만 판단.
        """
        qcfg = self._qcfg
        name_upper = (c.name or "").upper()
        symbol = c.symbol or ""

        # ETF / ETN 제외
        if sd and (sd.is_etf or sd.is_etn):
            return "ETF/ETN 플래그"
        for kw in ETF_ETN_KEYWORDS:
            if kw.upper() in name_upper:
                return f"ETF/ETN 키워드: {kw}"

        # 스팩 제외
        if sd and sd.is_spac:
            return "스팩"
        for kw in SPAC_KEYWORDS:
            if kw in c.name:
                return f"스팩 키워드: {kw}"

        # 리츠 제외
        if sd and sd.is_reit:
            return "리츠"
        for kw in REIT_KEYWORDS:
            if kw.upper() in name_upper:
                return f"리츠 키워드: {kw}"

        # 우선주 제외 (이름 끝 패턴 + 플래그)
        if sd and sd.is_preferred:
            return "우선주 플래그"
        if _PREFERRED_RE.search(c.name or ""):
            return "우선주(이름패턴)"

        # 거래정지 / 경고 제외
        if sd and (sd.is_warning or sd.is_halt):
            return "거래정지/투자경고"
        for kw in RISK_KEYWORDS:
            if kw in c.name:
                return f"위험키워드: {kw}"

        # 종목코드 이상 (6자리 숫자 아닌 경우)
        if not (len(symbol) == 6 and symbol.isdigit()):
            return f"종목코드 이상: {symbol}"

        # 최저가 필터
        min_price = qcfg.get("min_price", 1000)
        if c.current_price < min_price:
            return f"동전주(가격 {c.current_price} < {min_price})"

        # 거래대금 필터 (0920 기준 or 기본 기준)
        min_tv = qcfg.get("min_trading_value_0920", 3_000_000_000)
        if c.trade_value < min_tv:
            return f"거래대금부족({c.trade_value:,.0f} < {min_tv:,.0f})"

        # 시초가 갭 과다 제외
        max_gap = qcfg.get("max_open_gap_rate", 12.0)
        if c.gap_rate > max_gap:
            return f"갭과다({c.gap_rate:.1f}% > {max_gap}%)"

        return None

    # ------------------------------------------------------------------ #
    # Score Adjustments                                                    #
    # ------------------------------------------------------------------ #

    def _apply_score_adjustments(
        self,
        c: Candidate,
        sd: Optional[StockData],
        daily: list[dict],
    ) -> None:
        """c의 보너스/패널티 필드를 in-place로 설정한다."""
        qcfg = self._qcfg
        warnings: list[str] = []

        # ── 기존 점수 보존 ────────────────────────────────────────────
        # c.rule_score is used as existing_score baseline

        # ── 갭 모멘텀 bonus (2~7% 건강한 갭) ─────────────────────────
        caution_gap = qcfg.get("caution_gap_rate", 7.0)
        if 2.0 <= c.gap_rate <= caution_gap:
            # 피크: 5% 갭 → 최대 bonus
            normalized = 1.0 - abs(c.gap_rate - 5.0) / 3.0
            c.momentum_bonus = round(max(0.0, normalized) * 8.0, 2)
        elif c.gap_rate > caution_gap:
            # 7~12% caution 구간 감점
            penalty = (c.gap_rate - caution_gap) / (12.0 - caution_gap) * 5.0
            c.momentum_bonus = round(-penalty, 2)
        else:
            c.momentum_bonus = 0.0

        # ── 장초반 고점 대비 현재가 낙폭 리스크 ─────────────────────
        max_intraday_drop = qcfg.get("max_intraday_drop_from_high", 4.0)
        if c.high > 0:
            drop_from_high = (c.current_price - c.high) / c.high * 100.0
            if drop_from_high < -max_intraday_drop:
                c.risk_penalty_q += 5.0
                warnings.append(f"장초반 낙폭 {drop_from_high:.1f}%")
            elif drop_from_high < -2.0:
                c.risk_penalty_q += 2.0

        # ── 윗꼬리 리스크 ─────────────────────────────────────────────
        if c.open > 0 and c.high > 0:
            candle_range = c.high - c.low
            if candle_range > 0:
                upper_shadow_ratio = (c.high - c.current_price) / candle_range
                if upper_shadow_ratio > 0.45:
                    c.risk_penalty_q += 3.0
                    warnings.append(f"윗꼬리 비율 {upper_shadow_ratio:.2f}")

        # ── 일봉 데이터 필요 필터 (daily 없으면 스킵) ─────────────────
        if daily:
            self._apply_daily_based_filters(c, daily, warnings, qcfg)
        else:
            warnings.append("일봉 데이터 없음(MA/수익률 필터 스킵)")

        # ── 테마 분류 ──────────────────────────────────────────────────
        sector = sd.sector if sd else ""
        themes = match_kr_stock_to_themes(c.name, sector)
        c.matched_themes = ",".join(themes)
        c.theme = themes[0] if themes else ""

        # ── 경고 누적 ─────────────────────────────────────────────────
        c.warning_reason = "; ".join(warnings) if warnings else ""

    def _apply_daily_based_filters(
        self,
        c: Candidate,
        daily: list[dict],
        warnings: list[str],
        qcfg: dict,
    ) -> None:
        """일봉 데이터(최신순) 기반 필터를 적용한다."""
        closes = [d["close"] for d in daily if d.get("close", 0) > 0]
        if len(closes) < 5:
            warnings.append("일봉 데이터 부족(5일 미만)")
            return

        current = c.current_price or closes[0]

        # ── 최근 수익률 계산 ──────────────────────────────────────────
        ret3d  = (current / closes[2]  - 1) * 100 if len(closes) >= 3  else None
        ret5d  = (current / closes[4]  - 1) * 100 if len(closes) >= 5  else None
        ret20d = (current / closes[19] - 1) * 100 if len(closes) >= 20 else None

        # ── 과열 감점 ─────────────────────────────────────────────────
        max_3d = qcfg.get("max_3d_return", 25.0)
        max_5d = qcfg.get("max_5d_return", 35.0)

        if ret3d is not None and ret3d > max_3d:
            c.overheat_penalty += min(10.0, (ret3d - max_3d) / 5.0 * 5.0)
            warnings.append(f"3일 급등 {ret3d:.1f}%")
        if ret5d is not None and ret5d > max_5d:
            c.overheat_penalty += min(15.0, (ret5d - max_5d) / 5.0 * 5.0)
            warnings.append(f"5일 급등 {ret5d:.1f}%")
        if ret20d is not None and ret20d > 70.0:
            c.overheat_penalty += 5.0
            warnings.append(f"20일 급등 {ret20d:.1f}%")

        # ── 급락 리스크 감점 ─────────────────────────────────────────
        if ret5d is not None and ret5d < -18.0:
            c.risk_penalty_q += 5.0
            warnings.append(f"5일 급락 {ret5d:.1f}%")
        if ret20d is not None and ret20d < -30.0:
            c.risk_penalty_q += 5.0
            warnings.append(f"20일 급락 {ret20d:.1f}%")

        # ── MA 계산 ──────────────────────────────────────────────────
        if len(closes) < 20:
            warnings.append("MA20 계산 불가(데이터 부족)")
            return

        ma5  = sum(closes[:5])  / 5
        ma10 = sum(closes[:10]) / 10
        ma20 = sum(closes[:20]) / 20

        ma5_prev  = sum(closes[1:6])  / 5  if len(closes) >= 6  else ma5
        ma10_prev = sum(closes[1:11]) / 10 if len(closes) >= 11 else ma10
        ma20_prev = sum(closes[1:21]) / 20 if len(closes) >= 21 else ma20

        slope5_up  = ma5  > ma5_prev
        slope10_up = ma10 > ma10_prev
        slope20_up = ma20 > ma20_prev

        price_above_ma5  = current >= ma5
        price_above_ma10 = current >= ma10
        price_above_ma20 = current >= ma20

        aligned = (ma5 > ma10 > ma20)

        ma_bonus = 0.0
        if slope5_up and slope10_up and slope20_up:
            ma_bonus += 5.0   # 3개 MA slope 모두 우상향
        if price_above_ma5 and price_above_ma10 and price_above_ma20:
            ma_bonus += 3.0   # 현재가가 3개 MA 위
        if aligned:
            ma_bonus += 4.0   # 정배열

        # 과열 감점 (MA20 대비 15% 이상 높으면)
        max_ma20_ext = qcfg.get("max_ma20_extension_rate", 15.0)
        if ma20 > 0:
            ext = (current / ma20 - 1) * 100
            if ext > max_ma20_ext:
                ma_bonus -= 5.0
                warnings.append(f"MA20 대비 과열 {ext:.1f}%")

        c.ma_bonus = round(max(0.0, ma_bonus), 2)

    # ------------------------------------------------------------------ #
    # Theme Leader Bonus                                                   #
    # ------------------------------------------------------------------ #

    def _apply_theme_leader_bonus(self, candidates: list[Candidate]) -> None:
        """같은 테마 내 거래대금 1위에게 theme_leader_bonus를 부여한다."""
        theme_groups: dict[str, list[Candidate]] = {}
        for c in candidates:
            theme = c.theme or "__no_theme__"
            theme_groups.setdefault(theme, []).append(c)

        for theme, group in theme_groups.items():
            if theme == "__no_theme__" or len(group) < 2:
                continue
            leader = max(group, key=lambda x: x.trade_value)
            leader.theme_leader_bonus = 5.0
            logger.debug(f"[QFilter] 테마대장: {theme} → {leader.symbol} {leader.name}")

    # ------------------------------------------------------------------ #
    # Theme Cap                                                            #
    # ------------------------------------------------------------------ #

    def _apply_theme_cap(
        self,
        candidates: list[Candidate],
        max_theme: int,
        max_positions: int,
    ) -> list[Candidate]:
        """
        동일 대테마 종목이 max_theme개를 초과하면 점수 순으로 상위 max_theme개만 유지.
        결과 리스트는 점수 내림차순으로 max_positions개까지 반환.
        """
        selected: list[Candidate] = []
        overflow: list[Candidate] = []
        theme_counts: dict[str, int] = {}

        for c in candidates:
            theme = c.theme or f"__unique_{c.symbol}__"
            count = theme_counts.get(theme, 0)
            if count < max_theme:
                selected.append(c)
                theme_counts[theme] = count + 1
            else:
                overflow.append(c)

        # 남은 슬롯을 overflow에서 채움
        remaining = max_positions - len(selected)
        if remaining > 0:
            selected.extend(overflow[:remaining])

        result = selected[:max_positions]
        for idx, c in enumerate(result, 1):
            c.rank = idx
        return result
