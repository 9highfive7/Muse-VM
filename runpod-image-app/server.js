// RunPod かんたん画像生成 — サーバー本体（外部依存なし）
// ブラウザから直接 ComfyUI を呼ぶと CORS で止まるため、このサーバーが ComfyUI API を中継する。
import http from 'node:http';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import {
  UserError,
  normalizeBaseUrl,
  validateSettings,
  buildWorkflow,
  parseSystemStats,
  parseCheckpointList,
  parseHistory,
  explainPromptError,
  basicAuthHeader,
} from './lib/comfy.js';
import { serveStatic } from './lib/static.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const MAX_BODY = 1024 * 1024;
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * サーバーを作る（listen はしない）。
 * @param {object} [opts]
 * @param {typeof fetch} [opts.fetchImpl] テスト用に差し替え可能な fetch
 * @param {string} [opts.publicDir]
 * @param {boolean} [opts.checkHost] Host ヘッダーを localhost 系に限定する（DNSリバインディング対策）
 */
export function createAppServer({ fetchImpl = globalThis.fetch, publicDir = path.join(here, 'public'), checkHost = false } = {}) {
  // 現在の接続先。メモリ上だけに保持し、ディスクには一切書かない。
  let connection = null;

  function useConnection(body) {
    const c = body?.connection;
    if (c && typeof c === 'object') {
      const base = normalizeBaseUrl(c.url);
      const username = typeof c.username === 'string' ? c.username.trim() : '';
      const password = typeof c.password === 'string' ? c.password : '';
      if (username.includes(':')) throw new UserError('ユーザー名に「:」は使えません。');
      connection = { base, username, password };
    }
    if (!connection) throw new UserError('接続先が設定されていません。先に「接続テスト」をしてください。');
    return connection;
  }

  async function upstream(conn, pathAndQuery, { method = 'GET', body, timeoutMs = 20000, accept = 'application/json' } = {}) {
    const headers = { Accept: accept };
    const auth = basicAuthHeader(conn.username, conn.password);
    if (auth) headers.Authorization = auth;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      return await fetchImpl(conn.base + pathAndQuery, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
        throw new UserError('Podからの応答がタイムアウトしました。Podが起動中か、混み合っていないか確認してください。', 504);
      }
      throw new UserError('Podに接続できませんでした。URLが正しいか、Podが起動中（Running）か確認してください。', 502);
    }
  }

  function statusError(status, notFoundMessage) {
    if (status === 401 || status === 403) {
      return new UserError('認証に失敗しました。ユーザー名とパスワードを確認してください（認証を設定していないPodなら空欄にします）。', 401);
    }
    if (status === 404) {
      return new UserError(notFoundMessage ?? 'ComfyUIが見つかりません。ポート8188のURLか確認してください。', 502);
    }
    if (status === 502 || status === 503 || status === 504) {
      return new UserError('Podは見つかりましたが、ComfyUIがまだ準備中のようです。Pod起動直後は数分かかることがあるので、少し待ってからもう一度お試しください。', 502);
    }
    if (status >= 300 && status < 400) {
      return new UserError('Podが別のページへ転送しようとしました。ComfyUIのURL（ポート8188）か確認してください。', 502);
    }
    return new UserError(`ComfyUIがエラーを返しました（HTTP ${status}）。`, 502);
  }

  async function readJson(res) {
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch {
      throw new UserError('ComfyUIの応答ではないようです。ポート8188のURLか確認してください。', 502);
    }
  }

  async function getJson(conn, pathAndQuery, notFoundMessage) {
    const res = await upstream(conn, pathAndQuery);
    if (!res.ok) {
      await res.body?.cancel().catch(() => {});
      throw statusError(res.status, notFoundMessage);
    }
    return readJson(res);
  }

  const api = {
    async 'test-connection'(body) {
      const conn = useConnection(body);
      const stats = parseSystemStats(await getJson(conn, '/system_stats'));
      return { ok: true, base: conn.base, ...stats };
    },

    async models(body) {
      const conn = useConnection(body);
      const models = parseCheckpointList(await getJson(conn, '/object_info/CheckpointLoaderSimple'));
      return { ok: true, models };
    },

    async generate(body) {
      const conn = useConnection(body);
      const settings = validateSettings(body?.settings);
      const workflow = buildWorkflow(settings);
      const res = await upstream(conn, '/prompt', {
        method: 'POST',
        body: { prompt: workflow, client_id: 'kantan-image-app' },
        timeoutMs: 30000,
      });
      if (res.status === 400) throw new UserError(explainPromptError(await readJson(res).catch(() => null)), 400);
      if (!res.ok) {
        await res.body?.cancel().catch(() => {});
        throw statusError(res.status);
      }
      const json = await readJson(res);
      if (json?.node_errors && Object.keys(json.node_errors).length) {
        throw new UserError(explainPromptError(json), 400);
      }
      if (typeof json?.prompt_id !== 'string' || !json.prompt_id) {
        throw new UserError('ComfyUIから受付番号（prompt_id）が返りませんでした。', 502);
      }
      return { ok: true, prompt_id: json.prompt_id, seed: settings.seed, seedWasRandom: settings.seedWasRandom };
    },

    async status(body) {
      const conn = useConnection(body);
      const id = body?.prompt_id;
      if (typeof id !== 'string' || !/^[A-Za-z0-9-]{1,100}$/.test(id)) {
        throw new UserError('prompt_id が正しくありません。');
      }
      const json = await getJson(conn, `/history/${encodeURIComponent(id)}`);
      return { ok: true, ...parseHistory(json, id) };
    },
  };

  async function handleImage(req, res, url) {
    if (!connection) throw new UserError('接続先が設定されていません。先に「接続テスト」をしてください。');
    const filename = url.searchParams.get('filename') ?? '';
    const subfolder = url.searchParams.get('subfolder') ?? '';
    const type = url.searchParams.get('type') || 'output';
    if (!filename || filename.length > 255 || /[\\/]/.test(filename) || filename.startsWith('.')) {
      throw new UserError('画像ファイル名が正しくありません。');
    }
    if (subfolder.length > 255 || subfolder.split(/[\\/]/).includes('..')) {
      throw new UserError('サブフォルダ名が正しくありません。');
    }
    if (!['output', 'temp', 'input'].includes(type)) throw new UserError('画像の種類（type）が正しくありません。');

    const qs = new URLSearchParams({ filename, subfolder, type });
    const up = await upstream(connection, `/view?${qs}`, { timeoutMs: 120000, accept: 'image/*' });
    if (!up.ok) {
      await up.body?.cancel().catch(() => {});
      throw statusError(up.status, '画像が見つかりません。Podを削除・作り直した場合、以前の画像は消えています。');
    }
    const contentType = up.headers.get('content-type') ?? '';
    if (!/^image\//i.test(contentType)) {
      await up.body?.cancel().catch(() => {});
      throw new UserError('画像ではない応答が返りました。', 502);
    }
    const headers = {
      'Content-Type': contentType,
      'Cache-Control': 'private, max-age=3600',
      'X-Content-Type-Options': 'nosniff',
    };
    const len = up.headers.get('content-length');
    if (len && /^\d+$/.test(len)) headers['Content-Length'] = len;
    res.writeHead(200, headers);
    if (req.method === 'HEAD' || !up.body) {
      await up.body?.cancel().catch(() => {});
      return res.end();
    }
    try {
      await pipeline(Readable.fromWeb(up.body), res);
    } catch {
      res.destroy();
    }
  }

  async function readBody(req) {
    const chunks = [];
    let size = 0;
    for await (const chunk of req) {
      size += chunk.length;
      if (size > MAX_BODY) throw new UserError('送信データが大きすぎます。', 413);
      chunks.push(chunk);
    }
    const text = Buffer.concat(chunks).toString('utf8');
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch {
      throw new UserError('送信データ（JSON）の形式が正しくありません。');
    }
  }

  function sendJson(res, status, obj) {
    const body = JSON.stringify(obj);
    res.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(body),
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    res.end(body);
  }

  function hostAllowed(req) {
    if (!checkHost) return true;
    const host = (req.headers.host ?? '').toLowerCase().replace(/:\d+$/, '');
    return LOOPBACK_HOSTS.has(host);
  }

  function sameOrigin(req) {
    const origin = req.headers.origin;
    if (!origin) return true;
    try {
      return new URL(origin).host === req.headers.host;
    } catch {
      return false;
    }
  }

  async function handle(req, res) {
    const url = new URL(req.url ?? '/', 'http://localhost');
    try {
      if (!hostAllowed(req)) throw new UserError('このホスト名からのアクセスは許可されていません。http://localhost:ポート番号 で開いてください。', 403);

      if (url.pathname === '/api/image') {
        if (req.method !== 'GET' && req.method !== 'HEAD') throw new UserError('GET で呼び出してください。', 405);
        return await handleImage(req, res, url);
      }

      if (url.pathname.startsWith('/api/')) {
        const name = url.pathname.slice('/api/'.length);
        const fn = Object.hasOwn(api, name) ? api[name] : null;
        if (!fn) throw new UserError('そのAPIはありません。', 404);
        if (req.method !== 'POST') throw new UserError('POST で呼び出してください。', 405);
        if (!sameOrigin(req)) throw new UserError('別のサイトからの呼び出しは許可されていません。', 403);
        if (!/^application\/json\b/i.test(req.headers['content-type'] ?? '')) {
          throw new UserError('Content-Type は application/json にしてください。', 415);
        }
        const body = await readBody(req);
        return sendJson(res, 200, await fn(body));
      }

      if (req.method !== 'GET' && req.method !== 'HEAD') throw new UserError('GET で呼び出してください。', 405);
      return await serveStatic(req, res, url.pathname, publicDir);
    } catch (err) {
      if (res.headersSent) return res.destroy();
      if (err instanceof UserError) return sendJson(res, err.status, { ok: false, error: err.message });
      console.error('[server] 予期しないエラー:', err);
      return sendJson(res, 500, { ok: false, error: 'サーバー内部でエラーが起きました。ターミナルのログを確認してください。' });
    }
  }

  return http.createServer((req, res) => {
    handle(req, res);
  });
}

// `node server.js` で直接起動されたときだけ listen する（テストからは import のみ）
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const port = Number(process.env.PORT) || 3000;
  const host = process.env.HOST || '127.0.0.1';
  const loopback = host === '127.0.0.1' || host === 'localhost' || host === '::1';
  const server = createAppServer({ checkHost: loopback });
  server.listen(port, host, () => {
    const shown = host.includes(':') ? `[${host}]` : host;
    console.log(`RunPod かんたん画像生成: http://${shown === '127.0.0.1' ? 'localhost' : shown}:${port} を開いてください`);
    if (!loopback) console.warn('注意: localhost 以外で待ち受けています。同じネットワークの他人も接続情報を使えてしまうので、個人用の安全な環境でのみ使ってください。');
  });
}
