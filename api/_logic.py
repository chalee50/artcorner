"""_logic.py - ตัวรับคำขอจากหน้าเว็บ (POST /api/app) แล้วแยกตาม action

action ที่รองรับ: ping, register, login, state, passwd, adminUser, sync
ใช้ร่วมกันทั้งตอนรันบน Vercel (api/app.py) และรันในเครื่อง (server.py)
ผู้ใช้จะไม่เห็น Traceback เลย ทุกข้อผิดพลาดถูกแปลงเป็นข้อความภาษาไทย
"""
import json
import os
import sys
import traceback

from _auth import check_password, hash_password, make_user, new_token, own_view, verify_token
from _rules import COLS, authorize, view
from _storage import StorageError
from _validators import (ValidationError, fail, to_str, valid_email, valid_name,
                         valid_password, valid_phone, valid_role)

MAX_BODY = 6_000_000  # ไบต์


def ok(extra=None):
    """ผลลัพธ์สำเร็จ (status, json)"""
    body = {"ok": True}
    body.update(extra or {})
    return 200, body


def err(status, message):
    """ผลลัพธ์ล้มเหลว (status, json)"""
    return status, {"ok": False, "err": message}


def email_taken(store, email):
    """อีเมลนี้มีคนใช้แล้วหรือยัง"""
    users = store.all(["users"])["users"].values()
    return any(u.get("email") == email for u in users)


def action_register(store, env, secret, body, me):
    name = to_str(body.get("name"))
    email = to_str(body.get("email")).lower()
    phone = to_str(body.get("phone"))
    password = str(body.get("password") or "")
    if not valid_name(name):
        fail("ชื่ออย่างน้อย 2 ตัวอักษร")
    if not valid_email(email):
        fail("รูปแบบอีเมลไม่ถูกต้อง")
    if not valid_phone(phone):
        fail("เบอร์โทรต้องเป็นตัวเลข 10 หลักขึ้นต้นด้วย 0")
    if not valid_password(password):
        fail("รหัสผ่านอย่างน้อย 6 ตัวอักษร")
    users = list(store.all(["users"])["users"].values())
    if any(u.get("email") == email for u in users):
        fail("อีเมลนี้ถูกใช้แล้ว")
    admin_email = to_str(env.get("ADMIN_EMAIL")).lower()
    if admin_email:
        is_admin = email == admin_email
    else:
        is_admin = not users  # ไม่ได้ตั้ง ADMIN_EMAIL: คนแรกที่สมัครเป็นแอดมิน
    user = make_user(name, email, phone, password, "admin" if is_admin else "customer")
    store.batch([{"c": "users", "id": user["id"], "v": user}])
    store.bump()
    return ok({"token": new_token(user, secret), "user": own_view(user)})


def action_login(store, env, secret, body, me):
    email = to_str(body.get("email")).lower()
    users = store.all(["users"])["users"].values()
    user = next((u for u in users if u.get("email") == email), None)
    if not check_password(user, str(body.get("password") or "")):
        return err(401, "อีเมลหรือรหัสผ่านไม่ถูกต้อง")
    return ok({"token": new_token(user, secret), "user": own_view(user)})


def action_state(store, env, secret, body, me):
    version = store.ver()
    since = body.get("since")
    if since is not None and str(since) == version:
        return ok({"same": True, "v": version})  # ไม่มีอะไรเปลี่ยน ไม่ต้องส่งข้อมูลซ้ำ
    all_data = store.all(COLS)
    return ok({"v": version, "auth": bool(me), "me": own_view(me) if me else None, "data": view(all_data, me)})


def action_passwd(store, env, secret, body, me):
    new_password = str(body.get("newPassword") or "")
    if not valid_password(new_password):
        fail("รหัสผ่านใหม่อย่างน้อย 6 ตัวอักษร")
    if not check_password(me, str(body.get("oldPassword") or "")):
        fail("รหัสผ่านเดิมไม่ถูกต้อง")
    updated = dict(me)
    updated["salt"] = os.urandom(16).hex()
    updated["hash"] = hash_password(new_password, updated["salt"])
    store.batch([{"c": "users", "id": me["id"], "v": updated}])
    store.bump()
    return ok()


def action_admin_user(store, env, secret, body, me):
    if me.get("role") != "admin":
        return err(403, "เฉพาะแอดมิน")
    name = to_str(body.get("name"))
    email = to_str(body.get("email")).lower()
    password = str(body.get("password") or "")
    role = body.get("role")
    if not valid_name(name) or not valid_email(email) or len(password) < 6 or not valid_role(role):
        fail("ข้อมูลไม่ถูกต้อง")
    if email_taken(store, email):
        fail("อีเมลนี้ถูกใช้แล้ว")
    user = make_user(name, email, "", password, role)
    store.batch([{"c": "users", "id": user["id"], "v": user}])
    store.bump()
    return ok({"user": own_view(user)})


def action_sync(store, env, secret, body, me):
    ops = body.get("ops")
    all_data = store.all(COLS)
    checked = authorize(me, all_data, ops)  # ตรวจสิทธิ์ทุกคำสั่งก่อนเขียนจริง
    writes = [{"c": op["c"], "id": op["id"], "v": checked[i]} for i, op in enumerate(ops)]
    log_ids = sorted(all_data.get("logs", {}).keys())
    if len(log_ids) > 520:  # เก็บ log ล่าสุดไว้ 500 รายการ
        for old_id in log_ids[: len(log_ids) - 500]:
            writes.append({"c": "logs", "id": old_id, "v": None})
    store.batch(writes)
    return ok({"v": store.bump()})


PUBLIC_ACTIONS = {"register": action_register, "login": action_login, "state": action_state}
PRIVATE_ACTIONS = {"passwd": action_passwd, "adminUser": action_admin_user, "sync": action_sync}


def handle(store, env, body):
    """รับ body (dict) คืน (status, json) ไม่มีทางโยน error ออกไปถึงผู้ใช้"""
    secret = env.get("AUTH_SECRET")
    action = body.get("a")
    try:
        payload = verify_token(body.get("token"), secret)
        me = store.get("users", payload.get("uid")) if payload else None
        if action in PUBLIC_ACTIONS:
            return PUBLIC_ACTIONS[action](store, env, secret, body, me)
        if not me:
            return err(401, "กรุณาเข้าสู่ระบบ")
        if action in PRIVATE_ACTIONS:
            return PRIVATE_ACTIONS[action](store, env, secret, body, me)
        return err(400, "คำสั่งไม่ถูกต้อง")
    except ValidationError as error:
        return err(400, str(error))
    except StorageError as error:
        print("storage error:", error, file=sys.stderr)
        return err(503, "เชื่อมต่อฐานข้อมูลไม่ได้ กรุณาลองใหม่อีกครั้ง")
    except (TypeError, AttributeError, KeyError, ValueError, IndexError):
        return err(400, "ข้อมูลไม่ถูกต้อง")
    except Exception:  # กันตกหล่นทุกกรณี: บันทึกลง log ของเซิร์ฟเวอร์ แต่ผู้ใช้เห็นแค่ข้อความสั้นๆ
        traceback.print_exc(file=sys.stderr)
        return err(500, "เซิร์ฟเวอร์ผิดพลาด")


def process_request(raw, env, store):
    """จุดเข้าเดียวของ API: raw = ไบต์ของ body, คืน (status, json)"""
    if len(raw) > MAX_BODY:
        return err(413, "ข้อมูลที่ส่งมามีขนาดใหญ่เกินไป")
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    ready = store is not None and bool(env.get("AUTH_SECRET"))
    if body.get("a") == "ping":  # ให้หน้าเว็บรู้ว่าเซิร์ฟเวอร์พร้อมหรือยัง (ไม่พร้อม = โหมดเดโม)
        return ok({"remote": ready})
    if not ready:
        return err(500, "ยังไม่ได้ตั้งค่า Upstash Redis หรือ AUTH_SECRET")
    return handle(store, env, body)
