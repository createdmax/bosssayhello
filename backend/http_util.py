"""访问 www.zhipin.com 的共用 HTTP 客户端与指纹加密。

供微信扫码登录（qr_login）使用：模拟官网浏览器请求头，
并对 loginConfirm 所需的 fp 做与官网一致的 AES-CBC 封装。
"""

from __future__ import annotations

import base64

import httpx
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad

BASE = "https://www.zhipin.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# 与官网前端 X.f 相同的 AES 密钥（Base64）
_AES_KEY_B64 = "clRwXUJBK1VKK0k0IWFbbQ=="


def aes_zp(plain: str) -> str:
    """AES-CBC + 随机 IV，返回 Base64(IV||ciphertext)。"""
    key = base64.b64decode(_AES_KEY_B64)
    iv = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plain.encode("utf-8"), AES.block_size))
    return base64.b64encode(iv + ct).decode("ascii")


def merge_cookies(*parts: dict[str, str] | None) -> dict[str, str]:
    """合并多段 cookie 字典，后者覆盖前者；忽略空值。"""
    out: dict[str, str] = {}
    for p in parts:
        if not p:
            continue
        for k, v in p.items():
            if v is not None and v != "":
                out[str(k)] = str(v)
    return out


def boss_client(cookies: dict[str, str] | None = None) -> httpx.AsyncClient:
    """构造指向 Boss 官网的 AsyncClient，可选预置 cookies。"""
    client = httpx.AsyncClient(
        base_url=BASE,
        timeout=30.0,
        headers={
            "User-Agent": UA,
            "Referer": "https://www.zhipin.com/web/user/",
            "Origin": "https://www.zhipin.com",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        },
        follow_redirects=True,
    )
    if cookies:
        for k, v in cookies.items():
            if v is not None and v != "":
                client.cookies.set(k, str(v))
    return client
