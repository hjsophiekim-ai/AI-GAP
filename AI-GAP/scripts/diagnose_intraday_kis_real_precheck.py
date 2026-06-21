#!/usr/bin/env python
"""
diagnose_intraday_kis_real_precheck.py

KIS 실계좌 장중 자동매매 사전진단 스크립트.
실제 주문 API는 절대 호출하지 않습니다.

8개 항목이 모두 통과될 때만 KIS_REAL_INTRADAY_PRECHECK_PASSED 출력.
하나라도 실패하면 KIS_REAL_INTRADAY_PRECHECK_NOT_READY 출력.

점검 항목:
  [1] APP_KEY / APP_SECRET 존재
  [2] CANO (계좌번호) 존재
  [3] 토큰 발급 성공
  [4] 현재가 조회 성공
  [5] 1분봉 조회 성공
  [6] ENABLE_REAL_TRADING=true
  [7] ENABLE_REAL_BUY=true
  [8] ENABLE_REAL_SELL=true
"""
import os
import sys
import requests
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

TEST_SYMBOL = "005930"  # 삼성전자 (진단용)

# ── 체크리스트 ───────────────────────────────────────────────────────────────
# 각 항목: [번호, 이름, 통과여부(None=미실행), 상세]
_checks = [
    [1, "APP_KEY / APP_SECRET 존재",  None, ""],
    [2, "CANO (계좌번호) 존재",        None, ""],
    [3, "토큰 발급 성공",               None, ""],
    [4, "현재가 조회 성공",             None, ""],
    [5, "1분봉 조회 성공",             None, ""],
    [6, "ENABLE_REAL_TRADING=true",   None, ""],
    [7, "ENABLE_REAL_BUY=true",       None, ""],
    [8, "ENABLE_REAL_SELL=true",      None, ""],
]


def _pass(idx: int, detail: str = "") -> None:
    _checks[idx - 1][2] = True
    _checks[idx - 1][3] = detail
    print(f"  [OK]   {detail}")


def _fail(idx: int, detail: str) -> None:
    _checks[idx - 1][2] = False
    _checks[idx - 1][3] = detail
    print(f"  [FAIL] {detail}")


def _skip(idx: int, reason: str) -> None:
    _checks[idx - 1][2] = False
    _checks[idx - 1][3] = f"(SKIP) {reason}"
    print(f"  [SKIP] {reason}")


def _section(title: str) -> None:
    print(f"\n{'─' * 58}")
    print(f"  {title}")
    print(f"{'─' * 58}")


print("\n" + "=" * 58)
print("  AI-GAP KIS 실계좌 장중 자동매매 사전진단")
print("=" * 58)
print("  [주의] 실제 주문 API는 절대 호출하지 않습니다.")
print("  8개 항목 전부 통과해야 PASSED 판정됩니다.")

# ── Check 1: APP_KEY / APP_SECRET ────────────────────────────────────────────
_section("[1] APP_KEY / APP_SECRET 존재 (원문 미출력)")

real_key    = os.getenv("KIS_REAL_APP_KEY", "")
real_secret = os.getenv("KIS_REAL_APP_SECRET", "")

if real_key and real_secret:
    _pass(1, "KIS_REAL_APP_KEY: EXISTS / KIS_REAL_APP_SECRET: EXISTS")
else:
    missing = []
    if not real_key:
        missing.append("KIS_REAL_APP_KEY")
    if not real_secret:
        missing.append("KIS_REAL_APP_SECRET")
    _fail(1, f"MISSING: {', '.join(missing)}")

# KEY/SECRET 없으면 이후 API 불가 → 조기 종료
if not (real_key and real_secret):
    print("\n" + "=" * 58)
    print("  APP_KEY / APP_SECRET 없이 나머지 항목 확인 불가.")
    print("  .env 파일에 KIS_REAL_APP_KEY, KIS_REAL_APP_SECRET 설정 후 재실행")
    print("=" * 58)
    print("\nKIS_REAL_INTRADAY_PRECHECK_NOT_READY")
    print("  미통과 항목: [1] APP_KEY / APP_SECRET 존재")
    sys.exit(1)

# ── Check 2: CANO ────────────────────────────────────────────────────────────
_section("[2] CANO (계좌번호) 존재 (원문 미출력)")

real_cano = os.getenv("KIS_REAL_CANO", "") or os.getenv("KIS_REAL_ACCOUNT_NO", "")
real_prdt = os.getenv("KIS_REAL_ACNT_PRDT_CD", "") or os.getenv("KIS_REAL_ACCOUNT_PRODUCT_CODE", "01")

if real_cano:
    _pass(2, f"KIS_REAL_CANO: EXISTS (상품코드: {real_prdt or '01'})")
else:
    _fail(2, "KIS_REAL_CANO (또는 KIS_REAL_ACCOUNT_NO): MISSING")
    print("        → .env에 KIS_REAL_CANO=계좌번호8자리 설정 필요")

# ── base_url 설정 (진단용, 체크 항목 아님) ──────────────────────────────────
_section("[INFO] KIS_REAL_BASE_URL")

BASE_URL_REAL_DEFAULT = "https://openapi.koreainvestment.com:9443"
BASE_URL_REAL = BASE_URL_REAL_DEFAULT
try:
    from app.config import get_config
    cfg = get_config()
    cfg_url = cfg._raw.get("kis", {}).get("real", {}).get("base_url", "")
    if cfg_url:
        BASE_URL_REAL = cfg_url
        print(f"  config.yaml base_url: {cfg_url}")
    else:
        base_url_env = os.getenv("KIS_REAL_BASE_URL", "")
        BASE_URL_REAL = base_url_env or BASE_URL_REAL_DEFAULT
        print(f"  base_url: {BASE_URL_REAL}")
except Exception:
    base_url_env = os.getenv("KIS_REAL_BASE_URL", "")
    BASE_URL_REAL = base_url_env or BASE_URL_REAL_DEFAULT
    print(f"  base_url (config 없음): {BASE_URL_REAL}")

# ── Check 3: 토큰 발급 ────────────────────────────────────────────────────────
_section("[3] 토큰 발급 성공 (주문 API 미사용)")

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
        _pass(3, f"토큰 발급 성공 (expires_in={expires_in}s, 원문 미출력)")
    else:
        sc     = resp.status_code
        msg_cd = data.get("msg_cd", "-")
        msg1   = data.get("msg1", "-")
        _fail(3, f"토큰 발급 실패: HTTP {sc}, msg_cd={msg_cd}, msg1={msg1}")
except requests.exceptions.ConnectionError as e:
    _fail(3, f"토큰 발급 연결 오류: {e}")
except Exception as e:
    _fail(3, f"토큰 발급 예외: {e}")

# ── Check 4: 현재가 조회 ──────────────────────────────────────────────────────
_section(f"[4] 현재가 조회 ({TEST_SYMBOL} 삼성전자)")

if not _token:
    _skip(4, "토큰 없음 (Check 3 실패)")
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
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": TEST_SYMBOL}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()

        rt_cd = data.get("rt_cd", "-1")
        if rt_cd == "0":
            price = data.get("output", {}).get("stck_prpr", "?")
            _pass(4, f"현재가 조회 성공: {TEST_SYMBOL} = {price}원")
        else:
            sc     = resp.status_code
            msg_cd = data.get("msg_cd", "-")
            msg1   = data.get("msg1", "-")
            _fail(4, f"현재가 조회 실패: HTTP {sc}, rt_cd={rt_cd}, msg_cd={msg_cd}, msg1={msg1}")
    except requests.exceptions.ConnectionError as e:
        _fail(4, f"현재가 조회 연결 오류: {e}")
    except Exception as e:
        _fail(4, f"현재가 조회 예외: {e}")

# ── Check 5: 1분봉 조회 ───────────────────────────────────────────────────────
_section(f"[5] 1분봉 조회 ({TEST_SYMBOL}) - 장 중에만 데이터 반환")

if not _token:
    _skip(5, "토큰 없음 (Check 3 실패)")
else:
    try:
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
            cnt = len(output2) if isinstance(output2, list) else 0
            if cnt > 0:
                _pass(5, f"1분봉 조회 성공: {cnt}개 캔들 반환")
            else:
                _fail(5, "1분봉 rt_cd=0 이지만 캔들 0개 (장 마감 또는 데이터 없음)")
                print("        → 장 중(09:00-15:30)에 재실행 필요")
        else:
            sc     = resp_c.status_code
            msg_cd = data_c.get("msg_cd", "-")
            msg1   = data_c.get("msg1", "-")
            _fail(5, f"1분봉 조회 실패: HTTP {sc}, rt_cd={rt_cd_c}, msg_cd={msg_cd}, msg1={msg1}")
            print("        → 장 중(09:00-15:30)에 재실행 필요")
    except requests.exceptions.ConnectionError as e:
        _fail(5, f"1분봉 조회 연결 오류: {e}")
    except Exception as e:
        _fail(5, f"1분봉 조회 예외: {e}")

# ── Check 6/7/8: 안전 플래그 ─────────────────────────────────────────────────
_section("[6][7][8] 실전매매 안전 플래그")


def _bool_env(env_name: str, cfg_path: tuple, default: bool = False):
    val = os.getenv(env_name, "")
    if val.lower() in ("1", "true", "yes"):
        return True, f"env:{env_name}=true"
    if val.lower() in ("0", "false", "no"):
        return False, f"env:{env_name}=false"
    try:
        node = cfg._raw
        for k in cfg_path:
            node = node.get(k, {})
        resolved = bool(node) if not isinstance(node, bool) else node
        return resolved, f"config:{'.'.join(cfg_path)}={node}"
    except Exception:
        return default, f"default={default}"


try:
    enable_real, src_real = _bool_env("ENABLE_REAL_TRADING", ("safety", "enable_real_trading"))
    enable_buy,  src_buy  = _bool_env("ENABLE_REAL_BUY",     ("safety", "enable_real_buy"))
    enable_sell, src_sell = _bool_env("ENABLE_REAL_SELL",    ("safety", "enable_real_sell"))
except Exception:
    enable_real = enable_buy = enable_sell = False
    src_real = src_buy = src_sell = "default=false"

if enable_real:
    _pass(6, f"ENABLE_REAL_TRADING=true ({src_real})")
else:
    _fail(6, f"ENABLE_REAL_TRADING=false ({src_real})")
    print("        → .env에 ENABLE_REAL_TRADING=true 설정 또는 config.yaml safety.enable_real_trading: true")

if enable_buy:
    _pass(7, f"ENABLE_REAL_BUY=true ({src_buy})")
else:
    _fail(7, f"ENABLE_REAL_BUY=false ({src_buy})")
    print("        → .env에 ENABLE_REAL_BUY=true 설정 필요")

if enable_sell:
    _pass(8, f"ENABLE_REAL_SELL=true ({src_sell})")
else:
    _fail(8, f"ENABLE_REAL_SELL=false ({src_sell})")
    print("        → .env에 ENABLE_REAL_SELL=true 설정 필요")

# ── 보조 정보: 브로커 / KISClient 임포트 확인 (체크 항목 아님) ───────────────
_section("[INFO] 브로커 / KISClient 임포트 확인")

try:
    from app.trading.kis_real_broker import KisRealBroker
    from app.trading.real_broker import RealBroker
    print("  [OK]   KisRealBroker, RealBroker 임포트 성공")
    if not enable_real:
        print("  [OK]   ENABLE_REAL_TRADING=false → RealBroker 생성 차단 (안전장치 정상)")
    else:
        print("  [WARN] ENABLE_REAL_TRADING=true → RealBroker 생성 가능 상태 (주문 주의)")
except ImportError as e:
    print(f"  [WARN] 실전 브로커 임포트 실패 (자동매매 불가): {e}")

try:
    from app.trading.kis_client import KISClient
    has_mc = hasattr(KISClient, "get_minute_candles")
    print(f"  [OK]   KISClient.get_minute_candles: {'EXISTS' if has_mc else 'MISSING'}")
except ImportError as e:
    print(f"  [WARN] KISClient 임포트 실패: {e}")

# ── 최종 판정 ────────────────────────────────────────────────────────────────
print("\n" + "=" * 58)
print("  체크리스트 결과")
print("=" * 58)

all_passed = True
failed_items = []

for num, name, passed, detail in _checks:
    if passed is True:
        mark = " OK "
    elif passed is False:
        mark = "FAIL"
        all_passed = False
        failed_items.append((num, name, detail))
    else:
        mark = "SKIP"
        all_passed = False
        failed_items.append((num, name, detail))
    print(f"  [{mark}] [{num}] {name}")

print("=" * 58)

if all_passed:
    print("\nKIS_REAL_INTRADAY_PRECHECK_PASSED")
    print("  실계좌 장중 자동매매 준비 완료.")
else:
    print("\nKIS_REAL_INTRADAY_PRECHECK_NOT_READY")
    print(f"  미통과 항목 ({len(failed_items)}개):")
    for num, name, detail in failed_items:
        print(f"    [{num}] {name}")
        if detail and not detail.startswith("(SKIP)"):
            short = detail[:80] + "..." if len(detail) > 80 else detail
            print(f"         {short}")
    sys.exit(1)
