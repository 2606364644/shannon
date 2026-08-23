#!/usr/bin/env node
/**
 * Shannon intake console — serves the "New repository" page and registers
 * uploaded zip archives under <repo-root>/repos/.
 *
 * Zero-build: plain Node with fflate as the only dependency.
 * Start with `pnpm --filter @shannon/ui start` (or `node apps/ui/server.mjs`).
 */

import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync } from 'node:fs';
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { unzipSync } from 'fflate';

const PORT = Number(process.env.PORT ?? 8234);
const HOST = process.env.HOST ?? '127.0.0.1';
const MAX_ARCHIVE_BYTES = 512 * 1024 * 1024;
const MAX_MANIFEST_ENTRIES = 200;

const UI_DIR = fileURLToPath(new URL('.', import.meta.url));
const REPOS_DIR = path.join(fileURLToPath(new URL('../../', import.meta.url)), 'repos');

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

async function readBody(req) {
  const declared = Number(req.headers['content-length'] ?? '0');
  if (Number.isFinite(declared) && declared > MAX_ARCHIVE_BYTES) {
    throw new HttpError(413, 'Archive exceeds the 512 MB intake limit');
  }
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > MAX_ARCHIVE_BYTES) {
      throw new HttpError(413, 'Archive exceeds the 512 MB intake limit');
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

/**
 * Normalize a zip entry name and reject anything that could escape the
 * destination directory (zip-slip). Returns null for unsafe names.
 */
function safeEntryName(name) {
  if (typeof name !== 'string' || name.length === 0) return null;
  if (name.startsWith('/') || name.includes('..')) return null;
  const normalized = path.posix.normalize(name.replace(/\\/g, '/'));
  if (normalized.startsWith('/') || normalized.split('/').includes('..')) return null;
  return normalized;
}

/**
 * Longest directory prefix shared by every entry ('' when files sit at the
 * archive root). Used to strip the wrapper folder GitHub-style zips carry.
 */
function commonRootDir(names) {
  let prefix = names[0].includes('/') ? names[0].slice(0, names[0].lastIndexOf('/')) : '';
  for (const name of names) {
    if (!name.includes('/')) return '';
    const dir = name.slice(0, name.lastIndexOf('/'));
    while (prefix !== '' && dir !== prefix && !dir.startsWith(`${prefix}/`)) {
      prefix = prefix.includes('/') ? prefix.slice(0, prefix.lastIndexOf('/')) : '';
    }
  }
  return prefix;
}

function slugify(name) {
  const slug = name
    .toLowerCase()
    .replace(/\.zip$/, '')
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
  return slug || 'repository';
}

async function uniqueRepoDir(base) {
  await mkdir(REPOS_DIR, { recursive: true });
  let candidate = base;
  let n = 2;
  while (existsSync(path.join(REPOS_DIR, candidate))) {
    candidate = `${base}-${n}`;
    n += 1;
  }
  await mkdir(path.join(REPOS_DIR, candidate), { recursive: true });
  return candidate;
}

function extractArchive(buf) {
  try {
    return unzipSync(buf, {
      filter: (file) => {
        if (safeEntryName(file.name) === null) {
          throw new HttpError(400, `Unsafe path in archive ("${file.name}") — intake refused`);
        }
        return !file.name.endsWith('/');
      },
    });
  } catch (err) {
    if (err instanceof HttpError) throw err;
    throw new HttpError(400, 'Not a readable zip archive — confirm the file is a valid .zip');
  }
}

async function registerArchive(fileName, buf) {
  if (buf.length === 0) {
    throw new HttpError(400, 'Empty upload — no archive received');
  }
  const sha256 = createHash('sha256').update(buf).digest('hex');
  const files = extractArchive(buf);
  const names = Object.keys(files);
  if (names.length === 0) {
    throw new HttpError(400, 'Archive contains no files');
  }

  const root = commonRootDir(names);
  const slugBase = root ? slugify(root.split('/')[0]) : slugify(fileName);
  const slug = await uniqueRepoDir(slugBase);
  const destRoot = path.join(REPOS_DIR, slug);
  const stripPrefix = root ? `${root}/` : '';

  const manifest = [];
  let unpackedBytes = 0;
  for (const name of names) {
    const data = files[name];
    const rel = safeEntryName(name).slice(stripPrefix.length);
    if (!rel) continue;
    const target = path.join(destRoot, rel);
    if (!target.startsWith(`${destRoot}${path.sep}`)) {
      throw new HttpError(400, `Unsafe path in archive ("${name}") — intake refused`);
    }
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, data);
    unpackedBytes += data.length;
    if (manifest.length < MAX_MANIFEST_ENTRIES) {
      manifest.push({ path: rel, bytes: data.length });
    }
  }

  return {
    slug,
    fileName,
    archiveBytes: buf.length,
    sha256,
    fileCount: names.length,
    unpackedBytes,
    manifest,
    manifestTruncated: names.length > manifest.length,
  };
}

async function listRepos() {
  try {
    const entries = await readdir(REPOS_DIR, { withFileTypes: true });
    const repos = entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort();
    return { repos, count: repos.length };
  } catch {
    return { repos: [], count: 0 };
  }
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);

    if (req.method === 'GET' && url.pathname === '/api/repos') {
      return json(res, 200, await listRepos());
    }

    if (req.method === 'POST' && url.pathname === '/api/repos') {
      const name = url.searchParams.get('name') ?? 'archive.zip';
      const buf = await readBody(req);
      const receipt = await registerArchive(name, buf);
      return json(res, 201, receipt);
    }

    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = await readFile(path.join(UI_DIR, 'index.html'));
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      return res.end(html);
    }

    return json(res, 404, { error: 'Not found' });
  } catch (err) {
    const status = err instanceof HttpError ? err.status : 500;
    const message = err instanceof HttpError ? err.message : 'Intake failed';
    return json(res, status, { error: message });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`Shannon intake console → http://${HOST}:${PORT}`);
  console.log(`Archives unpack under ${REPOS_DIR}`);
});
