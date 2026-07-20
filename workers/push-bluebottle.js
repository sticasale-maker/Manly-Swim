/**
 * push-bluebottle — Cloudflare Worker
 * ------------------------------------------------------------------
 * Fans out a Web Push "bluebottles reported" alert when a photo-backed
 * bluebottle report is inserted.
 *
 * Trigger: a Supabase Database Webhook on public.bluebottle_reports (INSERT)
 * POSTs the new row here. We only push when the row has a photo_url.
 *
 * Push is PAYLOAD-LESS: we send a VAPID-signed POST with an empty body, so we
 * skip RFC 8291 payload encryption. The service worker's `push` handler shows a
 * fixed notification. Simpler and reliable.
 *
 * ── Environment (wrangler secrets / vars) ─────────────────────────
 *   WEBHOOK_SECRET             shared secret; must match the webhook's header
 *   VAPID_PUBLIC_KEY           base64url, raw (the same key the client uses)
 *   VAPID_PRIVATE_KEY          base64url, raw 32-byte d value
 *   VAPID_SUBJECT              e.g. "mailto:you@example.com"
 *   SUPABASE_URL               https://<project>.supabase.co
 *   SUPABASE_SERVICE_ROLE_KEY  service_role key (reads push_subscriptions)
 *
 * Deploy: `wrangler deploy`. Add secrets with `wrangler secret put <NAME>`.
 * Point a Supabase Database Webhook (INSERT on bluebottle_reports) at this
 * Worker's URL, adding header  x-webhook-secret: <WEBHOOK_SECRET>.
 */

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return new Response('method', { status: 405 });

    // Auth: shared secret from the webhook.
    const secret = request.headers.get('x-webhook-secret') || '';
    if (!env.WEBHOOK_SECRET || secret !== env.WEBHOOK_SECRET) {
      return new Response('unauthorized', { status: 401 });
    }

    let payload;
    try { payload = await request.json(); } catch (e) { return new Response('bad json', { status: 400 }); }

    // Supabase webhook shape: { type, table, record, old_record, ... }
    const record = payload && (payload.record || payload.new || payload);
    const photoUrl = record && record.photo_url;
    if (!photoUrl) return new Response('no-photo', { status: 200 }); // bare tap — nothing to push

    const subs = await getSubscriptions(env);
    if (!subs.length) return new Response('no-subs', { status: 200 });

    const results = await Promise.allSettled(subs.map(s => sendPush(s, env)));
    const gone = [];
    results.forEach((r, i) => {
      if (r.status === 'fulfilled' && (r.value === 404 || r.value === 410)) gone.push(subs[i].endpoint);
    });
    if (gone.length) await deleteSubscriptions(env, gone);

    const ok = results.filter(r => r.status === 'fulfilled' && r.value >= 200 && r.value < 300).length;
    return new Response(JSON.stringify({ sent: ok, total: subs.length, pruned: gone.length }), {
      status: 200, headers: { 'content-type': 'application/json' }
    });
  }
};

// ── Supabase (service_role) ───────────────────────────────────────
async function getSubscriptions(env) {
  const r = await fetch(`${env.SUPABASE_URL}/rest/v1/push_subscriptions?select=endpoint,p256dh,auth`, {
    headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` }
  });
  if (!r.ok) return [];
  return await r.json();
}

async function deleteSubscriptions(env, endpoints) {
  // Delete rows whose endpoint is in the stale list.
  const inList = endpoints.map(e => `"${e.replace(/"/g, '')}"`).join(',');
  await fetch(`${env.SUPABASE_URL}/rest/v1/push_subscriptions?endpoint=in.(${encodeURIComponent(inList)})`, {
    method: 'DELETE',
    headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` }
  }).catch(() => {});
}

// ── Web Push (VAPID, payload-less) ────────────────────────────────
async function sendPush(sub, env) {
  const endpoint = sub.endpoint;
  const aud = new URL(endpoint).origin;
  const jwt = await makeVapidJwt(aud, env);
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      TTL: '3600',
      Authorization: `vapid t=${jwt}, k=${env.VAPID_PUBLIC_KEY}`,
      'Content-Length': '0'
    }
  });
  return res.status;
}

async function makeVapidJwt(aud, env) {
  const header = { typ: 'JWT', alg: 'ES256' };
  const now = Math.floor(Date.now() / 1000);
  const payload = { aud, exp: now + 12 * 60 * 60, sub: env.VAPID_SUBJECT };
  const signingInput = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(payload))}`;

  const key = await importVapidKey(env.VAPID_PUBLIC_KEY, env.VAPID_PRIVATE_KEY);
  const sig = await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' }, key, new TextEncoder().encode(signingInput)
  );
  return `${signingInput}.${b64urlBytes(new Uint8Array(sig))}`;
}

// Build a P-256 JWK from the raw base64url VAPID keys and import for signing.
async function importVapidKey(pubB64, privB64) {
  const pub = b64urlDecode(pubB64);   // 65 bytes: 0x04 || X(32) || Y(32)
  const d   = privB64;                // 32-byte scalar, already base64url
  const x = b64urlBytes(pub.slice(1, 33));
  const y = b64urlBytes(pub.slice(33, 65));
  return crypto.subtle.importKey(
    'jwk',
    { kty: 'EC', crv: 'P-256', d, x, y, ext: true },
    { name: 'ECDSA', namedCurve: 'P-256' },
    false,
    ['sign']
  );
}

// ── base64url helpers ─────────────────────────────────────────────
function b64url(str) { return b64urlBytes(new TextEncoder().encode(str)); }
function b64urlBytes(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64urlDecode(b64) {
  const pad = '='.repeat((4 - b64.length % 4) % 4);
  const s = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  const arr = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) arr[i] = s.charCodeAt(i);
  return arr;
}
