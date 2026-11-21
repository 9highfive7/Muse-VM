import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeBaseUrl,
  buildWorkflow,
  validateSettings,
  parseSystemStats,
  parseCheckpointList,
  parseHistory,
  explainPromptError,
  cleanGpuName,
  randomSeed,
  basicAuthHeader,
  MAX_SEED,
  UserError,
} from '../lib/comfy.js';

const BASE = 'https://abc123-8188.proxy.runpod.net';

describe('normalizeBaseUrl', () => {
  test('完全なURLはそのまま', () => {
    assert.equal(normalizeBaseUrl('https://abc123-8188.proxy.runpod.net'), BASE);
  });

  test('末尾のスラッシュを取り除く', () => {
    assert.equal(normalizeBaseUrl('https://abc123-8188.proxy.runpod.net/'), BASE);
  });

  test('前後の空白・大文字・スキームなしも受け付ける', () => {
    assert.equal(normalizeBaseUrl('  HTTPS://ABC123-8188.proxy.runpod.net/  '), BASE);
    assert.equal(normalizeBaseUrl('abc123-8188.proxy.runpod.net'), BASE);
  });

  test('8188以外のポートのURLはポートを保つ', () => {
    assert.equal(normalizeBaseUrl('https://abc123-3000.proxy.runpod.net'), 'https://abc123-3000.proxy.runpod.net');
  });

  test('Pod IDだけなら8188のURLにする', () => {
    assert.equal(normalizeBaseUrl('abc123'), BASE);
    assert.equal(normalizeBaseUrl(' ABC123 '), BASE);
  });

  test('不正な入力は日本語のエラーを投げる', () => {
    const bad = [
      '',
      '   ',
      undefined,
      null,
      42,
      'abc 123',
      'http://example.com',
      'https://abc123-8188.proxy.runpod.net.evil.com',
      'https://evil.com/abc123-8188.proxy.runpod.net',
      'https://abc123-8188.proxy.runpod.net:8443',
      'https://user:pw@abc123-8188.proxy.runpod.net',
      'ftp://abc123-8188.proxy.runpod.net',
      'javascript:alert(1)',
    ];
    for (const input of bad) {
      assert.throws(
        () => normalizeBaseUrl(input),
        (err) => err instanceof UserError && /[ぁ-んァ-ン一-龥]/.test(err.message),
        `should throw for ${JSON.stringify(input)}`,
      );
    }
  });
});

const SETTINGS = {
  ckpt_name: 'sd_xl_base_1.0.safetensors',
  positive: 'a cat on a windowsill, watercolor',
  negative: 'lowres, blurry',
  width: 1216,
  height: 832,
  batch_size: 2,
  seed: 123456789,
  steps: 25,
  cfg: 5.5,
  sampler_name: 'dpmpp_2m_sde',
  scheduler: 'karras',
};

const nodesOf = (wf, type) => Object.entries(wf).filter(([, n]) => n.class_type === type);
const one = (wf, type) => {
  const found = nodesOf(wf, type);
  assert.equal(found.length, 1, `${type} は1つだけのはず`);
  return found[0];
};

describe('buildWorkflow', () => {
  const wf = buildWorkflow(SETTINGS);

  test('CheckpointLoaderSimple にモデル名が入る', () => {
    const [, ckpt] = one(wf, 'CheckpointLoaderSimple');
    assert.equal(ckpt.inputs.ckpt_name, SETTINGS.ckpt_name);
  });

  test('2つの CLIPTextEncode にポジティブ／ネガティブが入り、KSampler に正しくつながる', () => {
    const clips = nodesOf(wf, 'CLIPTextEncode');
    assert.equal(clips.length, 2);
    const [, ks] = one(wf, 'KSampler');
    const posNode = wf[ks.inputs.positive[0]];
    const negNode = wf[ks.inputs.negative[0]];
    assert.equal(posNode.class_type, 'CLIPTextEncode');
    assert.equal(negNode.class_type, 'CLIPTextEncode');
    assert.equal(posNode.inputs.text, SETTINGS.positive);
    assert.equal(negNode.inputs.text, SETTINGS.negative);
    assert.notEqual(ks.inputs.positive[0], ks.inputs.negative[0]);
  });

  test('EmptyLatentImage に幅・高さ・枚数が入る', () => {
    const [, latent] = one(wf, 'EmptyLatentImage');
    assert.deepEqual(latent.inputs, { width: 1216, height: 832, batch_size: 2 });
  });

  test('KSampler にシード・Steps・CFG・サンプラー・スケジューラー・denoise 1.0', () => {
    const [, ks] = one(wf, 'KSampler');
    assert.equal(ks.inputs.seed, 123456789);
    assert.equal(ks.inputs.steps, 25);
    assert.equal(ks.inputs.cfg, 5.5);
    assert.equal(ks.inputs.sampler_name, 'dpmpp_2m_sde');
    assert.equal(ks.inputs.scheduler, 'karras');
    assert.equal(ks.inputs.denoise, 1.0);
  });

  test('SaveImage の接頭辞は "kantan"', () => {
    const [, save] = one(wf, 'SaveImage');
    assert.equal(save.inputs.filename_prefix, 'kantan');
  });

  test('配線：入力が参照するノードIDはすべて存在し、出力番号も正しい', () => {
    const [ckptId] = one(wf, 'CheckpointLoaderSimple');
    const [latentId] = one(wf, 'EmptyLatentImage');
    const [ksId, ks] = one(wf, 'KSampler');
    const [decId, dec] = one(wf, 'VAEDecode');
    const [, save] = one(wf, 'SaveImage');

    for (const [id, node] of Object.entries(wf)) {
      for (const [name, v] of Object.entries(node.inputs)) {
        if (Array.isArray(v)) {
          assert.equal(v.length, 2, `${id}.${name}`);
          assert.equal(typeof v[0], 'string', `${id}.${name} のノードIDは文字列`);
          assert.ok(Object.hasOwn(wf, v[0]), `${id}.${name} が参照する ${v[0]} が存在しない`);
          assert.ok(Number.isInteger(v[1]) && v[1] >= 0, `${id}.${name} の出力番号`);
        }
      }
    }

    // CheckpointLoaderSimple の出力は 0=MODEL, 1=CLIP, 2=VAE
    assert.deepEqual(ks.inputs.model, [ckptId, 0]);
    assert.deepEqual(ks.inputs.latent_image, [latentId, 0]);
    for (const [, clip] of nodesOf(wf, 'CLIPTextEncode')) assert.deepEqual(clip.inputs.clip, [ckptId, 1]);
    assert.deepEqual(dec.inputs.samples, [ksId, 0]);
    assert.deepEqual(dec.inputs.vae, [ckptId, 2]);
    assert.deepEqual(save.inputs.images, [decId, 0]);
  });

  test('純粋関数：入力を変更せず、同じ入力から同じ結果', () => {
    const copy = structuredClone(SETTINGS);
    assert.deepEqual(buildWorkflow(copy), buildWorkflow(SETTINGS));
    assert.deepEqual(copy, SETTINGS);
    assert.doesNotThrow(() => JSON.stringify(wf));
  });
});

describe('validateSettings', () => {
  test('正しい設定はそのまま通る', () => {
    const s = validateSettings(SETTINGS);
    assert.equal(s.seed, 123456789);
    assert.equal(s.seedWasRandom, false);
    assert.equal(s.cfg, 5.5);
  });

  test('数値の文字列も受け付ける', () => {
    const s = validateSettings({ ...SETTINGS, steps: '30', width: '1024', seed: '42' });
    assert.equal(s.steps, 30);
    assert.equal(s.width, 1024);
    assert.equal(s.seed, 42);
  });

  test('seed が -1 / null / 未指定ならサーバー側で乱数を決める', () => {
    for (const seed of [-1, null, undefined, '']) {
      const s = validateSettings({ ...SETTINGS, seed }, { makeSeed: () => 777 });
      assert.equal(s.seed, 777);
      assert.equal(s.seedWasRandom, true);
    }
  });

  test('範囲外は日本語でまとめて弾く', () => {
    const cases = [
      { steps: 0 }, { steps: 101 }, { steps: 2.5 },
      { cfg: 0.5 }, { cfg: 21 }, { cfg: 'abc' },
      { width: 255 }, { width: 2049 }, { height: 4096 },
      { batch_size: 0 }, { batch_size: 5 },
      { seed: -2 }, { seed: 1.5 }, { seed: MAX_SEED + 2 },
      { sampler_name: 'unknown' }, { scheduler: 'linear' },
      { ckpt_name: '' }, { positive: '   ' },
    ];
    for (const c of cases) {
      assert.throws(() => validateSettings({ ...SETTINGS, ...c }), UserError, JSON.stringify(c));
    }
    assert.throws(() => validateSettings({ ...SETTINGS, steps: 0, cfg: 99 }), (e) => e.message.split('\n').length === 2);
  });

  test('境界値は通る', () => {
    assert.doesNotThrow(() => validateSettings({ ...SETTINGS, steps: 1, cfg: 1, width: 256, height: 2048, batch_size: 4, seed: 0 }));
    assert.doesNotThrow(() => validateSettings({ ...SETTINGS, steps: 100, cfg: 20, seed: MAX_SEED }));
  });
});

test('randomSeed は 0〜2^53-1 の整数', () => {
  for (let i = 0; i < 200; i++) {
    const s = randomSeed();
    assert.ok(Number.isSafeInteger(s) && s >= 0 && s <= MAX_SEED);
  }
});

describe('parseSystemStats', () => {
  test('GPU名とVRAM(GB)とバージョンを取り出す', () => {
    const r = parseSystemStats({
      system: { os: 'posix', comfyui_version: '0.3.40' },
      devices: [{ name: 'cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync', type: 'cuda', vram_total: 25757220864, vram_free: 25000000000 }],
    });
    assert.equal(r.gpu, 'NVIDIA GeForce RTX 4090');
    assert.equal(r.vramGB, 24);
    assert.equal(r.comfyuiVersion, '0.3.40');
  });

  test('バージョンがない古いComfyUIでも動く', () => {
    const r = parseSystemStats({ system: {}, devices: [{ name: 'cuda:0 NVIDIA A40 : native', vram_total: 47697362944 }] });
    assert.equal(r.gpu, 'NVIDIA A40');
    assert.equal(r.vramGB, 44.4);
    assert.equal(r.comfyuiVersion, null);
  });

  test('ComfyUIでない応答は弾く', () => {
    assert.throws(() => parseSystemStats({ hello: 'world' }), UserError);
    assert.throws(() => parseSystemStats(null), UserError);
  });

  test('cleanGpuName', () => {
    assert.equal(cleanGpuName('NVIDIA A100-SXM4-80GB'), 'NVIDIA A100-SXM4-80GB');
    assert.equal(cleanGpuName(undefined), null);
  });
});

describe('parseCheckpointList', () => {
  test('旧形式 [[...], {}]', () => {
    const json = { CheckpointLoaderSimple: { input: { required: { ckpt_name: [['a.safetensors', 'b.ckpt'], {}] } } } };
    assert.deepEqual(parseCheckpointList(json), ['a.safetensors', 'b.ckpt']);
  });

  test('新形式 ["COMBO", {options}]', () => {
    const json = { CheckpointLoaderSimple: { input: { required: { ckpt_name: ['COMBO', { options: ['x.safetensors'] }] } } } };
    assert.deepEqual(parseCheckpointList(json), ['x.safetensors']);
  });

  test('想定外の形は日本語エラー', () => {
    assert.throws(() => parseCheckpointList({}), UserError);
  });
});

describe('parseHistory', () => {
  const id = 'abc-123';

  test('まだ履歴がなければ running', () => {
    assert.deepEqual(parseHistory({}, id), { state: 'running' });
  });

  test('完了したら画像一覧', () => {
    const json = {
      [id]: {
        outputs: { 9: { images: [{ filename: 'kantan_00001_.png', subfolder: '', type: 'output' }] } },
        status: { status_str: 'success', completed: true, messages: [] },
      },
    };
    assert.deepEqual(parseHistory(json, id), {
      state: 'done',
      images: [{ filename: 'kantan_00001_.png', subfolder: '', type: 'output' }],
    });
  });

  test('エラーなら日本語メッセージ', () => {
    const json = {
      [id]: {
        outputs: {},
        status: {
          status_str: 'error',
          completed: false,
          messages: [['execution_error', { node_type: 'KSampler', exception_message: 'CUDA out of memory.' }]],
        },
      },
    };
    const r = parseHistory(json, id);
    assert.equal(r.state, 'error');
    assert.match(r.message, /VRAM/);
  });

  test('完了したのに画像がなければエラー', () => {
    const r = parseHistory({ [id]: { outputs: {}, status: { status_str: 'success', completed: true } } }, id);
    assert.equal(r.state, 'error');
  });
});

describe('explainPromptError / basicAuthHeader', () => {
  test('モデルが見つからないエラーを日本語に', () => {
    const msg = explainPromptError({
      error: { type: 'prompt_outputs_failed_validation', message: 'Prompt outputs failed validation' },
      node_errors: { 4: { class_type: 'CheckpointLoaderSimple', errors: [{ type: 'value_not_in_list', message: 'Value not in list' }] } },
    });
    assert.match(msg, /モデル/);
  });

  test('Basic認証ヘッダー', () => {
    assert.equal(basicAuthHeader('', ''), null);
    assert.equal(basicAuthHeader('user', 'pass'), `Basic ${Buffer.from('user:pass').toString('base64')}`);
  });
});
