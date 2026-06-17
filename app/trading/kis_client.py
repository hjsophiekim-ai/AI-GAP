"""
KISClient - 한국투자증권 Open API 공통 클라이언트.

mock(모의투자)과 real(실전투자)을 mode 파라미터로 분리합니다.
API 키/시크릿/토큰은 로그에 절대 출력하지 않습니다.
"""

import requests
from datetime import datetime, timedelta
from app.logger import logger

# ── base URLs ──────────────────────────────────────────────────────────────
BASE_URL_MOCK = "https://openapivts.koreainvestment.com:29443"
BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"

# ── TR IDs (공식 문서 기준) ────────────────────────────────────────────────
TR_CURRENT_PRICE = "FHKST01010100"

TR_BALANCE_REAL = "TTTC8434R"
TR_BALANCE_MOCK = "VTTC8434R"

TR_BUYABLE_REAL = "TTTC8908R"
TR_BUYABLE_MOCK = "VTTC8908R"

TR_BUY_REAL = "TTTC0802U"
TR_BUY_MOCK = "VTTC0802U"

TR_SELL_REAL = "TTTC0801U"
TR_SELL_MOCK = "VTTC0801U"

TR_ORDER_HISTORY_REAL = "TTTC8001R"   # 공식 문서 확인 필요
TR_ORDER_HISTORY_MOCK = "VTTC8001R"   # 공식 문서 확인 필요

TR_DAILY_PRICE = "FHKST01010400"      # 국내주식 일별 주가

ORD_DVSN_LIMIT = "00"
ORD_DVSN_MARKET = "01"


class KISClient:
    """
    한국투자증권 Open API 클라이언트.

    Parameters
    ----------
    app_key : str
    app_secret : str
    account_no : str   예: "12345678"
    product_code : str 예: "01"
    mode : str         "mock" 또는 "real"
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        product_code: str = "01",
        mode: str = "mock",
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self.account_no = account_no
        self.product_code = product_code
        self.mode = mode
        self.base_url = BASE_URL_MOCK if mode == "mock" else BASE_URL_REAL
        self._token: str = ""
        self._token_expires_at: datetime = datetime.min
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json; charset=utf-8"})

    # ── 공개 속성 (브로커 서브클래스에서 헤더 구성에 사용) ─────────────────

    @property
    def app_key(self) -> str:
        return self._app_key

    @property
    def app_secret(self) -> str:
        return self._app_secret

    # ── 공개 팩토리 ───────────────────────────────────────────────────────

    @classmethod
    def from_account_config(cls, account_cfg: dict) -> "KISClient":
        """get_kis_account_config()의 반환값을 받아 인스턴스를 생성합니다."""
        return cls(
            app_key=account_cfg["app_key"],
            app_secret=account_cfg["app_secret"],
            account_no=account_cfg["account_no"],
            product_code=account_cfg.get("product_code", "01"),
            mode=account_cfg.get("mode", "mock"),
        )

    def is_configured(self) -> bool:
        return bool(self._app_key and self._app_secret and self.account_no)

    # ── 토큰 ──────────────────────────────────────────────────────────────

    def get_access_token(self) -> str:
        """액세스 토큰 발급/갱신 (만료 1분 전 자동 갱신)."""
        now = datetime.now()
        if self._token and now < self._token_expires_at - timedelta(minutes=1):
            return self._token

        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            resp = self._session.post(url, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("access_token", "")
            expires_in = int(data.get("expires_in", 86400))
            self._token_expires_at = now + timedelta(seconds=expires_in)
            logger.info(f"[KIS-{self.mode.upper()}] 토큰 발급 완료 (만료: {self._token_expires_at:%H:%M:%S})")
            return self._token
        except Exception as e:
            logger.error(f"[KIS-{self.mode.upper()}] 토큰 발급 실패: {e}")
            raise

    def _auth_headers(self, tr_id: str) -> dict:
        token = self.get_access_token()
        return {
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # ── hashkey ────────────────────────────────────────────────────────────

    def get_hashkey(self, body: dict) -> str:
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "Content-Type": "application/json",
        }
        try:
            resp = self._session.post(url, json=body, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("HASH", "")
        except Exception as e:
            logger.warning(f"[KIS] hashkey 조회 실패: {e}")
            return ""

    # ── 현재가 조회 ────────────────────────────────────────────────────────

    def get_current_price(self, symbol: str) -> dict | None:
        """
        국내주식 현재가 조회.
        반환: {"current_price": float, "open": float, "high": float, "low": float,
               "prev_close": float, "change_rate": float, "volume": int, "trade_value": float}
        실패 시 None 반환.
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = self._auth_headers(TR_CURRENT_PRICE)
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        try:
            resp = self._session.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            d = resp.json().get("output", {})
            if not d:
                logger.warning(f"[KIS] 현재가 응답 없음: {symbol}")
                return None
            return {
                "current_price": float(d.get("stck_prpr", 0)),
                "open": float(d.get("stck_oprc", 0)),
                "high": float(d.get("stck_hgpr", 0)),
                "low": float(d.get("stck_lwpr", 0)),
                "prev_close": float(d.get("stck_sdpr", 0)),
                "change_rate": float(d.get("prdy_ctrt", 0)),
                "volume": int(d.get("acml_vol", 0)),
                "trade_value": float(d.get("acml_tr_pbmn", 0)),
            }
        except Exception as e:
            logger.warning(f"[KIS] 현재가 조회 실패 {symbol}: {e}")
            return None

    # ── 잔고 조회 ──────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """
        계좌 잔고 조회.
        반환: {"cash": float, "positions": [{"symbol","name","quantity","avg_price","current_price"}]}
        실패 시 {"cash": 0, "positions": [], "error": str} 반환.
        """
        tr_id = TR_BALANCE_MOCK if self.mode == "mock" else TR_BALANCE_REAL
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = self._auth_headers(tr_id)
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            resp = self._session.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            output2 = data.get("output2", [{}])
            cash = float((output2[0] if output2 else {}).get("dnca_tot_amt", 0))

            positions = []
            for item in data.get("output1", []):
                qty = int(item.get("hldg_qty", 0))
                if qty <= 0:
                    continue
                positions.append({
                    "symbol": item.get("pdno", ""),
                    "name": item.get("prdt_name", ""),
                    "quantity": qty,
                    "avg_price": float(item.get("pchs_avg_pric", 0)),
                    "current_price": float(item.get("prpr", 0)),
                })
            return {"cash": cash, "positions": positions}
        except Exception as e:
            logger.error(f"[KIS-{self.mode.upper()}] 잔고 조회 실패: {e}")
            return {"cash": 0.0, "positions": [], "error": str(e)}

    # ── 주문 가능 금액 ────────────────────────────────────────────────────

    def get_buyable_cash(self, symbol: str = "005930", price: int = 0) -> float:
        """주문 가능 현금 조회. 실패 시 0 반환."""
        tr_id = TR_BUYABLE_MOCK if self.mode == "mock" else TR_BUYABLE_REAL
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        headers = self._auth_headers(tr_id)
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.product_code,
            "PDNO": symbol,
            "ORD_UNPR": str(price),
            "ORD_DVSN": ORD_DVSN_LIMIT,
            "CMA_EVLU_AMT_ICLD_YN": "Y",
            "OVRS_ICLD_YN": "N",
        }
        try:
            resp = self._session.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            output = resp.json().get("output", {})
            return float(output.get("ord_psbl_cash", 0))
        except Exception as e:
            logger.warning(f"[KIS-{self.mode.upper()}] 주문가능금액 조회 실패: {e}")
            return 0.0

    # ── 일별 주가 조회 (MA 계산용) ────────────────────────────────────────

    def get_daily_prices(self, symbol: str, days: int = 65) -> list[dict]:
        """
        국내주식 일별 주가 조회 (최근 N 영업일).
        반환: [{"date": "20260617", "close": float, "open": float, "high": float, "low": float, "volume": int}, ...]
        날짜 내림차순 (가장 최근이 [0]).
        실패 시 [] 반환.
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        headers = self._auth_headers(TR_DAILY_PRICE)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        try:
            resp = self._session.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            output = resp.json().get("output", [])
            result = []
            for row in output[:days]:
                close = float(row.get("stck_clpr", 0) or 0)
                if close <= 0:
                    continue
                result.append({
                    "date": row.get("stck_bsop_date", ""),
                    "close": close,
                    "open": float(row.get("stck_oprc", 0) or 0),
                    "high": float(row.get("stck_hgpr", 0) or 0),
                    "low": float(row.get("stck_lwpr", 0) or 0),
                    "volume": int(row.get("acml_vol", 0) or 0),
                })
            return result
        except Exception as e:
            logger.warning(f"[KIS] 일별주가 조회 실패 {symbol}: {e}")
            return []

    # ── 매수 주문 ──────────────────────────────────────────────────────────

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """
        매수 주문 실행.
        반환: {"success": bool, "order_id": str, "message": str, "raw": dict}
        """
        tr_id = TR_BUY_MOCK if self.mode == "mock" else TR_BUY_REAL
        ord_dvsn = ORD_DVSN_MARKET if order_type == "market" else ORD_DVSN_LIMIT
        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.product_code,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0" if order_type == "market" else str(price),
        }
        return self._place_order(tr_id, body, "buy", symbol, quantity, price)

    # ── 매도 주문 ──────────────────────────────────────────────────────────

    def sell(
        self,
        symbol: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """
        매도 주문 실행.
        반환: {"success": bool, "order_id": str, "message": str, "raw": dict}
        """
        tr_id = TR_SELL_MOCK if self.mode == "mock" else TR_SELL_REAL
        ord_dvsn = ORD_DVSN_MARKET if order_type == "market" else ORD_DVSN_LIMIT
        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.product_code,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0" if order_type == "market" else str(price),
        }
        return self._place_order(tr_id, body, "sell", symbol, quantity, price)

    # ── 내부 공통 주문 처리 ────────────────────────────────────────────────

    def _place_order(
        self,
        tr_id: str,
        body: dict,
        side: str,
        symbol: str,
        quantity: int,
        price: int,
    ) -> dict:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        hashkey = self.get_hashkey(body)
        headers = self._auth_headers(tr_id)
        if hashkey:
            headers["hashkey"] = hashkey

        logger.info(
            f"[KIS-{self.mode.upper()}] 주문 시도: side={side} symbol={symbol} "
            f"qty={quantity} price={price:,} tr_id={tr_id}"
        )

        try:
            resp = self._session.post(url, json=body, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            rt_cd = data.get("rt_cd", "")
            output = data.get("output", {})
            order_id = output.get("ODNO", "")
            msg = data.get("msg1", "")

            if rt_cd == "0":
                logger.info(f"[KIS-{self.mode.upper()}] 주문 성공: order_id={order_id}")
                return {"success": True, "order_id": order_id, "message": msg, "raw": output}
            else:
                logger.warning(f"[KIS-{self.mode.upper()}] 주문 실패: rt_cd={rt_cd} msg={msg}")
                return {"success": False, "order_id": "", "message": msg, "raw": data}
        except Exception as e:
            logger.error(f"[KIS-{self.mode.upper()}] 주문 예외: {e}")
            return {"success": False, "order_id": "", "message": str(e), "raw": {}}


def create_kis_client(mode: str = "mock") -> "KISClient | None":
    """
    환경변수에서 인증 정보를 읽어 KISClient를 생성합니다.
    환경변수가 없으면 None을 반환합니다 (dry_run fallback용).
    """
    from app.config import get_kis_account_config
    try:
        account_cfg = get_kis_account_config(mode)
        client = KISClient.from_account_config(account_cfg)
        logger.info(f"[KIS] {mode} 클라이언트 초기화 완료")
        return client
    except ValueError as e:
        logger.warning(f"[KIS] 클라이언트 초기화 실패 ({mode}): {e}")
        return None
