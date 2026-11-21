// public/ 以下の静的ファイル配信（ディレクトリトラバーサル対策つき）
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import path from 'node:path';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
};

const CSP = [
  "default-src 'self'",
  "img-src 'self' data: blob:",
  "style-src 'self'",
  "script-src 'self'",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

export async function serveStatic(req, res, pathname, publicDir) {
  let rel;
  try {
    rel = decodeURIComponent(pathname);
  } catch {
    return sendText(res, 400, '不正なパスです。');
  }
  if (rel.includes('\0')) return sendText(res, 400, '不正なパスです。');
  if (rel.endsWith('/')) rel += 'index.html';

  const root = path.resolve(publicDir);
  const filePath = path.resolve(root, `.${rel}`);
  if (!filePath.startsWith(root + path.sep)) return sendText(res, 404, '見つかりません。');

  let info;
  try {
    info = await stat(filePath);
  } catch {
    return sendText(res, 404, '見つかりません。');
  }
  if (!info.isFile()) return sendText(res, 404, '見つかりません。');

  const type = MIME[path.extname(filePath).toLowerCase()] ?? 'application/octet-stream';
  res.writeHead(200, {
    'Content-Type': type,
    'Content-Length': info.size,
    'Cache-Control': 'no-cache',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    ...(type.startsWith('text/html') ? { 'Content-Security-Policy': CSP, 'X-Frame-Options': 'DENY' } : {}),
  });
  if (req.method === 'HEAD') return res.end();
  createReadStream(filePath).on('error', () => res.destroy()).pipe(res);
}

function sendText(res, status, text) {
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end(text);
}
