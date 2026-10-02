"""_rules.py - กติกา "ใครเห็นอะไรได้ / ใครเขียนอะไรได้" ตรวจที่ฝั่งเซิร์ฟเวอร์ทุกครั้ง

* view()       คัดข้อมูลที่แต่ละบทบาทมีสิทธิ์เห็น (ลูกค้าไม่เห็นออเดอร์ของคนอื่น ฯลฯ)
* authorize()  ตรวจทุกคำสั่งเขียนก่อนบันทึก ถ้าไม่ผ่านจะโยน ValidationError พร้อมข้อความภาษาไทย
"""
import json

from _auth import own_view, public_view
from _validators import PHONE_RE, ROLES, fail, is_int, is_number

COLS = ("works", "artists", "users", "orders", "coms", "reviews",
        "apps", "likes", "follows", "logs", "settings")
DEFAULT_CATS = ["จิตรกรรม", "ภาพพิมพ์", "ดิจิทัลอาร์ต", "ภาพถ่าย", "ภาพวาดเส้น"]
SHIP = 60  # ค่าส่งคงที่ (บาท)


# ---------- ตัวช่วยเล็กๆ ----------
def dumps(value):
    """JSON แบบเรียงคีย์ ใช้เปรียบเทียบความเท่ากันของข้อมูล"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def same(a, b):
    return dumps(a) == dumps(b)


def diff_keys(a, b):
    """รายชื่อคีย์ที่ค่าต่างกันระหว่าง a กับ b"""
    a, b = a or {}, b or {}
    return [k for k in sorted(set(a) | set(b)) if not same(a.get(k), b.get(k))]


def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def is_manager(user):
    return bool(user) and user.get("role") in ("admin", "staff")


def values(all_data, col):
    return list(all_data.get(col, {}).values())


def number(value):
    """ดึงตัวเลขจากข้อมูล ถ้าไม่ใช่ตัวเลขให้แจ้งผู้ใช้"""
    if not is_number(value):
        fail("ข้อมูลราคาไม่ถูกต้อง")
    return value


def num_is(value, target):
    """value เป็นตัวเลขที่เท่ากับ target (กัน True == 1)"""
    return is_number(value) and value == target


def num_ge(value, target):
    return is_number(value) and value >= target


class Ctx:
    """ข้อมูลที่ส่งให้กติกาแต่ละข้อ: ใคร (me) ทำอะไร (old -> v) กับระเบียนไหน (id)"""

    def __init__(self, me, old, new, item_id, deleting, all_data, cur, batch):
        self.me, self.old, self.v, self.id = me, old, new, item_id
        self.dele, self.all, self.cur, self.batch = deleting, all_data, cur, batch
        self.adm, self.mgr = is_admin(me), is_manager(me)

    def my_artist(self):
        return self.me.get("artist")


# ---------- ใครเห็นอะไร ----------
def view(all_data, me):
    """สร้างชุดข้อมูลที่ผู้ใช้ (หรือผู้เยี่ยมชม me=None) มีสิทธิ์เห็น"""
    mgr, adm = is_manager(me), is_admin(me)
    my_id = me.get("id") if me else None
    my_artist = me.get("artist") if me else None

    users = []
    for u in values(all_data, "users"):
        if adm or (me and u.get("id") == my_id):
            users.append(own_view(u))
        elif mgr:
            row = public_view(u)
            row["email"], row["role"] = u.get("email"), u.get("role")
            users.append(row)
        else:
            users.append(public_view(u))

    works = [w for w in values(all_data, "works")
             if mgr or w.get("approval") == "approved" or (my_artist and w.get("artist") == my_artist)]
    orders = [o for o in values(all_data, "orders") if mgr or (me and o.get("uid") == my_id)]
    coms = [c for c in values(all_data, "coms")
            if mgr or (me and (c.get("uid") == my_id or (my_artist and c.get("artist") == my_artist)))]
    apps = [a for a in values(all_data, "apps") if mgr or (me and a.get("uid") == my_id)]
    logs = sorted(values(all_data, "logs"), key=lambda x: str(x.get("i", "")), reverse=True) if adm else []

    st = all_data.get("settings") or {}
    return {
        "works": works, "artists": values(all_data, "artists"), "users": users,
        "orders": orders, "coms": coms, "reviews": values(all_data, "reviews"),
        "apps": apps, "likes": values(all_data, "likes"), "follows": values(all_data, "follows"),
        "logs": logs,
        "settings": {
            "cats": st.get("cats") or DEFAULT_CATS,
            "wm": st.get("wm") or {"text": "ArtCorner", "op": 0.35},
            "qr": st.get("qr") or "",
        },
    }


# ---------- ใครเขียนอะไรได้ (กติกาแยกตามคอลเลกชัน) ----------
def rule_users(c):
    users = c.all.get("users", {})

    def admin_count():
        return sum(1 for u in users.values() if u.get("role") == "admin")

    if c.dele:
        if not c.adm:
            fail("เฉพาะแอดมินลบผู้ใช้")
        if c.id == c.me["id"]:
            fail("ลบบัญชีตัวเองไม่ได้")
        if c.old and c.old.get("role") == "admin" and admin_count() < 2:
            fail("ต้องมีแอดมินอย่างน้อย 1 คน")
        return None
    if not c.old:
        fail("สร้างผู้ใช้ผ่านการสมัครหรือเมนูเพิ่มผู้ใช้เท่านั้น")

    changed = [k for k in c.v if k not in ("salt", "hash") and not same(c.v.get(k), c.old.get(k))]
    if c.adm:
        allowed = ["name", "phone", "role", "artist"]
    elif c.id == c.me["id"]:
        allowed = ["name", "phone"]
    else:
        allowed = []
    if (c.mgr and "artist" not in allowed and "artist" in changed and not c.old.get("artist")
            and any(a.get("uid") == c.id and a.get("status") == "approved" for a in c.batch("apps"))
            and any(a.get("id") == c.v.get("artist") for a in c.batch("artists"))):
        allowed.append("artist")
    if not all(k in allowed for k in changed):
        fail("ไม่มีสิทธิ์แก้ไขผู้ใช้นี้")

    if "role" in changed:
        if c.v.get("role") not in ROLES:
            fail("บทบาทไม่ถูกต้อง")
        if c.old.get("role") == "admin" and c.v.get("role") != "admin" and admin_count() < 2:
            fail("ต้องมีแอดมินอย่างน้อย 1 คน")
    if "name" in changed:
        name = c.v.get("name")
        if not (isinstance(name, str) and len(name.strip()) >= 2 and len(name) <= 60):
            fail("ชื่อไม่ถูกต้อง")
    if "phone" in changed and c.v.get("phone"):
        phone = c.v.get("phone")
        if not (isinstance(phone, str) and PHONE_RE.match(phone)):
            fail("เบอร์โทรไม่ถูกต้อง")

    result = dict(c.old)
    for k in changed:
        result[k] = c.v[k]
    return result


def rule_artists(c):
    if c.dele:
        fail("ห้ามลบศิลปิน")
    if c.adm:
        return c.v
    if c.mgr:
        if not c.old and not any(a.get("status") == "approved" for a in c.batch("apps")):
            fail("สร้างศิลปินผ่านการอนุมัติใบสมัครเท่านั้น")
        return c.v
    if c.old and c.my_artist() == c.id and all(k in ("name", "school", "bio") for k in diff_keys(c.old, c.v)):
        return c.v
    fail("ไม่มีสิทธิ์แก้ไขโปรไฟล์ศิลปินนี้")


def rule_works(c):
    me_artist = c.my_artist()

    def mine(artist):
        return bool(me_artist) and artist == me_artist

    if c.dele:
        if not (c.mgr or (c.old and mine(c.old.get("artist")))):
            fail("ไม่มีสิทธิ์ลบผลงาน")
        for o in values(c.all, "orders"):
            if not o.get("cancelled") and (c.id in (o.get("items") or []) or c.id in (o.get("dl") or [])):
                fail("ลบไม่ได้: ผลงานอยู่ในคำสั่งซื้อ")
        return None
    if c.mgr:
        if not c.all.get("artists", {}).get(c.v.get("artist")) and not any(
                a.get("id") == c.v.get("artist") for a in c.batch("artists")):
            fail("ไม่พบศิลปิน")
        return c.v
    owner = c.old.get("artist") if c.old else c.v.get("artist")
    if mine(owner):
        if c.v.get("artist") != me_artist:
            fail("เปลี่ยนศิลปินไม่ได้")
        if not c.old:
            if c.v.get("approval") != "pending" or c.v.get("status") != "available":
                fail("ผลงานใหม่ต้องรอแอดมินอนุมัติ")
            return c.v
        if c.v.get("status") != c.old.get("status"):
            fail("เปลี่ยนสถานะการขายไม่ได้")
        if c.v.get("approval") != "pending":
            fail("ผลงานที่แก้ไขต้องรออนุมัติใหม่")
        return c.v
    # ผู้ซื้อ: ทำได้อย่างเดียวคือเปลี่ยนสถานะ available -> sold หลังสั่งซื้อ
    if (c.old and c.old.get("status") == "available" and c.v.get("status") == "sold"
            and all(k == "status" for k in diff_keys(c.old, c.v))
            and any(o.get("uid") == c.me["id"] and c.id in (o.get("items") or []) for o in c.batch("orders"))):
        return c.v
    fail("ไม่มีสิทธิ์แก้ไขผลงานนี้")


def rule_orders(c):
    if c.dele:
        fail("ห้ามลบคำสั่งซื้อ")
    me, old, v = c.me, c.old, c.v
    if old and v.get("uid") != old.get("uid"):
        fail("เปลี่ยนเจ้าของคำสั่งซื้อไม่ได้")
    if c.mgr:
        if not old and not c.all.get("users", {}).get(v.get("uid")):
            fail("ไม่พบผู้ซื้อ")
        return v

    if not old:  # สร้างคำสั่งซื้อใหม่: คำนวณยอดซ้ำฝั่งเซิร์ฟเวอร์ ไม่เชื่อยอดจากหน้าเว็บ
        if v.get("uid") != me["id"] or not num_is(v.get("status"), 0) or v.get("cancelled") or v.get("slip"):
            fail("คำสั่งซื้อไม่ถูกต้อง")
        items, downloads, total = v.get("items") or [], v.get("dl") or [], 0
        works = c.all.get("works", {})
        for item in items:
            w = works.get(item)
            if not w or w.get("approval") != "approved" or (me.get("artist") and w.get("artist") == me.get("artist")):
                fail("ซื้อผลงานของตัวเองหรือผลงานที่ไม่พร้อมขายไม่ได้")
            if w.get("status") != "available":
                fail("ผลงาน “%s” ขายไปแล้ว" % w.get("title"))
            total += number(w.get("price"))
        for item in downloads:
            w = works.get(item)
            if (not w or w.get("approval") != "approved" or not w.get("digital")
                    or (me.get("artist") and w.get("artist") == me.get("artist"))):
                fail("ไฟล์ดิจิทัลไม่พร้อมขาย")
            total += number(w["digital"].get("price"))
        if items:
            total += SHIP
        if v.get("com"):
            com, com_now = c.all.get("coms", {}).get(v["com"]), c.cur("coms", v["com"])
            if (not com or com.get("uid") != me["id"] or not num_is(com.get("status"), 1) or com.get("end")
                    or not com_now or not num_is(com_now.get("status"), 2) or items or downloads):
                fail("งานจ้างไม่ถูกต้อง")
            total = number(com["quote"].get("price"))
        elif not items and not downloads:
            fail("คำสั่งซื้อว่าง")
        if not num_is(v.get("total"), total):
            fail("ยอดรวมไม่ตรงกับราคาจริง")
        return v

    if old.get("uid") != me["id"]:
        fail("ไม่มีสิทธิ์")
    changed = diff_keys(old, v)
    if not changed:
        return v
    if all(k == "slip" for k in changed):
        if not num_is(old.get("status"), 0) or old.get("cancelled"):
            fail("แนบสลิปไม่ได้ในสถานะนี้")
        return v
    if all(k == "cancelled" for k in changed) and v.get("cancelled") and num_is(old.get("status"), 0) and old.get("com"):
        return v
    if all(k == "status" for k in changed) and num_is(v.get("status"), 3) and old.get("com") and num_ge(old.get("status"), 1):
        com = c.cur("coms", old["com"])
        if com and com.get("uid") == me["id"] and num_is(com.get("status"), 5):
            return v
    fail("ไม่มีสิทธิ์แก้ไขคำสั่งซื้อนี้")


def rule_coms(c):
    if c.dele:
        fail("ห้ามลบงานจ้าง")
    if c.mgr:
        return c.v
    me, old, v = c.me, c.old, c.v
    if not old:
        if (v.get("uid") != me["id"] or not num_is(v.get("status"), 0) or v.get("end")
                or not c.all.get("artists", {}).get(v.get("artist"))
                or (me.get("artist") and v.get("artist") == me.get("artist"))):
            fail("คำขอจ้างไม่ถูกต้อง")
        return v
    if old.get("end"):
        fail("งานนี้สิ้นสุดแล้ว")
    changed, st = diff_keys(old, v), old.get("status")

    def only(*keys):
        return all(k in keys for k in changed)

    if old.get("uid") == me["id"]:  # ฝั่งผู้จ้าง
        order = next((o for o in c.batch("orders") if o.get("com") == old.get("id") and o.get("uid") == me["id"]), None)
        if num_is(st, 0) and only("end") and v.get("end") == "cancelled":
            return v
        if num_is(st, 1) and only("status", "orderId") and num_is(v.get("status"), 2) and order and v.get("orderId") == order.get("id"):
            return v
        if (num_is(st, 1) or num_is(st, 2)) and only("end") and v.get("end") == "cancelled":
            return v
        if num_is(st, 4) and only("status") and num_is(v.get("status"), 5):
            return v
        rev = old.get("rev") or 0
        if num_is(st, 4) and only("status", "rev", "revNote") and num_is(v.get("status"), 3) and rev < 2 and v.get("rev") == rev + 1:
            return v
    if me.get("artist") and old.get("artist") == me.get("artist"):  # ฝั่งศิลปิน
        quote = v.get("quote")
        if (num_is(st, 0) and only("status", "quote") and num_is(v.get("status"), 1) and isinstance(quote, dict)
                and is_int(quote.get("price")) and 300 <= quote["price"] <= 200000):
            return v
        if num_is(st, 0) and only("end", "reason") and v.get("end") == "declined":
            return v
        if num_is(st, 3) and only("status", "delivery") and num_is(v.get("status"), 4) and v.get("delivery") and v["delivery"].get("img"):
            return v
    fail("ไม่มีสิทธิ์ดำเนินการนี้")


def rule_reviews(c):
    me, old, v = c.me, c.old, c.v
    if c.dele:
        if not (c.mgr or (old and old.get("u") == me["id"])
                or (old and old.get("kind") == "w" and c.cur("works", old.get("ref")) is None)):
            fail("ไม่มีสิทธิ์ลบรีวิว")
        return None
    if v.get("u") != me["id"] or (old and old.get("u") != me["id"]):
        fail("รีวิวไม่ถูกต้อง")
    text = v.get("text")
    if not is_int(v.get("rating")) or not 1 <= v["rating"] <= 5 or not isinstance(text, str) or not 10 <= len(text) <= 500:
        fail("ข้อมูลรีวิวไม่ถูกต้อง")
    ref = v.get("ref")
    if v.get("kind") == "w":
        w = c.all.get("works", {}).get(ref)
        paid = any(o.get("uid") == me["id"] and not o.get("cancelled") and num_ge(o.get("status"), 1)
                   and (ref in (o.get("items") or []) or ref in (o.get("dl") or []))
                   for o in values(c.all, "orders"))
        if not w or (me.get("artist") and w.get("artist") == me.get("artist")) or not paid:
            fail("รีวิวได้เฉพาะผู้ซื้อที่ชำระเงินแล้ว")
        artist = w.get("artist")
    elif v.get("kind") == "c":
        com = c.all.get("coms", {}).get(ref)
        if not com or com.get("uid") != me["id"] or not num_is(com.get("status"), 5) or com.get("end"):
            fail("รีวิวได้หลังงานจ้างสำเร็จ")
        artist = com.get("artist")
    else:
        fail("ประเภทรีวิวไม่ถูกต้อง")
    result = dict(v)
    result["artist"] = artist
    return result


def rule_apps(c):
    me, old, v = c.me, c.old, c.v
    if c.dele:
        if not c.adm:
            fail("ห้ามลบใบสมัคร")
        return None
    if c.mgr:
        if not old:
            fail("ไม่พบใบสมัคร")
        if v.get("status") not in ("approved", "rejected"):
            fail("สถานะไม่ถูกต้อง")
        result = dict(old)
        result["status"] = v["status"]
        if "note" in v:
            result["note"] = v["note"]
        return result
    if not old:
        pending = any(a.get("uid") == me["id"] and a.get("status") == "pending" for a in values(c.all, "apps"))
        if (v.get("uid") != me["id"] or v.get("status") != "pending" or me.get("role") != "customer"
                or me.get("artist") or pending):
            fail("ใบสมัครไม่ถูกต้อง")
        return v
    fail("ไม่มีสิทธิ์แก้ไขใบสมัคร")


def _second_part(item_id):
    """รหัสไลก์/ติดตามมีรูปแบบ 'userId|targetId' คืนส่วนหลัง"""
    parts = item_id.split("|")
    return parts[1] if len(parts) > 1 else None


def rule_likes(c):
    me, v = c.me, c.v
    work_id = _second_part(c.id)
    mine = c.id.startswith(me["id"] + "|")
    if c.dele:
        if not (mine or c.adm or c.cur("works", work_id) is None):
            fail("ไม่มีสิทธิ์")
        return None
    if not mine or v.get("u") != me["id"] or v.get("w") != work_id or not c.all.get("works", {}).get(work_id):
        fail("ข้อมูลไม่ถูกต้อง")
    return v


def rule_follows(c):
    me, v = c.me, c.v
    artist_id = _second_part(c.id)
    mine = c.id.startswith(me["id"] + "|")
    if c.dele:
        if not (mine or c.adm):
            fail("ไม่มีสิทธิ์")
        return None
    if (not mine or v.get("u") != me["id"] or v.get("a") != artist_id
            or not c.all.get("artists", {}).get(artist_id)
            or (me.get("artist") and artist_id == me.get("artist"))):
        fail("ข้อมูลไม่ถูกต้อง")
    return v


def rule_logs(c):
    if c.dele:
        fail("ห้ามลบ log")

    def clip(x):
        return ("" if x is None else str(x))[:300]

    v = c.v
    return {"i": c.id, "t": clip(v.get("t")), "u": c.me.get("email"), "r": c.me.get("role"),
            "act": clip(v.get("act")), "ent": clip(v.get("ent")), "id": clip(v.get("id")), "d": clip(v.get("d"))}


def rule_settings(c):
    v = c.v
    if not c.adm or c.dele:
        fail("เฉพาะแอดมินแก้ไขการตั้งค่า")
    if c.id == "cats":
        if (not isinstance(v, list) or len(v) > 50
                or any(not isinstance(x, str) or not 2 <= len(x) <= 30 for x in v)
                or len(set(v)) != len(v)):
            fail("หมวดหมู่ไม่ถูกต้อง")
    elif c.id == "wm":
        if (not isinstance(v, dict) or not isinstance(v.get("text"), str) or not 2 <= len(v["text"]) <= 30
                or not is_number(v.get("op")) or not 0.1 <= v["op"] <= 0.6):
            fail("ตั้งค่าลายน้ำไม่ถูกต้อง")
    elif c.id == "qr":
        if not isinstance(v, str) or len(v) > 600000:
            fail("รูป QR ไม่ถูกต้อง")
    else:
        fail("ไม่รู้จักค่าตั้งนี้")
    return v


RULES = {
    "users": rule_users, "artists": rule_artists, "works": rule_works, "orders": rule_orders,
    "coms": rule_coms, "reviews": rule_reviews, "apps": rule_apps, "likes": rule_likes,
    "follows": rule_follows, "logs": rule_logs, "settings": rule_settings,
}


def authorize(me, all_data, ops):
    """ตรวจรายการคำสั่งเขียนทั้งชุด คืนค่าที่ 'ผ่านการตรวจแล้ว' ของแต่ละคำสั่ง (None = ลบ)"""
    if not isinstance(ops, list) or not ops or len(ops) > 80:
        fail("คำขอไม่ถูกต้อง")
    pending = {}  # สิ่งที่กำลังจะเขียนในชุดนี้ (ใช้ตรวจความสัมพันธ์ข้ามระเบียน)
    for op in ops:
        if (not isinstance(op, dict) or not isinstance(op.get("c"), str) or op["c"] not in COLS
                or not isinstance(op.get("id"), str) or not op["id"] or len(op["id"]) > 80 or "v" not in op):
            fail("คำขอไม่ถูกต้อง")
        value = op["v"]
        if value is not None and len(dumps(value)) > 900000:
            fail("ข้อมูลใหญ่เกินไป")
        if value is not None and op["c"] != "settings" and not isinstance(value, dict):
            fail("ข้อมูลไม่ถูกต้อง")
        pending.setdefault(op["c"], {})[op["id"]] = value

    def cur(col, item_id):
        if col in pending and item_id in pending[col]:
            return pending[col][item_id]
        return all_data.get(col, {}).get(item_id)

    def batch(col):
        return [x for x in pending.get(col, {}).values() if x]

    results = []
    for op in ops:
        old = all_data.get(op["c"], {}).get(op["id"])
        deleting = op["v"] is None
        ctx = Ctx(me, old, op["v"], op["id"], deleting, all_data, cur, batch)
        answer = RULES[op["c"]](ctx)
        results.append(None if deleting else answer)
    return results
