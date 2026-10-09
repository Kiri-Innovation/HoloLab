import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(import.meta.url);
const frontend = path.resolve(path.dirname(script), '..');
const vendor = path.join(frontend, 'vendor/ColmapUtil');
const cache = path.join(frontend, 'vendor/.cache');
const target = path.join(frontend, 'public/colmaputil');
const depsStamp = path.join(vendor, 'node_modules/.hololab-deps.json');
const buildStamp = path.join(cache, 'build.json');
const lock = path.join(cache, 'build.lock');
const stage = path.join(cache, 'stage');
const backup = path.join(cache, 'previous');
const ensure = process.argv.includes('--ensure');
const recovery = '缺少 vendor 构建，请先运行 `npm run build:vendor`（在 hololab/frontend 中）。';

function run(command, args, cwd = vendor, capture = false) {
  const result = spawnSync(command, args, {
    cwd, encoding: 'utf8', stdio: capture ? 'pipe' : 'inherit',
    timeout: 600_000,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.error?.message || result.stderr || `exit ${result.status}`}`);
  }
  return result.stdout?.trimEnd() || '';
}
function git(args, cwd = vendor) { return run('git', args, cwd, true); }
function npm(args) {
  // npm_execpath avoids shell quoting and npm.cmd spawning differences on Windows.
  if (!process.env.npm_execpath) throw new Error('Please invoke this script through npm run build:vendor.');
  run(process.execPath, [process.env.npm_execpath, ...args]);
}
function hash(value) { return createHash('sha256').update(value).digest('hex'); }
function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}
function writeJson(file, value) { fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); }
function checkSubmodules() {
  const help = 'Run `git submodule update --init --recursive` from the HoloLab repository root.';
  if (!fs.existsSync(path.join(vendor, '.git'))) throw new Error(`ColmapUtil submodule is missing. ${help}`);
  const status = git(['submodule', 'status', '--recursive']);
  if (status.split('\n').some(line => /^[-U]/.test(line))) {
    throw new Error(`Recursive submodule is missing or conflicted. ${help}\n${status}`);
  }
  if (!fs.existsSync(path.join(vendor, 'src/HoloEngineRuntime/.git'))) {
    throw new Error(`HoloEngineRuntime submodule is missing. ${help}`);
  }
}
function sourceKey(root = vendor) {
  const digest = createHash('sha256');
  digest.update(git(['rev-parse', 'HEAD'], root));
  // Includes dirty tracked files, untracked source and nested submodule contents;
  // gitignored dist, node_modules and local caches never invalidate the build.
  const files = [...new Set(git(['ls-files', '-z', '--cached', '--others', '--exclude-standard'], root).split('\0').filter(Boolean))].sort();
  for (const file of files) {
    const full = path.join(root, file);
    digest.update(file + '\0');
    if (!fs.existsSync(full)) { digest.update('missing'); continue; }
    if (fs.statSync(full).isDirectory()) digest.update(sourceKey(full));
    else digest.update(fs.readFileSync(full));
  }
  return digest.digest('hex');
}
function manifest(root) {
  const entries = {};
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else entries[path.relative(root, full).split(path.sep).join('/')] = hash(fs.readFileSync(full));
    }
  }
  walk(root);
  return Object.fromEntries(Object.entries(entries).sort());
}
function validOutput(files) {
  try { return JSON.stringify(manifest(target)) === JSON.stringify(files); } catch { return false; }
}

let locked = false;
try {
  checkSubmodules();
  fs.mkdirSync(cache, { recursive: true });
  try { fs.mkdirSync(lock); locked = true; }
  catch { throw new Error(`Vendor build lock exists: ${lock}. Wait for the other build; remove this directory only if no build is running.`); }
  // Recover a process interrupted between the two directory renames.
  if (fs.existsSync(backup) && !fs.existsSync(target)) fs.renameSync(backup, target);
  const dependencyKey = hash(JSON.stringify({
    package: hash(fs.readFileSync(path.join(vendor, 'package.json'))),
    lock: hash(fs.readFileSync(path.join(vendor, 'package-lock.json'))),
    node: process.version, npm: process.env.npm_config_user_agent,
    platform: process.platform, arch: process.arch,
  }));
  const dependenciesReady = readJson(depsStamp)?.key === dependencyKey
    && fs.existsSync(path.join(vendor, 'node_modules/vite/bin/vite.js'))
    && fs.existsSync(path.join(vendor, 'node_modules/.package-lock.json'));
  const key = hash(sourceKey() + dependencyKey + hash(fs.readFileSync(script)));
  const previous = readJson(buildStamp);
  if (ensure && dependenciesReady && previous?.key === key && validOutput(previous.files)) {
    console.log('[vendor] ColmapUtil is current; skipping build.');
  } else {
    if (!dependenciesReady) {
      console.log('[vendor] Dependencies missing or changed; running npm ci.');
      fs.rmSync(depsStamp, { force: true });
      try { npm(['ci']); }
      catch (error) { throw new Error(`ColmapUtil dependency installation failed. Check network/npm registry and retry.\n${error.message}`); }
      writeJson(depsStamp, { key: dependencyKey });
    }
    console.log('[vendor] Building ColmapUtil with base=/colmaputil/.');
    npm(['run', 'build:embed']);
    const dist = path.join(vendor, 'dist');
    fs.rmSync(path.join(dist, 'colmaputil-send.vsix'), { force: true });
    fs.rmSync(stage, { recursive: true, force: true });
    fs.cpSync(dist, stage, { recursive: true, filter: src => !src.endsWith('.vsix') });
    const html = fs.readFileSync(path.join(stage, 'index.html'), 'utf8');
    const assets = [...html.matchAll(/(?:src|href)="(\/colmaputil\/[^"?#]+)[^"]*"/g)];
    if (!html.includes('/colmaputil/assets/') || !assets.length) throw new Error('Invalid ColmapUtil embed base or missing assets.');
    for (const [, url] of assets) {
      if (!fs.statSync(path.join(stage, url.slice('/colmaputil/'.length))).isFile()) throw new Error(`Missing asset: ${url}`);
    }
    const files = manifest(stage);
    if (Object.keys(files).some(file => file.endsWith('.vsix'))) throw new Error('Unexpected VSIX in staged output.');
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.rmSync(backup, { recursive: true, force: true });
    if (fs.existsSync(target)) fs.renameSync(target, backup);
    try { fs.renameSync(stage, target); }
    catch (error) {
      if (fs.existsSync(backup)) fs.renameSync(backup, target);
      throw error;
    }
    writeJson(buildStamp, { key, files });
    fs.rmSync(backup, { recursive: true, force: true });
    console.log(`[vendor] Published ${Object.keys(files).length} files; VSIX excluded.`);
  }
} catch (error) {
  console.error(`[vendor] ${error.message}\n${recovery}`);
  process.exitCode = 1;
} finally {
  if (locked) {
    fs.rmSync(stage, { recursive: true, force: true });
    fs.rmSync(lock, { recursive: true, force: true });
  }
}
