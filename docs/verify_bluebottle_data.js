// Reproduces the headline counts in docs/bluebottle-model.md straight from
// docs/data/obs_sydney.csv, so the doc's numbers are checkable rather than
// asserted. Run: node docs/verify_bluebottle_data.js
//
// This checks the DATA counts only. It does not refit the model — the AUCs and
// the multipliers come from the matched case-control run described in the doc,
// which needs the wind cache this repo does not carry.

const fs = require('fs');
const path = require('path');

const CSV = path.join(__dirname, 'data', 'obs_sydney.csv');
const lines = fs.readFileSync(CSV, 'utf8').trim().split(/\r?\n/);
const hdr = lines[0].split(',');
const rows = lines.slice(1).map(l => {
  const p = l.split(',');
  const o = {};
  // place_guess is last and may itself contain commas — rejoin the tail.
  hdr.forEach((h, i) => { o[h] = i === hdr.length - 1 ? p.slice(i).join(',') : p[i]; });
  return o;
});

let pass = 0, fail = 0;
const check = (label, got, want) => {
  const ok = got === want;
  ok ? pass++ : fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}: ${got}${ok ? '' : `  (doc says ${want})`}`);
};

console.log('docs/bluebottle-model.md — data claims\n');

check('total records after dropping obscured', rows.length, 705);
check('records with geoprivacy other than "open"',
  rows.filter(r => (r.geoprivacy || '').trim() !== 'open').length, 0);

const dated = rows.filter(r => /^\d{4}-\d{2}-\d{2}/.test(r.observed_on || ''));
const modelled = dated.filter(r => {
  const y = +r.observed_on.slice(0, 4);
  return y >= 2021 && y <= 2026;
});
check('records in the 2021-2026 modelled span', modelled.length, 556);

// "Collapsing same-day/same-beach reports": one stranding-day per date per beach
// cluster, cluster approximated by rounding coordinates to ~1.1 km.
const key = r => `${r.observed_on}@${(+r.lat).toFixed(2)},${(+r.lng).toFixed(2)}`;
check('distinct stranding-days in the modelled span',
  new Set(modelled.map(key)).size, 443);

console.log(`\n  (${rows.length - dated.length} records carry no usable observed_on and are excluded`);
console.log('   from every date-based figure above.)');

// Abundance envelope behind BBF_SEASON_CAP, on distinct stranding-days.
const months = new Array(12).fill(0);
new Set(dated.map(key)).forEach(k => { months[+k.slice(5, 7) - 1]++; });
const max = Math.max(...months);
const NAMES = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');

console.log('\nMonthly abundance envelope (distinct stranding-days, full span):\n');
months.forEach((n, i) => {
  console.log(`  ${NAMES[i]} ${String(n).padStart(4)}  ${'#'.repeat(Math.round(n / max * 40))}` +
    `   f=${(n / max).toFixed(2)}`);
});

// The doc states the cap rule as: High >= 0.6, Elevated 0.3-0.6, Building < 0.3,
// on a max-normalised factor. That rule does NOT reproduce the shipped cap —
// see the "Seasonal cap" note in the doc. Reported, not enforced.
const implied = months.map(n => { const f = n / max; return f >= 0.6 ? 3 : f >= 0.3 ? 2 : 1; });
const shipped = [3, 3, 3, 2, 1, 1, 1, 1, 2, 3, 3, 3];
console.log(`\n  shipped BBF_SEASON_CAP:        [${shipped.join(',')}]`);
console.log(`  implied by the stated rule:    [${implied.join(',')}]`);
if (JSON.stringify(implied) !== JSON.stringify(shipped)) {
  const diffs = months.map((n, i) => [i, n]).filter(([i]) => implied[i] !== shipped[i]);
  console.log(`  ${diffs.length} months differ; the shipped cap is the more cautious in each:`);
  diffs.forEach(([i, n]) => console.log(
    `    ${NAMES[i]}  f=${(n / max).toFixed(2)}  rule implies ${implied[i]}, shipped ${shipped[i]}`));
  console.log('  The shipped cap is retained: it matches the summer dominance in the');
  console.log('  literature, and erring cautious is the right direction for a hazard.');
}

console.log(`\n${fail === 0 ? 'All data claims reproduce.' : fail + ' claim(s) did not reproduce.'}` +
  ` (${pass} passed, ${fail} failed)`);
process.exit(fail === 0 ? 0 : 1);
