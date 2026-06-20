"""
us_sector_strength_service.py

전날 미국장 섹터 강도를 자동 분석한다.

데이터 소스 우선순위:
1. Yahoo Finance ETF quote pages (파싱 안정적)
2. 캐시 파일 (data/cache/us_sector_strength_YYYYMMDD.json)
3. 데이터 없음 → us_sector_match_score=0 처리

캐시 유효기간: 24시간
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

try:
    from app.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ETF → US sector mapping
# ---------------------------------------------------------------------------

ETF_SECTOR_MAP: dict[str, str] = {
    "SMH": "semiconductor",
    "SOXX": "semiconductor",
    "NVDA": "semiconductor",
    "AMD": "semiconductor",
    "MU": "semiconductor",
    "XLK": "ai_data_center",
    "XLC": "ai_data_center",
    "MSFT": "ai_data_center",
    "GOOGL": "ai_data_center",
    "META": "ai_data_center",
    "XLU": "power_grid",
    "NEE": "power_grid",
    "ITA": "defense",
    "LMT": "defense",
    "RTX": "defense",
    "BOTZ": "robotics",
    "ARKQ": "robotics",
    "LIT": "battery_ev",
    "TSLA": "battery_ev",
    "ALB": "battery_ev",
    "XLI": "industrials",
    "GE": "industrials",
    "CAT": "industrials",
    "XLV": "healthcare_bio",
    "XLY": "consumer_discretionary",
    "XLE": "energy",
    "XLF": "financials",
    "XLB": "materials_copper",
    "COPX": "materials_copper",
    "FCX": "materials_copper",
    "SPY": "_benchmark",
    "QQQ": "_benchmark",
    "IWM": "_benchmark",
}

# Domestic sector → US sector key
DOMESTIC_TO_US_SECTOR_MAP: dict[str, Optional[str]] = {
    "semiconductor": "semiconductor",
    "ai_data_center": "ai_data_center",
    "power_grid": "power_grid",
    "shipbuilding": "industrials",
    "defense": "defense",
    "robotics": "robotics",
    "battery_ev": "battery_ev",
    "auto": "consumer_discretionary",
    "bio_healthcare": "healthcare_bio",
    "finance": "financials",
    "cosmetics_consumer": "consumer_discretionary",
    "entertainment_game": "consumer_discretionary",
    "construction_machinery": "industrials",
    "materials_copper": "materials_copper",
    "holding_company": None,
    "unknown": None,
}

# ETF symbols to fetch (benchmarks + sector leaders)
_ALL_SYMBOLS = [
    "SPY", "QQQ",
    "SMH", "SOXX",
    "XLK", "XLC",
    "XLU",
    "ITA",
    "BOTZ",
    "LIT",
    "XLI",
    "XLV",
    "XLY",
    "XLE",
    "XLF",
    "XLB", "COPX",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _ROOT / "data" / "cache"


class USSectorStrengthService:
    """전날 미국장 섹터 강도를 자동 분석한다."""

    def __init__(self, cfg=None):
        if cfg is None:
            try:
                from app.config import get_config
                cfg = get_config()
            except Exception:
                cfg = None
        self.cfg = cfg
        self._us_cfg: dict = self._load_us_cfg()

    def _load_us_cfg(self) -> dict:
        try:
            return self.cfg._raw.get("us_sector_strength", {}) if self.cfg else {}
        except AttributeError:
            return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_us_sector_strength(self) -> dict:
        """
        전날 미국장 섹터 강도를 반환한다.

        Returns dict with:
          market_regime, data_source_used, strong_sectors, moderate_sectors,
          sector_scores, sector_etf_changes, spy_change, qqq_change, collected_at
        """
        cache_enabled = self._us_cfg.get("cache_enabled", True)

        # 1. Try Yahoo Finance
        try:
            etf_changes = self._fetch_yahoo_etf_changes(_ALL_SYMBOLS)
        except Exception as exc:
            logger.warning("[USSectorStrength] Yahoo 수집 오류: %s", exc)
            etf_changes = {}

        if etf_changes:
            spy = etf_changes.get("SPY", 0.0)
            qqq = etf_changes.get("QQQ", 0.0)
            sector_result = self._build_result(etf_changes, spy, qqq, "yahoo")
            if cache_enabled:
                self._save_cache(sector_result)
            return sector_result

        # 2. Try cache
        if cache_enabled:
            cached = self._load_cache()
            if cached:
                cached["data_source_used"] = "cache"
                logger.info("[USSectorStrength] 캐시 사용")
                return cached

        # 3. No data — return zero-score result
        logger.warning("[USSectorStrength] 미국 섹터 데이터 없음 → 0점 처리")
        return self._empty_result()

    def get_us_sector_match_score(
        self,
        domestic_sector: str,
        us_result: dict,
        us_sector_match_score_max: int = 20,
    ) -> tuple[int, str, str]:
        """
        국내 섹터와 미국 섹터 매칭 점수를 반환한다.

        Returns:
            (score, matched_us_sector, reason)
        """
        if not us_result or us_result.get("data_source_used") == "none":
            return (0, "", "no_us_data")

        us_key = DOMESTIC_TO_US_SECTOR_MAP.get(domestic_sector)
        if not us_key:
            return (0, "", "no_us_mapping")

        strong = us_result.get("strong_sectors", [])
        moderate = us_result.get("moderate_sectors", [])
        regime = us_result.get("market_regime", "neutral")

        if us_key in strong:
            base_score = us_sector_match_score_max
            reason = f"us_strong_{us_key}"
        elif us_key in moderate:
            base_score = us_sector_match_score_max // 2
            reason = f"us_moderate_{us_key}"
        else:
            return (0, us_key, f"us_weak_{us_key}")

        if regime == "risk_off":
            base_score = int(base_score * 0.5)
            reason += "+risk_off_penalty"

        return (base_score, us_key, reason)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_yahoo_etf_changes(self, symbols: list[str]) -> dict[str, float]:
        """Yahoo Finance에서 ETF 등락률을 수집한다."""
        results: dict[str, float] = {}
        timeout = int(self._us_cfg.get("request_timeout_seconds", 8))

        session = requests.Session()
        session.headers.update(_HEADERS)

        for symbol in symbols:
            try:
                url = f"https://finance.yahoo.com/quote/{symbol}/"
                resp = session.get(url, timeout=timeout)
                if resp.status_code != 200:
                    logger.debug("[USSectorStrength] %s HTTP %s", symbol, resp.status_code)
                    continue
                change_pct = self._parse_yahoo_change(resp.text, symbol)
                results[symbol] = change_pct
                time.sleep(0.3)
            except Exception as exc:
                logger.debug("[USSectorStrength] %s 파싱 오류: %s", symbol, exc)
                continue

        logger.info("[USSectorStrength] Yahoo 수집 완료: %d/%d", len(results), len(symbols))
        return results

    def _parse_yahoo_change(self, html: str, symbol: str) -> float:
        """HTML에서 regularMarketChangePercent를 파싱한다."""
        # Pattern 1: JSON raw value in page data
        m = re.search(r'"regularMarketChangePercent"\s*:\s*\{"raw"\s*:\s*([-\d.]+)', html)
        if m:
            return float(m.group(1))

        # Pattern 2: data attribute in HTML
        m = re.search(r'data-field="regularMarketChangePercent"[^>]*data-value="([-\d.]+)"', html)
        if m:
            return float(m.group(1))

        # Pattern 3: fin-streamer tag
        m = re.search(
            r'<fin-streamer[^>]*data-field="regularMarketChangePercent"[^>]*value="([-\d.]+)"',
            html,
        )
        if m:
            return float(m.group(1))

        # Pattern 4: compute from previousClose and regularMarketPrice
        prev_m = re.search(r'"regularMarketPreviousClose"\s*:\s*\{"raw"\s*:\s*([\d.]+)', html)
        curr_m = re.search(r'"regularMarketPrice"\s*:\s*\{"raw"\s*:\s*([\d.]+)', html)
        if prev_m and curr_m:
            prev = float(prev_m.group(1))
            curr = float(curr_m.group(1))
            if prev > 0:
                return (curr - prev) / prev * 100.0

        logger.debug("[USSectorStrength] %s 등락률 파싱 실패", symbol)
        return 0.0

    def _compute_sector_scores(
        self,
        etf_changes: dict[str, float],
        spy_change: float,
        qqq_change: float,
    ) -> dict[str, float]:
        """섹터별 0-100 점수를 계산한다."""
        sector_avgs: dict[str, list[float]] = {}
        for sym, chg in etf_changes.items():
            sec = ETF_SECTOR_MAP.get(sym)
            if sec and not sec.startswith("_"):
                sector_avgs.setdefault(sec, []).append(chg)

        # Compute average per sector
        sector_avg: dict[str, float] = {
            s: sum(vals) / len(vals) for s, vals in sector_avgs.items()
        }

        # Relative strength vs SPY
        rel_strength: dict[str, float] = {
            s: avg - spy_change for s, avg in sector_avg.items()
        }

        # Normalize to 0-100 (spy baseline = 50)
        scores: dict[str, float] = {}
        for sec, rel in rel_strength.items():
            # rel < -3 → ~0,  rel = 0 → 50,  rel > 3 → ~100
            normalized = max(0.0, min(100.0, 50.0 + rel * 16.67))
            scores[sec] = round(normalized, 1)

        return scores

    def _build_result(
        self,
        etf_changes: dict[str, float],
        spy_change: float,
        qqq_change: float,
        source: str,
    ) -> dict:
        sector_scores = self._compute_sector_scores(etf_changes, spy_change, qqq_change)
        strong_threshold = float(self._us_cfg.get("strong_threshold", 70))
        moderate_threshold = float(self._us_cfg.get("moderate_threshold", 50))

        strong = [s for s, sc in sorted(sector_scores.items(), key=lambda x: -x[1]) if sc >= strong_threshold]
        moderate = [s for s, sc in sorted(sector_scores.items(), key=lambda x: -x[1]) if moderate_threshold <= sc < strong_threshold]

        return {
            "market_regime": self._determine_market_regime(spy_change, qqq_change),
            "data_source_used": source,
            "strong_sectors": strong,
            "moderate_sectors": moderate,
            "sector_scores": sector_scores,
            "sector_etf_changes": {k: round(v, 3) for k, v in etf_changes.items()},
            "spy_change": round(spy_change, 3),
            "qqq_change": round(qqq_change, 3),
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _determine_market_regime(self, spy: float, qqq: float) -> str:
        if spy > 0 and qqq > 0:
            return "risk_on"
        if spy < -0.3 and qqq < -0.3:
            return "risk_off"
        return "neutral"

    def _empty_result(self) -> dict:
        return {
            "market_regime": "neutral",
            "data_source_used": "none",
            "strong_sectors": [],
            "moderate_sectors": [],
            "sector_scores": {},
            "sector_etf_changes": {},
            "spy_change": 0.0,
            "qqq_change": 0.0,
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _load_cache(self) -> Optional[dict]:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        cache_file = _CACHE_DIR / f"us_sector_strength_{today}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            # Check age
            collected_at = datetime.fromisoformat(data.get("collected_at", "2000-01-01"))
            max_age = int(self._us_cfg.get("cache_max_age_hours", 24))
            if datetime.now() - collected_at > timedelta(hours=max_age):
                return None
            return data
        except Exception as exc:
            logger.debug("[USSectorStrength] 캐시 로드 오류: %s", exc)
            return None

    def _save_cache(self, data: dict) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        cache_file = _CACHE_DIR / f"us_sector_strength_{today}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("[USSectorStrength] 캐시 저장: %s", cache_file)
        except Exception as exc:
            logger.debug("[USSectorStrength] 캐시 저장 오류: %s", exc)
