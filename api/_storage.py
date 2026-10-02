"""_storage.py - ที่เก็บข้อมูล 3 แบบ ใช้เมธอดชุดเดียวกัน (all, get, batch, ver, bump)

* MemoryStore     เก็บในหน่วยความจำ (ใช้ทดสอบ)
* JsonFileStore   เก็บเป็นไฟล์ .json ในเครื่อง ปิดโปรแกรมแล้วเปิดใหม่ข้อมูลยังอยู่
* UpstashStore    เก็บใน Upstash Redis (ใช้บน Vercel ที่เขียนไฟล์ถาวรไม่ได้)

รูปแบบข้อมูล: คอลเลกชัน -> { id: ระเบียน } เช่น works -> { "w1": {...} }
"""
import json
import os
import secrets
import threading
import urllib.error
import urllib.request


class StorageError(Exception):
    """เชื่อมต่อหรืออ่าน/เขียนที่เก็บข้อมูลไม่สำเร็จ"""


def clone(value):
    """คัดลอกลึกผ่าน JSON กันไม่ให้แก้ข้อมูลในที่เก็บโดยไม่ตั้งใจ"""
    return json.loads(json.dumps(value))


class MemoryStore:
    """เก็บในหน่วยความจำ"""

    def __init__(self):
        self.cols = {}
        self.version = 0

    def all(self, cols):
        return {c: clone(self.cols.get(c, {})) for c in cols}

    def get(self, col, item_id):
        item = self.cols.get(col, {}).get(item_id)
        return None if item is None else clone(item)

    def batch(self, ops):
        for op in ops:
            table = self.cols.setdefault(op["c"], {})
            if op["v"] is None:
                table.pop(op["id"], None)
            else:
                table[op["id"]] = clone(op["v"])

    def ver(self):
        return str(self.version)

    def bump(self):
        self.version += 1
        return str(self.version)


class JsonFileStore(MemoryStore):
    """เก็บเป็นไฟล์ JSON อ่านจากดิสก์ทุกครั้งที่เรียก (เพื่อให้ server กับ cli ใช้ไฟล์เดียวกันได้)"""

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.lock = threading.RLock()
        self._load()

    def _load(self):
        """โหลดไฟล์ ถ้าไฟล์เสียจะเปลี่ยนชื่อเป็น .bad แล้วเริ่มใหม่ ไม่ให้ระบบพัง"""
        self.cols, self.version = {}, 0
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("cols"), dict):
                self.cols = data["cols"]
                self.version = int(data.get("ver", 0))
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, ValueError, OSError):
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass

    def _save(self):
        """เขียนไฟล์ชั่วคราวก่อนแล้วค่อยสลับ กันไฟล์พังถ้าปิดโปรแกรมกลางคัน"""
        folder = os.path.dirname(self.path)
        try:
            if folder:
                os.makedirs(folder, exist_ok=True)
            temp = self.path + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump({"ver": self.version, "cols": self.cols}, handle, ensure_ascii=False)
            os.replace(temp, self.path)
        except OSError as error:
            raise StorageError("บันทึกไฟล์ข้อมูลไม่สำเร็จ") from error

    def all(self, cols):
        with self.lock:
            self._load()
            return super().all(cols)

    def get(self, col, item_id):
        with self.lock:
            self._load()
            return super().get(col, item_id)

    def batch(self, ops):
        with self.lock:
            self._load()
            super().batch(ops)
            self._save()

    def ver(self):
        with self.lock:
            self._load()
            return str(self.version)

    def bump(self):
        with self.lock:
            self._load()
            self.version += 1
            self._save()
            return str(self.version)


class UpstashStore:
    """เก็บใน Upstash Redis ผ่าน REST (ใช้ urllib ที่มากับ Python ไม่ต้องติดตั้งอะไรเพิ่ม)"""

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def _call(self, commands):
        """ส่งหลายคำสั่งในคำขอเดียว แล้วคืนรายการผลลัพธ์"""
        request = urllib.request.Request(
            self.url + "/pipeline",
            data=json.dumps(commands).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                answers = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            raise StorageError("เชื่อมต่อฐานข้อมูลไม่ได้") from error
        results = []
        for answer in answers:
            if answer.get("error"):
                raise StorageError("ฐานข้อมูลตอบกลับผิดพลาด")
            results.append(answer.get("result"))
        return results

    def all(self, cols):
        results = self._call([["HGETALL", "ac:" + c] for c in cols])
        out = {}
        for col, flat in zip(cols, results):
            flat = flat or []
            table = {}
            for i in range(0, len(flat), 2):  # HGETALL คืน [key, value, key, value, ...]
                table[flat[i]] = json.loads(flat[i + 1])
            out[col] = table
        return out

    def get(self, col, item_id):
        (raw,) = self._call([["HGET", "ac:" + col, item_id]])
        return json.loads(raw) if raw else None

    def batch(self, ops):
        if not ops:
            return
        commands = []
        for op in ops:
            if op["v"] is None:
                commands.append(["HDEL", "ac:" + op["c"], op["id"]])
            else:
                commands.append(["HSET", "ac:" + op["c"], op["id"], json.dumps(op["v"])])
        self._call(commands)

    def ver(self):
        (raw,) = self._call([["GET", "ac:v"]])
        return str(raw or "0")

    def bump(self):
        (raw,) = self._call([["INCR", "ac:v"]])
        return str(raw)


def make_store(env, allow_file=False, data_path="data/artcorner.json"):
    """เลือกที่เก็บข้อมูลจากตัวแปรสภาพแวดล้อม

    มี KV_REST_API_URL + TOKEN (Vercel/Upstash) -> UpstashStore
    ไม่มีและ allow_file=True (รันในเครื่อง)    -> JsonFileStore
    ไม่มีและ allow_file=False (บน Vercel)       -> None (เว็บจะแจ้งว่ายังไม่ได้ตั้งค่า)
    """
    url = env.get("KV_REST_API_URL") or env.get("UPSTASH_REDIS_REST_URL")
    token = env.get("KV_REST_API_TOKEN") or env.get("UPSTASH_REDIS_REST_TOKEN")
    if url and token:
        return UpstashStore(url, token)
    if allow_file:
        return JsonFileStore(data_path)
    return None


def load_or_create_secret(path):
    """อ่านรหัสลับสำหรับเซ็นโทเคนจากไฟล์ .txt ถ้ายังไม่มีให้สุ่มสร้างและบันทึกไว้"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
        if len(text) >= 32:
            return text
    except OSError:
        pass
    text = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass  # เขียนไม่ได้ก็ใช้รหัสที่สุ่มนี้ไปก่อนในรอบนี้
    return text
