// De-bundle the deployed Worker back into a maintainable source file.
//
// The deployed script is esbuild output, but it was built without minification, so
// comments, indentation and `const` are all intact — the only difference from source
// is a thin layer of scaffolding. Stripping that gives a file that is source in every
// practical sense, and it is derived from PRODUCTION, so unlike a hand-port it cannot
// silently omit a dashboard-only edit.
//
// Every removal is counted and printed; anything unexpected shows up as a count that
// does not match.
const fs = require('fs');
let s = fs.readFileSync(process.argv[2], 'utf8');
const n = {};
const cut = (re, key, rep) => { let c = 0; s = s.replace(re, (...a) => { c++; return rep === undefined ? '' : (typeof rep === 'function' ? rep(...a) : rep); }); n[key] = c; };

// 1. esbuild's helper preamble (__defProp / __name / __defProp2 / __name2).
cut(/^var __(?:defProp|name)2? = .*\n/gm, 'preamble helper lines');
// 2. The bundler's own file-marker comment.
cut(/^\/\/ worker\.patched\.js\n/gm, 'bundler file marker');
// 3. Standalone rename statements, e.g.  __name(wxConflict, "wxConflict");
cut(/^[ \t]*__name2?\([A-Za-z_$][A-Za-z0-9_$]*, *"[^"]*"\);?[ \t]*\n/gm, 'standalone rename statements');
// 4a. SINGLE-LINE wraps:  var F = /* @__PURE__ */ __name2((a) => b, "F");
//     Greedy `.*` so the split is on the LAST `, "…"` on the line — the expression
//     itself often contains quoted strings.
cut(/\/\* @__PURE__ \*\/ __name2?\((.*), "[^"]*"\);$/gm, 'single-line __name wraps', (m, inner) => inner + ';');

// 4b. MULTI-LINE wraps. Each closes with `}, "<its own declared name>");`, so the
//     opening and closing pair by NAME — which also resolves the nested case
//     (`pick` declared inside `extract`) without needing to balance parens.
{
  const lines = s.split('\n');
  let opened = 0, closed = 0;
  for (let i = 0; i < lines.length; i++) {
    const o = lines[i].match(/^(\s*)((?:const|var|let) ([A-Za-z_$][A-Za-z0-9_$]*) = )\/\* @__PURE__ \*\/ __name2?\((.*)$/);
    if (!o) continue;
    const [, indent, decl, name, rest] = o;
    const closeRe = new RegExp('^(\\s*)\\}, "' + name.replace(/\$/g, '\\$') + '"\\);\\s*$');
    let j = -1;
    for (let k = i + 1; k < lines.length; k++) if (closeRe.test(lines[k])) { j = k; break; }
    if (j < 0) continue;                       // unpaired: leave it, the check below reports it
    lines[i] = indent + decl + rest;
    lines[j] = lines[j].replace(closeRe, '$1};');
    opened++; closed++;
  }
  s = lines.join('\n');
  n['multi-line __name wraps'] = opened;
}
// 5. Default export back to its source shape.
cut(/var worker_patched_default = /g, 'default export decl', 'export default ');
cut(/^export \{\n\s*worker_patched_default as default\n\};\n/gm, 'default export re-export');
// 6. Dangling sourcemap pointer — the .map is not published.
cut(/^\/\/# sourceMappingURL=.*\n/gm, 'sourceMappingURL');
// 7. esbuild hoists top-level const/let to var. Restore `const`, but ONLY where the
//    binding is never reassigned — some of these are deliberately mutable module
//    state (CHL_TOKEN is a cached OAuth token that gets replaced on refresh), and a
//    blanket conversion turns that into a build error. Caught by the dry-run build,
//    which is why the build is part of this pipeline and not an afterthought.
//    A property write (`CHL_TOKEN.value = x`) is not a reassignment and does not count.
{
  let toConst = 0, keptVar = 0;
  s = s.replace(/^var ([A-Za-z_$][A-Za-z0-9_$]*) = /gm, (m, name) => {
    const reassign = new RegExp('^(?!var )\\s*' + name.replace(/\$/g, '\\$') + '\\s*=[^=]', 'm');
    if (reassign.test(s)) { keptVar++; return m; }
    toConst++;
    return 'const ' + name + ' = ';
  });
  n['top-level var -> const'] = toConst;
  n['left as var (reassigned)'] = keptVar;
}

// Tidy: collapse any 3+ blank runs the removals opened up.
cut(/\n{3,}/g, 'blank-line runs collapsed', '\n\n');
s = s.replace(/^\n+/, '') .replace(/\n*$/, '\n');

fs.writeFileSync(process.argv[3], s);
for (const k of Object.keys(n)) console.log('  ' + String(n[k]).padStart(4) + '  ' + k);
console.log('\nwrote ' + process.argv[3].split(/[\\/]/).pop() + ' — ' + s.split('\n').length + ' lines');
if (/__name|__defProp|worker_patched_default/.test(s)) {
  console.log('\nWARNING: scaffolding still present:');
  s.split('\n').forEach((l, i) => { if (/__name|__defProp|worker_patched_default/.test(l)) console.log('  ' + (i + 1) + ': ' + l.trim().slice(0, 120)); });
}
