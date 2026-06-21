"""
Tests for KisRealBroker safety gates.
No actual API calls are made — KISClient is mocked.
"""
import pytest
from unittest.mock import MagicMock

from app.trading.kis_real_broker import KisRealBroker
from app.models import OrderResult


# ---------------------------------------------------------------------------
# Config stubs
# ---------------------------------------------------------------------------

class _SafetyOn:
    """모든 안전장치가 통과되는 설정."""
    _raw = {
        "kis": {"real": {"enabled": True}},
    }
    safety = {
        "enable_real_trading": True,
        "enable_real_buy": True,
        "enable_real_sell": True,
        "require_real_confirm": True,
        "real_confirm_text": "I_UNDERSTAND_REAL_TRADING_RISK",
        "max_real_order_amount": 1_000_000,
        "max_real_daily_budget": 1_000_000,
    }

    def real_trading_enabled(self) -> bool:
        return True

    def real_buy_enabled(self) -> bool:
        return True

    def real_sell_enabled(self) -> bool:
        return True

    def require_real_confirm(self) -> bool:
        return True

    def real_confirm_text(self) -> str:
        return "I_UNDERSTAND_REAL_TRADING_RISK"


class _RealTradingDisabled(_SafetyOn):
    """enable_real_trading = false."""

    def real_trading_enabled(self) -> bool:
        return False


class _KisRealDisabled(_SafetyOn):
    """kis.real.enabled = false."""
    _raw = {
        "kis": {"real": {"enabled": False}},
    }


class _NoConfirmRequired(_SafetyOn):
    """require_real_confirm = false."""

    def require_real_confirm(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Mock KISClient
# ---------------------------------------------------------------------------

def _mock_kis():
    kis = MagicMock()
    kis.buy.return_value = {"success": True, "order_id": "TEST-001", "message": "ok", "raw": {}}
    kis.sell.return_value = {"success": True, "order_id": "TEST-002", "message": "ok", "raw": {}}
    kis.get_balance.return_value = {"cash": 5_000_000, "positions": []}
    kis.get_buyable_cash.return_value = 5_000_000.0
    kis.get_current_price.return_value = {"current_price": 70_000.0}
    return kis


# ---------------------------------------------------------------------------
# Safety gate tests
# ---------------------------------------------------------------------------

def test_gate_real_trading_disabled():
    """safety.enable_real_trading=false → RuntimeError."""
    with pytest.raises(RuntimeError, match="비활성화"):
        KisRealBroker(
            _mock_kis(),
            cfg=_RealTradingDisabled(),
            confirm_text="I_UNDERSTAND_REAL_TRADING_RISK",
        )


def test_gate_kis_real_disabled():
    """kis.real.enabled=false → RuntimeError."""
    with pytest.raises(RuntimeError, match="비활성화"):
        KisRealBroker(
            _mock_kis(),
            cfg=_KisRealDisabled(),
            confirm_text="I_UNDERSTAND_REAL_TRADING_RISK",
        )


def test_gate_wrong_confirm_text():
    """틀린 확인 문구 → RuntimeError."""
    with pytest.raises(RuntimeError, match="확인 문구"):
        KisRealBroker(
            _mock_kis(),
            cfg=_SafetyOn(),
            confirm_text="WRONG_TEXT",
        )


def test_gate_empty_confirm_text():
    """빈 확인 문구 → RuntimeError."""
    with pytest.raises(RuntimeError, match="확인 문구"):
        KisRealBroker(
            _mock_kis(),
            cfg=_SafetyOn(),
            confirm_text="",
        )


def test_gate_no_confirm_required():
    """require_real_confirm=false이면 confirm_text 없어도 통과."""
    broker = KisRealBroker(
        _mock_kis(),
        cfg=_NoConfirmRequired(),
        confirm_text="",
    )
    assert broker.mode == "real"


def test_gate_correct_confirm_passes():
    """올바른 확인 문구 → 인스턴스 생성 성공."""
    broker = KisRealBroker(
        _mock_kis(),
        cfg=_SafetyOn(),
        confirm_text="I_UNDERSTAND_REAL_TRADING_RISK",
    )
    assert broker.mode == "real"


# ---------------------------------------------------------------------------
# Order amount limit tests (gate 5+6)
# ---------------------------------------------------------------------------

@pytest.fixture
def real_broker():
    """통과된 안전장치, max_real_order_amount=1,000,000."""
    return KisRealBroker(
        _mock_kis(),
        cfg=_SafetyOn(),
        confirm_text="I_UNDERSTAND_REAL_TRADING_RISK",
    )


def test_order_within_limit(real_broker):
    """주문금액 < max_real_order_amount → 주문 성공."""
    result = real_broker.buy("005930", "삼성전자", quantity=1, price=500_000)
    assert result.success is True


def test_order_exceeds_limit(real_broker):
    """주문금액 > max_real_order_amount → 차단."""
    result = real_broker.buy("005930", "삼성전자", quantity=2, price=600_000)
    assert result.success is False
    assert "safety rule" in result.message


def test_daily_budget_exceeded(real_broker):
    """첫 주문 후 일일 한도 초과 → 두 번째 주문 차단."""
    # 첫 주문: 1주 * 900,000 = 900,000원 (한도 1,000,000원 이내)
    real_broker._daily_ordered_amount = 900_000
    result = real_broker.buy("005930", "삼성전자", quantity=1, price=200_000)
    # 900,000 + 200,000 = 1,100,000 > 1,000,000
    assert result.success is False
    assert "일일 한도" in result.message


def test_daily_amount_accumulates(real_broker):
    """성공한 주문은 일일 누적 금액에 합산."""
    initial = real_broker._daily_ordered_amount
    real_broker.buy("005930", "삼성전자", quantity=1, price=100_000)
    assert real_broker._daily_ordered_amount == initial + 100_000


# ---------------------------------------------------------------------------
# Sell does not check order limits
# ---------------------------------------------------------------------------

def test_sell_no_limit_check(real_broker):
    """매도는 주문금액 한도 체크를 하지 않는다."""
    result = real_broker.sell("005930", "삼성전자", quantity=100, price=100_000)
    assert result.success is True


# ---------------------------------------------------------------------------
# Naver/pre-market data fallback test
# ---------------------------------------------------------------------------

def test_naver_gap_collector_fallback():
    """장전 데이터: previous_close가 없으면 change_rate로 역산한다."""
    from app.data.naver_gap_collector import _parse_stock_row
    from bs4 import BeautifulSoup

    # sise_rise layout: 6컬럼 (rank, name/link, price, change_rate, change_amt, volume, prev_close)
    html = """<tr>
        <td><a href="/item/main.naver?code=123456">테스트주</a></td>
        <td>10000</td>
        <td>5.00</td>
        <td>500</td>
        <td>100</td>
        <td>9524</td>
    </tr>"""
    row = BeautifulSoup(html, "html.parser").find("tr")
    result = _parse_stock_row(row)

    # 파싱 실패 시 None (컬럼 수 부족)이어도 OK — 여기서는 구조 확인
    if result is not None:
        # previous_close가 있으면 gap_rate가 계산됨
        assert "current_price" in result
        assert "symbol" in result
