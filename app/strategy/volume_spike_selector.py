"""
volume_spike_selector.py

거래량 급증 종목 중 상승률 5%~15% 종목만 후보로 사용하는 Top10 선정기.

필터 순서:
  1. ETF/ETN/우선주/스팩/리츠 제외
  2. 가격 20,000원 이하 제외
  3. 상승률 5% 미만 제외 (하드, fallback 복구 금지)
  4. 상승률 15% 초과 제외 (하드, fallback 복구 금지)
  5. 거래대금 30억 이상 → primary pass
  6. Top10 부족 시 거래대금 10억 이상 fallback (5~15% 조건 유지)
  7. 점수 계산 → 내림차순 정렬 → Top10
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from app.logger import logger

# 상승률 구간 경계
_MIN_CHANGE_RATE = 5.0
_MAX_CHANGE_RATE = 15.0

# 거래대금 기준
_PRIMARY_MIN_TV = 3_000_000_000   # 30억
_FALLBACK_MIN_TV = 1_000_000_000  # 10억

# 최소 주가
_MIN_PRICE = 20_000


def _change_rate_score(rate: float) -> Optional[float]:
    """상승률 구간별 점수. 범위 밖이면 None(하드 제외 처리)."""
    if rate < _MIN_CHANGE_RATE or rate > _MAX_CHANGE_RATE:
        return None
    if rate < 8.0:
        return 4.0   # 5~8%: 안정 수급
    if rate <= 12.0:
        return 8.0   # 8~12%: 강한 수급 (선호)
    return 3.0       # 12~15%: 과열 가능성


def _trading_value_score(tv: float) -> float:
    if tv >= 10_000_000_000:
        return 8.0
    if tv >= 5_000_000_000:
        return 6.0
    if tv >= 3_000_000_000:
        return 4.0
    if tv >= 1_000_000_000:
        return 2.0
    return 0.0


def _is_excluded_type(stock: dict) -> bool:
    return bool(
        stock.get("is_etf") or stock.get("is_etn")
        or stock.get("is_preferred") or stock.get("is_spac")
        or stock.get("is_reit") or stock.get("is_warning")
        or stock.get("is_halt") or stock.get("is_suspended")
    )


def _score(stock: dict) -> float:
    """종목 최종 점수 계산."""
    cr_score = _change_rate_score(stock.get("change_rate", 0.0))
    if cr_score is None:
        return -9999.0
    tv_score = _trading_value_score(stock.get("trade_value", 0.0))
    base = 5.0  # 거래량 급증 페이지 종목 기본 가점
    return base + cr_score + tv_score


class VolumeSpikeSelector:
    """거래량 급증 Top10 선정기."""

    def __init__(self, cfg=None):
        if cfg is None:
            try:
                from app.config import get_config
                cfg = get_config()
            except Exception:
                cfg = None
        self.cfg = cfg
        self._vs_cfg: dict = self._load_vs_cfg()

    def _load_vs_cfg(self) -> dict:
        try:
            return self.cfg._raw.get("volume_spike", {}) if self.cfg else {}
        except AttributeError:
            return {}

    def select(
        self,
        raw_stocks: list[dict],
    ) -> tuple[list[dict], dict]:
        """
        raw_stocks: collect_volume_spike_stocks() 반환값 (list[dict])

        Returns:
          top10: list[dict]  — 최종 선정 종목 (rank 포함)
          diag: dict         — 진단 정보
        """
        vs = self._vs_cfg
        target_n    = int(vs.get("target_top_n", 10))
        min_price   = float(vs.get("min_price", _MIN_PRICE))
        min_cr      = float(vs.get("min_change_rate", _MIN_CHANGE_RATE))
        max_cr      = float(vs.get("max_change_rate", _MAX_CHANGE_RATE))
        min_tv      = float(vs.get("min_trading_value", _PRIMARY_MIN_TV))
        fallback_tv = float(vs.get("fallback_min_trading_value", _FALLBACK_MIN_TV))
        max_cands   = int(vs.get("max_candidates_to_score", 80))

        diag: dict = {
            "total": 0,
            "excluded_type": 0,
            "excluded_price": 0,
            "excluded_below_5pct": 0,
            "excluded_above_15pct": 0,
            "passed_rate_filter": 0,
            "primary_pass": 0,
            "fallback_added": 0,
            "final_top10": 0,
        }
        excluded_records: list[dict] = []

        stocks = raw_stocks[:max_cands]
        diag["total"] = len(stocks)

        # ── Stage 1: 타입 필터 ─────────────────────────────────────────────
        after_type: list[dict] = []
        for s in stocks:
            if _is_excluded_type(s):
                diag["excluded_type"] += 1
                excluded_records.append({**s, "excluded_reason": "etf_etn_or_type"})
            else:
                after_type.append(s)

        # ── Stage 2: 가격 필터 ─────────────────────────────────────────────
        after_price: list[dict] = []
        for s in after_type:
            if s.get("current_price", 0) <= min_price:
                diag["excluded_price"] += 1
                excluded_records.append({**s, "excluded_reason": "price_below_20k"})
            else:
                after_price.append(s)

        # ── Stage 3: 상승률 하드 필터 (5% 미만 / 15% 초과 모두 제외) ────────
        after_rate: list[dict] = []
        for s in after_price:
            cr = s.get("change_rate", 0.0)
            if cr < min_cr:
                diag["excluded_below_5pct"] += 1
                excluded_records.append({**s, "excluded_reason": "change_rate_below_5"})
            elif cr > max_cr:
                diag["excluded_above_15pct"] += 1
                excluded_records.append({**s, "excluded_reason": "change_rate_above_15"})
            else:
                after_rate.append(s)

        diag["passed_rate_filter"] = len(after_rate)

        # ── Stage 4: 거래대금 1차 (30억 이상) ─────────────────────────────
        primary: list[dict] = [
            s for s in after_rate if s.get("trade_value", 0) >= min_tv
        ]
        diag["primary_pass"] = len(primary)

        # ── Stage 5: 점수 정렬 ────────────────────────────────────────────
        primary_scored = sorted(primary, key=lambda s: _score(s), reverse=True)

        # ── Stage 6: fallback (30억 미달 but 10억 이상, 5~15% 조건 유지) ──
        if len(primary_scored) < target_n:
            in_primary = {s["symbol"] for s in primary_scored}
            fallback_pool = [
                s for s in after_rate
                if s["symbol"] not in in_primary
                and s.get("trade_value", 0) >= fallback_tv
            ]
            fallback_sorted = sorted(fallback_pool, key=lambda s: _score(s), reverse=True)
            need = target_n - len(primary_scored)
            added = fallback_sorted[:need]
            diag["fallback_added"] = len(added)
            primary_scored = primary_scored + added
        else:
            diag["fallback_added"] = 0

        # ── Stage 7: Top N ────────────────────────────────────────────────
        top = primary_scored[:target_n]
        for i, s in enumerate(top, 1):
            s["rank"] = i
            cr = s.get("change_rate", 0.0)
            s["change_rate_score"] = _change_rate_score(cr) or 0.0
            s["final_score"] = _score(s)

        diag["final_top10"] = len(top)

        logger.info(
            "[VolumeSpikeSelector] 전체 %d → 5%%미만제외 %d → 15%%초과제외 %d "
            "→ 통과 %d → 30억이상 %d → fallback %d → 최종 %d",
            diag["total"],
            diag["excluded_below_5pct"],
            diag["excluded_above_15pct"],
            diag["passed_rate_filter"],
            diag["primary_pass"],
            diag["fallback_added"],
            diag["final_top10"],
        )

        self._last_excluded = excluded_records
        return top, diag

    def save_top10_csv(self, top10: list[dict], date_str: str = None) -> str:
        """Top10 CSV 저장. change_rate_score 컬럼 포함."""
        if not date_str:
            date_str = datetime.now().strftime("%Y%m%d")

        out_dir = Path(__file__).resolve().parent.parent.parent / "data" / "volume_spike"
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / f"{date_str}_volume_spike_top10.csv"

        columns = [
            "rank", "symbol", "name", "current_price", "change_rate",
            "change_rate_score", "trade_value", "final_score",
            "is_etf", "is_etn", "is_preferred", "is_spac", "is_reit",
        ]
        rows = [{col: s.get(col, "") for col in columns} for s in top10]

        df = pd.DataFrame(rows, columns=columns)
        df.to_csv(str(filepath), index=False, encoding="utf-8-sig")
        logger.info("[VolumeSpikeSelector] Top10 저장: %s", filepath)
        return str(filepath)

    def save_excluded_csv(self, date_str: str = None) -> Optional[str]:
        """제외 종목 CSV 저장. excluded_reason 포함."""
        excluded = getattr(self, "_last_excluded", [])
        if not excluded:
            return None
        if not date_str:
            date_str = datetime.now().strftime("%Y%m%d")

        out_dir = Path(__file__).resolve().parent.parent.parent / "data" / "volume_spike"
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / f"{date_str}_volume_spike_excluded.csv"

        df = pd.DataFrame(excluded)
        df.to_csv(str(filepath), index=False, encoding="utf-8-sig")
        logger.info("[VolumeSpikeSelector] 제외 저장: %s (%d개)", filepath, len(excluded))
        return str(filepath)
