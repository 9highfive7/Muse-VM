// RunPod かんたん画像生成 — フロントエンド（フレームワークなし）
'use strict';

(() => {
  const $ = (id) => document.getElementById(id);

  /** 要素を作る小さなヘルパー（ユーザー入力は必ず textContent で入れる） */
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  // ---------------------------------------------------------------------------
  // 設定ごとの初心者ガイド
  //   explain: 一行説明 / zones: 低・おすすめ・高の目安 / bar: 3色バー / label: 値に応じた判定
  //   level: good=おすすめ, info=ふつう, warn=注意, bad=危険
  // ---------------------------------------------------------------------------
  const eqSide = (w, h) => Math.round(Math.sqrt(w * h));

  const GUIDE = {
    model: {
      explain: '絵のもとになるAIモデル。画風や得意分野はモデルで決まります。SDXL系なら1024前後、SD1.5系なら512前後のサイズが基本です。',
      zones: [
        ['SD1.5系', '512〜768が得意。軽くて速い', 'info'],
        ['おすすめ', 'SDXL系（名前に xl など）。1024前後で安定', 'good'],
        ['特殊', 'Turbo / Lightning / FLUX系は設定の目安が別', 'warn'],
      ],
      label: () => {
        const name = currentModel().toLowerCase();
        if (!name) return ['warn', '未選択：接続テストをして一覧から選ぶか、手入力してください'];
        if (/turbo|lightning|lcm|hyper/.test(name)) return ['warn', '高速モデルのようです：Steps 4〜8・CFG 1〜2・sgm_uniform が目安（通常の目安とは違います）'];
        if (/flux/.test(name)) return ['warn', 'FLUX系のようです：CFGは1前後が目安。モデルによってはこのアプリの標準ワークフローで動きません'];
        if (/xl|pony|illustrious|noob/.test(name)) return ['good', 'SDXL系のようです：サイズは1024前後（1024×1024・1216×832など）がおすすめ'];
        if (/(^|[^0-9])1[._-]?5|sd15|v1-5/.test(name)) return ['info', 'SD1.5系のようです：サイズは512〜768がおすすめ（1024だと人物が増えやすい）'];
        return ['info', 'モデルの種類が名前から判断できません。まずはおすすめ設定で1枚試してください'];
      },
    },

    positive: {
      explain: '作りたい絵の説明。英語の単語や短い文をカンマで区切って書きます。大事なものほど前に書くと効きやすいです。',
      zones: [
        ['短すぎ', '1〜2語：細部はおまかせになりがち', 'warn'],
        ['おすすめ', '主題, 服装や様子, 背景, 画風, 光 など5〜20語', 'good'],
        ['長すぎ', '40語以上：後ろのほうの語が効きにくい', 'warn'],
      ],
      label: () => {
        const text = $('positive').value.trim();
        if (!text) return ['bad', '未入力：作りたい絵を書いてください（例：a cat, watercolor）'];
        const hasJa = /[぀-ヿ一-鿿]/.test(text);
        const n = text.split(/[,，、\n]+/).filter((t) => t.trim()).length;
        const ja = hasJa ? '／日本語は効きにくいので英語がおすすめ' : '';
        if (n <= 2) return ['warn', `短め：細部はおまかせになりやすい${ja}`];
        if (n < 40 && text.length <= 400) return [hasJa ? 'warn' : 'good', `おすすめ域：ちょうど良い長さ${ja}`];
        return ['warn', `長め：後ろのほうの語は効きにくい。大事な語を前へ${ja}`];
      },
    },

    negative: {
      explain: '出てほしくないものを書きます。空でも動きますが、定番の語を入れると失敗が減ります。',
      zones: [
        ['空', '制限なし。そのままでもOK', 'info'],
        ['おすすめ', 'lowres, blurry, bad anatomy, watermark など定番5〜10語', 'good'],
        ['多すぎ', '大量に書くと絵が地味・単調になることも', 'warn'],
      ],
      label: () => {
        const text = $('negative').value.trim();
        if (!text) return ['info', '空：制限なし（ぼやけ・崩れが気になったら定番の語を足しましょう）'];
        const n = text.split(/[,，、\n]+/).filter((t) => t.trim()).length;
        if (n <= 25) return ['good', 'おすすめ域：定番の失敗をよけられます'];
        return ['warn', '多め：絵が地味になることがあります。効いてほしい語に絞りましょう'];
      },
    },

    size: {
      explain: '画像の大きさ。大きいほど細かく描けますが、時間とVRAM（GPUメモリ）が増えます。モデルが学習したサイズに近いほど安定します。',
      zones: [
        ['小', '512前後：速いが粗い（SDXLでは崩れやすい）', 'warn'],
        ['おすすめ', '1024前後（約100万画素）：SDXLが安定', 'good'],
        ['大', '1536以上：遅くVRAM大。人物が2人になるなど崩れやすい', 'warn'],
      ],
      bar: { min: 256, max: 2048, good: [896, 1200], caption: '画素数の目安（正方形換算の一辺）', value: () => eqSide(numVal('width'), numVal('height')) },
      label: () => {
        const w = numVal('width');
        const h = numVal('height');
        if (!inRange(w, 256, 2048) || !inRange(h, 256, 2048)) return ['bad', '幅・高さは256〜2048の範囲で入れてください'];
        const side = eqSide(w, h);
        const long = Math.max(w, h);
        const mp = ((w * h) / 1e6).toFixed(2);
        const odd = w % 8 || h % 8 ? '／8の倍数にすると安全です' : '';
        if (long >= 1536 || side > 1400) return ['bad', `大きすぎ：遅く、VRAMを多く使い、人物が2人になるなど崩れやすい（${mp}M画素）${odd}`];
        if (side < 640) return ['warn', `小さめ：速いが粗い。SD1.5系向け（SDXLでは崩れやすい）${odd}`];
        if (side < 896) return ['info', `やや小さめ：速めだが細部は粗くなりがち（${mp}M画素）${odd}`];
        if (side <= 1200) return ['good', `おすすめ域：SDXLが安定するサイズ（${mp}M画素）${odd}`];
        return ['warn', `大きめ：時間とVRAMが増え、構図が崩れることも（${mp}M画素）${odd}`];
      },
    },

    steps: {
      explain: '絵を仕上げるまでに描き直す回数。多いほど丁寧になりますが、ある程度から先は時間が増えるだけです。',
      zones: [
        ['少', '5〜10：速いが粗く、ぼやけやすい', 'warn'],
        ['おすすめ', '20〜30：品質と速さのバランスが良い', 'good'],
        ['多', '50以上：時間の割に変化が小さい', 'warn'],
      ],
      bar: { min: 1, max: 100, good: [20, 30], value: () => numVal('steps') },
      label: () => {
        const v = numVal('steps');
        if (v < 5) return ['bad', 'とても少ない：ほぼ形になりません（高速モデル以外）'];
        if (v <= 10) return ['warn', '少なめ：速いけど粗く、ぼやけやすい'];
        if (v < 20) return ['info', 'やや少なめ：下書き・構図チェック向き'];
        if (v <= 30) return ['good', 'おすすめ域：品質と速さのバランス良好'];
        if (v < 50) return ['info', '多め：少し緻密になるが、時間も増える'];
        return ['warn', '多すぎ：時間の割に変化はわずか（30前後で十分なことが多い）'];
      },
    },

    cfg: {
      explain: 'プロンプトにどれだけ忠実に従うか。上げると指示が通りやすくなりますが、上げすぎると色が濃くなり絵が崩れます。',
      zones: [
        ['ゆるめ', '1〜3：色が薄く、指示が効きにくい', 'warn'],
        ['おすすめ', '4〜7（5が標準）：バランス良好', 'good'],
        ['強め', '8〜12：色が濃くギラつく。15以上は破綻しやすい', 'warn'],
      ],
      bar: { min: 1, max: 20, good: [4, 8], value: () => numVal('cfg') },
      label: () => {
        const v = numVal('cfg');
        if (v <= 2) return ['warn', 'かなりゆるめ：色が薄く、ぼんやりしやすい'];
        if (v < 4) return ['warn', 'ゆるめ：プロンプトが一部無視されやすい'];
        if (v < 8) return ['good', 'おすすめ域：バランス良好'];
        if (v <= 12) return ['warn', '強め：色が濃くギラつきやすい'];
        if (v < 15) return ['warn', 'かなり強め：コントラストが強すぎ、崩れが出始める'];
        return ['bad', '強すぎ：絵が崩れたり「焦げた」ような色になりやすい'];
      },
    },

    sampler: {
      explain: 'ノイズから絵を描き出す方法。仕上がりの雰囲気と速さが少し変わります。迷ったら dpmpp_2m_sde。',
      zones: [
        ['速さ重視', 'euler：速くて素直・あっさり', 'info'],
        ['おすすめ', 'dpmpp_2m_sde：質と安定のバランス', 'good'],
        ['変化重視', 'euler_ancestral：ランダム性が高く毎回変わる', 'info'],
      ],
      label: () => ({
        dpmpp_2m_sde: ['good', 'おすすめ：質と安定のバランスが良い（karrasと好相性）'],
        dpmpp_2m: ['good', '安定：落ち着いた仕上がり。同じシードで再現しやすい'],
        euler: ['info', '速い・素直：シンプルであっさりした仕上がり'],
        euler_ancestral: ['info', 'ランダム性が高い：毎回変化が出る。Stepsを変えると絵も変わりやすい'],
        ddim: ['info', '古典的：速め。少ないStepsでもそこそこまとまる'],
        uni_pc: ['info', '高速：少ないSteps（15〜20）でもまとまりやすい'],
      })[$('sampler').value] ?? ['info', ''],
    },

    scheduler: {
      explain: '描き込みの「力の入れ方」の配分。仕上げのなめらかさが変わります。迷ったら karras。',
      zones: [
        ['標準', 'normal：素直な配分', 'info'],
        ['おすすめ', 'karras：仕上げがなめらか', 'good'],
        ['特殊', 'sgm_uniform：Turbo/Lightning系向け', 'info'],
      ],
      label: () => ({
        normal: ['info', '標準：素直な配分。eulerと組み合わせることが多い'],
        karras: ['good', 'おすすめ：仕上げがなめらかになりやすい'],
        exponential: ['info', '特殊：一部のサンプラー向け。仕上げがやわらかめ'],
        sgm_uniform: ['info', '特殊：Turbo / Lightning などの高速モデル向け'],
      })[$('scheduler').value] ?? ['info', ''],
    },

    seed: {
      explain: '絵のもとになる乱数の番号。同じシード＋同じ設定なら同じ画像が再現できます。',
      zones: [
        ['ランダム', '毎回ちがう絵。良い構図を探すとき', 'info'],
        ['おすすめ', '気に入ったらシードを固定', 'good'],
        ['微調整', '固定したまま設定を1つずつ変えて比べる', 'good'],
      ],
      label: () => {
        if ($('seed-random').checked) return ['info', 'ランダム：毎回ちがう画像になります（良い絵を探す段階）'];
        const v = $('seed').value.trim();
        if (!/^\d+$/.test(v) || Number(v) > Number.MAX_SAFE_INTEGER) return ['bad', `0〜${Number.MAX_SAFE_INTEGER}の整数を入れてください`];
        return ['good', '固定：同じ設定なら同じ画像。設定を1つずつ変えて微調整しましょう'];
      },
    },

    batch: {
      explain: '一度に作る枚数。増やすと見比べやすくなりますが、時間とVRAMがほぼ枚数倍になります。',
      zones: [
        ['1枚', 'いちばん速い。まず1枚で確認', 'good'],
        ['おすすめ', '1〜2枚', 'good'],
        ['4枚', '時間・VRAMが約4倍。大きいサイズでは不足に注意', 'warn'],
      ],
      label: () => {
        const v = numVal('batch');
        const big = eqSide(numVal('width'), numVal('height')) > 1200;
        if (v === 1) return ['good', '1枚：いちばん速い（まず1枚で確認がおすすめ）'];
        if (v === 2) return [big ? 'warn' : 'good', big ? '2枚：大きいサイズなのでVRAMに注意' : '2枚：見比べやすい'];
        if (v === 3) return [big ? 'bad' : 'info', big ? '3枚：大きいサイズではVRAM不足になりやすい' : '3枚：時間とVRAMがやや増える'];
        return [big ? 'bad' : 'warn', big ? '4枚：大きいサイズではVRAM不足になりやすい。サイズか枚数を下げて' : '4枚：時間・VRAMが約4倍'];
      },
    },
  };

  const LEVEL_TEXT = { good: 'おすすめ', info: 'ふつう', warn: '注意', bad: '要注意' };

  // ---------------------------------------------------------------------------
  // プリセット
  // ---------------------------------------------------------------------------
  const PRESETS = {
    recommended: {
      values: { width: 1024, height: 1024, steps: 25, cfg: 5, sampler: 'dpmpp_2m_sde', scheduler: 'karras', batch: 1 },
      reason: 'SDXLが安定する1024×1024に、Steps 25・CFG 5・dpmpp_2m_sde＋karras の定番の組み合わせ。迷ったらここから始めましょう。',
    },
    fast: {
      values: { width: 768, height: 768, steps: 12, cfg: 5, sampler: 'euler', scheduler: 'normal', batch: 1 },
      reason: 'サイズとStepsを控えめにして、構図やアイデアを素早く確認する設定。良い絵が出たらシードを固定して「じっくり高品質」へ。',
    },
    quality: {
      values: { width: 1024, height: 1024, steps: 40, cfg: 6, sampler: 'dpmpp_2m_sde', scheduler: 'karras', batch: 1 },
      reason: 'Stepsを40に増やして細部を詰める設定（時間は約1.6倍）。50以上は変化が小さいので40で十分。サイズは崩れにくい1024のまま。',
    },
    faithful: {
      values: { steps: 30, cfg: 7.5, sampler: 'dpmpp_2m', scheduler: 'karras' },
      reason: 'CFGを7.5に上げて指示を強めに反映し、安定したdpmpp_2mで崩れを抑える設定。8を超えると色がギラつきやすいので、この辺りが上限の目安。',
    },
  };

  // ---------------------------------------------------------------------------
  // フォームの読み書き
  // ---------------------------------------------------------------------------
  const numVal = (id) => Number($(id).value);
  const inRange = (n, min, max) => Number.isFinite(n) && n >= min && n <= max;
  const currentModel = () => $('model-manual').value.trim() || $('model-select').value;

  function readSettings() {
    const random = $('seed-random').checked;
    return {
      ckpt_name: currentModel(),
      positive: $('positive').value,
      negative: $('negative').value,
      width: numVal('width'),
      height: numVal('height'),
      steps: numVal('steps'),
      cfg: numVal('cfg'),
      sampler_name: $('sampler').value,
      scheduler: $('scheduler').value,
      seed: random ? -1 : $('seed').value.trim() === '' ? NaN : Number($('seed').value),
      batch_size: numVal('batch'),
    };
  }

  function setModel(name) {
    if (!name) return;
    const sel = $('model-select');
    const found = [...sel.options].some((o) => o.value === name);
    if (found) {
      sel.value = name;
      $('model-manual').value = '';
    } else {
      $('model-manual').value = name;
    }
  }

  function applyValues(v) {
    if (v.width !== undefined) $('width').value = v.width;
    if (v.height !== undefined) $('height').value = v.height;
    if (v.steps !== undefined) $('steps').value = v.steps;
    if (v.cfg !== undefined) $('cfg').value = v.cfg;
    if (v.sampler !== undefined) $('sampler').value = v.sampler;
    if (v.scheduler !== undefined) $('scheduler').value = v.scheduler;
    if (v.batch !== undefined) $('batch').value = String(v.batch);
    updateAll();
  }

  // ---------------------------------------------------------------------------
  // ガイド表示
  // ---------------------------------------------------------------------------
  const guideParts = {};

  function buildGuides() {
    for (const box of document.querySelectorAll('.guide[data-guide]')) {
      const key = box.dataset.guide;
      const g = GUIDE[key];
      if (!g) continue;
      box.append(el('p', { class: 'explain', text: g.explain }));
      box.append(
        el('ul', { class: 'zones' },
          g.zones.map(([title, text, level]) =>
            el('li', { class: `zone zone-${level}` }, el('span', { class: 'zone-title', text: title }), el('span', { text })))),
      );
      let marker = null;
      if (g.bar) {
        // 見やすさ優先で 3ゾーンを 30% / 40% / 30% の固定幅で描き、マーカーはゾーン内で比例配置する
        const { min, max, good } = g.bar;
        const bar = el('div', { class: 'zone-bar', 'aria-hidden': 'true' });
        for (const [cls, text, width] of [['seg-warn', '注意', 30], ['seg-good', 'おすすめ', 40], ['seg-warn', '注意', 30]]) {
          const seg = el('span', { class: `seg ${cls}`, text });
          seg.style.width = `${width}%`;
          bar.append(seg);
        }
        marker = el('span', { class: 'marker' });
        bar.append(marker);
        const ticks = el('div', { class: 'zone-ticks', 'aria-hidden': 'true' });
        for (const [v, left] of [[min, 0], [good[0], 30], [good[1], 70], [max, 100]]) {
          const t = el('span', { text: String(v) });
          t.style.left = `${left}%`;
          ticks.append(t);
        }
        if (g.bar.caption) box.append(el('p', { class: 'bar-caption', text: g.bar.caption }));
        box.append(bar, ticks);
      }
      const label = el('p', { class: 'zone-label', 'aria-live': 'polite' });
      box.append(label);
      guideParts[key] = { label, marker };
    }
  }

  /** 値 → バー上の位置(%)。注意 0〜30% / おすすめ 30〜70% / 注意 70〜100% に区間ごとに比例配置 */
  function barPosition({ min, max, good: [g0, g1] }, v) {
    if (!Number.isFinite(v)) return 0;
    const x = Math.min(max, Math.max(min, v));
    if (x < g0) return ((x - min) / (g0 - min)) * 30;
    if (x <= g1) return 30 + ((x - g0) / (g1 - g0)) * 40;
    return 70 + ((x - g1) / (max - g1)) * 30;
  }

  function updateGuide(key) {
    const g = GUIDE[key];
    const parts = guideParts[key];
    if (!g || !parts) return;
    const [level, text] = g.label();
    parts.label.className = `zone-label level-${level}`;
    parts.label.textContent = '';
    parts.label.append(el('span', { class: 'badge', text: LEVEL_TEXT[level] ?? '' }), ` ${text}`);
    if (parts.marker && g.bar) {
      parts.marker.style.left = `${barPosition(g.bar, g.bar.value())}%`;
    }
  }

  function updateAll() {
    $('steps-value').textContent = $('steps').value;
    $('cfg-value').textContent = Number($('cfg').value).toFixed(1);
    $('seed').disabled = $('seed-random').checked;
    const w = $('width').value;
    const h = $('height').value;
    for (const b of document.querySelectorAll('.size-preset')) {
      b.classList.toggle('active', b.dataset.w === w && b.dataset.h === h);
      b.setAttribute('aria-pressed', String(b.dataset.w === w && b.dataset.h === h));
    }
    for (const key of Object.keys(GUIDE)) updateGuide(key);
  }

  // ---------------------------------------------------------------------------
  // API 呼び出し
  // ---------------------------------------------------------------------------
  function connectionPayload() {
    return {
      url: $('conn-url').value.trim(),
      username: $('conn-user').value,
      password: $('conn-pass').value,
    };
  }

  async function api(name, body) {
    let res;
    try {
      res = await fetch(`/api/${name}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {
      throw new Error('このアプリのサーバーに届きませんでした。ターミナルで npm start が動いているか確認してください。');
    }
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* 応答がJSONでない */
    }
    if (!res.ok || !data || data.ok === false) {
      throw new Error(data?.error || `エラーが起きました（HTTP ${res.status}）`);
    }
    return data;
  }

  function showResult(node, kind, text) {
    node.className = `result result-${kind}`;
    node.textContent = text;
  }

  // ---------------------------------------------------------------------------
  // 接続
  // ---------------------------------------------------------------------------
  async function testConnection() {
    const out = $('conn-result');
    const btn = $('btn-test');
    if (!$('conn-url').value.trim()) {
      showResult(out, 'error', 'PodのURLかPod IDを入力してください。');
      return;
    }
    btn.disabled = true;
    showResult(out, 'busy', '接続中…');
    try {
      const r = await api('test-connection', { connection: connectionPayload() });
      sessionStorage.setItem('kantan.url', $('conn-url').value.trim());
      const parts = [`接続OK：GPU ${r.gpu ?? '不明'}`];
      if (r.vramGB !== null && r.vramGB !== undefined) parts.push(`VRAM ${r.vramGB} GB`);
      if (r.comfyuiVersion) parts.push(`ComfyUI ${r.comfyuiVersion}`);
      showResult(out, 'ok', parts.join(' ／ '));
      await loadModels();
    } catch (e) {
      showResult(out, 'error', e.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function loadModels() {
    const sel = $('model-select');
    const btn = $('btn-models');
    const prev = sel.value;
    btn.disabled = true;
    try {
      const r = await api('models', { connection: connectionPayload() });
      sel.textContent = '';
      if (!r.models.length) {
        sel.append(el('option', { value: '', text: '（Podにモデルがありません。手入力するか、Podにモデルを入れてください）' }));
      } else {
        for (const m of r.models) sel.append(el('option', { value: m, text: m }));
        const remembered = localStorage.getItem('kantan.model');
        const pick = [prev, remembered].find((n) => n && r.models.includes(n))
          ?? r.models.find((n) => /xl/i.test(n))
          ?? r.models[0];
        sel.value = pick;
      }
    } catch (e) {
      sel.textContent = '';
      sel.append(el('option', { value: '', text: '（読み込めませんでした。手入力欄をお使いください）' }));
      showResult($('conn-result'), 'error', `モデル一覧：${e.message}`);
    } finally {
      btn.disabled = false;
      updateAll();
    }
  }

  // ---------------------------------------------------------------------------
  // 生成
  // ---------------------------------------------------------------------------
  const POLL_MS = 2000;
  const MAX_WAIT_MS = 10 * 60 * 1000;
  let generating = false;

  const imageUrl = (img) =>
    `/api/image?${new URLSearchParams({ filename: img.filename, subfolder: img.subfolder ?? '', type: img.type ?? 'output' })}`;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function clientCheck(s) {
    const errs = [];
    if (!$('conn-url').value.trim()) errs.push('先に「1. 接続」でPodのURLを入れて接続テストをしてください。');
    if (!s.ckpt_name) errs.push('モデルを選ぶか入力してください。');
    if (!s.positive.trim()) errs.push('プロンプトを入力してください。');
    if (!inRange(s.width, 256, 2048) || !inRange(s.height, 256, 2048)) errs.push('幅・高さは256〜2048で入れてください。');
    if (s.seed !== -1 && !(Number.isInteger(s.seed) && s.seed >= 0 && s.seed <= Number.MAX_SAFE_INTEGER)) {
      errs.push('シードは0以上の整数にするか「ランダム」にチェックしてください。');
    }
    return errs;
  }

  async function generate() {
    if (generating) return;
    const status = $('gen-status');
    const settings = readSettings();
    const errs = clientCheck(settings);
    if (errs.length) {
      showResult(status, 'error', errs.join('\n'));
      return;
    }

    generating = true;
    const btn = $('btn-generate');
    btn.disabled = true;
    btn.textContent = '生成中…';
    $('results').textContent = '';
    showResult(status, 'busy', 'ComfyUIに送信中…');

    try {
      const r = await api('generate', { connection: connectionPayload(), settings });
      localStorage.setItem('kantan.model', settings.ckpt_name);
      showLastSeed(r.seed);
      const started = Date.now();
      let failures = 0;
      let result = null;
      while (Date.now() - started < MAX_WAIT_MS) {
        const sec = Math.round((Date.now() - started) / 1000);
        const hint = sec >= 20 ? '（最初の1枚はモデルの読み込みで時間がかかります）' : '';
        showResult(status, 'busy', `生成中… ${sec}秒経過 ${hint}`);
        await sleep(POLL_MS);
        try {
          const s = await api('status', { connection: connectionPayload(), prompt_id: r.prompt_id });
          failures = 0;
          if (s.state === 'running') continue;
          result = s;
          break;
        } catch (e) {
          failures += 1;
          if (failures >= 5) throw e; // 一時的な失敗は数回まで見逃す
        }
      }
      if (!result) {
        throw new Error('10分たっても終わりませんでした。Podの状態（RunPodの画面やComfyUI本体）を確認してください。');
      }
      if (result.state === 'error') throw new Error(result.message);

      const secs = Math.round((Date.now() - started) / 1000);
      showResult(status, 'ok', `完了：${result.images.length}枚（約${secs}秒）。気に入った画像はダウンロードしてください。`);
      renderImages($('results'), result.images);
      addHistory({ settings, seed: r.seed, images: result.images });
    } catch (e) {
      showResult(status, 'error', e.message);
    } finally {
      generating = false;
      btn.disabled = false;
      btn.textContent = '画像を生成する';
    }
  }

  function renderImages(container, images) {
    container.textContent = '';
    for (const img of images) {
      const src = imageUrl(img);
      container.append(
        el('figure', { class: 'result-image' },
          el('a', { href: src, target: '_blank', rel: 'noopener' },
            el('img', { src, alt: `生成画像 ${img.filename}`, loading: 'lazy' })),
          el('figcaption', {},
            el('a', { href: src, download: img.filename, class: 'download' }, 'ダウンロード'),
            el('span', { class: 'muted small', text: ` ${img.filename}` }))),
      );
    }
  }

  function showLastSeed(seed) {
    const box = $('last-seed');
    box.textContent = '';
    box.hidden = false;
    box.append(
      el('span', { text: `今回のシード：${seed}` }),
      el('button', {
        type: 'button',
        class: 'secondary small-btn',
        onclick: () => {
          $('seed').value = String(seed);
          $('seed-random').checked = false;
          updateAll();
        },
      }, 'このシードで固定'),
    );
  }

  // ---------------------------------------------------------------------------
  // 履歴（localStorage、最新20件。パスワード・URLは入れない）
  // ---------------------------------------------------------------------------
  const HISTORY_KEY = 'kantan.history.v1';
  const HISTORY_MAX = 20;

  function loadHistory() {
    try {
      const list = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
      return Array.isArray(list) ? list.filter((h) => h && typeof h === 'object' && h.settings) : [];
    } catch {
      return [];
    }
  }

  function saveHistory(list) {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX)));
    } catch {
      /* 容量オーバーなどは無視 */
    }
  }

  function addHistory({ settings, seed, images }) {
    const list = loadHistory();
    list.unshift({
      date: new Date().toISOString(),
      prompt: settings.positive,
      settings: { ...settings, seed },
      seed,
      images: images.map(({ filename, subfolder, type }) => ({ filename, subfolder, type })),
    });
    saveHistory(list);
    renderHistory();
  }

  function restoreFromHistory(h) {
    const s = h.settings;
    setModel(s.ckpt_name);
    $('positive').value = s.positive ?? '';
    $('negative').value = s.negative ?? '';
    $('seed').value = String(h.seed ?? s.seed ?? 0);
    $('seed-random').checked = false;
    applyValues({
      width: s.width, height: s.height, steps: s.steps, cfg: s.cfg,
      sampler: s.sampler_name, scheduler: s.scheduler, batch: s.batch_size,
    });
    $('preset-reason').textContent = '履歴の設定を戻しました（シードも固定）。このまま生成すると同じ画像が再現されます。設定を1つ変えて比べるのがおすすめです。';
    $('generate').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderHistory() {
    const ul = $('history-list');
    const list = loadHistory();
    ul.textContent = '';
    $('history-empty').hidden = list.length > 0;
    $('btn-clear-history').hidden = list.length === 0;
    for (const h of list) {
      const s = h.settings;
      const date = new Date(h.date);
      const when = Number.isNaN(date.getTime()) ? '' : date.toLocaleString('ja-JP');
      const prompt = String(h.prompt ?? '');
      const summary = `${s.width}×${s.height} ／ Steps ${s.steps} ／ CFG ${Number(s.cfg).toFixed(1)} ／ ${s.sampler_name}・${s.scheduler} ／ ${s.batch_size}枚 ／ シード ${h.seed}`;
      const thumbs = el('div', { class: 'thumbs' });
      for (const img of (h.images ?? []).slice(0, 4)) {
        const t = el('img', { src: imageUrl(img), alt: '', loading: 'lazy', class: 'thumb' });
        t.addEventListener('error', () => {
          t.remove();
          if (!thumbs.querySelector('.gone')) {
            thumbs.append(el('span', { class: 'gone muted small', text: '（画像はPod上にないか、未接続です）' }));
          }
        });
        thumbs.append(t);
      }
      ul.append(
        el('li', {},
          el('button', { type: 'button', class: 'history-item', onclick: () => restoreFromHistory(h), title: 'この設定をフォームに戻す' },
            el('span', { class: 'history-date', text: when }),
            el('span', { class: 'history-prompt', text: prompt.length > 100 ? `${prompt.slice(0, 100)}…` : prompt }),
            el('span', { class: 'history-summary', text: `${s.ckpt_name ?? ''} ／ ${summary}` }),
            thumbs)),
      );
    }
  }

  // ---------------------------------------------------------------------------
  // 初期化
  // ---------------------------------------------------------------------------
  function init() {
    buildGuides();

    const savedUrl = sessionStorage.getItem('kantan.url');
    if (savedUrl) $('conn-url').value = savedUrl;

    $('btn-test').addEventListener('click', testConnection);
    $('conn-url').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') testConnection();
    });
    $('btn-models').addEventListener('click', () => {
      if (!$('conn-url').value.trim()) {
        showResult($('conn-result'), 'error', 'PodのURLかPod IDを入力してください。');
        return;
      }
      loadModels();
    });

    for (const id of ['model-select', 'model-manual', 'positive', 'negative', 'width', 'height', 'steps', 'cfg', 'sampler', 'scheduler', 'seed', 'seed-random', 'batch']) {
      $(id).addEventListener('input', updateAll);
      $(id).addEventListener('change', updateAll);
    }

    for (const b of document.querySelectorAll('.size-preset')) {
      b.addEventListener('click', () => applyValues({ width: Number(b.dataset.w), height: Number(b.dataset.h) }));
    }

    for (const b of document.querySelectorAll('.preset')) {
      b.addEventListener('click', () => {
        const p = PRESETS[b.dataset.preset];
        if (!p) return;
        applyValues(p.values); // シードは変えない（固定したまま設定だけ切り替えて比べられるように）
        for (const other of document.querySelectorAll('.preset')) other.classList.toggle('active', other === b);
        $('preset-reason').textContent = `「${b.textContent}」：${p.reason}`;
      });
    }

    $('btn-generate').addEventListener('click', generate);
    $('btn-clear-history').addEventListener('click', () => {
      if (confirm('履歴をすべて消しますか？（Pod上の画像は消えません）')) {
        localStorage.removeItem(HISTORY_KEY);
        renderHistory();
      }
    });

    updateAll();
    renderHistory();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
