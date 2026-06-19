"""
diagnose_kis_token.py

KIS 토큰 발급 상태를 진단합니다.
실행: python scripts/diagnose_kis_token.py [mock|real]

출력 항목:
  - 환경변수 존재 여부 (값 자체는 출력하지 않음)
  - 파일 캐시 상태 (만료 시각)
  - tokenP 응답 (rt_cd, msg_cd, msg1 — 민감정보 제외)

API 키 / 시크릿 / 계좌번호는 절대 출력하지 않습니다.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# repo root를 sys.path에 추가
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _check_env(mode: str) -> dict:
    """환경변수 존재 여부 확인 (값은 출력하지 않음)."""
    if mode == "mock":
        keys = ["KIS_MOCK_APP_KEY", "KIS_MOCK_APP_SECRET", "KIS_MOCK_ACCOUNT_NO"]
    else:
        keys = ["KIS_REAL_APP_KEY", "KIS_REAL_APP_SECRET", "KIS_ACCOUNT_NO"]

    result = {}
    for k in keys:
        val = os.environ.get(k, "")
        result[k] = bool(val)
    return result


def _check_token_cache(mode: str) -> dict:
    """파일 캐시 상태 확인."""
    cache_path = _REPO_ROOT / "data" / "cache" / f"kis_token_{mode}.json"
    if not cache_path.exists():
        return {"exists": False, "path": str(cache_path)}
    try:
        with open(cache_path) as f:
            data = json.load(f)
        expires_at = data.get("expires_at", "")
        base_url = data.get("base_url", "")
        # 토큰 유효 여부
        valid = False
        if expires_at:
            from datetime import timedelta
            exp_dt = datetime.fromisoformat(expires_at)
            valid = datetime.now() < exp_dt - timedelta(minutes=5)
        return {
            "exists": True,
            "path": str(cache_path),
            "expires_at": expires_at,
            "valid": valid,
            "base_url": base_url,
            # access_token은 출력하지 않음
        }
    except Exception as e:
        return {"exists": True, "parse_error": str(e)}


def _test_token_api(mode: str) -> dict:
    """tokenP API 호출 테스트 (실제 호출)."""
    try:
        from app.trading.kis_client import KISClient, KISTokenError
        from app.config import get_kis_account_config
        account_cfg = get_kis_account_config(mode)
        client = KISClient.from_account_config(account_cfg)
        # 메모리/파일 캐시 무효화 (강제 API 호출)
        client._token = ""
        from datetime import datetime, timedelta
        client._token_expires_at = datetime.min
        # 캐시 파일 임시 이름 변경 없이 _load_token_cache를 우회하기 위해
        # 직접 API 호출
        import requests
        url = f"{client.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": client._app_key,
            "appsecret": client._app_secret,
        }
        resp = requests.post(url, json=body, timeout=10)
        http_status = resp.status_code
        try:
            resp_data = resp.json()
        except Exception:
            resp_data = {}
        rt_cd = resp_data.get("rt_cd", "")
        msg_cd = resp_data.get("msg_cd", "")
        msg1 = resp_data.get("msg1", resp_data.get("error_description", ""))
        token_present = bool(resp_data.get("access_token", ""))
        return {
            "http_status": http_status,
            "success": http_status == 200 and token_present,
            "rt_cd": rt_cd,
            "msg_cd": msg_cd,
            "msg1": msg1,
            "token_received": token_present,
            "base_url": client.base_url,
            # access_token 값 자체는 출력하지 않음
        }
    except Exception as e:
        return {"error": str(e)}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "mock"
    if mode not in ("mock", "real"):
        print(f"사용법: python scripts/diagnose_kis_token.py [mock|real]")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"KIS 토큰 진단: mode={mode}")
    print(f"실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 1. 환경변수
    print("▶ 환경변수 확인")
    env_check = _check_env(mode)
    all_env_ok = all(env_check.values())
    for k, v in env_check.items():
        status = "[OK] 존재" if v else "[NG] 없음"
        print(f"   {k}: {status}")
    print()

    # 2. 파일 캐시
    print("▶ 토큰 파일 캐시")
    cache_info = _check_token_cache(mode)
    if not cache_info.get("exists"):
        print(f"   캐시 없음: {cache_info.get('path', '')}")
    elif "parse_error" in cache_info:
        print(f"   캐시 파싱 오류: {cache_info['parse_error']}")
    else:
        valid_str = "유효" if cache_info.get("valid") else "만료됨"
        print(f"   캐시 존재: {valid_str}")
        print(f"   만료: {cache_info.get('expires_at', '')}")
        print(f"   base_url: {cache_info.get('base_url', '')}")
    print()

    # 3. API 호출 테스트
    if not all_env_ok:
        print("▶ tokenP API 테스트: 건너뜀 (환경변수 없음)")
    else:
        print("▶ tokenP API 테스트 (실제 호출)")
        api_result = _test_token_api(mode)
        if "error" in api_result:
            print(f"   오류: {api_result['error']}")
        else:
            success = api_result.get("success", False)
            print(f"   HTTP: {api_result.get('http_status')}")
            print(f"   결과: {'[OK] 성공' if success else '[NG] 실패'}")
            print(f"   base_url: {api_result.get('base_url', '')}")
            print(f"   rt_cd: {api_result.get('rt_cd', '')!r}")
            print(f"   msg_cd: {api_result.get('msg_cd', '')!r}")
            print(f"   msg1: {api_result.get('msg1', '')!r}")
            print(f"   토큰 수신: {api_result.get('token_received', False)}")
    print()

    print("진단 완료.")
    print("※ API 키/시크릿/계좌번호/토큰 값은 출력하지 않습니다.\n")


if __name__ == "__main__":
    # .env 로드
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env")
    except ImportError:
        pass
    main()
