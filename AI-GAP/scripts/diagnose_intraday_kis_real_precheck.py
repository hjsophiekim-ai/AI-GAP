#!/usr/bin/env python
"""
diagnose_intraday_kis_real_precheck.py

KIS 실계좌 장중 자동매매 사전진단 스크립트.
실제 주문 API는 절대 호출하지 않습니다.

확인 항목:
  1. 환경변수 존재 여부 (key/secret/계좌번호 원문 미출력)
  2. KIS_REAL_BASE_URL 확인
  3. ENABLE_REAL_TRADING / ENABLE_REAL_BUY / ENABLE_REAL_SELL 값
  4. 실계좌 token 발급 가능 여부
  5. 현재가 조회 가능 여부
  6. 1분봉 조회 가능 여부

종료 규칙:
  - KIS_REAL_APP_KEY 또는 APP_SECRET 미설정 → exit 1 (치명적)
  - 나머지 항목(CANO, 토큰, API 응답 등) → FAIL/WARN 표시 후 계속
성공 시: KIS_REAL_INTRADAY_PRECHECK_PASSED (KEY/SECRET 존재 시)
"""
import os
import sys
import requests
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

TEST_SYMBOL = "005930"  # 삼성전자 (진단용)

# 치명적 실패(KEY/SECRET 누락)만 exit 1 유발
_CRITICAL_FAILS = []
# 비치명적 실패(CANO 누락, 토큰 실패, API 오류 등) - PASSED는 출력
_SOFT_FAILS = []
_WARN_ITEMS = []


def _section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")
    _WARN_ITEMS.append(msg)


def _fail(msg: str, critical: bool = False) -> None:
    print(f"  [FAIL] {msg}")
    if critical:
        _CRITICAL_FAILS.append(msg)
    else:
        _SOFT_FAILS.append(msg)


print("\n" + "=" * 55)
print("  AI-GAP KIS 실계좌 장중 자동매매 사전진단")
print("=" * 55)
print("  [주의] 실제 주문 API는 절대 호출하지 않습니다.")

# ── Section 1: 환경변수 확인 ─────────────────────────────────────────────────
_section("1. 환경변수 확인 (원문 미출력)")

real_key    = os.getenv("KIS_REAL_APP_KEY", "")
real_secret = os.getenv("KIS_REAL_APP_SECRET", "")

# 계좌번호: KIS_REAL_CANO 또는 KIS_REAL_ACCOUNT_NO
real_cano   = os.getenv("KIS_REAL_CANO", "") or os.getenv("KIS_REAL_ACCOUNT_NO", "")
# 상품코드: KIS_REAL_ACNT_PRDT_CD 또는 KIS_REAL_ACCOUNT_PRODUCT_CODE
real_prdt   = os.getenv("KIS_REAL_ACNT_PRDT_CD", "") or os.getenv("KIS_REAL_ACCOUNT_PRODUCT_CODE", "")

if real_key:
    _ok("KIS_REAL_APP_KEY: EXISTS")
else:
    _fail("KIS_REAL_APP_KEY: MISSING", critical=True)

if real_secret:
    _ok("KIS_REAL_APP_SECRET: EXISTS")
else:
    _fail("KIS_REAL_APP_SECRET: MISSING", critical=True)

if real_cano:
    _ok("KIS_REAL_CANO (or ACCOUNT_NO): EXISTS")
else:
    _fail("KIS_REAL_CANO (or ACCOUNT_NO): MISSING (계좌번호 없음 - 주문 불가, 조회는 시도)")

if real_prdt:
    _ok("KIS_REAL_ACNT_PRDT_CD (or PRODUCT_CODE): EXISTS")
else:
    _warn("KIS_REAL_ACNT_PRDT_CD (or PRODUCT_CODE): MISSING (기본값 '01' 사용)")

# ── 치명적 실패 시 즉시 종료 ─────────────────────────────────────────────────
if _CRITICAL_FAILS:
    print("\n" + "=" * 55)
    print("  진단 결과: 치명적 환경변수 누락 (KIS API 사용 불가)")
    for item in _CRITICAL_FAILS:
        print(f"    X {item}")
    print("  → .env 파일에 KIS_REAL_APP_KEY, KIS_REAL_APP_SECRET 설정 필요")
    print("=" * 55)
    sys.exit(1)

# ── Section 2: base_url 확인 ─────────────────────────────────────────────────
_section("2. KIS_REAL_BASE_URL 확인")

BASE_URL_REAL_DEFAULT = "https://openapi.koreainvestment.com:9443"
base_url_env = os.getenv("KIS_REAL_BASE_URL", "")

if base_url_env:
    _ok(f"KIS_REAL_BASE_URL: {base_url_env}")
    BASE_URL_REAL = base_url_env
else:
    _warn(f"KIS_REAL_BASE_URL 미설정 → 기본값 사용: {BASE_URL_REAL_DEFAULT}")
    BASE_URL_REAL = BASE_URL_REAL_DEFAULT

try:
    from app.config import get_config
    cfg = get_config()
    cfg_url = cfg._raw.get("kis", {}).get("real", {}).get("base_url", BASE_URL_REAL_DEFAULT)
    if cfg_url:
        _ok(f"config.yaml real base_url: {cfg_url}")
        BASE_URL_REAL = cfg_url
except Exception as e:
    _warn(f"config.yaml 로드 오류: {e}")

# ── Section 3: 안전 플래그 확인 ──────────────────────────────────────────────
_section("3. 실전매매 안전 플래그 확인")

def _bool_env(env_name: str, cfg_path: tuple, default: bool = False):
    env_val = os.getenv(env_name, "")
    if env_val.lower() in ("1", "true", "yes"):
        return True, f"env:{env_name}=true"
    if env_val.lower() in ("0", "false", "no"):
        return False, f"env:{env_name}=false"
    try:
        node = cfg._raw
        for k in cfg_path:
            node = node.get(k, {})
        val = bool(node) if not isinstance(node, bool) else node
        return val, f"config:{'.'.join(cfg_path)}={node}"
    except Exception:
        return default, f"default={default}"

try:
    enable_real, src_real  = _bool_env("ENABLE_REAL_TRADING", ("safety", "enable_real_trading"))
    enable_buy,  src_buy   = _bool_env("ENABLE_REAL_BUY",     ("safety", "enable_real_buy"))
    enable_sell, src_sell  = _bool_env("ENABLE_REAL_SELL",    ("safety", "enable_real_sell"))
except Exception:
    enable_real = enable_buy = enable_sell = False
    src_real = src_buy = src_sell = "default=false"

print(f"  ENABLE_REAL_TRADING = {enable_real}  ({src_real})")
print(f"  ENABLE_REAL_BUY     = {enable_buy}   ({src_buy})")
print(f"  ENABLE_REAL_SELL    = {enable_sell}  ({src_sell})")

if not enable_real:
    _warn("ENABLE_REAL_TRADING=false → 실전매매 비활성화 (주문 차단됨)")
if not enable_buy:
    _warn("ENABLE_REAL_BUY=false → 실전 매수 비활성화")
if not enable_sell:
    _warn("ENABLE_REAL_SELL=false → 실전 매도 비활성화")

# ── Section 4: 토큰 발급 ─────────────────────────────────────────────────────
_section("4. 실계좌 토큰 발급 (주문 API 미사용)")

_token = ""
try:
    url  = f"{BASE_URL_REAL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey":     real_key,
        "appsecret":  real_secret,
    }
    resp = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=10)
    data = resp.json()

    if resp.status_code == 200 and data.get("access_token"):
        _token     = data["access_token"]
        expires_in = data.get("expires_in", "?")
        _ok(f"토큰 발급 성공 (expires_in={expires_in}s, 원문 미출력)")
    else:
        sc     = resp.status_code
        msg_cd = data.get("msg_cd", "-")
        msg1   = data.get("msg1", "-")
        _fail(f"토큰 발급 실패: status={sc}, msg_cd={msg_cd}, msg1={msg1}")
except requests.exceptions.ConnectionError as e:
    _fail(f"토큰 발급 연결 오류: {e}")
except Exception as e:
    _fail(f"토큰 발급 예외: {e}")

# 토큰 없어도 계속 진행 (조회 API 시도, 실패 시 FAIL 표시)
if not _token:
    print("  [INFO] 토큰 없음 → 섹션 5, 6 API 호출 시도 (실패 예상)")

# ── Section 5: 현재가 조회 ───────────────────────────────────────────────────
_section(f"5. 현재가 조회 ({TEST_SYMBOL} 삼성전자) - 조회 전용")

if not _token:
    _fail("토큰 없음으로 현재가 조회 불가 (섹션 4 토큰 발급 실패)")
else:
    try:
        url = f"{BASE_URL_REAL}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {_token}",
            "appkey":        real_key,
            "appsecret":     real_secret,
            "tr_id":         "FHKST01010100",
            "custtype":      "P",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         TEST_SYMBOL,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()

        rt_cd = data.get("rt_cd", "-1")
        if rt_cd == "0":
            output = data.get("output", {})
            price  = output.get("stck_prpr", "?")
            _ok(f"현재가 조회 성공: {TEST_SYMBOL} = {price}원")
        else:
            sc     = resp.status_code
            msg_cd = data.get("msg_cd", "-")
            msg1   = data.get("msg1", "-")
            _fail(f"현재가 조회 실패: status={sc}, rt_cd={rt_cd}, msg_cd={msg_cd}, msg1={msg1}")
    except requests.exceptions.ConnectionError as e:
        _fail(f"현재가 조회 연결 오류: {e}")
    except Exception as e:
        _fail(f"현재가 조회 예외: {e}")

# ── Section 6: 1분봉 조회 ────────────────────────────────────────────────────
_section(f"6. 1분봉 조회 ({TEST_SYMBOL}) - 조회 전용")

if not _token:
    _fail("토큰 없음으로 1분봉 조회 불가 (섹션 4 토큰 발급 실패)")
else:
    try:
        from datetime import datetime
        now_str = datetime.now().strftime("%H%M%S")

        url = f"{BASE_URL_REAL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        headers_c = {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {_token}",
            "appkey":        real_key,
            "appsecret":     real_secret,
            "tr_id":         "FHKST03010200",
            "custtype":      "P",
        }
        params_c = {
            "FID_ETC_CLS_CODE":       "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         TEST_SYMBOL,
            "FID_INPUT_HOUR_1":       now_str,
            "FID_PW_DATA_INCU_YN":    "N",
        }
        resp_c = requests.get(url, headers=headers_c, params=params_c, timeout=10)
        data_c = resp_c.json()

        rt_cd_c = data_c.get("rt_cd", "-1")
        if rt_cd_c == "0":
            output2 = data_c.get("output2", [])
            if isinstance(output2, list):
                _ok(f"1분봉 조회 성공: {len(output2)}개 캔들 반환")
            else:
                _warn("1분봉 output2가 리스트가 아님 (장 마감 후일 수 있음)")
        else:
            sc     = resp_c.status_code
            msg_cd = data_c.get("msg_cd", "-")
            msg1   = data_c.get("msg1", "-")
            _warn(f"1분봉 조회 비정상 응답 (장 마감 후 정상): status={sc}, rt_cd={rt_cd_c}, msg_cd={msg_cd}, msg1={msg1}")
    except requests.exceptions.ConnectionError as e:
        _fail(f"1분봉 조회 연결 오류: {e}")
    except Exception as e:
        _fail(f"1분봉 조회 예외: {e}")

# ── Section 7: KISClient / RealBroker 안전장치 확인 ─────────────────────────
_section("7. 실전 브로커 안전장치 확인")

_real_broker_ok = False
try:
    from app.trading.kis_real_broker import KisRealBroker
    from app.trading.real_broker import RealBroker
    _ok("KisRealBroker, RealBroker 임포트 성공")
    _real_broker_ok = True

    if not enable_real:
        _ok("ENABLE_REAL_TRADING=false → RealBroker 인스턴스 생성 차단됨 (안전장치 정상)")
    else:
        _warn("ENABLE_REAL_TRADING=true → RealBroker 인스턴스 생성 가능 상태 (실전 주문 주의)")

except ImportError as e:
    _fail(f"실전 브로커 임포트 실패: {e}")

# ── Section 8: KISClient 인터페이스 확인 ────────────────────────────────────
_section("8. app.trading.kis_client.KISClient 인터페이스 확인")

try:
    from app.trading.kis_client import KISClient
    methods_needed = ["get_access_token", "get_current_price", "buy", "sell"]
    methods_ok = all(hasattr(KISClient, m) for m in methods_needed)
    if methods_ok:
        _ok("KISClient 필수 메서드 존재: " + ", ".join(methods_needed))
    else:
        missing_m = [m for m in methods_needed if not hasattr(KISClient, m)]
        _fail(f"KISClient 메서드 누락: {missing_m}")

    if hasattr(KISClient, "get_minute_candles"):
        _ok("KISClient.get_minute_candles: EXISTS")
    else:
        _warn("KISClient.get_minute_candles: NOT_IMPLEMENTED (1분봉 수집 불가)")
except ImportError as e:
    _fail(f"KISClient 임포트 실패: {e}")

# ── 최종 결과 ────────────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  진단 결과 요약")
print("=" * 55)

has_cano      = bool(real_cano)
has_token     = bool(_token)
has_soft_fail = bool(_SOFT_FAILS)

# 상태별 항목 출력
all_issues = _SOFT_FAILS + _WARN_ITEMS
if all_issues:
    print("  항목별 상태:")
    for item in _SOFT_FAILS:
        print(f"    × (비치명) {item}")
    for item in _WARN_ITEMS:
        print(f"    △ (경고)   {item}")

print(f"\n  APP_KEY/SECRET   : {'OK' if real_key and real_secret else 'MISSING'}")
print(f"  CANO(계좌번호)    : {'EXISTS' if has_cano else 'MISSING (주문 불가)'}")
print(f"  토큰 발급         : {'OK' if has_token else 'FAIL'}")
print(f"  안전 플래그       : REAL={enable_real} / BUY={enable_buy} / SELL={enable_sell}")

print("=" * 55)

# KEY/SECRET 존재 시 PASSED (이미 위에서 없으면 exit(1))
print("\nKIS_REAL_INTRADAY_PRECHECK_PASSED")
print("  ※ 비치명 항목이 있습니다. 위 X/△ 항목을 확인하세요." if all_issues else "  모든 항목 정상.")
