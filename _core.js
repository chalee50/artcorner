'use strict';
// ArtCorner server core — เก็บข้อมูลกลางใน Redis (Upstash) และตรวจสิทธิ์ทุกการเขียนฝั่งเซิร์ฟเวอร์
const crypto = require('crypto');
const SHIP = 60;
const COLS = ['works', 'artists', 'users', 'orders', 'coms', 'reviews', 'apps', 'likes', 'follows', 'logs', 'settings'];
const DEFAULT_CATS = ['จิตรกรรม', 'ภาพพิมพ์', 'ดิจิทัลอาร์ต', 'ภาพถ่าย', 'ภาพวาดเส้น'];
const EM = /^[^\s@]+@[^\s@]+\.[^\s@]+$/, PH = /^0\d{9}$/;
const J = JSON.stringify, same = (a, b) => J(a) === J(b);
const E = m => { const e = new Error(m); e.user = true; throw e; };
const diffKeys = (a, b) => [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].filter(k => !same((a || {})[k], (b || {})[k]));
const isAdm = u => u && u.role === 'admin', isMgrR = u => u && (u.role === 'admin' || u.role === 'staff');

// ---------- KV adapters ----------
function memKV() {
  const d = {}, h = c => d[c] || (d[c] = {}); let v = 0;
  return {
    async all(cols) { const o = {}; cols.forEach(c => { o[c] = JSON.parse(J(h(c))); }); return o; },
    async get(c, id) { const x = h(c)[id]; return x === undefined ? null : JSON.parse(J(x)); },
    async batch(ops) { ops.forEach(o => { if (o.v === null) delete h(o.c)[o.id]; else h(o.c)[o.id] = JSON.parse(J(o.v)); }); },
    async ver() { return String(v); }, async bump() { v++; return String(v); }
  };
}
function upstashKV(url, token, f = globalThis.fetch) {
  const call = async cmds => {
    const r = await f(url.replace(/\/$/, '') + '/pipeline', { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: J(cmds) });
    if (!r.ok) throw new Error('kv http ' + r.status);
    return (await r.json()).map(x => { if (x.error) throw new Error('kv ' + x.error); return x.result; });
  };
  return {
    async all(cols) { const rs = await call(cols.map(c => ['HGETALL', 'ac:' + c])), o = {}; cols.forEach((c, i) => { const a = rs[i] || [], m = {}; for (let j = 0; j < a.length; j += 2) m[a[j]] = JSON.parse(a[j + 1]); o[c] = m; }); return o; },
    async get(c, id) { const [r] = await call([['HGET', 'ac:' + c, id]]); return r ? JSON.parse(r) : null; },
    async batch(ops) { if (ops.length) await call(ops.map(o => o.v === null ? ['HDEL', 'ac:' + o.c, o.id] : ['HSET', 'ac:' + o.c, o.id, J(o.v)])); },
    async ver() { const [r] = await call([['GET', 'ac:v']]); return r || '0'; },
    async bump() { const [r] = await call([['INCR', 'ac:v']]); return String(r); }
  };
}

// ---------- auth ----------
const scrypt = (pw, salt) => crypto.scryptSync(String(pw), salt, 32).toString('hex');
const b64 = s => Buffer.from(s).toString('base64url');
function sign(p, sec) { const b = b64(J(p)); return b + '.' + crypto.createHmac('sha256', sec).update(b).digest('base64url'); }
function verify(t, sec) {
  if (typeof t !== 'string') return null; const [b, s] = t.split('.'); if (!b || !s) return null;
  const x = crypto.createHmac('sha256', sec).update(b).digest('base64url');
  if (x.length !== s.length || !crypto.timingSafeEqual(Buffer.from(x), Buffer.from(s))) return null;
  try { const p = JSON.parse(Buffer.from(b, 'base64url').toString()); return p.exp > Date.now() ? p : null; } catch (e) { return null; }
}
const own = u => ({ id: u.id, name: u.name, email: u.email, phone: u.phone || '', role: u.role, artist: u.artist || undefined });
const pub = u => ({ id: u.id, name: u.name, artist: u.artist || undefined });
const checkPw = (u, pw) => { const h = scrypt(pw, u ? u.salt : '0'.repeat(32)); return !!u && crypto.timingSafeEqual(Buffer.from(h, 'hex'), Buffer.from(u.hash, 'hex')); };
const newUser = (name, email, phone, pw, role) => { const salt = crypto.randomBytes(16).toString('hex'); return { id: 'u' + Date.now().toString(36) + crypto.randomBytes(3).toString('hex'), name, email, phone, role, salt, hash: scrypt(pw, salt) }; };

// ---------- what each role may SEE ----------
function view(all, me) {
  const mgr = isMgrR(me), adm = isAdm(me), L = c => Object.values(all[c] || {});
  const users = L('users').map(u => adm || (me && u.id === me.id) ? own(u) : mgr ? { ...pub(u), email: u.email, role: u.role } : pub(u));
  const st = all.settings || {};
  return {
    works: L('works').filter(w => mgr || w.approval === 'approved' || (me && me.artist && w.artist === me.artist)),
    artists: L('artists'), users,
    orders: L('orders').filter(o => mgr || (me && o.uid === me.id)),
    coms: L('coms').filter(c => mgr || (me && (c.uid === me.id || (me.artist && c.artist === me.artist)))),
    reviews: L('reviews'), apps: L('apps').filter(a => mgr || (me && a.uid === me.id)),
    likes: L('likes'), follows: L('follows'),
    logs: adm ? L('logs').sort((a, b) => (b.i > a.i ? 1 : -1)) : [],
    settings: { cats: st.cats || DEFAULT_CATS, wm: st.wm || { text: 'ArtCorner', op: 0.35 }, qr: st.qr || '' }
  };
}

// ---------- what each role may WRITE ----------
const rules = {
  users({ me, adm, mgr, old, v, id, del, all, batch }) {
    const admins = () => Object.values(all.users).filter(u => u.role === 'admin').length;
    if (del) { if (!adm) E('เฉพาะแอดมินลบผู้ใช้'); if (id === me.id) E('ลบบัญชีตัวเองไม่ได้'); if (old && old.role === 'admin' && admins() < 2) E('ต้องมีแอดมินอย่างน้อย 1 คน'); return null; }
    if (!old) E('สร้างผู้ใช้ผ่านการสมัครหรือเมนูเพิ่มผู้ใช้เท่านั้น');
    const ch = Object.keys(v).filter(k => !['salt', 'hash'].includes(k) && !same(v[k], old[k]));
    let ok = adm ? ['name', 'phone', 'role', 'artist'] : id === me.id ? ['name', 'phone'] : [];
    if (mgr && !ok.includes('artist') && ch.includes('artist') && !old.artist && batch('apps').some(a => a.uid === id && a.status === 'approved') && batch('artists').some(a => a.id === v.artist)) ok = [...ok, 'artist'];
    if (!ch.every(k => ok.includes(k))) E('ไม่มีสิทธิ์แก้ไขผู้ใช้นี้');
    if (ch.includes('role')) { if (!['admin', 'staff', 'customer'].includes(v.role)) E('บทบาทไม่ถูกต้อง'); if (old.role === 'admin' && v.role !== 'admin' && admins() < 2) E('ต้องมีแอดมินอย่างน้อย 1 คน'); }
    if (ch.includes('name') && !(typeof v.name === 'string' && v.name.trim().length >= 2 && v.name.length <= 60)) E('ชื่อไม่ถูกต้อง');
    if (ch.includes('phone') && v.phone && !PH.test(v.phone)) E('เบอร์โทรไม่ถูกต้อง');
    const n = { ...old }; ch.forEach(k => { n[k] = v[k]; }); return n;
  },
  artists({ me, adm, mgr, old, v, id, del, batch }) {
    if (del) E('ห้ามลบศิลปิน');
    if (adm) return v;
    if (mgr) { if (!old && !batch('apps').some(a => a.status === 'approved')) E('สร้างศิลปินผ่านการอนุมัติใบสมัครเท่านั้น'); return v; }
    if (old && me.artist === id && diffKeys(old, v).every(k => ['name', 'school', 'bio'].includes(k))) return v;
    E('ไม่มีสิทธิ์แก้ไขโปรไฟล์ศิลปินนี้');
  },
  works({ me, mgr, old, v, id, del, all, batch }) {
    const mine = a => !!me.artist && a === me.artist;
    if (del) {
      if (!(mgr || (old && mine(old.artist)))) E('ไม่มีสิทธิ์ลบผลงาน');
      if (Object.values(all.orders).some(o => !o.cancelled && ((o.items || []).includes(id) || (o.dl || []).includes(id)))) E('ลบไม่ได้: ผลงานอยู่ในคำสั่งซื้อ');
      return null;
    }
    if (mgr) { if (!all.artists[v.artist] && !batch('artists').some(a => a.id === v.artist)) E('ไม่พบศิลปิน'); return v; }
    if (old ? mine(old.artist) : mine(v.artist)) {
      if (v.artist !== me.artist) E('เปลี่ยนศิลปินไม่ได้');
      if (!old) { if (v.approval !== 'pending' || v.status !== 'available') E('ผลงานใหม่ต้องรอแอดมินอนุมัติ'); return v; }
      if (v.status !== old.status) E('เปลี่ยนสถานะการขายไม่ได้'); if (v.approval !== 'pending') E('ผลงานที่แก้ไขต้องรออนุมัติใหม่'); return v;
    }
    if (old && old.status === 'available' && v.status === 'sold' && diffKeys(old, v).every(k => k === 'status') && batch('orders').some(o => o.uid === me.id && (o.items || []).includes(id))) return v;
    E('ไม่มีสิทธิ์แก้ไขผลงานนี้');
  },
  orders({ me, mgr, old, v, del, all, cur }) {
    if (del) E('ห้ามลบคำสั่งซื้อ');
    if (old && v.uid !== old.uid) E('เปลี่ยนเจ้าของคำสั่งซื้อไม่ได้');
    if (mgr) { if (!old && !all.users[v.uid]) E('ไม่พบผู้ซื้อ'); return v; }
    if (!old) {
      if (v.uid !== me.id || v.status !== 0 || v.cancelled || v.slip) E('คำสั่งซื้อไม่ถูกต้อง');
      const items = v.items || [], dl = v.dl || []; let tot = 0;
      for (const i of items) { const w = all.works[i]; if (!w || w.approval !== 'approved' || (me.artist && w.artist === me.artist)) E('ซื้อผลงานของตัวเองหรือผลงานที่ไม่พร้อมขายไม่ได้'); if (w.status !== 'available') E('ผลงาน “' + w.title + '” ขายไปแล้ว'); tot += w.price; }
      for (const i of dl) { const w = all.works[i]; if (!w || w.approval !== 'approved' || !w.digital || (me.artist && w.artist === me.artist)) E('ไฟล์ดิจิทัลไม่พร้อมขาย'); tot += w.digital.price; }
      if (items.length) tot += SHIP;
      if (v.com) { const c = all.coms[v.com], c2 = cur('coms', v.com); if (!c || c.uid !== me.id || c.status !== 1 || c.end || !c2 || c2.status !== 2 || items.length || dl.length) E('งานจ้างไม่ถูกต้อง'); tot = c.quote.price; }
      else if (!items.length && !dl.length) E('คำสั่งซื้อว่าง');
      if (v.total !== tot) E('ยอดรวมไม่ตรงกับราคาจริง');
      return v;
    }
    if (old.uid !== me.id) E('ไม่มีสิทธิ์');
    const ch = diffKeys(old, v);
    if (!ch.length) return v;
    if (ch.every(k => k === 'slip')) { if (old.status !== 0 || old.cancelled) E('แนบสลิปไม่ได้ในสถานะนี้'); return v; }
    if (ch.every(k => k === 'cancelled') && v.cancelled && old.status === 0 && old.com) return v;
    if (ch.every(k => k === 'status') && v.status === 3 && old.com && old.status >= 1) { const c = cur('coms', old.com); if (c && c.uid === me.id && c.status === 5) return v; }
    E('ไม่มีสิทธิ์แก้ไขคำสั่งซื้อนี้');
  },
  coms({ me, mgr, old, v, del, all, batch }) {
    if (del) E('ห้ามลบงานจ้าง'); if (mgr) return v;
    if (!old) { if (v.uid !== me.id || v.status !== 0 || v.end || !all.artists[v.artist] || (me.artist && v.artist === me.artist)) E('คำขอจ้างไม่ถูกต้อง'); return v; }
    if (old.end) E('งานนี้สิ้นสุดแล้ว');
    const ch = diffKeys(old, v), is = ks => ch.every(x => ks.includes(x));
    if (old.uid === me.id) {
      const ord = batch('orders').find(o => o.com === old.id && o.uid === me.id);
      if (old.status === 0 && is(['end']) && v.end === 'cancelled') return v;
      if (old.status === 1 && is(['status', 'orderId']) && v.status === 2 && ord && v.orderId === ord.id) return v;
      if ((old.status === 1 || old.status === 2) && is(['end']) && v.end === 'cancelled') return v;
      if (old.status === 4 && is(['status']) && v.status === 5) return v;
      if (old.status === 4 && is(['status', 'rev', 'revNote']) && v.status === 3 && (old.rev || 0) < 2 && v.rev === (old.rev || 0) + 1) return v;
    }
    if (me.artist && old.artist === me.artist) {
      if (old.status === 0 && is(['status', 'quote']) && v.status === 1 && v.quote && Number.isInteger(v.quote.price) && v.quote.price >= 300 && v.quote.price <= 200000) return v;
      if (old.status === 0 && is(['end', 'reason']) && v.end === 'declined') return v;
      if (old.status === 3 && is(['status', 'delivery']) && v.status === 4 && v.delivery && v.delivery.img) return v;
    }
    E('ไม่มีสิทธิ์ดำเนินการนี้');
  },
  reviews({ me, mgr, old, v, del, all, cur }) {
    if (del) { if (!(mgr || (old && old.u === me.id) || (old && old.kind === 'w' && cur('works', old.ref) == null))) E('ไม่มีสิทธิ์ลบรีวิว'); return null; }
    if (v.u !== me.id || (old && old.u !== me.id)) E('รีวิวไม่ถูกต้อง');
    if (!Number.isInteger(v.rating) || v.rating < 1 || v.rating > 5 || typeof v.text !== 'string' || v.text.length < 10 || v.text.length > 500) E('ข้อมูลรีวิวไม่ถูกต้อง');
    let art;
    if (v.kind === 'w') {
      const w = all.works[v.ref];
      if (!w || (me.artist && w.artist === me.artist) || !Object.values(all.orders).some(o => o.uid === me.id && !o.cancelled && o.status >= 1 && ((o.items || []).includes(v.ref) || (o.dl || []).includes(v.ref)))) E('รีวิวได้เฉพาะผู้ซื้อที่ชำระเงินแล้ว');
      art = w.artist;
    } else if (v.kind === 'c') {
      const c = all.coms[v.ref]; if (!c || c.uid !== me.id || c.status !== 5 || c.end) E('รีวิวได้หลังงานจ้างสำเร็จ'); art = c.artist;
    } else E('ประเภทรีวิวไม่ถูกต้อง');
    return { ...v, artist: art };
  },
  apps({ me, adm, mgr, old, v, del, all }) {
    if (del) { if (!adm) E('ห้ามลบใบสมัคร'); return null; }
    if (mgr) { if (!old) E('ไม่พบใบสมัคร'); if (!['approved', 'rejected'].includes(v.status)) E('สถานะไม่ถูกต้อง'); return { ...old, status: v.status, note: v.note }; }
    if (!old) { if (v.uid !== me.id || v.status !== 'pending' || me.role !== 'customer' || me.artist || Object.values(all.apps).some(a => a.uid === me.id && a.status === 'pending')) E('ใบสมัครไม่ถูกต้อง'); return v; }
    E('ไม่มีสิทธิ์แก้ไขใบสมัคร');
  },
  likes({ me, adm, id, v, del, all, cur }) {
    const w = id.split('|')[1];
    if (del) { if (!(id.startsWith(me.id + '|') || adm || cur('works', w) == null)) E('ไม่มีสิทธิ์'); return null; }
    if (!id.startsWith(me.id + '|') || v.u !== me.id || v.w !== w || !all.works[w]) E('ข้อมูลไม่ถูกต้อง'); return v;
  },
  follows({ me, adm, id, v, del, all }) {
    const a = id.split('|')[1];
    if (del) { if (!(id.startsWith(me.id + '|') || adm)) E('ไม่มีสิทธิ์'); return null; }
    if (!id.startsWith(me.id + '|') || v.u !== me.id || v.a !== a || !all.artists[a] || (me.artist && a === me.artist)) E('ข้อมูลไม่ถูกต้อง'); return v;
  },
  logs({ me, id, v, del }) {
    if (del) E('ห้ามลบ log');
    const s = x => String(x == null ? '' : x).slice(0, 300);
    return { i: id, t: s(v.t), u: me.email, r: me.role, act: s(v.act), ent: s(v.ent), id: s(v.id), d: s(v.d) };
  },
  settings({ adm, id, v, del }) {
    if (!adm || del) E('เฉพาะแอดมินแก้ไขการตั้งค่า');
    if (id === 'cats') { if (!Array.isArray(v) || v.length > 50 || v.some(x => typeof x !== 'string' || x.length < 2 || x.length > 30) || new Set(v).size !== v.length) E('หมวดหมู่ไม่ถูกต้อง'); }
    else if (id === 'wm') { if (!v || typeof v.text !== 'string' || v.text.length < 2 || v.text.length > 30 || !(v.op >= 0.1 && v.op <= 0.6)) E('ตั้งค่าลายน้ำไม่ถูกต้อง'); }
    else if (id === 'qr') { if (typeof v !== 'string' || v.length > 600000) E('รูป QR ไม่ถูกต้อง'); }
    else E('ไม่รู้จักค่าตั้งนี้');
    return v;
  }
};

function authorize(me, all, ops) {
  if (!Array.isArray(ops) || !ops.length || ops.length > 80) E('คำขอไม่ถูกต้อง');
  const nw = {};
  for (const o of ops) {
    if (!o || typeof o.c !== 'string' || !COLS.includes(o.c) || typeof o.id !== 'string' || !o.id || o.id.length > 80 || !('v' in o)) E('คำขอไม่ถูกต้อง');
    if (o.v !== null && J(o.v).length > 900000) E('ข้อมูลใหญ่เกินไป');
    if (o.v !== null && o.c !== 'settings' && (typeof o.v !== 'object' || Array.isArray(o.v))) E('ข้อมูลไม่ถูกต้อง');
    (nw[o.c] = nw[o.c] || {})[o.id] = o.v;
  }
  const cur = (c, id) => (nw[c] && id in nw[c]) ? nw[c][id] : (all[c] || {})[id];
  const batch = c => Object.values(nw[c] || {}).filter(x => x);
  return ops.map(o => {
    const old = (all[o.c] || {})[o.id], del = o.v === null;
    const r = rules[o.c]({ me, mgr: isMgrR(me), adm: isAdm(me), old, v: o.v, id: o.id, del, all, cur, batch });
    return del ? null : r;
  });
}

// ---------- request handler ----------
const ok = (j = {}) => ({ status: 200, json: { ok: true, ...j } });
const fail = (status, err) => ({ status, json: { ok: false, err } });
async function handle(kv, env, body) {
  const b = body || {}, sec = env.AUTH_SECRET;
  try {
    if (b.a === 'ping') return ok({ remote: !!sec });
    if (!sec) return fail(500, 'ยังไม่ได้ตั้งค่า AUTH_SECRET บนเซิร์ฟเวอร์');
    const p = verify(b.token, sec), me = p ? await kv.get('users', p.uid) : null;
    const tok = u => sign({ uid: u.id, exp: Date.now() + 30 * 864e5 }, sec);
    const str = x => String(x == null ? '' : x).trim();

    if (b.a === 'register') {
      const name = str(b.name), email = str(b.email).toLowerCase(), phone = str(b.phone), pw = String(b.password || '');
      if (name.length < 2 || name.length > 60) E('ชื่ออย่างน้อย 2 ตัวอักษร');
      if (!EM.test(email)) E('รูปแบบอีเมลไม่ถูกต้อง');
      if (!PH.test(phone)) E('เบอร์โทรต้องเป็นตัวเลข 10 หลักขึ้นต้นด้วย 0');
      if (pw.length < 6 || pw.length > 100) E('รหัสผ่านอย่างน้อย 6 ตัวอักษร');
      const users = Object.values((await kv.all(['users'])).users);
      if (users.some(u => u.email === email)) E('อีเมลนี้ถูกใช้แล้ว');
      const admin = env.ADMIN_EMAIL ? email === String(env.ADMIN_EMAIL).toLowerCase() : !users.length;
      const u = newUser(name, email, phone, pw, admin ? 'admin' : 'customer');
      await kv.batch([{ c: 'users', id: u.id, v: u }]); await kv.bump();
      return ok({ token: tok(u), user: own(u) });
    }
    if (b.a === 'login') {
      const email = str(b.email).toLowerCase(), u = Object.values((await kv.all(['users'])).users).find(x => x.email === email);
      if (!checkPw(u, String(b.password || ''))) return fail(401, 'อีเมลหรือรหัสผ่านไม่ถูกต้อง');
      return ok({ token: tok(u), user: own(u) });
    }
    if (b.a === 'state') {
      const v = await kv.ver();
      if (b.since != null && String(b.since) === v) return ok({ same: true, v });
      const all = await kv.all(COLS);
      return ok({ v, auth: !!me, me: me ? own(me) : null, data: view(all, me) });
    }
    if (!me) return fail(401, 'กรุณาเข้าสู่ระบบ');
    if (b.a === 'passwd') {
      const np = String(b.newPassword || '');
      if (np.length < 6 || np.length > 100) E('รหัสผ่านใหม่อย่างน้อย 6 ตัวอักษร');
      if (!checkPw(me, String(b.oldPassword || ''))) E('รหัสผ่านเดิมไม่ถูกต้อง');
      const salt = crypto.randomBytes(16).toString('hex');
      await kv.batch([{ c: 'users', id: me.id, v: { ...me, salt, hash: scrypt(np, salt) } }]); await kv.bump();
      return ok();
    }
    if (b.a === 'adminUser') {
      if (!isAdm(me)) return fail(403, 'เฉพาะแอดมิน');
      const name = str(b.name), email = str(b.email).toLowerCase(), pw = String(b.password || ''), role = b.role;
      if (name.length < 2 || !EM.test(email) || pw.length < 6 || !['admin', 'staff', 'customer'].includes(role)) E('ข้อมูลไม่ถูกต้อง');
      if (Object.values((await kv.all(['users'])).users).some(u => u.email === email)) E('อีเมลนี้ถูกใช้แล้ว');
      const u = newUser(name, email, '', pw, role); await kv.batch([{ c: 'users', id: u.id, v: u }]); await kv.bump();
      return ok({ user: own(u) });
    }
    if (b.a === 'sync') {
      const all = await kv.all(COLS), res = authorize(me, all, b.ops);
      const writes = b.ops.map((o, i) => ({ c: o.c, id: o.id, v: res[i] }));
      const logs = Object.keys(all.logs || {});
      if (logs.length > 520) logs.sort().slice(0, logs.length - 500).forEach(id => writes.push({ c: 'logs', id, v: null }));
      await kv.batch(writes); const v = await kv.bump();
      return ok({ v });
    }
    return fail(400, 'คำสั่งไม่ถูกต้อง');
  } catch (e) {
    if (e.user) return fail(400, e.message);
    console.error(e); return fail(500, 'เซิร์ฟเวอร์ผิดพลาด');
  }
}
module.exports = { handle, memKV, upstashKV, view, authorize, COLS, SHIP };
