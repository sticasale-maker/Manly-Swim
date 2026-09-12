#!/usr/bin/env node
//
// bake-check — does SITE_DEFAULTS still agree with the live Worker KV tune?
//
// CLAUDE.md §7 is a standing rule: whenever a LOCAL tune knob changes, the value
// gets written into the SITE defaults in index.html. The rule exists because the
// two stores fail apart SILENTLY. loadLiveTune() overlays KV at boot and caches it
// in localStorage, so an online device scores on the KV value and nobody ever sees
// the default — until a first open on a dead connection in a car park (§9), which
// scores on whatever the code says. That device is then the only one running a
// different model, and there is no symptom to notice.
//
// It found exactly that on 12 Sep 2026: offshoreMaxPts and offshoreFloorKmh had
// been tuned in the panel and pushed to KV on 27 Aug, but the last bake was from
// the 25 Aug save, so code said 21/0 while every online device used 18/3. Two
// weeks, no symptom.
//
//   node tools/bake-check.js             # check the working copy
//   node tools/bake-check.js --live      # check what is actually deployed
//   node tools/bake-check.js path/to/index.html
//
// Exit 0 = every KV knob matches. Exit 1 = drift, and it names the knobs. Run it
// before any push that touches the knob block, and after any tune-panel push.
//
// A knob that is live-pushable but has never been pushed is listed separately and
// is NOT a failure: KV has no opinion about it, so the code is already its only
// home and there is nothing to bake.

const fs = require('fs');
const https = require('https');

const TUNE_URL = 'https://bold-rain-6ded.sticasale.workers.dev/tune';
const LIVE_URL = 'https://app.viz.net.au/Manly-Swim/index.html';

const arg = process.argv[2];
const wantLive = arg === '--live';

function get(url) {
  return new Promise((resolve, reject) => {
    https.get(url + (url.includes('?') ? '&' : '?') + 'cb=' + Date.now(), res => {
      if (res.statusCode !== 200) return reject(new Error(url + ' -> HTTP ' + res.statusCode));
      let s = '';
      res.on('data', d => s += d);
      res.on('end', () => resolve(s));
    }).on('error', reject);
  });
}

// Read a numeric knob out of the SITE_DEFAULTS object literal only. Deliberately
// NOT a whole-file search: several of these names also appear in the tune-panel
// slider specs and in prose inside comments, and matching one of those would
// report a knob as baked while the real default sat somewhere else entirely.
function makeReader(html) {
  const from = html.indexOf('const SITE_DEFAULTS');
  if (from < 0) throw new Error('SITE_DEFAULTS not found — wrong file?');
  const to = html.indexOf('\n};', from);
  const block = html.slice(from, to);
  return key => {
    const m = block.match(new RegExp('(^|\\n)\\s*' + key + ':\\s*(-?[0-9.]+)\\s*,'));
    return m ? parseFloat(m[2]) : undefined;
  };
}

function liveKeys(html) {
  const m = html.match(/const TUNE_LIVE_KEYS = \[([\s\S]*?)\]/);
  return m ? [...m[1].matchAll(/'([^']+)'/g)].map(x => x[1]) : [];
}

(async () => {
  const html = wantLive ? await get(LIVE_URL)
                        : fs.readFileSync(arg || 'index.html', 'utf8');
  const build = (html.match(/var APP_BUILD = '([^']*)'/) || [])[1] || '(unstamped)';
  const tune = JSON.parse(await get(TUNE_URL));
  const kv = tune.values || {};
  const defaultOf = makeReader(html);

  console.log((wantLive ? 'deployed' : (arg || 'index.html')) + '  build ' + build);
  console.log('live KV saved ' + tune.savedAt + ' by ' + tune.by + '\n');

  const drifted = [];
  const absent = [];
  for (const k of Object.keys(kv).sort()) {
    const d = defaultOf(k);
    let state;
    if (d === undefined) { state = 'NOT IN SITE_DEFAULTS'; absent.push(k); }
    else if (d === kv[k]) { state = 'baked'; }
    else { state = 'DRIFT'; drifted.push(k + ' (KV ' + kv[k] + ', code ' + d + ')'); }
    console.log('  ' + k.padEnd(18) +
                'KV ' + String(kv[k]).padStart(7) +
                '   code ' + String(d === undefined ? '-' : d).padStart(7) +
                '   ' + state);
  }

  const never = liveKeys(html).filter(k => !(k in kv));
  if (never.length) {
    console.log('\n  live-pushable but never pushed (code is the only home, nothing to bake):');
    for (const k of never) console.log('    ' + k.padEnd(18) + 'code ' + defaultOf(k));
  }

  if (!drifted.length && !absent.length) {
    console.log('\nevery KV knob matches SITE_DEFAULTS — §7 satisfied');
    process.exit(0);
  }
  console.log('');
  if (drifted.length) console.log('DRIFT — bake these into SITE_DEFAULTS: ' + drifted.join(', '));
  if (absent.length)  console.log('IN KV BUT NOT IN SITE_DEFAULTS: ' + absent.join(', '));
  console.log('§7 NOT satisfied');
  process.exit(1);
})().catch(e => { console.error('bake-check failed: ' + e.message); process.exit(2); });
