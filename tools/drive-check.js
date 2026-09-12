#!/usr/bin/env node
//
// drive-check — are the Google-Drive copies still in step with the repo?
//
// The working folder is a Drive-synced copy, not a clone, so every tracked file
// exists twice and nothing in the normal workflow reconciles them. The two that
// matter drift for different reasons and carry different risks, so they get
// different policies:
//
//   CLAUDE.md    FIXABLE. This is the copy LOADED as a session's instructions, so
//                staleness is not inert — it briefs the next session with rules the
//                code no longer follows. On 12 Sep 2026 it was 26 days behind and
//                briefed a session with the pre-20-Aug siteCheck rule while that
//                session was fixing exactly that drift in index.html. --fix writes
//                the committed copy across.
//
//   index.html   WARN ONLY. Never fixed, by design, and never changes the exit code.
//                Its APP_BUILD is stamped by CI in the repo and never syncs back, so
//                the two copies ALWAYS differ and a pass/fail gate here would cry
//                wolf on every run. The useful signal is how far behind it is, and
//                whether Drive holds lines the repo does not.
//
// Why index.html is never auto-fixed, in either direction:
//
//   Drive -> repo is the wholesale copy CLAUDE.md forbids outright. It has reached
//   main and destroyed work more than once.
//
//   repo -> Drive is safe for content but is a 25,000-line overwrite of a file in
//   someone's working folder, and it would silently discard an edit made there but
//   not yet applied to HEAD. Refreshing it is a judgement call for a human, so this
//   tool reports and stops.
//
// Reading the index.html warning: lines present ONLY in Drive look like Drive being
// ahead, and usually are not. On 12 Sep 2026 all 452 of them were code deliberately
// RETIRED from the repo — the calibration-pair logger, the My-call freeze rig and
// the camera-read helpers, all of whose Supabase tables have since been dropped.
// Copying that back would resurrect inserts against tables that no longer exist. So
// treat the count as "how much retired code is still sitting in Drive", and only
// after reading the lines as "someone's unpushed work".
//
//   node tools/drive-check.js                  # check both
//   node tools/drive-check.js --fix            # ...and write CLAUDE.md across
//   node tools/drive-check.js --fix --force    # ...even if Drive looks ahead
//   MANLY_DRIVE_DIR=/some/other/path node tools/drive-check.js
//
// Exit 0/1 is decided by CLAUDE.md alone. index.html only ever prints.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const DEFAULT_DRIVE_DIR = 'C:\\Users\\stica\\Google Drive\\VIZ\\APPS\\Manly swim';
const driveDir = process.env.MANLY_DRIVE_DIR || DEFAULT_DRIVE_DIR;

const args = process.argv.slice(2);
const wantFix = args.includes('--fix');
const wantForce = args.includes('--force');

let root;
try {
  root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
} catch (e) {
  console.error('drive-check: not inside the git clone — ' + e.message);
  process.exit(1);
}

const norm = s => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

// maxBuffer must be raised explicitly: index.html is ~1.4 MB and execFileSync
// defaults to 1 MB, so the default fails with ENOBUFS on the one file this tool
// most needs to read.
function committedText(rel) {
  return execFileSync('git', ['show', 'HEAD:' + rel], {
    cwd: root, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  });
}

// Lines on each side that the other does not have, blanks ignored.
function splitLines(repoText, driveText) {
  const repoSet = new Set(repoText.split('\n'));
  const driveSet = new Set(driveText.split('\n'));
  return {
    onlyRepo: [...repoSet].filter(l => l.trim() && !driveSet.has(l)),
    onlyDrive: [...driveSet].filter(l => l.trim() && !repoSet.has(l)),
  };
}

// ── CLAUDE.md — the fixable one ──────────────────────────────────────────────
function checkClaudeMd() {
  const rel = 'CLAUDE.md';
  const drivePath = path.join(driveDir, rel);
  const repoPath = path.join(root, rel);

  let committed;
  try {
    committed = committedText(rel);
  } catch (e) {
    console.error('drive-check: cannot read HEAD:' + rel + ' — ' + e.message);
    return 1;
  }

  // Write the committed text in CRLF, which is what both copies use today.
  const writeDrive = () =>
    fs.writeFileSync(drivePath, committed.replace(/\r?\n/g, '\r\n'), 'utf8');

  if (fs.existsSync(repoPath) && norm(fs.readFileSync(repoPath, 'utf8')) !== norm(committed)) {
    console.error('note: the working-tree CLAUDE.md differs from HEAD. This checks and writes the');
    console.error('      COMMITTED text — commit your edit first if you meant to publish it.');
  }

  if (!fs.existsSync(drivePath)) {
    console.error('DRIFT: no CLAUDE.md at ' + drivePath);
    console.error('       (set MANLY_DRIVE_DIR if the Drive folder moved)');
    if (wantFix) {
      writeDrive();
      console.log('fixed: wrote HEAD:CLAUDE.md -> Drive');
      return 0;
    }
    return 1;
  }

  const driveText = norm(fs.readFileSync(drivePath, 'utf8'));
  const repoText = norm(committed);

  if (driveText === repoText) {
    console.log('ok:    CLAUDE.md matches HEAD (' + repoText.split('\n').length + ' lines)');
    return 0;
  }

  const { onlyRepo, onlyDrive } = splitLines(repoText, driveText);
  console.error('DRIFT: CLAUDE.md differs from HEAD');
  console.error('  repo ' + repoText.split('\n').length + ' lines, drive ' + driveText.split('\n').length + ' lines');
  console.error('  only in repo : ' + onlyRepo.length + '  (Drive is behind by these)');
  console.error('  only in drive: ' + onlyDrive.length);

  if (onlyDrive.length) {
    console.error('');
    console.error('  Lines ONLY in the Drive copy — read these before fixing. If any is someone\'s');
    console.error('  unpushed edit rather than superseded text, commit it to the repo FIRST,');
    console.error('  because --fix overwrites Drive:');
    onlyDrive.slice(0, 20).forEach(l => console.error('    | ' + l));
    if (onlyDrive.length > 20) console.error('    | ... and ' + (onlyDrive.length - 20) + ' more');
  }

  if (!wantFix) {
    console.error('  Run with --fix to write the repo copy over the Drive one.');
    return 1;
  }
  if (onlyDrive.length && !wantForce) {
    console.error('');
    console.error('REFUSING to fix: the Drive copy has ' + onlyDrive.length + ' line(s) the repo does not.');
    console.error('  Drive may be AHEAD. Read them above, and if they are superseded text rather');
    console.error('  than unpushed work, re-run with --fix --force.');
    return 1;
  }

  writeDrive();
  console.log('fixed: wrote HEAD:CLAUDE.md -> ' + drivePath);
  return 0;
}

// ── index.html — warn only, never fixed, never affects the exit code ─────────
function warnIndexHtml() {
  const rel = 'index.html';
  const drivePath = path.join(driveDir, rel);

  let committed;
  try {
    committed = committedText(rel);
  } catch (e) {
    console.error('warn:  cannot read HEAD:' + rel + ' — ' + e.message);
    return;
  }
  if (!fs.existsSync(drivePath)) {
    console.error('warn:  no index.html at ' + drivePath);
    return;
  }

  const driveText = norm(fs.readFileSync(drivePath, 'utf8'));
  const repoText = norm(committed);

  const stamp = t => {
    const m = t.match(/APP_BUILD = '([^']*)'/);
    return m ? m[1] : '(none)';
  };
  const repoStamp = stamp(repoText);
  const driveStamp = stamp(driveText);

  if (driveText === repoText) {
    console.log('ok:    index.html matches HEAD exactly');
    return;
  }

  const { onlyRepo, onlyDrive } = splitLines(repoText, driveText);

  // APP_BUILD alone is the expected, harmless state: CI stamps the repo and that
  // never flows back to Drive. Say so plainly rather than calling it drift.
  const onlyStamp =
    onlyRepo.every(l => l.includes('APP_BUILD =')) &&
    onlyDrive.every(l => l.includes('APP_BUILD ='));
  if (onlyStamp) {
    console.log('ok:    index.html matches HEAD apart from APP_BUILD (expected — CI stamps the');
    console.log('       repo and that never syncs back to Drive)');
    return;
  }

  // Date the Drive copy from its stamp: YYYYMMDD-HHMMSS.
  let age = '';
  const d = driveStamp.match(/^(\d{4})(\d{2})(\d{2})-/);
  const r = repoStamp.match(/^(\d{4})(\d{2})(\d{2})-/);
  if (d && r) {
    const days = Math.round(
      (Date.UTC(+r[1], +r[2] - 1, +r[3]) - Date.UTC(+d[1], +d[2] - 1, +d[3])) / 86400000
    );
    if (days > 0) age = '  (~' + days + ' day' + (days === 1 ? '' : 's') + ' behind)';
  }

  console.error('warn:  index.html in Drive is out of step' + age);
  console.error('       drive APP_BUILD ' + driveStamp + '   repo ' + repoStamp);
  console.error('       lines only in repo : ' + onlyRepo.length);
  console.error('       lines only in drive: ' + onlyDrive.length +
                (onlyDrive.length ? '   <- read before assuming this is unpushed work' : ''));
  console.error('       NOT fixed, and this does not fail the check. Never copy the file');
  console.error('       wholesale in either direction — apply only the hunks you meant, onto');
  console.error('       HEAD, and gate on `git diff --stat HEAD`.');
}

const code = checkClaudeMd();
warnIndexHtml();
process.exit(code);
