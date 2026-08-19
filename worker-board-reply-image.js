// ─────────────────────────────────────────────────────────────────────────────
// PASTE JOB — bold-rain-6ded, Cloudflare dashboard editor. NOT deployed from
// this repo (see CLAUDE.md §6: the Worker is shared with the Forecast App and is
// dashboard-managed). Nothing here changes an existing route's defaults; this is
// one additive field on /board-reply.
//
// WHY: Bay Talk replies already carry photos end to end — the public composer
// uploads to the `board-images` bucket, submit_feature_reply takes p_image_url,
// and the renderer draws r.image_url. Marco's own replies could not, because
// they do not use that RPC: they go through this admin route, which accepted
// only { post_id, body }. So the one person answering every question was the one
// person who could not attach a photo to the answer.
//
// The client (index.html, doReply) now sends image_url and, after the refresh,
// checks whether the reply came back carrying it. Until this patch is pasted it
// will say so out loud rather than dropping the photo silently.
//
// ─────────────────────────────────────────────────────────────────────────────
// FIND the /board-reply handler. It currently reads post_id / body / delete_id
// from the JSON body and inserts into the replies table with author_role
// 'admin'. Two edits, both additive:
//
//   1. read the new field alongside the others
//   2. include it in the insert
//
// NO DATABASE WORK IS NEEDED — verified read-only on 19 Aug 2026:
//   * the table is public.feature_replies (probing the other candidate names
//     returns PGRST205 "perhaps you meant ...", this one returns a permission
//     error, which means it exists)
//   * submit_feature_reply ALREADY takes p_image_url: a probe naming all five
//     params reached the function's own validation (400 "body length" from a
//     deliberately empty body) instead of 404/PGRST203, so the 5-arg overload is
//     there. No row was written.
//   * image_url is already populated and rendered for PUBLIC replies, so the
//     column exists and the renderer reads it.
// The gap is only this Worker route. If you want the belt-and-braces check anyway,
// this is idempotent and safe to run in the Supabase SQL editor:
//
//     alter table public.feature_replies
//       add column if not exists image_url text;
//
// ─────────────────────────────────────────────────────────────────────────────
// 1. WHERE THE BODY IS READ — add image_url to the destructure/reads:

//     const { post_id, body, delete_id } = payload;
// becomes
//     const { post_id, body, delete_id, image_url } = payload;

// ─────────────────────────────────────────────────────────────────────────────
// 2. WHERE THE ROW IS BUILT — validate and attach. Keep it strict: this route is
//    admin-authenticated, but the value still ends up in an <img src>, so accept
//    ONLY a URL inside our own public bucket. Anything else is stored as null
//    rather than rejected, so a malformed link can never cost Marco the reply he
//    just typed.

function boardReplyImage(image_url, SUPABASE_URL) {
  if (typeof image_url !== "string") return null;
  const u = image_url.trim();
  if (!u) return null;
  // Must be exactly our own public board-images prefix on https. No other host,
  // no data: URI, no path traversal above the bucket.
  const prefix = SUPABASE_URL.replace(/\/+$/, "") +
                 "/storage/v1/object/public/board-images/";
  if (u.indexOf(prefix) !== 0) return null;
  const name = u.slice(prefix.length);
  // One flat filename, the shape uploadPhoto() writes: p_<ts>_<rand>.jpg
  if (!/^[A-Za-z0-9._-]+$/.test(name) || name.indexOf("..") !== -1) return null;
  if (u.length > 400) return null;
  return u;
}

// Then in the insert, alongside post_id / body / author_role:
//
//     image_url: boardReplyImage(image_url, SUPABASE_URL),
//
// If the insert goes through PostgREST rather than a direct query, add the same
// key to the JSON body. If it calls an RPC, the RPC needs the extra parameter —
// mirror the p_image_url pattern submit_feature_reply already uses.
//
// ─────────────────────────────────────────────────────────────────────────────
// AFTER PASTING — re-declare BOTH crons before deploying (CLAUDE.md §6: a
// 30 Jul 2026 deploy silently stripped the daily-sentence cron and it went
// unnoticed for two days), and keep the KV binding or /tune falls back to cache.
//
// TO VERIFY: open Bay Talk with ?board=1, reply as Marco with a photo attached.
// Success is the photo appearing under the reply. Failure is now explicit — the
// client prints "Reply posted, but the photo did not attach".
