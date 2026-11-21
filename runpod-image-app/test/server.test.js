import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createAppServer } from '../server.js';

const BASE = 'https://abc123-8188.proxy.runpod.net';

// ComfyUI の代わりに応答する偽の fetch
const calls = [];
async function fakeFetch(url, init = {}) {
  calls.push({ url, init });
  const u = new URL(url);
  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });

  if (init.headers?.Authorization && init.headers.Authorization !== `Basic ${Buffer.from('me:secret').toString('base64')}`) {
    return new Response('unauthorized', { status: 401 });
  }
  switch (u.pathname) {
    case '/system_stats':
      return json({
        system: { comfyui_version: '0.3.40' },
        devices: [{ name: 'cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync', vram_total: 25757220864, vram_free: 1 }],
      });
    case '/object_info/CheckpointLoaderSimple':
      return json({ CheckpointLoaderSimple: { input: { required: { ckpt_name: [['sd_xl_base_1.0.safetensors'], {}] } } } });
    case '/prompt': {
      const body = JSON.parse(init.body);
      if (body.prompt['4'].inputs.ckpt_name !== 'sd_xl_base_1.0.safetensors') {
        return json({
          error: { type: 'prompt_outputs_failed_validation', message: 'Prompt outputs failed validation' },
          node_errors: { 4: { class_type: 'CheckpointLoaderSimple', errors: [{ type: 'value_not_in_list', message: 'Value not in list' }] } },
        }, 400);
      }
      return json({ prompt_id: 'p-1', number: 1, node_errors: {} });
    }
    case '/history/p-1':
      return json({ 'p-1': { outputs: { 9: { images: [{ filename: 'kantan_00001_.png', subfolder: '', type: 'output' }] } }, status: { status_str: 'success', completed: true } } });
    case '/history/p-running':
      return json({});
    case '/view':
      return new Response(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), { status: 200, headers: { 'Content-Type': 'image/png' } });
    default:
      return new Response('not found', { status: 404 });
  }
}

describe('server API（偽ComfyUI）', () => {
  let server;
  let origin;

  before(async () => {
    server = createAppServer({ fetchImpl: fakeFetch });
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    origin = `http://127.0.0.1:${server.address().port}`;
  });
  after(() => new Promise((r) => server.close(r)));

  const post = async (path, body) => {
    const res = await fetch(origin + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { status: res.status, body: await res.json() };
  };
  const connection = { url: 'abc123', username: '', password: '' };
  const settings = {
    ckpt_name: 'sd_xl_base_1.0.safetensors', positive: 'a cat', negative: '', width: 1024, height: 1024,
    batch_size: 1, seed: -1, steps: 25, cfg: 5, sampler_name: 'dpmpp_2m_sde', scheduler: 'karras',
  };

  test('トップページを返す', async () => {
    const res = await fetch(`${origin}/`);
    assert.equal(res.status, 200);
    assert.match(res.headers.get('content-type'), /text\/html/);
    assert.match(await res.text(), /RunPod かんたん画像生成/);
  });

  test('public/ の外は読めない', async () => {
    const res = await fetch(`${origin}/..%2fserver.js`);
    assert.equal(res.status, 404);
  });

  test('接続前の画像取得は日本語エラー', async () => {
    const res = await fetch(`${origin}/api/image?filename=a.png`);
    assert.equal(res.status, 400);
    assert.match((await res.json()).error, /接続/);
  });

  test('test-connection: GPU名とVRAM', async () => {
    const r = await post('/api/test-connection', { connection });
    assert.equal(r.status, 200);
    assert.equal(r.body.gpu, 'NVIDIA GeForce RTX 4090');
    assert.equal(r.body.vramGB, 24);
    assert.equal(r.body.comfyuiVersion, '0.3.40');
    assert.equal(calls.at(-1).url, `${BASE}/system_stats`);
  });

  test('認証失敗は日本語エラー', async () => {
    const r = await post('/api/test-connection', { connection: { url: BASE, username: 'me', password: 'wrong' } });
    assert.equal(r.status, 401);
    assert.match(r.body.error, /認証/);
    const ok = await post('/api/test-connection', { connection: { url: BASE, username: 'me', password: 'secret' } });
    assert.equal(ok.status, 200);
  });

  test('不正なURLは日本語エラー', async () => {
    const r = await post('/api/test-connection', { connection: { url: 'http://example.com' } });
    assert.equal(r.status, 400);
    assert.match(r.body.error, /RunPod/);
  });

  test('models: モデル一覧', async () => {
    const r = await post('/api/models', { connection });
    assert.deepEqual(r.body.models, ['sd_xl_base_1.0.safetensors']);
  });

  test('generate: prompt_id と実際に使ったシードを返す', async () => {
    const r = await post('/api/generate', { connection, settings });
    assert.equal(r.status, 200);
    assert.equal(r.body.prompt_id, 'p-1');
    assert.ok(Number.isSafeInteger(r.body.seed) && r.body.seed >= 0);
    const sent = JSON.parse(calls.at(-1).init.body);
    assert.equal(sent.prompt['3'].inputs.seed, r.body.seed);

    const fixed = await post('/api/generate', { connection, settings: { ...settings, seed: 42 } });
    assert.equal(fixed.body.seed, 42);
  });

  test('generate: 設定の検証エラーとモデル不明エラー', async () => {
    const bad = await post('/api/generate', { connection, settings: { ...settings, steps: 500 } });
    assert.equal(bad.status, 400);
    assert.match(bad.body.error, /Steps/);
    const missing = await post('/api/generate', { connection, settings: { ...settings, ckpt_name: 'nope.safetensors' } });
    assert.equal(missing.status, 400);
    assert.match(missing.body.error, /モデル/);
  });

  test('status: running / done', async () => {
    assert.deepEqual((await post('/api/status', { connection, prompt_id: 'p-running' })).body, { ok: true, state: 'running' });
    const done = await post('/api/status', { connection, prompt_id: 'p-1' });
    assert.equal(done.body.state, 'done');
    assert.equal(done.body.images[0].filename, 'kantan_00001_.png');
  });

  test('image: 画像をそのまま中継する', async () => {
    const res = await fetch(`${origin}/api/image?filename=kantan_00001_.png&subfolder=&type=output`);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('content-type'), 'image/png');
    assert.deepEqual([...new Uint8Array(await res.arrayBuffer())], [0x89, 0x50, 0x4e, 0x47]);
    assert.equal(calls.at(-1).url, `${BASE}/view?filename=kantan_00001_.png&subfolder=&type=output`);
  });

  test('image: 不正なファイル名は弾く', async () => {
    const res = await fetch(`${origin}/api/image?filename=../secret.png`);
    assert.equal(res.status, 400);
  });

  test('別サイトからの POST と JSON 以外は拒否', async () => {
    const cross = await fetch(`${origin}/api/models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Origin: 'https://evil.example' },
      body: JSON.stringify({ connection }),
    });
    assert.equal(cross.status, 403);
    const plain = await fetch(`${origin}/api/models`, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: '{}' });
    assert.equal(plain.status, 415);
  });

  test('Podに届かないときは日本語エラー', async () => {
    const s = createAppServer({ fetchImpl: async () => { throw new TypeError('fetch failed'); } });
    await new Promise((r) => s.listen(0, '127.0.0.1', r));
    try {
      const res = await fetch(`http://127.0.0.1:${s.address().port}/api/test-connection`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ connection }),
      });
      assert.equal(res.status, 502);
      assert.match((await res.json()).error, /接続できませんでした/);
    } finally {
      await new Promise((r) => s.close(r));
    }
  });
});
