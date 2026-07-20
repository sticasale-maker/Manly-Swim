# Bluebottle push notifications — activation checklist

Everything is written and verified in code. The feature stays **dormant** until
the steps below are done: the client opt-in toggle is hidden while
`VAPID_PUBLIC_KEY` is blank, and the service worker's push handler simply never
fires without a sender. So the code is safe to ship before activating.

## Pieces (already in the repo)
- **Client**: opt-in toggle + subscribe/unsubscribe in `index.html`
  (`bbPushToggle`, `VAPID_PUBLIC_KEY` constant).
- **Service worker**: `push` + `notificationclick` handlers in `sw.js`.
- **DB**: `sql/2026-07-20_push_subscriptions.sql` (table + `save_/delete_push_subscription` RPCs).
- **Sender**: `workers/push-bluebottle.js` (Cloudflare Worker, VAPID, payload-less).

## Activation steps (owner)

1. **Generate VAPID keys**
   ```
   npx web-push generate-vapid-keys
   ```
   Note the `Public Key` and `Private Key` (both base64url).

2. **Set the public key in the client**
   In `index.html`, set:
   ```js
   const VAPID_PUBLIC_KEY = '<the public key>';
   ```
   (Ask Claude to paste it in and push, or edit directly.) This unhides the toggle.

3. **Create the subscriptions table + RPCs**
   Run `sql/2026-07-20_push_subscriptions.sql` in the Supabase SQL editor.

4. **Deploy the Worker**
   ```
   cd workers
   wrangler deploy push-bluebottle.js       # or add to your existing worker project
   wrangler secret put WEBHOOK_SECRET         # invent a long random string
   wrangler secret put VAPID_PUBLIC_KEY       # same public key as step 2
   wrangler secret put VAPID_PRIVATE_KEY      # the private key from step 1
   wrangler secret put VAPID_SUBJECT          # e.g. mailto:you@example.com
   wrangler secret put SUPABASE_URL           # https://gkspukabnfbzrvjoewpc.supabase.co
   wrangler secret put SUPABASE_SERVICE_ROLE_KEY   # Supabase → Settings → API → service_role
   ```
   Note the deployed Worker URL.

5. **Wire the trigger (Supabase Database Webhook)**
   Supabase → Database → Webhooks → Create:
   - Table: `public.bluebottle_reports`
   - Events: **Insert**
   - Type: **HTTP Request**, method **POST**, URL: the Worker URL
   - HTTP header: `x-webhook-secret: <the WEBHOOK_SECRET from step 4>`

   The Worker ignores rows without a `photo_url`, so only photo-backed reports push.

## Test end-to-end
1. On an **installed** instance (Home Screen on iOS; installed PWA on Android/desktop),
   tap **🔔 Get notified about bluebottles** and allow.
2. From a second device, submit a bluebottle report **with a photo**.
3. The first device should get a "🪼 Bluebottles at Manly" notification; tapping it opens the app.
4. A report **without** a photo should NOT push.

## Notes
- iOS delivers web push only to a Home-Screen install (16.4+); the toggle tells
  users this when it detects iOS-in-browser.
- Payload-less by design: every alert shows the same fixed message. If you later
  want dynamic text (e.g. a count), it needs RFC 8291 payload encryption in the
  Worker — a bigger change.
- Stale subscriptions (404/410 from the push service) are auto-pruned by the Worker.
