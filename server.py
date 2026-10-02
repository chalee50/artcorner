"""server.py - รันเว็บในเครื่อง:  python server.py   แล้วเปิด http://localhost:8000

ข้อมูลเก็บเป็นไฟล์ data/artcorner.json (ปิดโปรแกรมแล้วเปิดใหม่ข้อมูลยังอยู่)
รหัสลับเซ็นโทเคนเก็บใน data/secret.txt (สร้างให้เองครั้งแรก)
ตัวเลือก: ตั้ง ADMIN_EMAIL เป็นอีเมลแอดมินได้  เช่น  set ADMIN_EMAIL=you@gmail.com   (Windows)
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
# อนุญาตให้เปิดได้เฉพาะไฟล์เหล่านี้ (กันการเปิดไฟล์อื่นในเครื่องผ่าน URL)
STATIC_FILES = {
    "/": "index.html", "/index.html": "index.html", "/style.css": "style.css",
    "/app.js": "app.js", "/qr.js": "qr.js",
}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8"}


def load_modules():
    """โหลดโมดูลฝั่งเซิร์ฟเวอร์จากโฟลเดอร์ api/"""
    api_dir = os.path.join(ROOT, "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    import _logic
    import _storage
    import _validators
    return _logic, _storage, _validators


def build_env(storage):
    """รวมตัวแปรสภาพแวดล้อม + รหัสลับ (ถ้ายังไม่ตั้ง AUTH_SECRET ใช้ไฟล์ secret.txt)"""
    env = dict(os.environ)
    if not env.get("AUTH_SECRET"):
        env["AUTH_SECRET"] = storage.load_or_create_secret(os.path.join(DATA_DIR, "secret.txt"))
    return env


def make_handler(logic, store, env):
    """สร้างคลาสรับคำขอที่ผูกกับ store และ env ที่เลือกไว้"""

    class LocalHandler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            name = STATIC_FILES.get(self.path.split("?")[0])
            if name is None:
                self._send(404, "ไม่พบหน้านี้".encode("utf-8"), "text/plain; charset=utf-8")
                return
            try:
                with open(os.path.join(ROOT, name), "rb") as handle:
                    content = handle.read()
            except OSError:
                self._send(500, "อ่านไฟล์ไม่สำเร็จ".encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(200, content, CONTENT_TYPES[os.path.splitext(name)[1]])

        def do_POST(self):
            if self.path.split("?")[0] != "/api/app":
                self._send(404, b"{}", "application/json")
                return
            try:
                length = max(0, int(self.headers.get("Content-Length", 0)))
            except ValueError:
                length = 0
            try:
                if length > logic.MAX_BODY:
                    status, payload = logic.err(413, "ข้อมูลที่ส่งมามีขนาดใหญ่เกินไป")
                else:
                    status, payload = logic.process_request(self.rfile.read(length), env, store)
            except Exception:  # ผู้ใช้ต้องไม่เห็น Traceback
                status, payload = 500, {"ok": False, "err": "เซิร์ฟเวอร์ผิดพลาด"}
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def log_message(self, fmt, *args):
            pass  # ไม่พิมพ์ log ทุกคำขอ ให้หน้าจอสะอาด

    return LocalHandler


def main():
    logic, storage, validators = load_modules()
    port = validators.to_int(sys.argv[1], 8000) if len(sys.argv) > 1 else 8000
    store = storage.make_store(os.environ, allow_file=True, data_path=os.path.join(DATA_DIR, "artcorner.json"))
    env = build_env(storage)
    kind = type(store).__name__
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(logic, store, env))
    except OSError:
        print("เปิดพอร์ต %d ไม่ได้ (อาจมีโปรแกรมอื่นใช้อยู่) ลองใหม่: python server.py 8001" % port)
        return
    print("ArtCorner รันแล้วที่ http://localhost:%d  (เก็บข้อมูลแบบ %s)" % (port, kind))
    print("กด Ctrl+C เพื่อหยุด")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nปิดเซิร์ฟเวอร์เรียบร้อย")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
