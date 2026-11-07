"""访问密码、设备会话、错误锁定、局域网限制。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time

from ..storage import Storage

SESSION_COOKIE = "ashare_sid"
SESSION_TTL = 7 * 24 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
MIN_PASSWORD_LEN = 6
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=_N, r=_R, p=_P, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_lan_ip(ip: str | None) -> bool:
    """只允许本机和局域网地址（含 IPv6 本地地址），拒绝公网来源。"""
    if not ip:
        return False
    if ip in ("testclient",):
        return True
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback or addr.is_link_local:
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        return addr.is_private and not addr.is_reserved
    return addr in ipaddress.ip_network("fc00::/7")


class Auth:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    @property
    def configured(self) -> bool:
        return bool(self.storage.get("auth_password_hash"))

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"密码至少 {MIN_PASSWORD_LEN} 位")
        self.storage.set("auth_password_hash", hash_password(password))
        self.storage.delete_session(None)

    def locked_for(self, ip: str) -> int:
        now = time.time()
        fails = self.storage.failed_logins_since(ip, now - LOCK_SECONDS)
        if len(fails) >= MAX_FAILS:
            return int(max(fails) + LOCK_SECONDS - now) + 1
        return 0

    def login(self, ip: str, password: str, agent: str = "") -> tuple[str | None, str]:
        wait = self.locked_for(ip)
        if wait:
            return None, f"密码错误次数过多，请 {wait // 60 + 1} 分钟后再试"
        if not verify_password(password, self.storage.get("auth_password_hash", "")):
            self.storage.record_failed_login(ip)
            left = MAX_FAILS - len(self.storage.failed_logins_since(ip, time.time() - LOCK_SECONDS))
            if left <= 0:
                return None, "密码错误次数过多，已锁定 15 分钟"
            return None, f"密码不对，还可以再试 {left} 次"
        self.storage.clear_failed_logins(ip)
        token = secrets.token_urlsafe(32)
        self.storage.add_session(token_hash(token), SESSION_TTL, ip, agent[:200])
        return token, ""

    def valid(self, token: str | None) -> bool:
        return bool(token) and self.storage.session_valid(token_hash(token))

    def logout(self, token: str | None) -> None:
        if token:
            self.storage.delete_session(token_hash(token))

    def logout_all(self) -> None:
        self.storage.delete_session(None)
