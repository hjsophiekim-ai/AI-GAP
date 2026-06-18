"""
Tests for VolumeSpikeSelector — change_rate 5~15% 필터 검증.

8개 테스트:
  1. change_rate 4.9% 종목 제외
  2. change_rate 5.0% 종목 통과
  3. change_rate 10.0% 종목이 가장 높은 change_rate_score(8)를 받는지
  4. change_rate 15.0% 종목 통과
  5. change_rate 15.1% 종목 제외
  6. Top10 부족 시에도 5% 미만 종목은 fallback 복구 금지
  7. Top10 부족 시에도 15% 초과 종목은 fallback 복구 금지
  8. Top10 dict에 change_rate_score 컬럼 포함
"""
import pytest

from app.strategy.volume_spike_selector import VolumeSpikeSelector, _change_rate_score


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_stock(
    symbol: str,
    name: str,
    change_rate: float,
    trade_value: float = 5_000_000_000,
    current_price: float = 50_000,
) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "current_price": current_price,
        "change_rate": change_rate,
        "trade_value": trade_value,
        "volume": 1_000_000,
        "is_etf": False,
        "is_etn": False,
        "is_preferred": False,
        "is_spac": False,
        "is_reit": False,
    }


def _selector_with_cfg(
    min_cr=5.0,
    max_cr=15.0,
    target_n=10,
    min_tv=3_000_000_000,
    fallback_tv=1_000_000_000,
):
    """config 없는 stub selector (vs_cfg를 직접 주입)."""
    sel = VolumeSpikeSelector.__new__(VolumeSpikeSelector)
    sel.cfg = None
    sel._vs_cfg = {
        "target_top_n": target_n,
        "min_price": 20_000,
        "min_change_rate": min_cr,
        "max_change_rate": max_cr,
        "min_trading_value": min_tv,
        "fallback_min_trading_value": fallback_tv,
        "max_candidates_to_score": 80,
    }
    return sel


# ---------------------------------------------------------------------------
# 1. 4.9% 종목은 제외
# ---------------------------------------------------------------------------

def test_change_rate_49_excluded():
    sel = _selector_with_cfg()
    stocks = [_make_stock("000001", "테스트A", change_rate=4.9)]
    top10, diag = sel.select(stocks)
    assert diag["excluded_below_5pct"] == 1
    assert not any(s["symbol"] == "000001" for s in top10)


# ---------------------------------------------------------------------------
# 2. 5.0% 종목은 통과
# ---------------------------------------------------------------------------

def test_change_rate_50_passes():
    sel = _selector_with_cfg()
    stocks = [_make_stock("000002", "테스트B", change_rate=5.0)]
    top10, diag = sel.select(stocks)
    assert diag["excluded_below_5pct"] == 0
    assert any(s["symbol"] == "000002" for s in top10)


# ---------------------------------------------------------------------------
# 3. 10.0% 종목이 가장 높은 change_rate_score(8) 받는지
# ---------------------------------------------------------------------------

def test_change_rate_100_highest_score():
    score = _change_rate_score(10.0)
    assert score == 8.0, f"expected 8.0, got {score}"


def test_change_rate_score_ordering():
    """8~12% 구간이 5~8% 및 12~15% 구간보다 점수가 높아야 한다."""
    assert _change_rate_score(10.0) > _change_rate_score(6.0)
    assert _change_rate_score(10.0) > _change_rate_score(13.0)


# ---------------------------------------------------------------------------
# 4. 15.0% 종목 통과
# ---------------------------------------------------------------------------

def test_change_rate_150_passes():
    sel = _selector_with_cfg()
    stocks = [_make_stock("000004", "테스트D", change_rate=15.0)]
    top10, diag = sel.select(stocks)
    assert diag["excluded_above_15pct"] == 0
    assert any(s["symbol"] == "000004" for s in top10)


# ---------------------------------------------------------------------------
# 5. 15.1% 종목 제외
# ---------------------------------------------------------------------------

def test_change_rate_151_excluded():
    sel = _selector_with_cfg()
    stocks = [_make_stock("000005", "테스트E", change_rate=15.1)]
    top10, diag = sel.select(stocks)
    assert diag["excluded_above_15pct"] == 1
    assert not any(s["symbol"] == "000005" for s in top10)


# ---------------------------------------------------------------------------
# 6. Top10 부족 시 5% 미만 종목은 fallback 복구 금지
# ---------------------------------------------------------------------------

def test_fallback_does_not_recover_below_5pct():
    """primary pass가 0개여도 4.9% 종목은 fallback에 포함 안 됨."""
    sel = _selector_with_cfg(target_n=10)
    stocks = [
        _make_stock(f"A{i:03d}", f"종목{i}", change_rate=4.9, trade_value=2_000_000_000)
        for i in range(5)
    ]
    top10, diag = sel.select(stocks)
    assert diag["excluded_below_5pct"] == 5
    assert diag["final_top10"] == 0


# ---------------------------------------------------------------------------
# 7. Top10 부족 시 15% 초과 종목은 fallback 복구 금지
# ---------------------------------------------------------------------------

def test_fallback_does_not_recover_above_15pct():
    """primary pass가 0개여도 15.1% 종목은 fallback에 포함 안 됨."""
    sel = _selector_with_cfg(target_n=10)
    stocks = [
        _make_stock(f"B{i:03d}", f"종목{i}", change_rate=15.5, trade_value=2_000_000_000)
        for i in range(5)
    ]
    top10, diag = sel.select(stocks)
    assert diag["excluded_above_15pct"] == 5
    assert diag["final_top10"] == 0


# ---------------------------------------------------------------------------
# 8. Top10 dict에 change_rate_score 컬럼 포함
# ---------------------------------------------------------------------------

def test_top10_dict_has_change_rate_score():
    sel = _selector_with_cfg()
    stocks = [_make_stock("005930", "삼성전자", change_rate=9.0)]
    top10, _ = sel.select(stocks)
    assert top10, "선정 결과 없음"
    assert "change_rate_score" in top10[0]
    assert top10[0]["change_rate_score"] == 8.0  # 8~12% 구간
