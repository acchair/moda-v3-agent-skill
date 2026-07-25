"""
AKShare 东方财富反限流模块
==========================
劫持 requests.Session.send() 注入:
  - NID 令牌 (东方财富会话cookie)
  - 随机 User-Agent (浏览器指纹池)
  - 真实浏览器请求头 (Sec-*, Accept-*, Referer)

使用方式 (在 import akshare 之前导入):
    from tools.akshare.anti_rate_limit import apply_patch
    apply_patch()  # 全局生效，所有后续 requests 调用自动注入

环境变量:
    EASTMONEY_NID=xxxx  设置东方财富NID令牌
    EASTMONEY_COOKIE=xxxx  设置完整cookie字符串
"""

import os
import random
import time
from functools import wraps

# ══════════════════════════════════════════════════════
#  User-Agent 池 (主流浏览器最新版)
# ══════════════════════════════════════════════════════
_USER_AGENTS = [
    # Chrome 131 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome 131 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    # Chrome 126 (slightly older)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
]

# ══════════════════════════════════════════════════════
#  东方财富 NID Cookie 管理
# ══════════════════════════════════════════════════════

def _build_eastmoney_cookie() -> str:
    """构建东方财富cookie字符串"""
    cookies = []

    # 从环境变量读取完整cookie或NID
    env_cookie = os.environ.get("EASTMONEY_COOKIE", "")
    env_nid = os.environ.get("EASTMONEY_NID", "")

    if env_cookie:
        return env_cookie

    if env_nid:
        cookies.append(f"nid18={env_nid}")
    else:
        # 生成随机访客ID (qgqp_b_id格式: 32位hex)
        qgqp = ''.join(random.choices('0123456789abcdef', k=32))
        cookies.append(f"qgqp_b_id={qgqp}")

        # 无NID时用通用访客cookie
        cookies.append("st_nvi=ulN5JAj9FUocz3p4klMME9f20")
        cookies.append(f"st_si={random.randint(10000000000000, 99999999999999)}")
        cookies.append(f"st_pvi={random.randint(10000000000000, 99999999999999)}")
        cookies.append("st_sp=" + time.strftime("%Y-%m-%d%%20%H%%3A%M%%3A%S"))

    return "; ".join(cookies)


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


# ══════════════════════════════════════════════════════
#  Monkey-patch requests.Session.send()
# ══════════════════════════════════════════════════════
_original_session_send = None
_patch_applied = False


def _patched_send(self, request, **kwargs):
    """注入浏览器头: API端点轻量化, 网页端点完整指纹"""
    host = request.url.split("/")[2] if "//" in request.url else ""

    # ══ API端点: 仅随机UA + 轻量头, 不注入Sec-/Cookie — 防反爬触发RST ══
    is_api = any(kw in host for kw in [
        "push2his", "push2", "datacenter-web", "dcfm.eastmoney",
        "emdata", "api", "mdata", "hq.sinajs", "ifzq.gtimg"
    ])
    if is_api:
        if "User-Agent" not in request.headers or \
           request.headers.get("User-Agent", "").startswith("python-requests"):
            request.headers["User-Agent"] = _random_ua()
        request.headers["Accept-Encoding"] = "gzip, deflate"
        request.headers["Connection"] = "keep-alive"
        return _original_session_send(self, request, **kwargs)

    # ══ 网页端点: 完整浏览器指纹 + Cookie + Referer ══
    if "User-Agent" not in request.headers or \
       request.headers.get("User-Agent", "").startswith("python-requests"):
        request.headers["User-Agent"] = _random_ua()

    if "Accept" not in request.headers:
        request.headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        )
    if "Accept-Language" not in request.headers:
        request.headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"
    if "Accept-Encoding" not in request.headers:
        request.headers["Accept-Encoding"] = "gzip, deflate, br"

    if "Sec-Ch-Ua" not in request.headers:
        request.headers["Sec-Ch-Ua"] = (
            '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
        )
    if "Sec-Ch-Ua-Mobile" not in request.headers:
        request.headers["Sec-Ch-Ua-Mobile"] = "?0"
    if "Sec-Ch-Ua-Platform" not in request.headers:
        request.headers["Sec-Ch-Ua-Platform"] = '"Windows"'
    if "Sec-Fetch-Dest" not in request.headers:
        request.headers["Sec-Fetch-Dest"] = "document"
    if "Sec-Fetch-Mode" not in request.headers:
        request.headers["Sec-Fetch-Mode"] = "navigate"
    if "Sec-Fetch-Site" not in request.headers:
        request.headers["Sec-Fetch-Site"] = "none"

    if "Cache-Control" not in request.headers:
        request.headers["Cache-Control"] = "max-age=0"
    request.headers["Connection"] = "keep-alive"

    if any(kw in host for kw in ["eastmoney.com", "eastmoney", "em.com"]):
        existing_cookie = request.headers.get("Cookie", "")
        em_cookie = _build_eastmoney_cookie()
        if existing_cookie:
            request.headers["Cookie"] = existing_cookie + "; " + em_cookie
        else:
            request.headers["Cookie"] = em_cookie
        if "Referer" not in request.headers:
            request.headers["Referer"] = "https://www.eastmoney.com/"

    return _original_session_send(self, request, **kwargs)


def apply_patch():
    """
    全局启用反限流补丁。
    在 import akshare 之前调用效果最好（覆盖所有后续requests）。
    """
    global _patch_applied, _original_session_send
    if _patch_applied:
        return

    import requests

    # 1. 劫持 Session.send
    _original_session_send = requests.Session.send
    requests.Session.send = _patched_send

    # 2. 更新 akshare 集中式 headers
    try:
        import akshare.utils.cons as cons
        cons.headers["User-Agent"] = _random_ua()
        cons.headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        )
        cons.headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"
    except ImportError:
        pass

    _patch_applied = True
    print(f"  [anti-rate-limit] patched (UA pool {len(_USER_AGENTS)})")


def remove_patch():
    """Remove patch (for testing)"""
    global _patch_applied, _original_session_send
    if not _patch_applied:
        return
    import requests
    requests.Session.send = _original_session_send
    _patch_applied = False
    print("  [anti-rate-limit] removed")


# ══════════════════════════════════════════════════════
#  便捷函数: 带重试的请求
# ══════════════════════════════════════════════════════

def fetch_with_retry(url: str, params: dict = None, max_retries: int = 5,
                     base_delay: float = 1.0) -> "requests.Response":
    """带指数退避的GET请求 (增强版: 5次重试 + 随机抖动)"""
    import requests
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
            if resp.status_code >= 500:
                delay = base_delay * (1.5 ** attempt)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 2)
                time.sleep(delay)
                continue
    raise last_error


if __name__ == "__main__":
    print("Testing anti-rate-limit...")
    apply_patch()
    import requests
    resp = requests.get("https://httpbin.org/headers", timeout=10)
    print(resp.json()["headers"])
    remove_patch()

