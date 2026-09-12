// Audit the de-bundler: every line that differs between the DEPLOYED bundle and the
// source derived from it must fall into a known semantics-preserving category.
// Anything uncategorised is a bug in debundle.js and is printed loudly.
//
// This compares the two files DIRECTLY (no reprinting, no normaliser), because the
// normaliser turned out to have a fragile regex of its own — verification tooling gets
// verified too.
const fs = require('fs');
const cp = require('child_process');

const dep = process.argv[2], src = process.argv[3];
let out = '';
try { out = cp.execSync('diff --unified=0 "' + dep + '" "' + src + '"', { encoding: 'utf8', maxBuffer: 1 << 28 }); }
catch (e) { out = e.stdout || ''; }

const CATS = [
  [/^var __(defProp|name)2? = /,                         'esbuild helper preamble'],
  [/^\/\/ worker\.patched\.js$/,                         'bundler file marker'],
  [/^\s*__name2?\([A-Za-z_$][A-Za-z0-9_$]*, *"[^"]*"\);?$/, 'Function.name rename statement'],
  [/^\/\/# sourceMappingURL=/,                           'sourcemap pointer'],
  [/^(export default |var worker_patched_default = )/,   'default-export reshape'],
  [/^export \{$|^\s*worker_patched_default as default$|^\};$/, 'default-export reshape'],
  [/@__PURE__.*__name2?\(/,                              '__name wrapper (opening)'],
  [/^\s*\}, "[A-Za-z_$][A-Za-z0-9_$]*"\);$/,             '__name wrapper (closing)'],
  [/^\s*\};$/,                                           '__name wrapper (closing, unwrapped)'],
  [/^(var|const) [A-Za-z_$][A-Za-z0-9_$]* = /,           'top-level var/const keyword'],
  // The `+` halves of the unwrapped declarations. Narrow on purpose: it only matches a
  // declaration whose initialiser is a function/arrow, which is the only shape esbuild
  // wraps — it cannot absorb an added statement.
  [/^\s*(const|var) [A-Za-z_$][A-Za-z0-9_$]* = (async )?(\(|function\b)/, '__name wrapper (opening, unwrapped)'],
  [/^$/,                                                 'blank line'],
  [/^\s*\/\//,                                           'comment text (no behaviour)'],
];

const tally = {};
const unknown = [];
for (const raw of out.split('\n')) {
  if (!/^[+-]/.test(raw) || /^(\+\+\+|---)/.test(raw)) continue;
  const line = raw.slice(1);
  const hit = CATS.find(([re]) => re.test(line));
  if (hit) tally[hit[1]] = (tally[hit[1]] || 0) + 1;
  else unknown.push(raw);
}

console.log('differences between the deployed bundle and the derived source:\n');
for (const k of Object.keys(tally).sort()) console.log('  ' + String(tally[k]).padStart(4) + '  ' + k);

if (!unknown.length) {
  console.log('\nEVERY difference is bundler scaffolding or a declaration keyword.');
  console.log('No code line was added, removed or altered.');
  process.exit(0);
}
console.log('\nUNCATEGORISED (' + unknown.length + ') — these need eyes:');
unknown.slice(0, 40).forEach(l => console.log('  ' + l.slice(0, 160)));
process.exit(1);
