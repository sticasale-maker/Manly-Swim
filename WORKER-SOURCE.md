# The Worker source, and how it gets deployed

**Since 12 Sep 2026 the direction has flipped.** `worker.js` is now the canonical
source and it is deployed with `npx wrangler deploy` — verified working that day, with
both crons and the `RATE` binding re-declared from `wrangler.toml` (the deploy output
lists them, so you see it happen). Edit the source, deploy, done.

The dashboard editor still works, but prefer not to use it: an edit made there does not
reach `worker.js`, and the next `wrangler deploy` would revert it. If someone does edit
in the dashboard, the rescue path below pulls it back into the source.

## Rescue: getting the source back from the dashboard

`bold-rain-6ded` is edited in the Cloudflare dashboard (CLAUDE.md §6). What that
actually means, and why it bites:

**The dashboard editor edits the deployed bundle, not a source file.** The script
Cloudflare holds is esbuild output — it still has a `__defProp`/`__name` preamble and
a `// worker.patched.js` marker from a build that no longer exists anywhere. Every
dashboard edit since then has gone into *that*, so the bundle is the accumulated
truth and the source files drift behind it, silently.

`wrangler.toml` sets `main = "worker.js"`, and **`worker.js` is gitignored**: it
carries `WW_KEY`, `NS_CLIENT_SECRET` and `NS_TOKEN_DEFAULT` in plain text and this
repo is public. The working copy lives at `Manly swim/worker-NEW.js` in Drive and is
copied into the clone to deploy.

So a `wrangler deploy` publishes whatever that stale file says — and **reverts every
dashboard-only edit made since it was last refreshed**.

On 12 Sep 2026 the gap was: the whole `/chl` Sentinel-3 chlorophyll route (~200
lines), the bluebottle prompt rules and their two validator guards, the Washy
mitigation guard, the `/tune` bounds for `offshoreFloorKmh`/`offshoreMaxFrac`, and
the point-break `lineCheck` work. A deploy that day would have taken all of it out.

## Refreshing the source after a dashboard edit

```bash
# 1. Fetch the deployed script (Cloudflare MCP workers_get_worker_code, or the
#    dashboard's own download). It arrives multipart-wrapped: keep only the body
#    between the boundary lines.

# 2. Turn the bundle back into source. Every removal is counted; it warns if any
#    scaffolding survives.
node tools/worker-debundle.js deployed-bundle.js worker.js

# 3. Prove nothing but scaffolding changed. Must print "No code line was added,
#    removed or altered."
node tools/worker-audit.js deployed-bundle.js worker.js

# 4. Prove it still builds, and that wrangler picks up the RATE binding.
npx wrangler deploy --dry-run --outdir=dist

# 5. Copy worker.js back to "Manly swim/worker-NEW.js" in Drive so the two agree.
```

`worker-debundle.js` restores `const` only where a binding is never reassigned —
`CHL_TOKEN` is deliberately mutable module state and a blanket conversion breaks the
build. Step 4 is what catches that class of mistake, so do not skip it.

## Before you ever run a real deploy

Do steps 1–4 first. If the audit is clean, deploying is a no-op and the source is
safe to publish from. If it is not, the source is behind and deploying would revert
something.

Then follow the procedure in `wrangler.toml` itself — it lists both crons, the KV
namespace and the four post-deploy checks, all read off the dashboard rather than
guessed.

One known cost: the newer esbuild strips comments from the artifact, so the first
real `wrangler deploy` will leave a less readable bundle in the dashboard than the
one sitting there now. The comments live in the source either way; it only makes a
future de-bundle terser.

## Superseded copies

There are now exactly TWO Worker source files and they are kept identical: `worker.js` in the
clone (gitignored) and `Manly swim/worker-NEW.js` in Drive. If they ever disagree, neither
is trustworthy — re-derive from production using the steps above.

`worker-current.js` was deleted from the Drive app folder on 12 Sep 2026. Its name implied
it was current; it was a 2,099-line snapshot with no `/chl`, no `lineCheck` and a
superseded two-paragraph `SWIMSUM_SYS`, and it held nothing that is not in the source
today. It and the pre-reconcile originals of both working copies are kept in
`Manly swim/worker-pastes/pre-reconcile-2026-09-12/` — history, never a deploy source.
