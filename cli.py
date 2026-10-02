"""cli.py - เมนูจัดการระบบผ่านหน้าจอ (ใช้กับข้อมูลในไฟล์ data/artcorner.json)

รัน:  python cli.py
เมนูวนซ้ำจนกว่าจะเลือก 0 เพื่อออก  ข้อมูลเก็บเป็นไฟล์ จึงอยู่ต่อแม้ปิดโปรแกรมแล้วเปิดใหม่
"""
import csv
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "data", "artcorner.json")
EXPORT_DIR = os.path.join(ROOT, "data", "export")
MENU = (
    ("1", "สรุปภาพรวมระบบ (dashboard)"),
    ("2", "รายชื่อผู้ใช้"),
    ("3", "เพิ่มผู้ใช้ใหม่"),
    ("4", "เปลี่ยนบทบาทผู้ใช้"),
    ("5", "ดู log ล่าสุด"),
    ("6", "ส่งออกคำสั่งซื้อเป็น CSV"),
    ("7", "สำรองไฟล์ข้อมูล"),
    ("0", "ออกจากโปรแกรม"),
)


def load_modules():
    api_dir = os.path.join(ROOT, "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    import _auth
    import _storage
    import _validators
    return _auth, _storage, _validators


def ask(prompt):
    """รับข้อความจากผู้ใช้ ถ้าปิดอินพุต (Ctrl+D/Ctrl+Z) ให้ถือว่าเลือกออก"""
    try:
        return input(prompt).strip()
    except EOFError:
        return "0"


def show_summary(store):
    """dashboard แบบข้อความ: นับผู้ใช้ตามบทบาท, ผลงาน, ยอดขายรวม"""
    data = store.all(["users", "works", "orders", "artists"])
    roles = {}  # dict: บทบาท -> จำนวน
    for user in data["users"].values():
        role = user.get("role", "?")
        roles[role] = roles.get(role, 0) + 1
    revenue = 0.0
    buyers = set()  # set: ผู้ซื้อที่ไม่ซ้ำกัน
    for order in data["orders"].values():
        if not order.get("cancelled") and order.get("status", 0) >= 1:
            revenue += float(order.get("total", 0))
            buyers.add(order.get("uid"))
    print("ผู้ใช้ทั้งหมด:", len(data["users"]), roles)
    print("ศิลปิน: %d   ผลงาน: %d   คำสั่งซื้อ: %d" % (len(data["artists"]), len(data["works"]), len(data["orders"])))
    print("ยอดขายที่ชำระแล้ว: %.2f บาท จากผู้ซื้อ %d คน" % (revenue, len(buyers)))


def show_users(store):
    users = sorted(store.all(["users"])["users"].values(), key=lambda u: u.get("email", ""))
    if not users:
        print("ยังไม่มีผู้ใช้")
    for number, user in enumerate(users, start=1):
        print("%2d. %-28s %-9s %s" % (number, user.get("email", ""), user.get("role", ""), user.get("name", "")))


def add_user(store, auth, valid):
    """เพิ่มผู้ใช้ โดยวนถามจนกรอกถูกหรือพิมพ์ว่างเพื่อยกเลิก"""
    fields = {}
    steps = (("name", "ชื่อ: ", valid.valid_name), ("email", "อีเมล: ", valid.valid_email),
             ("password", "รหัสผ่าน (6 ตัวขึ้นไป): ", valid.valid_password),
             ("role", "บทบาท (admin/staff/customer): ", valid.valid_role))
    for key, prompt, check in steps:
        while True:
            text = ask(prompt)
            if text == "":
                print("ยกเลิกการเพิ่มผู้ใช้")
                return
            if check(text):
                fields[key] = text.lower() if key == "email" else text
                break
            print("ข้อมูลไม่ถูกต้อง ลองใหม่อีกครั้ง (พิมพ์ว่างเพื่อยกเลิก)")
    taken = any(u.get("email") == fields["email"] for u in store.all(["users"])["users"].values())
    if taken:
        print("อีเมลนี้ถูกใช้แล้ว")
        return
    user = auth.make_user(fields["name"], fields["email"], "", fields["password"], fields["role"])
    store.batch([{"c": "users", "id": user["id"], "v": user}])
    store.bump()
    print("เพิ่มผู้ใช้แล้ว:", user["email"])


def change_role(store, valid):
    email = ask("อีเมลผู้ใช้: ").lower()
    users = store.all(["users"])["users"]
    target = next((u for u in users.values() if u.get("email") == email), None)
    if target is None:
        print("ไม่พบผู้ใช้นี้")
        return
    role = ask("บทบาทใหม่ (admin/staff/customer): ")
    if not valid.valid_role(role):
        print("บทบาทไม่ถูกต้อง")
        return
    admins = sum(1 for u in users.values() if u.get("role") == "admin")
    if target.get("role") == "admin" and role != "admin" and admins < 2:
        print("ต้องมีแอดมินอย่างน้อย 1 คน")
        return
    target["role"] = role
    store.batch([{"c": "users", "id": target["id"], "v": target}])
    store.bump()
    print("เปลี่ยนบทบาทเป็น", role, "แล้ว")


def show_logs(store, valid):
    count = valid.to_int(ask("จะดูกี่รายการ (ค่าเริ่มต้น 10): "), 10)
    if count is None or count < 1:
        count = 10
    logs = sorted(store.all(["logs"])["logs"].values(), key=lambda x: str(x.get("i", "")), reverse=True)
    for entry in logs[:count]:
        print(entry.get("t", ""), "|", entry.get("u", ""), "|", entry.get("act", ""), entry.get("ent", ""), entry.get("d", ""))
    if not logs:
        print("ยังไม่มี log")


def export_orders(store):
    """ส่งออกคำสั่งซื้อทั้งหมดเป็นไฟล์ CSV (เปิดใน Excel ได้ ภาษาไทยไม่เพี้ยนด้วย utf-8-sig)"""
    orders = store.all(["orders"])["orders"].values()
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, "orders_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "uid", "status", "cancelled", "total"])
        for order in orders:
            writer.writerow([order.get("id"), order.get("uid"), order.get("status"),
                             bool(order.get("cancelled")), order.get("total")])
    print("ส่งออกแล้ว:", path)


def backup_data():
    if not os.path.exists(DATA_PATH):
        print("ยังไม่มีไฟล์ข้อมูลให้สำรอง")
        return
    target = DATA_PATH + "." + time.strftime("%Y%m%d_%H%M%S") + ".bak"
    shutil.copyfile(DATA_PATH, target)
    print("สำรองไว้ที่:", target)


def run_choice(choice, store, auth, valid):
    """ทำตามเมนูที่เลือก (แยกฟังก์ชันเพื่อให้ main สั้นและอ่านง่าย)"""
    if choice == "1":
        show_summary(store)
    elif choice == "2":
        show_users(store)
    elif choice == "3":
        add_user(store, auth, valid)
    elif choice == "4":
        change_role(store, valid)
    elif choice == "5":
        show_logs(store, valid)
    elif choice == "6":
        export_orders(store)
    elif choice == "7":
        backup_data()
    else:
        print("ไม่มีเมนูนี้ เลือก 0-7")


def main():
    auth, storage, valid = load_modules()
    store = storage.JsonFileStore(DATA_PATH)
    choice = ""
    while choice != "0":  # เมนูวนซ้ำ จนกว่าจะเลือกออก
        print("\n=== ArtCorner: เมนูผู้ดูแล ===")
        for key, label in MENU:
            print(" %s) %s" % (key, label))
        choice = ask("เลือกเมนู: ")
        try:
            if choice != "0":
                run_choice(choice, store, auth, valid)
        except (storage.StorageError, OSError, ValueError, KeyError, TypeError):
            print("ทำรายการไม่สำเร็จ กรุณาลองใหม่อีกครั้ง")
        except KeyboardInterrupt:
            choice = "0"
    print("ออกจากโปรแกรมเรียบร้อย")


if __name__ == "__main__":
    main()
