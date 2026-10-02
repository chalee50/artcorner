"""api/app.py - จุดเข้าของ Vercel Function: POST /api/app

ข้อมูลเก็บใน Upstash Redis (ตัวแปร KV_REST_API_URL / KV_REST_API_TOKEN ที่ Vercel ใส่ให้)
ต้องตั้ง AUTH_SECRET และ (ควรตั้ง) ADMIN_EMAIL ใน Environment Variables
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler


def load_modules():
    """เพิ่มโฟลเดอร์นี้เข้า sys.path แล้วโหลดโมดูลช่วย (_logic, _storage) ตอนมีคำขอเข้ามา"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import _logic
    import _storage
    return _logic, _storage


def read_length(headers):
    """อ่านขนาด body จาก header อย่างปลอดภัย (ผิดรูปแบบ = 0)"""
    try:
        return max(0, int(headers.get("Content-Length", 0)))
    except (TypeError, ValueError):
        return 0


class handler(BaseHTTPRequestHandler):
    """Vercel เรียกคลาสชื่อ handler นี้"""

    def _send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            logic, storage = load_modules()
            length = read_length(self.headers)
            if length > logic.MAX_BODY:
                status, payload = logic.err(413, "ข้อมูลที่ส่งมามีขนาดใหญ่เกินไป")
            else:
                raw = self.rfile.read(length) if length else b""
                env = dict(os.environ)
                store = storage.make_store(env, allow_file=False)
                status, payload = logic.process_request(raw, env, store)
        except Exception:  # ผู้ใช้ต้องไม่เห็น Traceback
            status, payload = 500, {"ok": False, "err": "เซิร์ฟเวอร์ผิดพลาด"}
        self._send(status, payload)

    def do_GET(self):
        self._send(405, {"ok": False, "err": "POST only"})
