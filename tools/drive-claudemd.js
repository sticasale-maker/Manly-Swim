#!/usr/bin/env node
//
// drive-claudemd — is the Drive copy of CLAUDE.md still the repo's copy?
//
// There are two CLAUDE.md files and only one of them is authoritative. The repo's
// is the source of truth; the Drive one is what actually gets LOADED as project
// instructions for a session whose working folder is the Drive copy. So a stale
// Drive file does not sit there harmlessly — it briefs the next session with rules
// the code no longer follows, and nothing in the normal workflow ever corrects it.
//
// That is not hypothetical. On 12 Sep 2026 the Drive copy was 26 days behind: it
// still carried the pre-20-Aug siteCheck rule ("Surge = Washy" rather than "Surgy
// or Washy"), and a session was briefed with it while fixing that exact drift in
// index.html. It also still described sea temperature as single-source months after
// the labelled satellite fallback landed, cited CSS that had been orphaned, and was
// missing the shared-clone warnings entirely.
//
//   node tools/drive-claudemd.js                  # check; exit 1 if the two differ
//   node tools/drive-claudemd.js --fix            # write repo copy -> Drive
//   node tools/drive-claudemd.js --fix --force    # ...even if Drive looks ahead
//   MANLY_DRIVE_DIR=/some/other/path node tools/drive-claudemd.js
//
// Exit 0 = identical. Exit 1 = drift (or the Drive copy is missing).
//
// --fix REFUSES when the Drive file contains lines the repo does not have, because
// that is the one case where Drive might be AHEAD — another session's unpushed
// edit — and copying over it destroys work. CLAUDE.md warns about exactly this
// direction. Pass --force only once you have read the lines it prints and decided
// they are superseded text rather than someone's work.
//
// It reads and writes the COMMITTED text, never the working tree: an uncommitted
// local edit is not the source of truth, and letting one escape to Drive would turn
// it into instructions before anyone had reviewed it.
//
// Comparison ignores CR. The repo worktree and the Drive copy are both CRLF today,
// but the index stores LF, so a naive byte compare against `git show` reports a
// whole-file difference that is purely line endings.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const DEFAULT_DRIVE_DIR = 'C:\\Users\\stica\\Google Drive\\VIZ\\APPS\\Manly swim';
const driveDir = process.env.MANLY_DRIVE_DIR || DEFAULT_DRIVE_DIR;
const drivePath = path.join(driveDir, 'CLAUDE.md');

const args = process.argv.slice(2);
const wantFix = args.includes('--fix');
const wantForce = args.includes('--force');

let root;
try {
  root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
} catch (e) {
  console.error('drive-claudemd: not inside the git clone — ' + e.message);
  process.exit(1);
}
const repoPath = path.join(root, 'CLAUDE.md');

let committed;
try {
  committed = execFileSync('git', ['show', 'HEAD:CLAUDE.md'], { cwd: root, encoding: 'utf8' });
} catch (e) {
  console.error('drive-claudemd: cannot read HEAD:CLAUDE.md — ' + e.message);
  process.exit(1);
}

const norm = s => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

// Write the committed text in CRLF, which is what both copies use today.
function writeDrive() {
  fs.writeFileSync(drivePath, committed.replace(/\r?\n/g, '\r\n'), 'utf8');
}

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
    process.exit(0);
  }
  process.exit(1);
}

const driveText = norm(fs.readFileSync(drivePath, 'utf8'));
const repoText = norm(committed);

if (driveText === repoText) {
  console.log('ok: Drive CLAUDE.md matches HEAD (' + repoText.split('\n').length + ' lines)');
  process.exit(0);
}

// Report the SHAPE of the drift rather than a full diff: the useful facts are how
// far behind it is and which way it runs. The reader can open the two files.
const repoLines = new Set(repoText.split('\n'));
const driveLines = new Set(driveText.split('\n'));
const onlyDrive = [...driveLines].filter(l => l.trim() && !repoLines.has(l));
const onlyRepo = [...repoLines].filter(l => l.trim() && !driveLines.has(l));

console.error('DRIFT: Drive CLAUDE.md differs from HEAD');
console.error('  repo : ' + repoText.split('\n').length + ' lines');
console.error('  drive: ' + driveText.split('\n').length + ' lines');
console.error('  lines only in repo : ' + onlyRepo.length + '  (Drive is behind by these)');
console.error('  lines only in drive: ' + onlyDrive.length);

if (onlyDrive.length) {
  console.error('');
  console.error('  Lines present ONLY in the Drive copy — read these before fixing. If any is');
  console.error("  someone's unpushed edit rather than superseded text, commit it to the repo");
  console.error('  FIRST, because --fix overwrites Drive:');
  onlyDrive.slice(0, 20).forEach(l => console.error('    | ' + l));
  if (onlyDrive.length > 20) console.error('    | ... and ' + (onlyDrive.length - 20) + ' more');
}

if (!wantFix) {
  console.error('');
  console.error('  Run with --fix to write the repo copy over the Drive one.');
  process.exit(1);
}

if (onlyDrive.length && !wantForce) {
  console.error('');
  console.error('REFUSING to fix: the Drive copy has ' + onlyDrive.length + ' line(s) the repo does not.');
  console.error('  Drive may be AHEAD. Read them above, and if they are superseded text rather');
  console.error('  than unpushed work, re-run with --fix --force.');
  process.exit(1);
}

writeDrive();
console.log('fixed: wrote HEAD:CLAUDE.md -> ' + drivePath);
process.exit(0);
