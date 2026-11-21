// ComfyUI まわりの純粋関数（URL正規化・設定検証・ワークフロー生成・応答の解釈）
import { randomBytes } from 'node:crypto';

export const SAMPLERS = ['dpmpp_2m_sde', 'dpmpp_2m', 'euler', 'euler_ancestral', 'ddim', 'uni_pc'];
export const SCHEDULERS = ['normal', 'karras', 'exponential', 'sgm_uniform'];
export const MAX_SEED = Number.MAX_SAFE_INTEGER; // 2^53 - 1
export const FILENAME_PREFIX = 'kantan';

/** 利用者に見せてよい（日本語の）エラー。status は HTTP ステータス。 */
export class UserError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.name = 'UserError';
    this.status = status;
  }
}

const POD_ID_RE = /^[a-z0-9]{3,40}$/;
const RUNPOD_HOST_RE = /^[a-z0-9]{3,40}-\d{2,5}\.proxy\.runpod\.net$/;
const URL_HINT = '（例：https://abc123-8188.proxy.runpod.net または Pod ID の abc123）';

/**
 * RunPod の ComfyUI の URL か Pod ID を受け取り、`https://<id>-<port>.proxy.runpod.net` に正規化する。
 */
export function normalizeBaseUrl(input) {
  if (typeof input !== 'string' || input.trim() === '') {
    throw new UserError(`PodのURLかPod IDを入力してください${URL_HINT}`);
  }
  let s = input.trim();

  if (POD_ID_RE.test(s.toLowerCase())) {
    return `https://${s.toLowerCase()}-8188.proxy.runpod.net`;
  }

  // 「abc123-8188.proxy.runpod.net」のようにスキームなしで貼られた場合も受け付ける
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `https://${s}`;

  let url;
  try {
    url = new URL(s);
  } catch {
    throw new UserError(`URLの形式が正しくありません${URL_HINT}`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new UserError(`https:// で始まるURLを入力してください${URL_HINT}`);
  }
  if (url.username || url.password) {
    throw new UserError('URLにユーザー名やパスワードを含めず、下の「ユーザー名・パスワード」欄に入力してください。');
  }
  if (!RUNPOD_HOST_RE.test(url.hostname) || url.port !== '') {
    throw new UserError(`RunPodのComfyUIのURLではないようです${URL_HINT}`);
  }
  return `https://${url.hostname}`;
}

/** 0〜2^53-1 の乱数シード */
export function randomSeed() {
  return Number(randomBytes(8).readBigUInt64BE() & BigInt(MAX_SEED));
}

function toNumber(v) {
  if (typeof v === 'number') return v;
  if (typeof v === 'string' && v.trim() !== '') return Number(v);
  return NaN;
}

/**
 * フォームの設定を検証して、ワークフローに使える値にそろえる。
 * seed が -1 / null / 未指定 / 空文字ならサーバー側で乱数を決める。
 * 問題があれば日本語のエラー（UserError）を投げる。
 */
export function validateSettings(raw, { makeSeed = randomSeed } = {}) {
  const s = raw && typeof raw === 'object' ? raw : {};
  const errors = [];

  const ckpt = typeof s.ckpt_name === 'string' ? s.ckpt_name.trim() : '';
  if (!ckpt) errors.push('モデル（チェックポイント）を選ぶか入力してください。');
  else if (ckpt.length > 500) errors.push('モデル名が長すぎます。');

  const positive = typeof s.positive === 'string' ? s.positive : '';
  if (!positive.trim()) errors.push('プロンプト（作りたい絵の説明）を入力してください。');
  else if (positive.length > 10000) errors.push('プロンプトが長すぎます（1万文字まで）。');

  const negative = typeof s.negative === 'string' ? s.negative : '';
  if (negative.length > 10000) errors.push('ネガティブプロンプトが長すぎます（1万文字まで）。');

  const int = (key, label, min, max) => {
    const n = toNumber(s[key]);
    if (!Number.isInteger(n) || n < min || n > max) {
      errors.push(`${label}は${min}〜${max}の整数で指定してください。`);
      return null;
    }
    return n;
  };

  const steps = int('steps', 'Steps', 1, 100);
  const width = int('width', '幅', 256, 2048);
  const height = int('height', '高さ', 256, 2048);
  const batch_size = int('batch_size', '枚数', 1, 4);

  let cfg = toNumber(s.cfg);
  if (!Number.isFinite(cfg) || cfg < 1 || cfg > 20) {
    errors.push('CFGは1〜20の数値で指定してください。');
    cfg = null;
  }

  const sampler_name = s.sampler_name;
  if (!SAMPLERS.includes(sampler_name)) {
    errors.push(`サンプラーは ${SAMPLERS.join(' / ')} から選んでください。`);
  }
  const scheduler = s.scheduler;
  if (!SCHEDULERS.includes(scheduler)) {
    errors.push(`スケジューラーは ${SCHEDULERS.join(' / ')} から選んでください。`);
  }

  let seed = null;
  let seedWasRandom = false;
  if (s.seed === -1 || s.seed === '-1' || s.seed === null || s.seed === undefined || s.seed === '') {
    seed = makeSeed();
    seedWasRandom = true;
  } else {
    const n = toNumber(s.seed);
    if (!Number.isInteger(n) || n < 0 || n > MAX_SEED) {
      errors.push(`シードは0〜${MAX_SEED}の整数で指定してください（ランダムなら -1）。`);
    } else {
      seed = n;
    }
  }

  if (errors.length) throw new UserError(errors.join('\n'));

  return {
    ckpt_name: ckpt,
    positive,
    negative,
    width,
    height,
    batch_size,
    steps,
    cfg,
    sampler_name,
    scheduler,
    seed,
    seedWasRandom,
  };
}

/**
 * ComfyUI の API 形式ワークフローを組み立てる（純粋関数）。
 * ノード番号は ComfyUI の標準ワークフローに合わせている。
 */
export function buildWorkflow(settings) {
  const s = settings;
  return {
    4: {
      class_type: 'CheckpointLoaderSimple',
      inputs: { ckpt_name: s.ckpt_name },
      _meta: { title: 'Load Checkpoint' },
    },
    6: {
      class_type: 'CLIPTextEncode',
      inputs: { text: s.positive, clip: ['4', 1] },
      _meta: { title: 'Positive Prompt' },
    },
    7: {
      class_type: 'CLIPTextEncode',
      inputs: { text: s.negative ?? '', clip: ['4', 1] },
      _meta: { title: 'Negative Prompt' },
    },
    5: {
      class_type: 'EmptyLatentImage',
      inputs: { width: s.width, height: s.height, batch_size: s.batch_size },
      _meta: { title: 'Empty Latent Image' },
    },
    3: {
      class_type: 'KSampler',
      inputs: {
        seed: s.seed,
        steps: s.steps,
        cfg: s.cfg,
        sampler_name: s.sampler_name,
        scheduler: s.scheduler,
        denoise: 1.0,
        model: ['4', 0],
        positive: ['6', 0],
        negative: ['7', 0],
        latent_image: ['5', 0],
      },
      _meta: { title: 'KSampler' },
    },
    8: {
      class_type: 'VAEDecode',
      inputs: { samples: ['3', 0], vae: ['4', 2] },
      _meta: { title: 'VAE Decode' },
    },
    9: {
      class_type: 'SaveImage',
      inputs: { filename_prefix: FILENAME_PREFIX, images: ['8', 0] },
      _meta: { title: 'Save Image' },
    },
  };
}

const GIB = 1024 ** 3;
const round1 = (n) => Math.round(n * 10) / 10;

/** "cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync" → "NVIDIA GeForce RTX 4090" */
export function cleanGpuName(name) {
  if (typeof name !== 'string') return null;
  return name.replace(/^[a-z]+:\d+\s+/i, '').replace(/\s+:\s+\S+$/, '').trim() || null;
}

/** GET /system_stats の応答から GPU 名・VRAM(GB)・ComfyUI バージョンを取り出す */
export function parseSystemStats(json) {
  if (!json || typeof json !== 'object' || (!json.system && !Array.isArray(json.devices))) {
    throw new UserError('ComfyUIの応答ではないようです。ポート8188のURLか確認してください。', 502);
  }
  const dev = Array.isArray(json.devices) && json.devices.length ? json.devices[0] : null;
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const total = dev ? num(dev.vram_total) : null;
  const free = dev ? num(dev.vram_free) : null;
  const version = json.system && typeof json.system.comfyui_version === 'string' ? json.system.comfyui_version : null;
  return {
    gpu: dev ? cleanGpuName(dev.name) : null,
    vramGB: total !== null ? round1(total / GIB) : null,
    vramFreeGB: free !== null ? round1(free / GIB) : null,
    comfyuiVersion: version,
  };
}

/** GET /object_info/CheckpointLoaderSimple の応答からモデル名一覧を取り出す（新旧両形式に対応） */
export function parseCheckpointList(json) {
  const spec = json?.CheckpointLoaderSimple?.input?.required?.ckpt_name;
  let list = null;
  if (Array.isArray(spec)) {
    if (Array.isArray(spec[0])) list = spec[0];
    else if (spec[0] === 'COMBO' && Array.isArray(spec[1]?.options)) list = spec[1].options;
  }
  if (!list) {
    throw new UserError('モデル一覧を読み取れませんでした。ComfyUIのバージョンが想定と違う可能性があります。手入力欄をお使いください。', 502);
  }
  return list.filter((x) => typeof x === 'string');
}

function explainExecutionError(messages) {
  if (Array.isArray(messages)) {
    for (const m of messages) {
      if (!Array.isArray(m)) continue;
      const [type, data] = m;
      if (type === 'execution_interrupted') return '生成が中断されました。';
      if (type === 'execution_error' && data) {
        const msg = String(data.exception_message ?? '').trim();
        if (/out of memory|OutOfMemory|allocat/i.test(msg) || /OutOfMemory/i.test(String(data.exception_type ?? ''))) {
          return 'VRAM（GPUメモリ）が足りませんでした。サイズか枚数を下げてもう一度お試しください。';
        }
        const where = data.node_type ? `（${data.node_type}）` : '';
        return `ComfyUIで生成中にエラーが起きました${where}：${msg || '詳細不明'}`;
      }
    }
  }
  return 'ComfyUIで生成中にエラーが起きました。';
}

/**
 * GET /history/{prompt_id} の応答を解釈する。
 * → {state:"running"} / {state:"done", images:[...]} / {state:"error", message}
 */
export function parseHistory(json, promptId) {
  const entry = json && typeof json === 'object' ? json[promptId] : undefined;
  if (!entry || typeof entry !== 'object') return { state: 'running' };

  const status = entry.status && typeof entry.status === 'object' ? entry.status : {};
  if (status.status_str === 'error') {
    return { state: 'error', message: explainExecutionError(status.messages) };
  }

  const images = [];
  for (const out of Object.values(entry.outputs ?? {})) {
    for (const img of out?.images ?? []) {
      if (img && typeof img.filename === 'string' && img.filename) {
        images.push({
          filename: img.filename,
          subfolder: typeof img.subfolder === 'string' ? img.subfolder : '',
          type: typeof img.type === 'string' ? img.type : 'output',
        });
      }
    }
  }
  if (images.length) return { state: 'done', images };
  if (status.completed) {
    return { state: 'error', message: '生成は終わりましたが、画像が見つかりませんでした。' };
  }
  return { state: 'running' };
}

/** POST /prompt が 400 などを返したときの応答を日本語に直す */
export function explainPromptError(json) {
  const nodeErrors = json && typeof json.node_errors === 'object' && json.node_errors ? json.node_errors : {};
  const details = [];
  for (const ne of Object.values(nodeErrors)) {
    for (const e of ne?.errors ?? []) {
      if (e?.type === 'value_not_in_list' && ne.class_type === 'CheckpointLoaderSimple') {
        return '選んだモデルがPodに見つかりません。「再読み込み」でモデル一覧を取り直し、一覧から選んでください。';
      }
      details.push([e?.message, e?.details].filter(Boolean).join(': '));
    }
  }
  const top = json?.error;
  if (top && typeof top === 'object') details.unshift([top.message, top.details].filter(Boolean).join(': '));
  else if (typeof top === 'string') details.unshift(top);
  const text = details.filter(Boolean).join(' / ');
  return `ComfyUIが設定を受け付けませんでした${text ? `：${text}` : '。'}`;
}

/** Basic 認証ヘッダー（ユーザー名もパスワードも空なら null） */
export function basicAuthHeader(username, password) {
  if (!username && !password) return null;
  return `Basic ${Buffer.from(`${username ?? ''}:${password ?? ''}`, 'utf8').toString('base64')}`;
}
