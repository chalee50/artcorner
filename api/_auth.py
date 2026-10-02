"""_auth.py - รหัสผ่าน (scrypt + salt), โทเคนล็อกอิน (HMAC-SHA256) และข้อมูลผู้ใช้ที่ปลอดภัยต่อการส่งออก

ไม่เก็บรหัสผ่านจริง เก็บเฉพาะค่าแฮช และใช้ hmac.compare_digest เทียบแบบกัน timing attack
"""
import base64
import hashlib
import hmac
import json
import os
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1  # tuple unpacking
TOKEN_DAYS = 30
DIGITS36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def now_ms():
    """เวลาปัจจุบันเป็นมิลลิวินาที"""
    return int(time.time() * 1000)


def to_base36(number):
    """แปลงจำนวนเต็มเป็นเลขฐาน 36 (ใช้สร้างรหัสผู้ใช้ให้สั้น)"""
    if number == 0:
        return "0"
    out = ""
    while number > 0:
        number, rest = divmod(number, 36)
        out = DIGITS36[rest] + out
    return out


def hash_password(password, salt):
    """คืนค่าแฮช scrypt (hex) ของรหัสผ่านกับ salt"""
    return hashlib.scrypt(
        str(password).encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=32, maxmem=64 * 1024 * 1024,
    ).hex()


def check_password(user, password):
    """ตรวจรหัสผ่าน ถ้าไม่มีผู้ใช้ก็ยังคำนวณแฮชเพื่อให้เวลาใกล้เคียงกัน"""
    salt = user.get("salt", "") if user else "0" * 32
    digest = hash_password(password, salt)
    if not user:
        return False
    return hmac.compare_digest(digest, str(user.get("hash", "")))


def make_user(name, email, phone, password, role):
    """สร้างระเบียนผู้ใช้ใหม่ (พร้อม salt และแฮช)"""
    salt = os.urandom(16).hex()
    return {
        "id": "u" + to_base36(now_ms()) + os.urandom(3).hex(),
        "name": name, "email": email, "phone": phone, "role": role,
        "salt": salt, "hash": hash_password(password, salt),
    }


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body, secret):
    return _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def sign_token(payload, secret):
    """สร้างโทเคน = base64(payload).ลายเซ็น"""
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return body + "." + _mac(body, secret)


def verify_token(token, secret):
    """ตรวจโทเคน คืน payload ถ้าถูกต้องและยังไม่หมดอายุ ไม่งั้นคืน None"""
    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    try:
        if not hmac.compare_digest(_mac(parts[0], secret), parts[1]):
            return None
        payload = json.loads(_unb64(parts[0]).decode("utf-8"))
        if isinstance(payload, dict) and payload.get("exp", 0) > now_ms():
            return payload
    except (ValueError, TypeError, UnicodeError):
        return None
    return None


def new_token(user, secret):
    """โทเคนล็อกอินอายุ TOKEN_DAYS วัน"""
    return sign_token({"uid": user["id"], "exp": now_ms() + TOKEN_DAYS * 86400000}, secret)


def own_view(user):
    """ข้อมูลที่เจ้าของบัญชี (และแอดมิน) เห็นได้ ไม่มี salt/hash"""
    data = {
        "id": user.get("id"), "name": user.get("name"), "email": user.get("email"),
        "phone": user.get("phone") or "", "role": user.get("role"),
    }
    if user.get("artist"):
        data["artist"] = user["artist"]
    return data


def public_view(user):
    """ข้อมูลที่คนทั่วไปเห็นได้ (แค่ id, ชื่อ, ศิลปิน)"""
    data = {"id": user.get("id"), "name": user.get("name")}
    if user.get("artist"):
        data["artist"] = user["artist"]
    return data
