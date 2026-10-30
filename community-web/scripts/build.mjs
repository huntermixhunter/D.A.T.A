import { build } from 'esbuild';
import { readFile, writeFile, mkdir, readdir, copyFile, lstat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const release = process.argv.includes('--release');
let config;
try { config = JSON.parse(await readFile(path.join(root, 'config.json'), 'utf8')); }
catch (error) {
  if (error.code !== 'ENOENT') throw error;
  if (release) throw new Error('Create config.json with the public Supabase settings before a release build.');
  config = JSON.parse(await readFile(path.join(root, 'config.example.json'), 'utf8'));
}
const allowedKeys = new Set(['supabaseUrl', 'supabasePublishableKey', 'turnstileSiteKey', 'publicUrl', 'registrationEnabled']);
if (Object.keys(config).some(key => !allowedKeys.has(key))) throw new Error('config.json contains unsupported fields. Only public settings belong here.');
let connect = "'self'";
if (config.supabaseUrl || config.supabasePublishableKey) {
  const origin = new URL(config.supabaseUrl);
  if (origin.protocol !== 'https:' || !/^[a-z0-9-]+\.supabase\.co$/.test(origin.hostname) || origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) throw new Error('Use the HTTPS Supabase project URL.');
  const key = config.supabasePublishableKey;
  if (typeof key !== 'string' || !key) throw new Error('A Supabase publishable key is required.');
  if (!key.startsWith('sb_publishable_')) {
    let decoded;
    try { decoded = JSON.parse(Buffer.from(key.split('.')[1], 'base64url')); } catch { /* rejected below */ }
    if (decoded?.role !== 'anon') throw new Error('Only a publishable or legacy anon key is allowed. Never use a secret or service-role key.');
  }
  config.supabaseUrl = origin.origin;
  connect += ' ' + origin.origin;
} else if (release) throw new Error('Release builds require a connected Supabase project.');
const canonical = new URL(config.publicUrl);
if (canonical.protocol !== 'https:' || canonical.username || canonical.password || canonical.pathname !== '/' || canonical.search || canonical.hash) throw new Error('publicUrl must be an HTTPS origin.');
if (typeof config.registrationEnabled !== 'boolean') throw new Error('registrationEnabled must be true or false. Enable only after email delivery is verified.');
if (config.turnstileSiteKey && !/^[a-zA-Z0-9_-]{20,100}$/.test(config.turnstileSiteKey)) throw new Error('Invalid Turnstile site key.');
if (release && (!config.turnstileSiteKey || /^[123]x0+/.test(config.turnstileSiteKey))) throw new Error('Release builds require a production Turnstile site key.');

await mkdir(dist, { recursive: true });
if ((await lstat(dist)).isSymbolicLink()) throw new Error('Refusing to write through a symlink for dist.');
const staticFiles = ['index.html', 'base.css', 'forum.css', 'favicon.svg'];
const outputFiles = new Set([...staticFiles, 'forum-app.js', 'forum-app.js.LEGAL.txt', 'config.json', '_headers', '_redirects']);
for (const item of await readdir(dist)) {
  if (!outputFiles.has(item)) throw new Error(`Unexpected output artifact: ${item}. Review it before building.`);
}
for (const file of staticFiles) await copyFile(path.join(root, 'public', file), path.join(dist, file));
await build({ entryPoints: [path.join(root, 'src', 'forum.js')], outfile: path.join(dist, 'forum-app.js'), bundle: true, minify: true, format: 'esm', target: 'es2022', sourcemap: false, legalComments: 'external' });
await writeFile(path.join(dist, 'config.json'), JSON.stringify(config, null, 2) + '\n');
const challengeOrigin = config.turnstileSiteKey ? ' https://challenges.cloudflare.com' : '';
const csp = `default-src 'none'; script-src 'self'${challengeOrigin}; frame-src ${challengeOrigin.trim() || "'none'"}; style-src 'self'; connect-src ${connect}${challengeOrigin}; img-src 'self'; font-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'`;
await writeFile(path.join(dist, '_headers'), `/*\n  Content-Security-Policy: ${csp}\n  Referrer-Policy: no-referrer\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY\n  Permissions-Policy: camera=(), microphone=(), geolocation=()\n  Cache-Control: public, max-age=0, must-revalidate\n/config.json\n  Cache-Control: no-store\n`);
await writeFile(path.join(dist, '_redirects'), '/forum / 302\n');
console.log(`${release ? 'Release' : 'Preview'} built: ${dist}`);
console.log(`Supabase: ${config.supabaseUrl ? 'configured' : 'not connected'}; registration: ${config.registrationEnabled ? 'open' : 'closed'}`);
