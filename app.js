'use strict';
// Vercel Function: POST /api/app  — ใช้ Upstash Redis เป็นฐานข้อมูล
const core = require('./_core');
let kv = null;

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'POST') return res.status(405).json({ ok: false, err: 'POST only' });
  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
  body = body || {};
  const env = process.env;
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL;
  const tok = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN;
  const ready = !!(url && tok && env.AUTH_SECRET);
  // ping: ให้หน้าเว็บรู้ว่าเซิร์ฟเวอร์พร้อมหรือยัง (ถ้ายังไม่พร้อม เว็บจะทำงานแบบเดโมในเครื่องเหมือนเดิม)
  if (body.a === 'ping') return res.status(200).json({ ok: true, remote: ready });
  if (!ready) return res.status(500).json({ ok: false, err: 'ยังไม่ได้ตั้งค่า Upstash Redis หรือ AUTH_SECRET' });
  kv = kv || core.upstashKV(url, tok);
  const r = await core.handle(kv, { AUTH_SECRET: env.AUTH_SECRET, ADMIN_EMAIL: env.ADMIN_EMAIL }, body);
  res.status(r.status).json(r.json);
};
