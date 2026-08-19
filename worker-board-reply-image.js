// ─────────────────────────────────────────────────────────────────────────────
// APPLIED 19 Aug 2026 — pasted, deployed and confirmed working by Marco. Kept as
// the record of what was changed and why; do NOT apply it a second time. If the
// Worker is ever rebuilt from scratch, this is the patch it needs again.
//
// PASTE JOB — bold-rain-6ded, Cloudflare dashboard editor. NOT deployed from this
// repo (CLAUDE.md §6: the Worker is shared with the Forecast App and is
// dashboard-managed). Additive only — no existing route's behaviour changes.
//
// WHY: Bay Talk replies already carry photos end to end — the public composer
// uploads to the `board-images` bucket, submit_feature_reply takes p_image_url,
// and the reply renderer draws r.image_url. Marco's replies could not, because
// they do not use that RPC: they go through /board-reply, whose insert body was
// { post_id, body, is_admin }. So the one person answering every question was the
// only one who could not attach a photo to the answer.
//
// The client (index.html, doReply) sends image_url and, after the refresh, checks
// whether the reply came back carrying it — so until this is pasted it says
// "Reply posted, but the photo did not attach" rather than dropping it silently.
//
// NO DATABASE WORK NEEDED — verified read-only 19 Aug 2026:
//   * the table is public.feature_replies (other candidate names return PGRST205
//     "perhaps you meant ..."; this one returns a permission error, i.e. it exists)
//   * image_url is already stored and rendered for PUBLIC replies, so the column
//     is there — and it is the same table this admin route inserts into
//   * submit_feature_reply already carries the 5-arg p_image_url overload (a probe
//     naming all five params reached the function's own validation, not a 404,
//     and wrote no row)
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 1 of 2 — add this function at top level, next to boardPostId().
//
// The value lands in an <img src> every swimmer sees, so an arbitrary URL is not
// acceptable even from an authenticated admin. Anything failing validation is
// stored as null rather than rejected: a malformed link must never cost Marco the
// reply he just typed.

function boardReplyImage(v, SUPABASE_URL) {
  if (typeof v !== "string") return null;
  const u = v.trim();
  if (!u || u.length > 400) return null;
  const prefix = String(SUPABASE_URL).replace(/\/+$/, "") +
                 "/storage/v1/object/public/board-images/";
  if (u.indexOf(prefix) !== 0) return null;
  const name = u.slice(prefix.length);           // must be ONE flat filename
  if (!/^[A-Za-z0-9._-]+$/.test(name) || name.indexOf("..") !== -1) return null;
  return u;
}

// ─────────────────────────────────────────────────────────────────────────────
// EDIT 2 of 2 — in the /board-reply handler, the insert. FIND:
//
//     const ir = await fetch(`${SUPABASE_URL}/rest/v1/feature_replies`, {
//       method: "POST",
//       headers: sbHeaders(env, { "Prefer": "return=representation" }),
//       body: JSON.stringify({ post_id: postId, body: text, is_admin: true })
//     });
//
// REPLACE WITH:
//
//     const ir = await fetch(`${SUPABASE_URL}/rest/v1/feature_replies`, {
//       method: "POST",
//       headers: sbHeaders(env, { "Prefer": "return=representation" }),
//       body: JSON.stringify({
//         post_id: postId,
//         body: text,
//         is_admin: true,
//         image_url: boardReplyImage(payload.image_url, SUPABASE_URL)
//       })
//     });
//
// That is the whole change. The handler reads payload.* inline rather than
// destructuring, so there is nothing to add further up — the earlier draft of this
// file predicted a second edit there and was wrong. A reply with no photo sends
// image_url: null, which validates to null and inserts as null, exactly as the
// column already holds for text-only public replies.
//
// ─────────────────────────────────────────────────────────────────────────────
// BEFORE DEPLOYING — scheduled() branches on event.cron, so BOTH triggers must
// still be declared in Settings:
//     "0 18 * * *"   → postDailySentence
//     the hourly one → logHour + nsKeepTokenFresh
// A 30 Jul 2026 deploy silently stripped the daily-sentence cron and it went
// unnoticed for two days. Keep the KV binding (RATE) too, or /tune, /obs and the
// NS last-known-good cache all lose their store.
//
// TO VERIFY: Bay Talk with ?board=1, reply as Marco with a photo attached.
// Success is the photo under the reply. Failure is still explicit — the client
// prints "Reply posted, but the photo did not attach".
