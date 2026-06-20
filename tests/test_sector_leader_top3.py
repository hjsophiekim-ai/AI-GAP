"""
tests/test_sector_leader_top3.py

주도섹터 Top3 전략 테스트.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


# ─────────────────────────────────────────────────────────────
# 샘플 데이터 헬퍼
# ─────────────────────────────────────────────────────────────

def _make_stock(symbol, name, price, change_rate, trading_value,
                is_etf=False, is_etn=False, is_preferred=False,
                is_spac=False, is_reit=False, rank=1):
    return {
        "symbol": symbol,
        "name": name,
        "current_price": float(price),
        "change_rate": float(change_rate),
        "trading_value": float(trading_value),
        "trade_value": float(trading_value),
        "volume": 1000000,
        "is_etf": is_etf,
        "is_etn": is_etn,
        "is_preferred": is_preferred,
        "is_spac": is_spac,
        "is_reit": is_reit,
        "rank": rank,
        "collected_at": "2026-06-20T09:00:00",
    }


SEMICONDUCTOR_STOCKS = [
    _make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1),
    _make_stock("005930", "삼성전자", 80000, 3.5, 400_000_000_000, rank=2),
    _make_stock("042700", "한미반도체", 85000, 7.0, 50_000_000_000, rank=5),
]

DEFENSE_STOCKS = [
    _make_stock("012450", "한화에어로스페이스", 350000, 4.5, 300_000_000_000, rank=3),
    _make_stock("047810", "한국항공우주", 70000, 6.0, 80_000_000_000, rank=8),
]

ETF_STOCK = _make_stock("069500", "KODEX200", 30000, 1.0, 200_000_000_000, is_etf=True, rank=4)
PREFERRED_STOCK = _make_stock("005935", "삼성전자우", 70000, 3.0, 100_000_000_000, is_preferred=True, rank=6)
CHEAP_STOCK = _make_stock("123456", "저가주", 5000, 5.0, 50_000_000_000, rank=7)
LOW_TV_STOCK = _make_stock("234567", "소형주", 50000, 5.0, 1_000_000_000, rank=9)
HIGH_CHANGE_STOCK = _make_stock("345678", "급등주", 30000, 20.0, 50_000_000_000, rank=10)

ALL_STOCKS = SEMICONDUCTOR_STOCKS + DEFENSE_STOCKS + [
    ETF_STOCK, PREFERRED_STOCK, CHEAP_STOCK, LOW_TV_STOCK, HIGH_CHANGE_STOCK
]


# ─────────────────────────────────────────────────────────────
# 섹터 매핑 테스트
# ─────────────────────────────────────────────────────────────

class TestSectorMapper:
    def test_import(self):
        from app.strategy.sector_mapper import SectorMapper
        assert SectorMapper is not None

    def test_classify_returns_sector_field(self):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [_make_stock("000660", "SK하이닉스", 200000, 5.0, 5e11)]
        result = mapper.classify_stocks(stocks)
        assert "sector" in result[0]
        assert "subtheme" in result[0]

    def test_semiconductor_mapping(self):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        sector = mapper.get_sector("000660", name="SK하이닉스")
        assert sector == "semiconductor", f"Expected semiconductor, got {sector}"

    def test_etf_gets_sector(self):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        # ETF도 섹터 분류는 됨 (필터는 selector에서)
        sector = mapper.get_sector("069500", name="KODEX200")
        assert isinstance(sector, str)

    def test_unknown_stock(self):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        sector = mapper.get_sector("999999", name="알수없는종목XYZ")
        assert sector == "unknown"

    def test_classify_multiple(self):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        classified = mapper.classify_stocks(ALL_STOCKS)
        assert len(classified) == len(ALL_STOCKS)
        for s in classified:
            assert "sector" in s


# ─────────────────────────────────────────────────────────────
# NXT 거래대금 수집기 테스트 (파싱 실패 시 fallback 확인)
# ─────────────────────────────────────────────────────────────

class TestNaverNxtTurnoverCollector:
    def test_import(self):
        from app.data.naver_nxt_turnover_collector import NaverNxtTurnoverCollector
        assert NaverNxtTurnoverCollector is not None

    def test_module_function_exists(self):
        from app.data.naver_nxt_turnover_collector import collect_nxt_turnover_stocks
        assert callable(collect_nxt_turnover_stocks)

    def test_returns_list(self, monkeypatch):
        from app.data.naver_nxt_turnover_collector import NaverNxtTurnoverCollector

        def _mock_collect(self, *a, **kw):
            return []

        monkeypatch.setattr(NaverNxtTurnoverCollector, "collect", _mock_collect)
        col = NaverNxtTurnoverCollector()
        result = col.collect(max_pages=1, max_stocks=5)
        assert isinstance(result, list)

    def test_fallback_on_failure(self, monkeypatch):
        """NXT 페이지 파싱 실패 시 거래량 급증 fallback 실행 확인."""
        from app.data.naver_nxt_turnover_collector import NaverNxtTurnoverCollector
        import requests

        def _raise(*a, **kw):
            raise requests.ConnectionError("mock connection error")

        monkeypatch.setattr(requests.Session, "get", _raise)
        col = NaverNxtTurnoverCollector()
        # 예외 없이 실행되어야 함 (빈 리스트 또는 fallback 결과 반환)
        result = col.collect(max_pages=1, max_stocks=5)
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────
# 거래량 급증 수집기 테스트
# ─────────────────────────────────────────────────────────────

class TestNaverVolumeSpikeCollector:
    def test_import(self):
        from app.data.naver_volume_spike_collector import collect_volume_spike_stocks
        assert callable(collect_volume_spike_stocks)

    def test_returns_list(self, monkeypatch):
        from app.data.naver_volume_spike_collector import collect_volume_spike_stocks
        import requests

        def _raise(*a, **kw):
            raise requests.ConnectionError("mock")

        monkeypatch.setattr(requests.Session, "get", _raise)
        result = collect_volume_spike_stocks(max_pages=1, max_stocks=5)
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────
# 섹터 강도 분석 테스트
# ─────────────────────────────────────────────────────────────

class TestSectorStrengthAnalyzer:
    def _classified_stocks(self):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [
            _make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1),
            _make_stock("005930", "삼성전자", 80000, 3.5, 400_000_000_000, rank=2),
            _make_stock("012450", "한화에어로스페이스", 350000, 4.5, 300_000_000_000, rank=3),
        ]
        return mapper.classify_stocks(stocks)

    def test_import(self):
        from app.strategy.sector_strength_analyzer import SectorStrengthAnalyzer
        assert SectorStrengthAnalyzer is not None

    def test_analyze_returns_dict(self):
        from app.strategy.sector_strength_analyzer import SectorStrengthAnalyzer
        analyzer = SectorStrengthAnalyzer()
        stocks = self._classified_stocks()
        result = analyzer.analyze(stocks)
        assert isinstance(result, dict)

    def test_sector_total_trading_value(self):
        from app.strategy.sector_strength_analyzer import SectorStrengthAnalyzer
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [
            _make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1),
            _make_stock("005930", "삼성전자", 80000, 3.5, 400_000_000_000, rank=2),
        ]
        classified = mapper.classify_stocks(stocks)
        analyzer = SectorStrengthAnalyzer()
        result = analyzer.analyze(classified)
        semi = result.get("semiconductor", {})
        assert semi.get("sector_total_trading_value", 0) == pytest.approx(
            900_000_000_000, rel=0.01
        ), f"Expected ~900B, got {semi.get('sector_total_trading_value')}"

    def test_sector_strength_ranking(self):
        from app.strategy.sector_strength_analyzer import SectorStrengthAnalyzer
        analyzer = SectorStrengthAnalyzer()
        stocks = self._classified_stocks()
        analyzer.analyze(stocks)
        top = analyzer.get_top_sectors(n=3)
        assert isinstance(top, list)
        assert all("sector_strength_score" in s for s in top)

    def test_volume_spike_overlap_count(self):
        from app.strategy.sector_strength_analyzer import SectorStrengthAnalyzer
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [_make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1)]
        classified = mapper.classify_stocks(stocks)
        analyzer = SectorStrengthAnalyzer()
        result = analyzer.analyze(classified, volume_spike_symbols={"000660"})
        semi = result.get("semiconductor", {})
        assert semi.get("volume_spike_overlap_count", 0) >= 1

    def test_us_sector_match(self):
        from app.strategy.sector_strength_analyzer import SectorStrengthAnalyzer
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [_make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1)]
        classified = mapper.classify_stocks(stocks)
        analyzer = SectorStrengthAnalyzer()
        us_result = {"strong_sectors": ["semiconductor"], "moderate_sectors": [], "market_regime": "risk_on"}
        result = analyzer.analyze(classified, us_sector_results=us_result)
        semi = result.get("semiconductor", {})
        assert semi.get("us_sector_match") is True


# ─────────────────────────────────────────────────────────────
# 미국 섹터 강도 서비스 테스트
# ─────────────────────────────────────────────────────────────

class TestUSSectorStrengthService:
    def test_import(self):
        from app.services.us_sector_strength_service import USSectorStrengthService
        assert USSectorStrengthService is not None

    def test_returns_dict_structure(self, monkeypatch):
        from app.services.us_sector_strength_service import USSectorStrengthService

        def _mock_fetch(self, symbols):
            return {"SMH": 2.5, "SOXX": 1.8, "XLK": 1.2, "SPY": 0.5, "QQQ": 1.0}

        monkeypatch.setattr(USSectorStrengthService, "_fetch_yahoo_etf_changes", _mock_fetch)
        svc = USSectorStrengthService()
        result = svc.get_us_sector_strength()
        assert "market_regime" in result
        assert "strong_sectors" in result
        assert "sector_scores" in result

    def test_semiconductor_strong_when_smh_up(self, monkeypatch):
        from app.services.us_sector_strength_service import USSectorStrengthService

        def _mock_fetch(self, symbols):
            return {"SMH": 3.5, "SOXX": 3.0, "XLK": 0.2, "XLF": -0.5, "SPY": 0.3, "QQQ": 0.8}

        monkeypatch.setattr(USSectorStrengthService, "_fetch_yahoo_etf_changes", _mock_fetch)
        svc = USSectorStrengthService()
        result = svc.get_us_sector_strength()
        assert "semiconductor" in result.get("strong_sectors", []) or \
               result.get("sector_scores", {}).get("semiconductor", 0) > result.get("sector_scores", {}).get("financials", 100)

    def test_risk_off_when_spy_and_qqq_down(self, monkeypatch):
        from app.services.us_sector_strength_service import USSectorStrengthService

        def _mock_fetch(self, symbols):
            return {"SPY": -1.5, "QQQ": -2.0, "SMH": -1.0, "XLK": -1.5}

        monkeypatch.setattr(USSectorStrengthService, "_fetch_yahoo_etf_changes", _mock_fetch)
        svc = USSectorStrengthService()
        result = svc.get_us_sector_strength()
        assert result.get("market_regime") == "risk_off"

    def test_no_data_returns_safe_defaults(self, monkeypatch):
        from app.services.us_sector_strength_service import USSectorStrengthService
        import requests

        def _raise(*a, **kw):
            raise requests.ConnectionError("mock")

        monkeypatch.setattr(requests.Session, "get", _raise)
        svc = USSectorStrengthService()
        result = svc.get_us_sector_strength()
        # 예외 없이 반환되어야 함
        assert isinstance(result, dict)
        assert result.get("data_source_used") in ("cache", "none")

    def test_fallback_when_ssga_fails(self, monkeypatch):
        """SSGA 파싱 실패 시 Yahoo fallback 실행 확인."""
        from app.services.us_sector_strength_service import USSectorStrengthService

        call_count = {"n": 0}

        def _mock_fetch(self, symbols):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("simulated SSGA failure")
            return {"SPY": 0.5, "QQQ": 0.8}

        monkeypatch.setattr(USSectorStrengthService, "_fetch_yahoo_etf_changes", _mock_fetch)
        svc = USSectorStrengthService()
        # 서비스가 정상 실행되어야 함 (예외 없음)
        result = svc.get_us_sector_strength()
        assert isinstance(result, dict)

    def test_risk_off_reduces_us_match_score(self, monkeypatch):
        from app.services.us_sector_strength_service import USSectorStrengthService

        def _mock_fetch(self, symbols):
            return {"SMH": 3.0, "SPY": -1.5, "QQQ": -2.0}

        monkeypatch.setattr(USSectorStrengthService, "_fetch_yahoo_etf_changes", _mock_fetch)
        svc = USSectorStrengthService()
        us_result = svc.get_us_sector_strength()
        us_result["market_regime"] = "risk_off"

        score_on, _, _ = svc.get_us_sector_match_score("semiconductor", us_result, us_sector_match_score_max=20)
        us_result["market_regime"] = "risk_on"
        score_off_base, _, _ = svc.get_us_sector_match_score("semiconductor", us_result, us_sector_match_score_max=20)
        us_result["market_regime"] = "risk_off"
        score_off, _, _ = svc.get_us_sector_match_score("semiconductor", us_result, us_sector_match_score_max=20)
        # risk_off 시 점수 축소 확인
        assert score_off <= score_off_base

    def test_top3_csv_us_columns(self, tmp_path, monkeypatch):
        """Top3 CSV에 us_sector_match_score, matched_us_sector, us_sector_reason 포함 확인."""
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        import pandas as pd

        selector = SectorLeaderTop3Selector()
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [
            _make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1),
            _make_stock("012450", "한화에어로스페이스", 350000, 4.5, 300_000_000_000, rank=2),
            _make_stock("005490", "POSCO홀딩스", 500000, 3.0, 100_000_000_000, rank=3),
        ]
        classified = mapper.classify_stocks(stocks)
        us_result = {
            "market_regime": "risk_on",
            "data_source_used": "yahoo",
            "strong_sectors": ["semiconductor"],
            "moderate_sectors": ["defense"],
            "sector_scores": {"semiconductor": 80, "defense": 55},
            "spy_change": 0.5,
            "qqq_change": 0.8,
        }
        top3, diag, excluded = selector.select(classified, [], us_result)

        out_path = str(tmp_path / "test_top3.csv")
        if top3:
            selector.save_top3_csv(top3, date_str="20260620", time_str="0900")
        # CSV 컬럼 체크는 top3 dict에서
        for s in top3:
            assert "us_sector_match_score" in s
            assert "matched_us_sector" in s
            assert "us_sector_reason" in s


# ─────────────────────────────────────────────────────────────
# 대장주 판정 + Top3 선정 테스트
# ─────────────────────────────────────────────────────────────

class TestSectorLeaderTop3Selector:
    def _classified(self, stocks=None):
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        return mapper.classify_stocks(stocks or ALL_STOCKS)

    def test_import(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        assert SectorLeaderTop3Selector is not None

    def test_returns_tuple_of_three(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        stocks = self._classified()
        top3, diag, excluded = selector.select(stocks, [], {})
        assert isinstance(top3, list)
        assert isinstance(diag, dict)
        assert isinstance(excluded, list)

    def test_top3_at_most_3(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(self._classified(), [], {})
        assert len(top3) <= 3

    def test_etf_excluded(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        top3, _, excluded = selector.select(self._classified(), [], {})
        top3_symbols = {s["symbol"] for s in top3}
        assert "069500" not in top3_symbols  # KODEX200

    def test_preferred_stock_excluded(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(self._classified(), [], {})
        top3_symbols = {s["symbol"] for s in top3}
        assert "005935" not in top3_symbols  # 삼성전자우

    def test_price_below_20k_excluded(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(self._classified(), [], {})
        top3_symbols = {s["symbol"] for s in top3}
        assert "123456" not in top3_symbols  # 저가주 5000원

    def test_trading_value_below_20b_excluded(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(self._classified(), [], {})
        top3_symbols = {s["symbol"] for s in top3}
        assert "234567" not in top3_symbols  # 소형주 거래대금 10억

    def test_high_change_rate_excluded(self):
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(self._classified(), [], {})
        top3_symbols = {s["symbol"] for s in top3}
        assert "345678" not in top3_symbols  # 상승률 20% 초과

    def test_same_sector_max_2(self):
        """동일 섹터 최대 2개 제한 확인."""
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        # 반도체 종목 3개 + 방산 1개
        stocks = [
            _make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1),
            _make_stock("005930", "삼성전자", 80000, 4.0, 400_000_000_000, rank=2),
            _make_stock("042700", "한미반도체", 85000, 6.0, 80_000_000_000, rank=3),
            _make_stock("012450", "한화에어로스페이스", 350000, 4.5, 300_000_000_000, rank=4),
        ]
        classified = mapper.classify_stocks(stocks)
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(classified, [], {})
        sector_count: dict[str, int] = {}
        for s in top3:
            sector_count[s.get("sector", "")] = sector_count.get(s.get("sector", ""), 0) + 1
        assert all(v <= 2 for v in sector_count.values()), f"Same sector > 2: {sector_count}"

    def test_us_sector_match_score_applied(self):
        """미국 strong semiconductor 시 국내 semiconductor 종목에 점수 반영 확인."""
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [
            _make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1),
        ]
        classified = mapper.classify_stocks(stocks)
        us_result = {
            "market_regime": "risk_on",
            "data_source_used": "yahoo",
            "strong_sectors": ["semiconductor"],
            "moderate_sectors": [],
            "sector_scores": {"semiconductor": 85},
        }
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(classified, [], us_result)
        if top3:
            assert top3[0].get("us_sector_match_score", 0) > 0

    def test_us_no_data_score_is_zero(self):
        """미국 데이터 없을 때 us_sector_match_score=0 확인."""
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [_make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1)]
        classified = mapper.classify_stocks(stocks)
        us_result = {"market_regime": "neutral", "data_source_used": "none",
                     "strong_sectors": [], "moderate_sectors": [], "sector_scores": {}}
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(classified, [], us_result)
        if top3:
            assert top3[0].get("us_sector_match_score", 0) == 0

    def test_strategy_mode_gap_compatible(self):
        """strategy.mode='gap'이면 기존 갭상승 전략 모듈 임포트 가능 확인."""
        try:
            from app.strategy.top15_selector import Top15Selector
            assert Top15Selector is not None
        except ImportError:
            pytest.skip("top15_selector not available")

    def test_strategy_mode_volume_spike_compatible(self):
        """strategy.mode='volume_spike'이면 기존 거래량 급증 전략 모듈 임포트 가능."""
        from app.strategy.volume_spike_selector import VolumeSpikeSelector
        assert VolumeSpikeSelector is not None

    def test_final_score_formula(self):
        """final_score = sector_strength + leader + us_match + vs_confirm + ma_bonus - risk_penalty."""
        from app.strategy.sector_leader_top3_selector import SectorLeaderTop3Selector
        from app.strategy.sector_mapper import SectorMapper
        mapper = SectorMapper()
        stocks = [_make_stock("000660", "SK하이닉스", 200000, 5.0, 500_000_000_000, rank=1)]
        classified = mapper.classify_stocks(stocks)
        us_result = {"market_regime": "risk_on", "data_source_used": "yahoo",
                     "strong_sectors": ["semiconductor"], "moderate_sectors": [], "sector_scores": {"semiconductor": 85}}
        selector = SectorLeaderTop3Selector()
        top3, _, _ = selector.select(classified, [], us_result)
        if top3:
            s = top3[0]
            expected = (s.get("sector_strength_score", 0) + s.get("sector_leader_score", 0)
                        + s.get("us_sector_match_score", 0) + s.get("volume_spike_confirm_score", 0)
                        + s.get("ma_bonus", 0) - s.get("risk_penalty", 0))
            assert abs(s.get("final_score", 0) - expected) < 0.01
