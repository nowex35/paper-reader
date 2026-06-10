/* Naruhodo — PDF選択 → その場で日本語訳＋解説 */

function appConfirm(msg) {
  return new Promise((resolve) => {
    const d = document.getElementById("confirmDialog");
    const m = document.getElementById("confirmMsg");
    const ok = document.getElementById("confirmOk");
    const cancel = document.getElementById("confirmCancel");
    if (!d) { resolve(confirm(msg)); return; }
    m.textContent = msg;
    function close(result) {
      d.close();
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      resolve(result);
    }
    function onOk() { close(true); }
    function onCancel() { close(false); }
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    d.showModal();
  });
}

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
marked.setOptions({ breaks: true });

// 数式($...$, $$...$$, \(...\), \[...\])を KaTeX で描画する。
// marked が TeX 内の _ や \ を誤って解釈しないよう、いったんプレースホルダに退避し
// Markdown 化＋サニタイズの後で KaTeX 出力に差し替える。
function renderTeX(tex, display) {
  if (window.katex) {
    try {
      return katex.renderToString(tex, {
        displayMode: display,
        throwOnError: false,
        output: "html",
      });
    } catch (e) {
      /* 失敗時は元の記法をそのまま表示 */
    }
  }
  const d = display ? "$$" : "$";
  const esc = document.createElement("div");
  esc.textContent = d + tex + d;
  return esc.innerHTML;
}

function renderMarkdown(md) {
  let src = md || "";
  const math = [];
  const stash = (tex, display) => {
    const token = `@@KATEX${math.length}@@`;
    math.push({ tex: tex.trim(), display });
    return token;
  };
  src = src
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => stash(t, true))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => stash(t, false))
    .replace(/\$([^\s$][^$\n]*?)\$/g, (_, t) => stash(t, false));

  let html;
  if (window.marked) {
    html = marked.parse(src);
    if (window.DOMPurify) html = DOMPurify.sanitize(html);
  } else {
    const div = document.createElement("div");
    div.textContent = src;
    html = `<pre>${div.innerHTML}</pre>`;
  }

  return html.replace(/@@KATEX(\d+)@@/g, (_, i) => {
    const m = math[+i];
    return m ? renderTeX(m.tex, m.display) : "";
  });
}

const els = {
  openBtn: document.getElementById("openBtn"),
  fileInput: document.getElementById("fileInput"),
  fileName: document.getElementById("fileName"),
  status: document.getElementById("status"),
  pdfPane: document.getElementById("pdfPane"),
  viewport: document.getElementById("pdfViewport"),
  container: document.getElementById("pdfContainer"),
  results: document.getElementById("results"),
  fab: document.getElementById("explainFab"),
  resizer: document.getElementById("resizer"),
  sidePane: document.getElementById("sidePane"),
  modelSelect: document.getElementById("modelSelect"),
};


/* ---------- ローカルLLM(Ollama) セットアップ確認 ＋ モデル切替 ---------- */
// ヘッダーの <select> にインストール済みモデルを並べ、選んだら /api/model で
// 即座に切り替える（サーバが設定ファイルへ永続化）。Ollama 未起動・モデル
// 未取得のときは従来どおり結果欄にセットアップ案内を出す。
const Model = (() => {
  const sel = els.modelSelect;

  function showGuide(s) {
    const guide = document.createElement("div");
    guide.className = "card";
    if (!s.running) {
      guide.innerHTML = '<div class="body"></div>';
      guide.querySelector(".body").innerHTML = renderMarkdown(
        "### ⚙️ 初回セットアップ\n\n" +
        "解説にはローカルLLM（Ollama）を使います。クラウド送信なし・APIキー不要。\n\n" +
        "1. [Ollama をインストール](https://ollama.com/download)\n" +
        "2. Ollama アプリを起動\n" +
        "3. このページを再読み込み\n\n" +
        "モデルのダウンロードはアプリが自動で行います。"
      );
    } else {
      guide.innerHTML =
        '<div class="body"></div>' +
        '<div class="toolbar"><button class="pullBtn">📥 モデルをダウンロード（' + s.model + '）</button></div>';
      guide.querySelector(".body").innerHTML = renderMarkdown(
        "### ⚙️ モデルが必要です\n\n" +
        "下のボタンを押すとダウンロードが始まります（初回のみ・約3GB）。"
      );
      guide.querySelector(".pullBtn").onclick = async (ev) => {
        const btn = ev.target;
        btn.disabled = true;
        btn.textContent = "ダウンロード中…";
        const body = guide.querySelector(".body");
        try {
          const resp = await fetch("/api/pull-model", { method: "POST" });
          const reader = resp.body.getReader();
          const dec = new TextDecoder();
          let log = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            log += dec.decode(value, { stream: true });
            body.textContent = log;
          }
          if (log.includes("✅")) {
            setTimeout(() => location.reload(), 1500);
          } else {
            btn.disabled = false;
            btn.textContent = "📥 再試行";
          }
        } catch (e) {
          body.textContent = "⚠️ エラー: " + e.message;
          btn.disabled = false;
          btn.textContent = "📥 再試行";
        }
      };
    }
    els.results.prepend(guide);
  }

  function populate(s) {
    if (!sel) return;
    const models = s.models || [];
    if (!s.running || !models.length) {
      sel.hidden = true;
      return;
    }
    sel.innerHTML = "";
    // 現在のモデルが一覧に無ければ（タグ違い・未取得など）先頭に補っておく
    if (s.model && !models.includes(s.model)) {
      const o = document.createElement("option");
      o.value = s.model;
      o.textContent = s.model + (s.model_present ? "" : "（未取得）");
      sel.appendChild(o);
    }
    for (const name of models) {
      const o = document.createElement("option");
      o.value = name;
      o.textContent = name;
      sel.appendChild(o);
    }
    sel.value = s.model || models[0];
    sel.hidden = false;
  }

  async function refresh() {
    try {
      const r = await fetch("/api/llm-status");
      if (!r.ok) return;
      const s = await r.json();
      populate(s);
      if (!(s.running && s.model_present)) showGuide(s);
    } catch {}
  }

  if (sel) {
    sel.addEventListener("change", async () => {
      const model = sel.value;
      els.status.textContent = "モデル切替中…";
      try {
        const r = await fetch("/api/model", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model }),
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const s = await r.json();
        els.status.textContent = "モデル: " + (s.model || model);
        if (!s.model_present)
          showGuide({ running: true, model: s.model, model_present: false });
      } catch (e) {
        els.status.textContent = "⚠️ モデル切替失敗: " + e.message;
      }
    });
  }

  refresh();
  return { refresh };
})();

/* ---------- PDF 読み込み ---------- */
els.openBtn.onclick = () => els.fileInput.click();
els.fileInput.onchange = (e) => {
  if (e.target.files[0]) loadPdf(e.target.files[0]);
};
["dragover", "dragleave", "drop"].forEach((ev) =>
  els.pdfPane.addEventListener(ev, (e) => {
    e.preventDefault();
    els.pdfPane.classList.toggle("dragover", ev === "dragover");
    if (ev === "drop" && e.dataTransfer.files[0]) loadPdf(e.dataTransfer.files[0]);
  })
);

let pdfDoc = null;
let zoom = 1; // 確定ズーム（ページはこの倍率で描画済み）
let live = 1; // ジェスチャ中の一時的な見た目倍率（確定描画に対する相対）
let natW = 0;
let natH = 0; // 確定描画時のコンテンツ自然サイズ(px)
let renderToken = 0;
let loadToken = 0;
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 5;
const clampZoom = (z) => Math.min(Math.max(z, ZOOM_MIN), ZOOM_MAX);
const PAD = 24; // 余白(基準px)
const GAP = 16; // ページ間隔(基準px)

// 余白・間隔もズームに比例させる。これでライブ(transform)時の幾何と
// 確定再描画後の幾何が完全一致し、スワップ時の位置ズレが出ない。
function applySpacing(z) {
  els.container.style.padding = PAD * z + "px";
  els.container.style.gap = GAP * z + "px";
}

function resetLive() {
  live = 1;
  els.container.style.transform = "";
  els.container.style.width = "";
  els.viewport.style.display = ""; // CSS の display:contents に戻す
  els.viewport.style.width = "";
  els.viewport.style.height = "";
}

function pageScale(page, paneW) {
  const base = page.getViewport({ scale: 1 });
  return Math.min(Math.max(paneW, 360), 1200) / base.width;
}

// 1ページ分の要素を生成し描画まで完了させて返す（DOM未挿入でも可）
async function renderPageNode(page, scale, dpr) {
  const viewport = page.getViewport({ scale });
  const wrap = document.createElement("div");
  wrap.className = "pageWrap";
  wrap.style.width = viewport.width + "px";
  wrap.style.height = viewport.height + "px";
  wrap.style.setProperty("--scale-factor", scale);

  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(viewport.width * dpr);
  canvas.height = Math.floor(viewport.height * dpr);
  canvas.style.width = viewport.width + "px";
  canvas.style.height = viewport.height + "px";

  const textLayer = document.createElement("div");
  textLayer.className = "textLayer";
  textLayer.style.setProperty("--scale-factor", scale);

  wrap.appendChild(canvas);
  wrap.appendChild(textLayer);

  await page.render({
    canvasContext: canvas.getContext("2d"),
    viewport,
    transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
  }).promise;
  const tc = await page.getTextContent();
  await pdfjsLib.renderTextLayer({
    textContentSource: tc,
    container: textLayer,
    viewport,
  }).promise;
  const textItemIndexes = tc.items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => (item.str || "").trim())
    .map(({ index }) => index);
  const spans = [...textLayer.querySelectorAll("span")].filter((s) =>
    (s.textContent || "").trim()
  );
  spans.forEach((span, i) => {
    if (textItemIndexes[i] !== undefined) {
      span.dataset.itemIndex = String(textItemIndexes[i]);
    }
  });
  wrap._pageText = tc.items.map((i) => i.str).join(" ");
  wrap._textItems = tc.items.map((item, i) => {
    const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
    const fontHeight = Math.hypot(tx[2], tx[3]) || Math.abs(item.height * scale) || 1;
    const style = tc.styles && tc.styles[item.fontName];
    let fontAscent = fontHeight;
    if (style && style.ascent) fontAscent = style.ascent * fontHeight;
    else if (style && style.descent) fontAscent = (1 + style.descent) * fontHeight;
    const width = Math.abs(item.width * scale) || Math.max(1, (item.str || "").length * fontHeight * 0.45);
    const left = tx[4];
    const top = tx[5] - fontAscent;
    return {
      index: i,
      str: item.str || "",
      hasEOL: !!item.hasEOL,
      rel: {
        left,
        top,
        width,
        height: fontHeight,
      },
    };
  });
  return wrap;
}

// 初回ロード用: 空状態からページを順次追加（ここはチラつき対象外）
async function renderAll() {
  if (!pdfDoc) return false;
  const token = ++renderToken;
  els.container.innerHTML = "";
  resetLive();
  applySpacing(zoom);
  const paneW = els.pdfPane.clientWidth - 48;
  const dpr = window.devicePixelRatio || 1;
  for (let n = 1; n <= pdfDoc.numPages; n++) {
    if (token !== renderToken) return false;
    const page = await pdfDoc.getPage(n);
    const node = await renderPageNode(page, pageScale(page, paneW) * zoom, dpr);
    if (token !== renderToken) return false;
    els.container.appendChild(node);
  }
  natW = els.container.scrollWidth;
  natH = els.container.scrollHeight;
  els.status.textContent =
    `${pdfDoc.numPages} ページ` +
    (zoom !== 1 ? ` · ${Math.round(zoom * 100)}%` : "");
  return true;
}

// ArrayBuffer から描画（id・名前は確定済み前提）。getDocument は buf を
// 消費しうるので、ハッシュ計算・キャッシュ送信は呼び出し側で先に済ませる。
async function openPdfBuffer(buf, name, id) {
  const token = ++loadToken;
  if (!(await Memo.flush())) return false;
  if (token !== loadToken) return false;

  clearTimeout(commitTimer); // 保留中のズーム確定をキャンセル（別PDFと混ざらない）
  if (typeof PdfSelection !== "undefined") PdfSelection.clear();
  els.fab.hidden = true;
  els.fileName.textContent = name;
  els.fileName.classList.remove("muted");
  els.status.textContent = "読み込み中…";
  els.container.innerHTML = "";
  Bookmarks.load(id);
  Conversations.load(id);
  Ask.reset();
  const doc = await pdfjsLib.getDocument({
    data: buf,
    cMapUrl: "https://unpkg.com/pdfjs-dist@3.11.174/cmaps/",
    cMapPacked: true,
  }).promise;
  if (token !== loadToken) {
    try { await doc.destroy(); } catch {}
    return false;
  }
  pdfDoc = doc;
  zoom = 1;
  live = 1;
  els.pdfPane.scrollTop = 0;
  fetch("/api/last-pdf", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, name }),
  }).catch(() => {});
  const rendered = await renderAll();
  if (token !== loadToken || !rendered) return false;
  await Memo.openForPdf(id, name);
  if (token !== loadToken) return false;
  Bookmarks.renderMarkers();
  Finder.refresh();
  els.pdfPane.focus();
  return true;
}

// PDF 本体をローカル（自Mac内のサーバ）にキャッシュ。失敗しても描画は継続。
function cachePdf(id, buf) {
  fetch("/api/pdf/" + id, {
    method: "PUT",
    headers: { "Content-Type": "application/pdf" },
    body: buf,
  }).catch((e) => console.warn("[pdf-cache] 保存失敗", e));
}

async function loadPdf(file) {
  if (file.type && file.type !== "application/pdf") {
    els.status.textContent = "PDFではありません";
    return;
  }
  const buf = await file.arrayBuffer();
  const id = await sha256Id(buf.slice(0));
  cachePdf(id, buf.slice(0)); // 別コピーを保存（buf は描画で消費されるため）
  await openPdfBuffer(buf, file.name, id);
}

// キャッシュ済み PDF を id で復元（リロード時 / 一覧クリック時）。
// 未キャッシュなら false を返し、ビューワは触らない。
async function loadPdfFromCache(id, name) {
  try {
    const r = await fetch("/api/pdf/" + id);
    if (!r.ok) return false;
    const buf = await r.arrayBuffer();
    await openPdfBuffer(buf, name || "document.pdf", id);
    return true;
  } catch {
    return false;
  }
}

/* ---------- ズーム（ピンチ中はtransformで即追従 → 離したら再描画で鮮明化） ---------- */
let commitTimer = null;

// ジェスチャ中: CSS transform で即座に拡縮（60fps）。スクロール範囲は
// #pdfViewport を実寸で確保。指を止めたら commitZoom で pdf.js 再描画。
function liveZoom(targetEff, cxv, cyv) {
  if (!pdfDoc || !natW) return;
  targetEff = clampZoom(targetEff);
  const effOld = zoom * live;
  if (Math.abs(targetEff - effOld) < 1e-4) return;
  const sOld = live;
  live = targetEff / zoom;
  const ratio = live / sOld;

  els.container.style.width = natW + "px";
  els.container.style.transform = `scale(${live})`;
  els.viewport.style.display = "block"; // 実寸スペーサとして機能させる
  els.viewport.style.width = natW * live + "px";
  els.viewport.style.height = natH * live + "px";

  const pane = els.pdfPane; // カーソル直下の点を固定
  pane.scrollLeft = (pane.scrollLeft + cxv) * ratio - cxv;
  pane.scrollTop = (pane.scrollTop + cyv) * ratio - cyv;
  els.status.textContent = `ズーム ${Math.round(targetEff * 100)}%`;

  renderToken++; // 進行中の再描画を無効化
  clearTimeout(commitTimer);
  commitTimer = setTimeout(commitZoom, 200);
}

async function commitZoom() {
  if (!pdfDoc) return;
  if (typeof PdfSelection !== "undefined") PdfSelection.clear();
  const eff = clampZoom(zoom * live);
  const token = ++renderToken; // 進行中描画を無効化＆自分の番号
  const paneW = els.pdfPane.clientWidth - 48;
  const dpr = window.devicePixelRatio || 1;

  // 古い（transformで拡大中の）表示は残したまま、裏で全ページを新倍率で生成
  const nodes = [];
  for (let n = 1; n <= pdfDoc.numPages; n++) {
    const page = await pdfDoc.getPage(n);
    const node = await renderPageNode(page, pageScale(page, paneW) * eff, dpr);
    if (token !== renderToken) return; // 途中で新しいピンチ → この結果は破棄
    nodes.push(node);
  }

  // ここから await 無しの同期処理。旧→新を1フレームで入替え、
  // 空白や先頭へのジャンプのフレームを発生させない。
  const pane = els.pdfPane;
  const keepL = pane.scrollLeft;
  const keepT = pane.scrollTop;
  zoom = eff;
  els.fab.hidden = true; // 旧選択は消えるので解説ボタンも隠す
  els.container.replaceChildren(...nodes);
  resetLive(); // transform / 固定幅 / viewport サイズを解除
  applySpacing(zoom); // 余白も新倍率に比例（ライブ時の幾何と一致させる）
  natW = els.container.scrollWidth; // 同期レイアウト
  natH = els.container.scrollHeight;
  pane.scrollLeft = keepL; // 描画される前に復元 → ジャンプしない
  pane.scrollTop = keepT;
  els.status.textContent =
    `${pdfDoc.numPages} ページ` +
    (zoom !== 1 ? ` · ${Math.round(zoom * 100)}%` : "");
  Bookmarks.renderMarkers(); // 再描画でマーカーDOMも消えるので貼り直し
  Finder.refresh(); // ハイライトも貼り直し
}

els.pdfPane.addEventListener(
  "wheel",
  (e) => {
    if (!pdfDoc || !(e.ctrlKey || e.metaKey)) return; // ピンチは ctrlKey で届く
    e.preventDefault(); // ブラウザ全体ズームを抑止し PDF をズーム
    const r = els.pdfPane.getBoundingClientRect();
    liveZoom(
      zoom * live * Math.exp(-e.deltaY * 0.01),
      e.clientX - r.left,
      e.clientY - r.top
    );
  },
  { passive: false }
);

document.addEventListener("keydown", (e) => {
  if (!pdfDoc || !(e.metaKey || e.ctrlKey)) return;
  const r = els.pdfPane.getBoundingClientRect();
  const c = [r.width / 2, r.height / 2];
  const eff = zoom * live;
  if (e.key === "0") { e.preventDefault(); liveZoom(1, ...c); }
  else if (e.key === "=" || e.key === "+") { e.preventDefault(); liveZoom(eff * 1.15, ...c); }
  else if (e.key === "-") { e.preventDefault(); liveZoom(eff / 1.15, ...c); }
});

/* ---------- vim 風スクロール (hjkl) ---------- */
document.addEventListener("keydown", (e) => {
  if (!pdfDoc || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  const step = 60;
  const page = els.pdfPane.clientHeight * 0.8;
  switch (e.key) {
    case "j": els.pdfPane.scrollTop += step; break;
    case "k": els.pdfPane.scrollTop -= step; break;
    case "h": els.pdfPane.scrollLeft -= step; break;
    case "l": els.pdfPane.scrollLeft += step; break;
    case "d": els.pdfPane.scrollTop += page; break;
    case "u": els.pdfPane.scrollTop -= page; break;
    case "ArrowLeft": {
      const wraps = els.container.querySelectorAll(".pageWrap");
      const pr = els.pdfPane.getBoundingClientRect();
      for (let i = wraps.length - 1; i >= 0; i--) {
        if (wraps[i].getBoundingClientRect().top < pr.top - 10) {
          wraps[i].scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        }
      }
      break;
    }
    case "ArrowRight": {
      const wraps = els.container.querySelectorAll(".pageWrap");
      const pr = els.pdfPane.getBoundingClientRect();
      for (const w of wraps) {
        if (w.getBoundingClientRect().top > pr.top + 10) {
          w.scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        }
      }
      break;
    }
    default: return;
  }
  e.preventDefault();
});

/* ---------- PDF テキスト選択（独自選択） ---------- */
// 数式の斜体（𝑄 等の BMP 外文字）は UTF-16 サロゲートペアで表現される。
// 単純な substring/slice が片割れだけ残すと、サーバ側で UTF-8 エンコードに
// 失敗する（surrogates not allowed）ため、孤立サロゲートを除いて返す。
function sanitize(s) {
  return (s || "").replace(
    /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g,
    ""
  );
}
function safeSlice(s, n) {
  if (!s) return "";
  let cut = Math.min(n, s.length);
  const c = s.charCodeAt(cut - 1);
  if (cut < s.length && c >= 0xD800 && c <= 0xDBFF) cut--; // 高サロゲートで終わるなら一つ戻す
  return sanitize(s.slice(0, cut));
}

function pageTextItems(wrap) {
  const items = wrap._textItems || [];
  const wr = wrap.getBoundingClientRect();
  const sx = wr.width / (wrap.offsetWidth || wr.width || 1);
  const sy = wr.height / (wrap.offsetHeight || wr.height || 1);
  const pageIndex = [...els.container.children].indexOf(wrap);
  return items
    .map((item) => {
      if (!item || !item.str.trim()) return null;
      const rel = item.rel || {};
      if (!rel.width || !rel.height) return null;
      const r = {
        left: wr.left + rel.left * sx,
        top: wr.top + rel.top * sy,
        width: rel.width * sx,
        height: rel.height * sy,
      };
      r.right = r.left + r.width;
      r.bottom = r.top + r.height;
      return {
        ...item,
        wrap,
        pageIndex,
        rect: r,
        rel: {
          left: rel.left,
          top: rel.top,
          width: rel.width,
          height: rel.height,
        },
        cx: r.left + r.width / 2,
        cy: r.top + r.height / 2,
      };
    })
    .filter(Boolean);
}

function sliceByCodePoint(s, from, to) {
  return Array.from(s || "").slice(from, to).join("");
}

function itemSlice(item, from, to) {
  const chars = Array.from(item.str || "");
  const a = Math.max(0, Math.min(chars.length, from));
  const b = Math.max(a, Math.min(chars.length, to));
  const leftRatio = chars.length ? a / chars.length : 0;
  const rightRatio = chars.length ? b / chars.length : 1;
  const left = item.rect.left + item.rect.width * leftRatio;
  const width = item.rect.width * (rightRatio - leftRatio);
  return {
    ...item,
    str: sliceByCodePoint(item.str, a, b),
    rect: {
      left,
      right: left + width,
      top: item.rect.top,
      bottom: item.rect.bottom,
      width,
      height: item.rect.height,
    },
    rel: {
      ...item.rel,
      left: item.rel.left + item.rel.width * leftRatio,
      width: item.rel.width * (rightRatio - leftRatio),
    },
    cx: left + width / 2,
  };
}

function selectedTextFromItems(items) {
  items = items.filter((i) => (i.str || "").trim());
  if (!items.length) return "";
  const lines = [];
  let cur = [];
  let lastCy = null;
  const lineTol = Math.max(4, median(items.map((i) => i.rect.height)) * 0.65);
  for (const item of items) {
    if (lastCy !== null && Math.abs(item.cy - lastCy) > lineTol) {
      lines.push(cur);
      cur = [];
    }
    cur.push(item);
    lastCy = lastCy === null ? item.cy : (lastCy * 0.7 + item.cy * 0.3);
  }
  if (cur.length) lines.push(cur);
  return sanitize(
    lines
      .map((line) =>
        line
          .sort((a, b) => a.rect.left - b.rect.left)
          .map((i) => i.str)
          .join(" ")
          .replace(/\s+/g, " ")
          .trim()
      )
      .filter(Boolean)
      .join("\n")
      .replace(/-\n(?=[a-z])/g, "")
      .trim()
  );
}

function median(xs) {
  const a = xs.filter((x) => Number.isFinite(x)).sort((x, y) => x - y);
  return a.length ? a[Math.floor(a.length / 2)] : 0;
}

function selectionContext(items) {
  const wrap = items[0] && items[0].wrap;
  if (!wrap) return "";
  const indexes = items.filter((i) => i.wrap === wrap).map((i) => i.index);
  const first = Math.max(0, Math.min(...indexes) - 18);
  const last = Math.min((wrap._textItems || []).length - 1, Math.max(...indexes) + 18);
  return safeSlice(
    (wrap._textItems || [])
      .slice(first, last + 1)
      .map((i) => i.str)
      .join(" "),
    1800
  );
}

const PdfSelection = (() => {
  let drag = null;
  let current = null;

  function clear() {
    document.querySelectorAll(".select-hit").forEach((el) => el.remove());
    current = null;
    els.fab.hidden = true;
  }

  function point(e) {
    return { x: e.clientX, y: e.clientY };
  }

  function draw(items) {
    document.querySelectorAll(".select-hit").forEach((el) => el.remove());
    for (const item of items.filter((i) => (i.str || "").trim() && i.rel.width > 0)) {
      const h = document.createElement("div");
      h.className = "select-hit";
      h.style.left = item.rel.left + "px";
      h.style.top = item.rel.top + "px";
      h.style.width = item.rel.width + "px";
      h.style.height = item.rel.height + "px";
      item.wrap.appendChild(h);
    }
  }

  function allItems() {
    return [...els.container.querySelectorAll(".pageWrap")]
      .flatMap((wrap) => pageTextItems(wrap))
      .sort((a, b) => a.pageIndex - b.pageIndex || a.index - b.index);
  }

  function itemOrder(item) {
    return item.pageIndex * 100000 + item.index;
  }

  function charOffset(item, x) {
    const chars = Array.from(item.str || "");
    if (!chars.length || !item.rect.width) return 0;
    const ratio = Math.max(0, Math.min(1, (x - item.rect.left) / item.rect.width));
    return Math.round(ratio * chars.length);
  }

  function anchorAt(p, items) {
    let best = null;
    let bestScore = Infinity;
    for (const item of items) {
      const r = item.rect;
      const dx = p.x < r.left ? r.left - p.x : p.x > r.right ? p.x - r.right : 0;
      const dy = p.y < r.top ? r.top - p.y : p.y > r.bottom ? p.y - r.bottom : 0;
      const verticalPenalty = p.y >= r.top - r.height * 0.6 && p.y <= r.bottom + r.height * 0.6 ? 0 : 2000;
      const score = dy * 6 + dx + verticalPenalty;
      if (score < bestScore) {
        bestScore = score;
        best = item;
      }
    }
    if (!best) return null;
    return { item: best, offset: charOffset(best, p.x), order: itemOrder(best) };
  }

  function collect(a, b) {
    const items = allItems();
    const start = anchorAt(a, items);
    const end = anchorAt(b, items);
    if (!start || !end) return [];

    let first = start;
    let last = end;
    if (
      first.order > last.order ||
      (first.order === last.order && first.offset > last.offset)
    ) {
      first = end;
      last = start;
    }

    if (first.order === last.order) {
      const frag = itemSlice(first.item, first.offset, last.offset);
      return frag.str.trim() ? [frag] : [];
    }

    const out = [];
    for (const item of items) {
      const order = itemOrder(item);
      if (order < first.order || order > last.order) continue;
      const len = Array.from(item.str || "").length;
      if (order === first.order) out.push(itemSlice(item, first.offset, len));
      else if (order === last.order) out.push(itemSlice(item, 0, last.offset));
      else out.push(item);
    }
    return out.filter((item) => (item.str || "").trim());
  }

  function finish(e) {
    if (!drag) return;
    const end = point(e);
    const items = collect(drag.start, end);
    drag = null;
    if (!items.length) {
      clear();
      return;
    }
    draw(items);
    const rects = items.map((i) => i.rect);
    const rect = {
      left: Math.min(...rects.map((r) => r.left)),
      right: Math.max(...rects.map((r) => r.right)),
      top: Math.min(...rects.map((r) => r.top)),
      bottom: Math.max(...rects.map((r) => r.bottom)),
    };
    current = {
      text: selectedTextFromItems(items),
      rect,
      context: selectionContext(items),
    };
    if (!current.text) {
      clear();
      return;
    }
    els.fab.style.left = Math.max(8, rect.left) + "px";
    els.fab.style.top = rect.bottom + 8 + "px";
    els.fab.hidden = false;
    els.fab._payload = current;
  }

  els.pdfPane.addEventListener("pointerdown", (e) => {
    if (!pdfDoc || e.button !== 0 || e.target.closest("button,input,textarea,select,.bookmark-marker,.bookmark-guide")) return;
    const wrap = e.target.closest(".pageWrap");
    if (!wrap) return;
    window.getSelection()?.removeAllRanges();
    clear();
    drag = { start: point(e), pointerId: e.pointerId };
    els.pdfPane.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  els.pdfPane.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const items = collect(drag.start, point(e));
    draw(items);
    e.preventDefault();
  });

  els.pdfPane.addEventListener("pointerup", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    finish(e);
    els.pdfPane.releasePointerCapture(e.pointerId);
    e.preventDefault();
  });

  els.pdfPane.addEventListener("pointercancel", clear);

  return {
    current: () => current,
    clear,
  };
})();

function currentSelection() {
  return PdfSelection.current();
}

document.addEventListener("mousedown", (e) => {
  if (e.target !== els.fab && !e.target.closest(".pageWrap")) PdfSelection.clear();
});
els.pdfPane.addEventListener("scroll", () => {
  els.fab.hidden = true;
});

els.fab.onclick = () => {
  els.fab.hidden = true;
  const s = els.fab._payload;
  if (s) explain(s.text, s.context);
};

// ⌘/Ctrl + E
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "e") {
    e.preventDefault();
    const s = currentSelection();
    if (s) {
      els.fab.hidden = true;
      explain(s.text, s.context);
    }
  }
});

/* ---------- 会話履歴の保存・復元 ---------- */
const Conversations = (() => {
  const MAX_ITEMS = 100;
  let pdfId = null;
  let items = []; // [{type, src, body}]
  let saveTimer = null;

  function persist() {
    if (!pdfId) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      fetch("/api/conversations/" + pdfId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      }).catch(() => {});
    }, 500);
  }

  function push(type, src, body) {
    if (body.startsWith("> ⚠️")) return;
    items.push({ type, src, body });
    if (items.length > MAX_ITEMS) items = items.slice(-MAX_ITEMS);
    persist();
  }

  function renderCard(item) {
    const card = document.createElement("div");
    card.className = "card" + (item.type === "ask" ? " ask" : "");
    card.innerHTML = `
      <div class="src"></div>
      <div class="body"></div>
      <div class="toolbar"><button class="copyBtn">${
        item.type === "ask" ? "コピー(質問+回答)" : "コピー(原文+解説)"
      }</button></div>`;
    card.querySelector(".src").textContent = item.src;
    card.querySelector(".body").innerHTML = renderMarkdown(item.body);
    card.querySelector(".copyBtn").onclick = (ev) => {
      const prefix = item.type === "ask" ? `## Q\n${item.src}\n\n## A\n` : `> ${item.src}\n\n`;
      navigator.clipboard.writeText(prefix + item.body);
      ev.target.textContent = "コピーしました ✓";
      setTimeout(() => {
        ev.target.textContent = item.type === "ask" ? "コピー(質問+回答)" : "コピー(原文+解説)";
      }, 1500);
    };
    return card;
  }

  async function load(id) {
    pdfId = id;
    items = [];
    els.results.innerHTML = "";
    try {
      const r = await fetch("/api/conversations/" + id);
      if (!r.ok) return;
      const data = await r.json();
      items = data.items || [];
    } catch { return; }
    if (!items.length) {
      const base = "テキストを選択して ⌘E で日本語訳＋解説";
      const askForm = document.getElementById("askForm");
      const askHint = (askForm && !askForm.hidden) ? "<br />下の欄から論文全文を踏まえた質問もできます。" : "";
      els.results.innerHTML = '<div class="empty">' + base + askHint + '</div>';
      return;
    }
    const frag = document.createDocumentFragment();
    for (const item of items) frag.appendChild(renderCard(item));
    els.results.appendChild(frag);
  }

  function reset() {
    pdfId = null;
    items = [];
  }

  return { load, push, reset };
})();

/* ---------- 解説リクエスト ---------- */
async function explain(text, context) {
  const empty = els.results.querySelector(".empty");
  if (empty) empty.remove();

  const card = document.createElement("div");
  card.className = "card loading";
  card.innerHTML = `
    <div class="src"></div>
    <div class="body"></div>
    <div class="toolbar"><button class="copyBtn">コピー(原文+解説)</button></div>`;
  card.querySelector(".src").textContent = text;
  const body = card.querySelector(".body");
  body.textContent = "解説を生成中…";
  body.classList.add("waiting");
  els.results.prepend(card);
  els.results.scrollTop = 0;

  let acc = "";
  let pinTop = true;
  const onScroll = () => { if (els.results.scrollTop > 10) pinTop = false; };
  els.results.addEventListener("scroll", onScroll);
  try {
    const resp = await fetch("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      acc += dec.decode(value, { stream: true });
      body.classList.remove("waiting");
      body.innerHTML = renderMarkdown(acc);
      if (pinTop) els.results.scrollTop = 0;
    }
  } catch (e) {
    acc = `> ⚠️ ${e.message}`;
    body.innerHTML = renderMarkdown(acc);
  } finally {
    els.results.removeEventListener("scroll", onScroll);
  }
  card.classList.remove("loading");
  if (acc) Conversations.push("explain", text, acc);

  card.querySelector(".copyBtn").onclick = (ev) => {
    navigator.clipboard.writeText(`> ${text}\n\n${acc}`);
    ev.target.textContent = "コピーしました ✓";
    setTimeout(() => (ev.target.textContent = "コピー(原文+解説)"), 1500);
  };
}

/* ---------- 内容についての質問（クラウドLLM・論文全文を文脈に） ---------- */

// 全ページの抽出テキストを結合して論文全文を作る。renderPageNode が各 .pageWrap の
// _pageText に保存済みなので、ここでは並べて繋ぐだけ。
function paperFullText() {
  return [...els.container.querySelectorAll(".pageWrap")]
    .map((w) => w._pageText || "")
    .join("\n\n")
    .trim();
}

const Ask = (() => {
  const form = document.getElementById("askForm");
  const input = document.getElementById("askInput");
  const quote = document.getElementById("askQuote");
  const send = document.getElementById("askSend");
  const meta = document.getElementById("askMeta");
  if (!form || !input) return { reset() {} };

  let history = []; // [{role:"user"|"model", text}] — PDF単位の会話履歴
  let quoted = ""; // いま引用中の選択テキスト（送信まで保持）
  let busy = false;

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }
  input.addEventListener("input", autosize);

  // 引用の設定/解除。PDF を選択するとキャプチャし、送信するかクリックするまで保持。
  function setQuote(text) {
    quoted = text || "";
    if (quoted) {
      const short = quoted.replace(/\s+/g, " ").slice(0, 200);
      quote.textContent = "“" + short + (quoted.length > 200 ? "…" : "") + "”";
      quote.title = "クリックで引用を外す";
      quote.hidden = false;
    } else {
      quote.hidden = true;
    }
  }
  // PDF テキストを選択したら引用に取り込む（textarea 入力中も消えないよう保持）。
  document.addEventListener("mouseup", () => {
    const s = currentSelection();
    if (s && s.text) setQuote(s.text);
  });
  quote.addEventListener("click", () => setQuote(""));

  function setFormVisible(visible) {
    form.hidden = !visible;
  }

  async function checkStatus() {
    try {
      const r = await fetch("/api/ask-status");
      if (!r.ok) return;
      const s = await r.json();
      const NAMES = { gemini: "Gemini", openai: "OpenAI", anthropic: "Claude" };
      if (s.available) {
        setFormVisible(true);
        meta.textContent = "質問: " + (NAMES[s.provider] || s.provider) + " " + s.model;
      } else {
        setFormVisible(false);
        meta.textContent = "";
      }
    } catch {}
  }

  async function submit() {
    const q = input.value.trim();
    if (!q || busy) return;
    if (!pdfDoc) {
      meta.textContent = "先に PDF を開いてください";
      return;
    }
    const selection = quoted;

    busy = true;
    send.disabled = true;
    input.value = "";
    autosize();
    setQuote("");

    const empty = els.results.querySelector(".empty");
    if (empty) empty.remove();
    const card = document.createElement("div");
    card.className = "card ask loading";
    card.innerHTML = `
      <div class="src"></div>
      <div class="body"></div>
      <div class="toolbar"><button class="copyBtn">コピー(質問+回答)</button></div>`;
    card.querySelector(".src").textContent =
      (selection ? "「" + selection.replace(/\s+/g, " ").slice(0, 200) + "」\n\n" : "") +
      "Q: " + q;
    const body = card.querySelector(".body");
    body.textContent = "回答を生成中…";
    body.classList.add("waiting");
    els.results.prepend(card);
    els.results.scrollTop = 0;

    let acc = "";
    let pinTop = true;
    const onScroll = () => { if (els.results.scrollTop > 10) pinTop = false; };
    els.results.addEventListener("scroll", onScroll);
    try {
      const resp = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, paper: paperFullText(), selection, history }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        acc += dec.decode(value, { stream: true });
        body.classList.remove("waiting");
        body.innerHTML = renderMarkdown(acc);
        if (pinTop) els.results.scrollTop = 0;
      }
      history.push({
        role: "user",
        text: (selection ? "（引用）" + selection + "\n" : "") + q,
      });
      history.push({ role: "model", text: acc });
    } catch (e) {
      acc = `> ⚠️ ${e.message}`;
      body.innerHTML = renderMarkdown(acc);
    } finally {
      els.results.removeEventListener("scroll", onScroll);
    }
    card.classList.remove("loading");
    const srcText = card.querySelector(".src").textContent;
    if (acc) Conversations.push("ask", srcText, acc);
    card.querySelector(".copyBtn").onclick = (ev) => {
      navigator.clipboard.writeText(`## Q\n${q}\n\n## A\n${acc}`);
      ev.target.textContent = "コピーしました ✓";
      setTimeout(() => (ev.target.textContent = "コピー(質問+回答)"), 1500);
    };
    busy = false;
    send.disabled = false;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });
  // ⌘/Ctrl+Enter で送信（Enter 単独は改行のまま）。
  input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  });

  // ⌘/ で質問欄にフォーカス（質問機能有効時のみ）
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "/") {
      if (form.hidden) return;
      e.preventDefault();
      input.focus();
    }
  });

  function reset() {
    history = [];
    setQuote("");
  }

  checkStatus();
  return { reset, checkStatus };
})();

/* ---------- サイドペイン幅リサイズ ---------- */
let dragging = false;
els.resizer.addEventListener("pointerdown", (e) => {
  dragging = true;
  els.resizer.setPointerCapture(e.pointerId);
  document.body.style.userSelect = "none";
  e.preventDefault();
});
document.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const w = Math.min(Math.max(window.innerWidth - e.clientX, 280), 720);
  els.sidePane.style.width = w + "px";
});
document.addEventListener("pointerup", () => {
  if (!dragging) return;
  dragging = false;
  document.body.style.userSelect = "";
  localStorage.setItem("sidePaneW", els.sidePane.style.width);
});
{
  const savedW = localStorage.getItem("sidePaneW");
  if (savedW) els.sidePane.style.width = savedW;
}

/* ---------- メモパネル幅リサイズ（columnsレイアウト用） ---------- */
(() => {
  const memoResizer = document.getElementById("memoResizer");
  const memoPanel = document.getElementById("memoPanel");
  if (!memoResizer || !memoPanel) return;
  let drag = false;
  memoResizer.addEventListener("pointerdown", (e) => {
    drag = true;
    memoResizer.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const mainRect = document.getElementById("main").getBoundingClientRect();
    const w = Math.min(Math.max(e.clientX - mainRect.left, 200), 600);
    memoPanel.style.setProperty("--memo-col-w", w + "px");
  });
  document.addEventListener("pointerup", () => {
    if (!drag) return;
    drag = false;
    document.body.style.userSelect = "";
  });
})();

/* ---------- ブックマーク（任意の行に印をつけて行き来する） ---------- */
// 位置は { pageIndex, y(0..1) } で持つので、ズームしても倍率に追従する。
// PDF ごとに localStorage に保存（ローカル完結。PDFキャッシュと同じ思想）。
const Bookmarks = (() => {
  const KEY = (id) => `bookmarks_${id}`; // 旧localStorageキー（オフライン用バックアップ兼・移行元）
  const API = (id) => `/api/bookmarks/${id}`;
  const NEAR = 0.02; // 同位置とみなす許容（ページ高比）
  const btn = document.getElementById("bookmarkBtn");
  let pdfId = null;
  let items = [];

  const cmp = (a, b) => a.pageIndex - b.pageIndex || a.y - b.y;
  const readLocal = (id) => {
    try {
      const a = JSON.parse(localStorage.getItem(KEY(id)) || "[]");
      return Array.isArray(a) ? a : [];
    } catch {
      return [];
    }
  };

  // サーバ(bookmarks/<id>.json)を正とする。未取得時は localStorage にフォールバックし、
  // サーバが空で旧localStorageに残っていれば一度だけサーバへ移行する。
  async function load(id) {
    pdfId = id;
    items = [];
    updateBadge();
    let serverItems = null;
    try {
      const r = await fetch(API(id));
      if (r.ok) serverItems = (await r.json()).items || [];
    } catch {
      /* オフライン/サーバ不達 */
    }
    if (pdfId !== id) return; // 取得中に別PDFへ切り替わった
    if (serverItems === null) {
      items = readLocal(id); // サーバ不達 → ローカルバックアップ
    } else if (serverItems.length === 0) {
      const local = readLocal(id);
      items = local;
      if (local.length) persist(); // 旧ローカルしおりをサーバへ移行
    } else {
      items = serverItems;
      try {
        localStorage.setItem(KEY(id), JSON.stringify(items)); // オフライン用に控える
      } catch {}
    }
    items.sort(cmp);
    updateBadge();
    renderMarkers(); // fetch 完了後に確実に貼り直す（描画完了前に呼ばれていても）
  }
  function persist() {
    if (!pdfId) return;
    const id = pdfId;
    try {
      localStorage.setItem(KEY(id), JSON.stringify(items)); // オフライン用バックアップ
    } catch {}
    fetch(API(id), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }).catch(() => flash("⚠️ しおりのサーバ保存に失敗（ローカルには保存済み）"));
  }
  function updateBadge() {
    if (btn) btn.textContent = `🔖 ${items.length}`;
    const group = document.getElementById("bookmarkGroup");
    if (group) group.classList.toggle("empty", items.length === 0);
  }
  function flash(msg) {
    els.status.textContent = msg;
  }

  function pages() {
    return els.container.querySelectorAll(".pageWrap");
  }

  function renderMarkers() {
    document.querySelectorAll(".bookmark-marker").forEach((el) => el.remove());
    const ps = pages();
    items.forEach((b, idx) => {
      const wrap = ps[b.pageIndex];
      if (!wrap) return;
      const m = document.createElement("div");
      m.className = "bookmark-marker";
      m.title = `#${idx + 1} クリックで削除`;
      m.style.top = b.y * 100 + "%";
      m.textContent = String(idx + 1);
      m.addEventListener("mousedown", (e) => e.stopPropagation());
      m.addEventListener("click", (e) => {
        e.stopPropagation();
        remove(idx);
      });
      wrap.appendChild(m);
    });
    updateBadge();
  }

  // 現在位置を { pageIndex, y } で返す（選択範囲があればその中央、なければビューポート上1/4）
  function currentLocation() {
    const ps = pages();
    if (!ps.length) return null;
    const activeSelection =
      typeof PdfSelection !== "undefined" && PdfSelection.current();
    if (activeSelection && activeSelection.rect) {
      const centerY =
        activeSelection.rect.top +
        (activeSelection.rect.bottom - activeSelection.rect.top) / 2;
      for (let i = 0; i < ps.length; i++) {
        const wr = ps[i].getBoundingClientRect();
        if (wr.top <= centerY && wr.bottom >= centerY) {
          return {
            pageIndex: i,
            y: Math.max(0, Math.min(1, (centerY - wr.top) / wr.height)),
          };
        }
      }
    }
    const sel = window.getSelection();
    if (sel && sel.rangeCount && sel.toString().trim()) {
      const node = sel.anchorNode;
      const elNode = node && (node.nodeType === 1 ? node : node.parentElement);
      const wrap = elNode && elNode.closest(".pageWrap");
      if (wrap) {
        const r = sel.getRangeAt(0).getBoundingClientRect();
        const wr = wrap.getBoundingClientRect();
        return {
          pageIndex: [...ps].indexOf(wrap),
          y: Math.max(0, Math.min(1, (r.top + r.height / 2 - wr.top) / wr.height)),
        };
      }
    }
    const paneRect = els.pdfPane.getBoundingClientRect();
    const targetY = paneRect.top + paneRect.height * 0.25; // 「いま読んでる行」の感覚に近い位置
    for (let i = 0; i < ps.length; i++) {
      const r = ps[i].getBoundingClientRect();
      if (r.top <= targetY && r.bottom >= targetY) {
        return {
          pageIndex: i,
          y: Math.max(0, Math.min(1, (targetY - r.top) / r.height)),
        };
      }
    }
    // 全ページが上 or 下: 最も近いページの端に
    let best = 0;
    let bestDist = Infinity;
    ps.forEach((p, i) => {
      const r = p.getBoundingClientRect();
      const d = Math.min(Math.abs(r.top - targetY), Math.abs(r.bottom - targetY));
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    const r = ps[best].getBoundingClientRect();
    return { pageIndex: best, y: r.top > targetY ? 0 : 1 };
  }

  function toggle() {
    if (!pdfDoc) {
      flash("PDFを開いてからブックマークできます");
      return;
    }
    const loc = currentLocation();
    if (!loc) return;
    const i = items.findIndex(
      (b) => b.pageIndex === loc.pageIndex && Math.abs(b.y - loc.y) < NEAR
    );
    if (i >= 0) {
      items.splice(i, 1);
      flash(`ブックマーク削除（残り ${items.length}）`);
    } else {
      items.push({ ...loc, t: Date.now() });
      items.sort(cmp);
      flash(`ブックマーク追加（合計 ${items.length}）`);
    }
    persist();
    renderMarkers();
  }

  function remove(idx) {
    items.splice(idx, 1);
    persist();
    renderMarkers();
    flash(`ブックマーク削除（残り ${items.length}）`);
  }

  async function clear() {
    if (!items.length) return;
    if (!(await appConfirm(`ブックマーク ${items.length} 件を全て削除しますか？`))) return;
    items = [];
    persist();
    renderMarkers();
    flash("ブックマークを全削除");
  }

  function jumpTo(idx) {
    const b = items[idx];
    if (!b) return;
    const wrap = pages()[b.pageIndex];
    if (!wrap) return;
    const wr = wrap.getBoundingClientRect();
    const pr = els.pdfPane.getBoundingClientRect();
    const delta = wr.top - pr.top + wr.height * b.y - pr.height * 0.25;
    els.pdfPane.scrollTop += delta;
    // ジャンプ先のマーカーを一瞬光らせる（renderMarkers後でも生きるよう DOM 再取得）
    requestAnimationFrame(() => {
      document
        .querySelectorAll(".bookmark-marker.flash")
        .forEach((el) => el.classList.remove("flash"));
      const m = document.querySelectorAll(".bookmark-marker")[idx];
      if (m) {
        m.classList.add("flash");
        setTimeout(() => m.classList.remove("flash"), 1200);
      }
    });
    flash(`ブックマーク #${idx + 1} / ${items.length}`);
  }

  function next() {
    if (!items.length) return flash("ブックマークはまだありません（⌘B で追加）");
    const loc = currentLocation();
    let i = items.findIndex(
      (b) =>
        b.pageIndex > loc.pageIndex ||
        (b.pageIndex === loc.pageIndex && b.y > loc.y + 0.005)
    );
    if (i < 0) i = 0; // 末尾を超えたら巡回
    jumpTo(i);
  }

  function prev() {
    if (!items.length) return flash("ブックマークはまだありません（⌘B で追加）");
    const loc = currentLocation();
    let i = -1;
    for (let k = items.length - 1; k >= 0; k--) {
      const b = items[k];
      if (
        b.pageIndex < loc.pageIndex ||
        (b.pageIndex === loc.pageIndex && b.y < loc.y - 0.005)
      ) {
        i = k;
        break;
      }
    }
    if (i < 0) i = items.length - 1;
    jumpTo(i);
  }

  // ガイド矢印: しおり挿入位置を示す（マーカーと同じ形で薄く表示）
  const guide = document.createElement("div");
  guide.className = "bookmark-guide";
  guide.textContent = "+";
  guide.hidden = true;

  function showGuide() {
    if (!pdfDoc) return;
    const loc = currentLocation();
    if (!loc) return;
    const wrap = pages()[loc.pageIndex];
    if (!wrap) return;
    guide.style.top = loc.y * 100 + "%";
    guide.hidden = false;
    if (guide.parentNode !== wrap) wrap.appendChild(guide);
  }
  function hideGuide() { guide.hidden = true; }

  // ボタン: クリックでトグル、Shift+クリックで全削除
  if (btn) {
    btn.addEventListener("click", (e) => {
      hideGuide();
      if (e.shiftKey) clear();
      else toggle();
    });
    btn.addEventListener("pointerenter", showGuide);
    btn.addEventListener("pointerleave", hideGuide);
  }

  // 前/次ナビボタン
  const prevBtn = document.getElementById("bookmarkPrev");
  const nextBtn = document.getElementById("bookmarkNext");
  if (prevBtn) prevBtn.addEventListener("click", () => prev());
  if (nextBtn) nextBtn.addEventListener("click", () => next());

  // ショートカット。入力欄にフォーカス中は誤爆を避ける。
  function isEditing(t) {
    return (
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)
    );
  }
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "d") {
      if (isEditing(e.target)) return;
      e.preventDefault();
      toggle();
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "j") {
      if (isEditing(e.target)) return;
      e.preventDefault();
      next();
    } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "k") {
      if (isEditing(e.target)) return;
      e.preventDefault();
      prev();
    }
  });

  updateBadge();
  return { load, renderMarkers, toggle, next, prev, clear };
})();

/* ---------- ページ内検索（⌘F） ---------- */
// pdf.js の textLayer span にインラインで <mark> を埋め込む方式。
// span のレイアウト位置は変えないので選択動作と表示は保たれる。
const Finder = (() => {
  const bar = document.getElementById("findBar");
  const input = document.getElementById("findInput");
  const countEl = document.getElementById("findCount");
  const prevBtn = document.getElementById("findPrev");
  const nextBtn = document.getElementById("findNext");
  const closeBtn = document.getElementById("findClose");
  if (!bar || !input) return { open() {}, close() {}, refresh() {} };

  let hits = []; // [mark, ...]
  let cur = -1;

  function isOpen() {
    return !bar.hidden;
  }
  function open() {
    bar.hidden = false;
    input.focus();
    input.select();
    if (input.value) search(input.value);
  }
  function close() {
    bar.hidden = true;
    clearHits();
    cur = -1;
    countEl.textContent = "0 / 0";
  }
  function clearHits() {
    // <mark> を外して textNode を span 直下に戻す
    document
      .querySelectorAll(".pageWrap .textLayer mark.search-hit")
      .forEach((m) => {
        const p = m.parentNode;
        if (!p) return;
        while (m.firstChild) p.insertBefore(m.firstChild, m);
        p.removeChild(m);
        p.normalize();
      });
    hits = [];
  }
  function search(q) {
    clearHits();
    cur = -1;
    if (!q || !pdfDoc) {
      countEl.textContent = "0 / 0";
      return;
    }
    const needle = q.toLowerCase();
    const pages = els.container.querySelectorAll(".pageWrap");
    pages.forEach((wrap) => {
      const tl = wrap.querySelector(".textLayer");
      if (!tl) return;
      tl.querySelectorAll("span").forEach((span) => {
        const text = span.textContent;
        if (!text) return;
        const lo = text.toLowerCase();
        const ranges = [];
        let pos = 0;
        for (;;) {
          const i = lo.indexOf(needle, pos);
          if (i < 0) break;
          ranges.push([i, i + needle.length]);
          pos = i + needle.length;
        }
        if (!ranges.length) return;
        // 子要素が複雑な span は扱わない（pdf.js は通常 1 textNode）
        if (span.children.length) return;
        const frag = document.createDocumentFragment();
        let last = 0;
        for (const [a, b] of ranges) {
          if (a > last) frag.appendChild(document.createTextNode(text.slice(last, a)));
          const m = document.createElement("mark");
          m.className = "search-hit";
          m.textContent = text.slice(a, b);
          frag.appendChild(m);
          hits.push(m);
          last = b;
        }
        if (last < text.length)
          frag.appendChild(document.createTextNode(text.slice(last)));
        span.textContent = "";
        span.appendChild(frag);
      });
    });
    if (hits.length) {
      cur = 0;
      focusCurrent(true);
    } else {
      countEl.textContent = "0 / 0";
    }
  }
  function focusCurrent(scroll) {
    hits.forEach((m, i) => m.classList.toggle("current", i === cur));
    countEl.textContent = hits.length ? `${cur + 1} / ${hits.length}` : "0 / 0";
    if (scroll && cur >= 0) {
      const r = hits[cur].getBoundingClientRect();
      const pr = els.pdfPane.getBoundingClientRect();
      const delta = r.top - pr.top + r.height / 2 - pr.height / 3;
      els.pdfPane.scrollTop += delta;
    }
  }
  function next() {
    if (!hits.length) return;
    cur = (cur + 1) % hits.length;
    focusCurrent(true);
  }
  function prev() {
    if (!hits.length) return;
    cur = (cur - 1 + hits.length) % hits.length;
    focusCurrent(true);
  }
  // PDF差替・ズーム後に呼ぶ: 開いていれば再ハイライト
  function refresh() {
    if (isOpen() && input.value) {
      // hits は古い DOM を参照しているので捨てて再走査。
      hits = [];
      search(input.value);
    }
  }

  // 入力中の検索は軽くデバウンス（大きなPDFで全span走査するため）
  let debounce = null;
  input.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => search(input.value), 100);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.shiftKey) prev();
      else next();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });
  prevBtn.onclick = prev;
  nextBtn.onclick = next;
  closeBtn.onclick = close;

  // ⌘F / Ctrl+F でオープン（テキストエリア中でもPDF検索を優先）
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "f") {
      e.preventDefault();
      open();
    }
  });

  return { open, close, refresh };
})();

/* ---------- メモ（論文ごとの自分用まとめ） ---------- */
async function sha256Id(buf) {
  const h = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(h)]
    .slice(0, 8)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/* ---------- Markdown リスト編集支援（textarea 用） ----------
 * - Enter: リスト行なら同じレベルでマーカーを継続（番号付きは+1）。
 *          本文が空のリスト項目で Enter を押すと 1 段だけ左へ（最上位なら解除）。
 * - Tab / Shift+Tab: 行(複数選択可)を右/左にインデント。
 * IME 変換確定の Enter は無視する。
 */
const LIST_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?(.*)$/;
const LIST_INDENT = "  "; // 2スペース = 1レベル

function fireInput(ta) {
  ta.dispatchEvent(new Event("input", { bubbles: true }));
}

function lineRegion(v, s, e) {
  const start = v.lastIndexOf("\n", s - 1) + 1;
  const probe = e > s ? e - 1 : e; // 末尾が行頭(改行直後)の空行を巻き込まない
  let end = v.indexOf("\n", probe);
  if (end === -1) end = v.length;
  return { start, end };
}

function indentSelection(ta) {
  const v = ta.value;
  const s = ta.selectionStart, e = ta.selectionEnd;
  const { start, end } = lineRegion(v, s, e);
  const lines = v.slice(start, end).split("\n");
  const out = lines.map((l) => LIST_INDENT + l).join("\n");
  ta.value = v.slice(0, start) + out + v.slice(end);
  const w = LIST_INDENT.length;
  ta.setSelectionRange(s + w, e + w * lines.length);
  fireInput(ta);
}

function outdentSelection(ta) {
  const v = ta.value;
  const s = ta.selectionStart, e = ta.selectionEnd;
  const { start, end } = lineRegion(v, s, e);
  const lines = v.slice(start, end).split("\n");
  let firstCut = 0, totalCut = 0;
  const out = lines
    .map((l, i) => {
      let n = 0;
      if (l.startsWith(LIST_INDENT)) n = LIST_INDENT.length;
      else if (l.startsWith("\t")) n = 1;
      else { const m = /^ +/.exec(l); if (m) n = Math.min(m[0].length, LIST_INDENT.length); }
      if (i === 0) firstCut = n;
      totalCut += n;
      return l.slice(n);
    })
    .join("\n");
  ta.value = v.slice(0, start) + out + v.slice(end);
  const newS = Math.max(start, s - firstCut);
  ta.setSelectionRange(newS, Math.max(newS, e - totalCut));
  fireInput(ta);
}

function handleListEnter(ta, ev) {
  const v = ta.value, s = ta.selectionStart;
  if (s !== ta.selectionEnd) return; // 範囲選択中は通常改行
  const ls = v.lastIndexOf("\n", s - 1) + 1;
  let le = v.indexOf("\n", s);
  if (le === -1) le = v.length;
  const m = LIST_RE.exec(v.slice(ls, le));
  if (!m) return;
  const [, indent, marker, space, checkbox, content] = m;
  ev.preventDefault();

  // 本文が空 → 左へ1段ずらす（最上位ならマーカー解除）
  if (content.trim() === "") {
    let line;
    if (indent.length >= LIST_INDENT.length)
      line = indent.slice(LIST_INDENT.length) + marker + space + (checkbox ? "[ ] " : "");
    else if (indent.length > 0)
      line = marker + space + (checkbox ? "[ ] " : "");
    else line = "";
    ta.value = v.slice(0, ls) + line + v.slice(le);
    const c = ls + line.length;
    ta.setSelectionRange(c, c);
    fireInput(ta);
    return;
  }

  // 本文あり → 同レベルでマーカー継続（番号付きはインクリメント）
  let next = marker;
  const num = /^(\d+)([.)])$/.exec(marker);
  if (num) next = parseInt(num[1], 10) + 1 + num[2];
  const ins = "\n" + indent + next + space + (checkbox ? "[ ] " : "");
  ta.setSelectionRange(s, s);
  if (!document.execCommand("insertText", false, ins)) {
    ta.value = v.slice(0, s) + ins + v.slice(s);
    const c = s + ins.length;
    ta.setSelectionRange(c, c);
    fireInput(ta);
  }
}

// execCommand("insertText") で置換すると undo 履歴が保たれる。失敗時は value 直接書き換え。
function replaceRange(ta, start, end, text) {
  ta.setSelectionRange(start, end);
  if (!document.execCommand("insertText", false, text)) {
    const v = ta.value;
    ta.value = v.slice(0, start) + text + v.slice(end);
    fireInput(ta);
  }
}

// ⌘/Ctrl+B: 選択を **太字** で囲む / 既に囲まれていれば外す。未選択なら **** を挿入して中へ。
const BOLD = "**";
function toggleBold(ta) {
  const v = ta.value;
  const s = ta.selectionStart, e = ta.selectionEnd;
  if (s === e) {
    replaceRange(ta, s, s, BOLD + BOLD);
    ta.setSelectionRange(s + 2, s + 2);
    return;
  }
  const sel = v.slice(s, e);
  if (sel.length >= 4 && sel.startsWith(BOLD) && sel.endsWith(BOLD)) {
    const inner = sel.slice(2, -2); // 選択ごと太字 → 中身だけに
    replaceRange(ta, s, e, inner);
    ta.setSelectionRange(s, s + inner.length);
  } else if (v.slice(s - 2, s) === BOLD && v.slice(e, e + 2) === BOLD) {
    replaceRange(ta, s - 2, e + 2, sel); // 選択の外側に ** → 外す
    ta.setSelectionRange(s - 2, s - 2 + sel.length);
  } else {
    replaceRange(ta, s, e, BOLD + sel + BOLD); // 太字化
    ta.setSelectionRange(s + 2, e + 2);
  }
}

// ⌘/Ctrl+Shift+H: 行頭に "## " を付ける / 既に付いていれば外す（他レベルの見出しは ## に統一）。
const HEADING_RE = /^(#{1,6})\s+/;
const H2 = "## ";
function toggleHeading(ta) {
  const v = ta.value;
  const s = ta.selectionStart, e = ta.selectionEnd;
  const { start, end } = lineRegion(v, s, e);
  const lines = v.slice(start, end).split("\n");
  const off = lines[0].startsWith(H2); // 先頭行が ## なら全行で解除、でなければ ## に揃える
  let dFirst = 0, dTotal = 0;
  const out = lines
    .map((l, i) => {
      const m = HEADING_RE.exec(l);
      const nl = off
        ? (m ? l.slice(m[0].length) : l)
        : (m ? H2 + l.slice(m[0].length) : H2 + l);
      const d = nl.length - l.length;
      if (i === 0) dFirst = d;
      dTotal += d;
      return nl;
    })
    .join("\n");
  replaceRange(ta, start, end, out);
  const ns = Math.max(start, s + dFirst);
  ta.setSelectionRange(ns, Math.max(ns, e + dTotal));
}

function attachListEditing(ta) {
  ta.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return; // IME 変換中
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.shiftKey && e.key.toLowerCase() === "h") {
      e.preventDefault();
      toggleHeading(ta);
    } else if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
      e.preventDefault();
      toggleBold(ta);
    } else if (e.key === "Tab") {
      e.preventDefault();
      e.shiftKey ? outdentSelection(ta) : indentSelection(ta);
    } else if (e.key === "Enter" && !e.shiftKey) {
      handleListEnter(ta, e);
    }
  });
}

/* ---------- CodeMirror（メモ本文のリッチ編集）用のトグル ---------- */
// ⌘/Ctrl+B 相当: 選択を **太字** で囲む / 既に囲まれていれば外す。未選択なら **** を挿入。
function cmToggleBold(cm) {
  if (cm.somethingSelected()) {
    const sel = cm.getSelection();
    if (sel.length >= 4 && sel.startsWith(BOLD) && sel.endsWith(BOLD)) {
      cm.replaceSelection(sel.slice(2, -2), "around");
    } else {
      cm.replaceSelection(BOLD + sel + BOLD, "around");
    }
  } else {
    const c = cm.getCursor();
    cm.replaceRange(BOLD + BOLD, c);
    cm.setCursor({ line: c.line, ch: c.ch + 2 });
  }
}
// ⌘/Ctrl+Shift+H 相当: 選択行頭に "## " を付ける / 既にあれば外す（他レベルは ## に統一）。
function cmToggleHeading(cm) {
  const from = cm.getCursor("from"), to = cm.getCursor("to");
  const off = H2_RE.test(cm.getLine(from.line)); // 先頭行が ## なら全行で解除
  for (let l = from.line; l <= to.line; l++) {
    const text = cm.getLine(l);
    const m = HEADING_RE.exec(text);
    const nl = off
      ? (m ? text.slice(m[0].length) : text)
      : (m ? H2 + text.slice(m[0].length) : H2 + text);
    if (nl !== text)
      cm.replaceRange(nl, { line: l, ch: 0 }, { line: l, ch: text.length });
  }
}
const H2_RE = /^##\s/;

const Memo = (() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("memoPanel");
  const toggle = $("memoToggle");
  const titleEl = $("memoTitle");
  const bodyEl = $("memoBody");
  const previewEl = $("memoPreview");
  const previewBtn = $("memoPreviewBtn");
  const statusEl = $("memoStatus");
  const labelEl = document.querySelector("#memoHead .memoLabel");
  const tabsEl = $("memoTabs");
  const memoView = $("memoMemoView");
  const summaryView = $("memoSummaryView");

  // DOM が古い（キャッシュ食い違い等）場合でも本体を巻き込まない
  if (!panel || !toggle || !titleEl || !bodyEl || !summaryView) {
    console.warn("[memo] UI要素が見つからないためメモ機能を無効化（ハードリロード推奨）");
    return { openForPdf() {}, openExisting() {}, setOpen() {}, async flush() { return true; } };
  }

  // 落合フォーマット6項目。key はサーバの SUMMARY_KEYS と一致させること。
  // 出典: 落合陽一「先端技術とメディア表現1 #FTMA15」(本ツールの考案ではない)
  // https://www.slideshare.net/Ochyai/1-ftma15
  const SUMMARY_FIELDS = [
    ["what", "① どんなもの？", "この研究を一言で。何を達成したか。"],
    ["prior", "② 先行研究と比べてどこがすごい？", "既存手法・従来研究との違い／優位点。"],
    ["method", "③ 技術や手法のキモはどこ？", "提案手法の核となるアイデア・仕組み。"],
    ["verify", "④ どうやって有効だと検証した？", "実験・データセット・評価指標と結果。"],
    ["discuss", "⑤ 議論はある？", "限界・課題・著者や自分が感じた論点。"],
    ["next", "⑥ 次に読むべき論文は？", "関連文献・次に読む対象。"],
  ];

  // summary タブの「質問 → メモ欄」ブロックを生成（key -> {ta, pv, cm}）
  const cmOpts = {
    mode: { name: "markdown", fencedCodeBlockHighlighting: false },
    lineWrapping: true,
    indentUnit: 2,
    tabSize: 2,
    indentWithTabs: false,
    extraKeys: {
      Enter: "newlineAndIndentContinueMarkdownList",
      Tab: (c) => c.execCommand("indentMore"),
      "Shift-Tab": (c) => c.execCommand("indentLess"),
      "Cmd-B": cmToggleBold,
      "Ctrl-B": cmToggleBold,
      "Shift-Cmd-H": cmToggleHeading,
      "Shift-Ctrl-H": cmToggleHeading,
    },
  };
  const sumFields = {};
  for (const [key, q, ph] of SUMMARY_FIELDS) {
    const block = document.createElement("div");
    block.className = "summaryField";
    const label = document.createElement("label");
    label.textContent = q;
    const ta = document.createElement("textarea");
    ta.placeholder = ph;
    ta.dataset.key = key;
    const pv = document.createElement("div");
    pv.className = "fieldPreview";
    pv.hidden = true;
    block.append(label, ta, pv);
    summaryView.appendChild(block);
    let scm = null;
    if (window.CodeMirror) {
      scm = CodeMirror.fromTextArea(ta, { ...cmOpts, placeholder: ph });
    }
    sumFields[key] = { ta, pv, cm: scm };
  }
  const eachSum = (fn) => SUMMARY_FIELDS.forEach(([k]) => fn(k, sumFields[k]));

  const emit = (name, detail) =>
    document.dispatchEvent(new CustomEvent(name, { detail }));

  let cur = null; // {id, title, pdf}
  let dirty = false;
  let dirtyVersion = 0;
  let saveTimer = null;
  let loadingSeq = 0;
  let saving = null;
  let preview = false;
  let tab = "memo"; // "memo" | "summary"
  let cm = null; // メモ本文の CodeMirror（未ロード時は null → textarea にフォールバック）

  // 本文の読み書きは cm 経由（CM 不使用時は textarea）。以降この2つだけを使う。
  const getBody = () => (cm ? cm.getValue() : bodyEl.value);
  const setBody = (v) => {
    if (cm) cm.setValue(v || "");
    else bodyEl.value = v || "";
  };
  const refreshCM = () => {
    if (cm) requestAnimationFrame(() => cm.refresh());
    eachSum((_, f) => { if (f.cm) requestAnimationFrame(() => f.cm.refresh()); });
  };

  const stripExt = (n) => (n || "").replace(/\.pdf$/i, "");
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function setOpen(open) {
    panel.classList.toggle("open", open);
    toggle.classList.toggle("active", open);
    localStorage.setItem("memoOpen", open ? "1" : "0");
    if (open) refreshCM(); // 非表示中に生成/更新された CM は表示時に測り直す
  }
  const setStatus = (t) => (statusEl.textContent = t);
  function setEditable(on) {
    titleEl.disabled = bodyEl.disabled = !on;
    if (cm) {
      cm.setOption("readOnly", on ? false : "nocursor");
      cm.getWrapperElement().classList.toggle("cm-readonly", !on);
    }
    eachSum((_, f) => {
      if (f.cm) {
        f.cm.setOption("readOnly", on ? false : "nocursor");
        f.cm.getWrapperElement().classList.toggle("cm-readonly", !on);
      } else {
        f.ta.disabled = !on;
      }
    });
    const saveBtn = $("memoSaveBtn"); if (saveBtn) saveBtn.disabled = !on;
    $("memoDeleteBtn").disabled = !on;
  }
  const collectSummary = () => {
    const o = {};
    eachSum((k, f) => (o[k] = f.cm ? f.cm.getValue() : f.ta.value));
    return o;
  };
  function fillSummary(s) {
    s = s || {};
    eachSum((k, f) => {
      if (f.cm) f.cm.setValue(s[k] || "");
      else f.ta.value = s[k] || "";
    });
  }

  function load(note) {
    cur = { id: note.id, title: note.title || "", pdf: note.pdf || "" };
    titleEl.value = cur.title;
    setBody(note.body || "");
    fillSummary(note.summary);
    dirty = false;
    dirtyVersion++;
    syncDirtyIndicator();
    setEditable(true);
    refreshCM();
    if (preview) renderPreview();
    setStatus(note.updated ? "保存済み " + fmtDate(note.updated) : "新規メモ");
    emit("memo-opened", { id: cur.id });
  }
  function blank(id, pdfName) {
    cur = { id, title: stripExt(pdfName), pdf: pdfName };
    titleEl.value = cur.title;
    setBody("");
    fillSummary(null);
    dirty = false;
    dirtyVersion++;
    syncDirtyIndicator();
    setEditable(true);
    refreshCM();
    if (preview) renderPreview();
    setStatus("新規メモ（入力すると自動保存）");
    emit("memo-opened", { id: cur.id });
  }

  async function openForPdf(id, pdfName) {
    if (!(await flush())) return;
    const seq = ++loadingSeq;
    try {
      const r = await fetch("/api/notes/" + id);
      if (seq !== loadingSeq) return;
      if (r.ok) {
        load(await r.json());
      } else {
        blank(id, pdfName);
        dirty = true;
        await save();
      }
    } catch {
      if (seq === loadingSeq) blank(id, pdfName);
    }
  }
  async function openExisting(id) {
    if (!(await flush())) return;
    const seq = ++loadingSeq;
    setOpen(true);
    try {
      const r = await fetch("/api/notes/" + id);
      if (seq !== loadingSeq) return;
      if (r.ok) load(await r.json());
    } catch {}
  }

  function syncDirtyIndicator() {
    toggle.classList.toggle("dirty", dirty);
  }
  function markDirty() {
    if (!cur) return;
    dirty = true;
    dirtyVersion++;
    syncDirtyIndicator();
    setStatus("編集中…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1200);
    if (preview) renderPreview();
  }
  async function save() {
    if (saving) return saving;
    if (!cur || !dirty) return;
    clearTimeout(saveTimer);
    setStatus("保存中…");
    const id = cur.id;
    const pdf = cur.pdf || null;
    const payload = {
      title: titleEl.value.trim() || "Untitled",
      body: getBody(),
      pdf,
      summary: collectSummary(),
    };
    const version = dirtyVersion;
    saving = (async () => {
      try {
        const r = await fetch("/api/notes/" + id, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        if (cur && cur.id === id && dirtyVersion === version) {
          cur.title = d.title;
          dirty = false;
          syncDirtyIndicator();
          setStatus("保存済み " + fmtDate(d.updated));
        }
        emit("memos-changed");
      } catch (e) {
        setStatus("⚠️ 保存失敗: " + e.message);
      } finally {
        saving = null;
      }
    })();
    return saving;
  }

  async function flush() {
    clearTimeout(saveTimer);
    if (saving) await saving;
    if (dirty) await save();
    return !dirty;
  }

  function renderPreview() {
    previewEl.innerHTML = renderMarkdown(
      getBody() || "_（まだ何も書かれていません）_"
    );
    eachSum((_, f) => {
      const v = f.cm ? f.cm.getValue() : f.ta.value;
      f.pv.innerHTML = v.trim() ? renderMarkdown(v) : "";
    });
  }
  function setPreview(on) {
    preview = on;
    localStorage.setItem("memoPreview", on ? "1" : "0");
    if (cm) cm.getWrapperElement().style.display = on ? "none" : "";
    else bodyEl.hidden = on;
    previewEl.hidden = !on;
    eachSum((_, f) => {
      if (f.cm) f.cm.getWrapperElement().style.display = on ? "none" : "";
      else f.ta.hidden = on;
      f.pv.hidden = !on;
    });
    previewBtn.textContent = on ? "編集に戻る" : "プレビュー";
    if (on) renderPreview();
    else refreshCM();
  }
  function setTab(name) {
    tab = name === "summary" ? "summary" : "memo";
    memoView.hidden = tab !== "memo";
    summaryView.hidden = tab !== "summary";
    tabsEl.querySelectorAll(".memoTab").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab)
    );
    if (labelEl)
      labelEl.textContent = tab === "summary" ? "🧩 落合まとめ" : "📝 このメモ";
    localStorage.setItem("memoTab", tab);
    if (tab === "memo") refreshCM(); // メモタブ表示時に CM を測り直す
  }

  toggle.onclick = () => setOpen(!panel.classList.contains("open"));
  $("memoClose").onclick = () => setOpen(false);
  tabsEl.querySelectorAll(".memoTab").forEach((b) => {
    b.onclick = () => setTab(b.dataset.tab);
  });
  titleEl.addEventListener("input", markDirty);
  // メモ本文: CodeMirror があればリッチ編集、無ければ従来の textarea。
  if (window.CodeMirror) {
    cm = CodeMirror.fromTextArea(bodyEl, {
      mode: { name: "markdown", fencedCodeBlockHighlighting: false },
      lineWrapping: true,
      indentUnit: 2,
      tabSize: 2,
      indentWithTabs: false,
      placeholder: bodyEl.placeholder,
      extraKeys: {
        Enter: "newlineAndIndentContinueMarkdownList",
        Tab: (c) => c.execCommand("indentMore"),
        "Shift-Tab": (c) => c.execCommand("indentLess"),
        "Cmd-B": cmToggleBold,
        "Ctrl-B": cmToggleBold,
        "Shift-Cmd-H": cmToggleHeading,
        "Shift-Ctrl-H": cmToggleHeading,
      },
    });
    cm.on("change", markDirty);
  } else {
    bodyEl.addEventListener("input", markDirty);
    attachListEditing(bodyEl);
  }
  eachSum((_, f) => {
    if (f.cm) f.cm.on("change", markDirty);
    else {
      f.ta.addEventListener("input", markDirty);
      attachListEditing(f.ta);
    }
  });
  const memoSaveBtn = $("memoSaveBtn"); if (memoSaveBtn) memoSaveBtn.onclick = save;
  previewBtn.onclick = () => setPreview(!preview);
  $("memoDeleteBtn").onclick = async () => {
    if (!cur || !(await appConfirm("このメモを削除しますか？"))) return;
    await fetch("/api/notes/" + cur.id, { method: "DELETE" });
    cur = null;
    titleEl.value = "";
    setBody("");
    fillSummary(null);
    setEditable(false);
    setStatus("削除しました");
    emit("memos-changed");
  };
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (!panel.classList.contains("open")) setOpen(true);
      save();
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "m") {
      e.preventDefault();
      setOpen(!panel.classList.contains("open"));
    }
  });

  // 高さドラッグ
  let dh = false;
  $("memoDrag").addEventListener("mousedown", () => {
    dh = true;
    document.body.style.userSelect = "none";
  });
  document.addEventListener("mousemove", (e) => {
    if (!dh) return;
    const h = Math.min(
      Math.max(window.innerHeight - e.clientY, 140),
      window.innerHeight * 0.7
    );
    panel.style.setProperty("--memo-h", h + "px");
  });
  document.addEventListener("mouseup", () => {
    if (!dh) return;
    dh = false;
    document.body.style.userSelect = "";
    localStorage.setItem(
      "memoH",
      getComputedStyle(panel).getPropertyValue("--memo-h").trim()
    );
  });

  const savedH = localStorage.getItem("memoH");
  if (savedH) panel.style.setProperty("--memo-h", savedH);
  setEditable(false);
  setTab(localStorage.getItem("memoTab") || "memo");
  if (localStorage.getItem("memoPreview") === "1") setPreview(true);
  if (localStorage.getItem("memoOpen") === "1") setOpen(true);

  return { openForPdf, openExisting, setOpen, flush };
})();

/* ---------- 左サイドバー: 読んだ論文の一覧（ChatGPT風） ---------- */
(() => {
  const $ = (id) => document.getElementById(id);
  const pane = $("listPane");
  const toggle = $("listToggle");
  const collapse = $("listCollapse");
  const searchEl = $("listSearch");
  const itemsEl = $("listItems");
  const countEl = $("listCount");
  if (!pane || !toggle || !itemsEl) return;

  let all = [];
  let activeId = null;

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function setOpen(open) {
    pane.classList.toggle("collapsed", !open);
    toggle.classList.toggle("active", open);
    localStorage.setItem("listOpen", open ? "1" : "0");
  }

  function render() {
    const q = searchEl.value.trim().toLowerCase();
    const list = q
      ? all.filter(
          (n) =>
            (n.title || "").toLowerCase().includes(q) ||
            (n.pdf || "").toLowerCase().includes(q)
        )
      : all;
    countEl.textContent = all.length ? `${list.length}/${all.length}` : "";
    itemsEl.innerHTML = "";
    if (!list.length) {
      itemsEl.innerHTML =
        '<li class="li-empty">' +
        (all.length ? "該当なし" : "まだメモはありません。PDFを開いて書き始めましょう。") +
        "</li>";
      return;
    }
    for (const n of list) {
      const li = document.createElement("li");
      li.className = "li-item" + (n.id === activeId ? " active" : "");
      li.innerHTML =
        '<div class="li-main"><div class="li-title"></div>' +
        '<div class="li-sub"></div></div>' +
        '<button class="li-del" title="削除">×</button>';
      li.querySelector(".li-title").textContent = n.title || "(無題)";
      li.querySelector(".li-sub").textContent =
        fmtDate(n.updated) + (n.pdf ? " · " + n.pdf : "");
      li.querySelector(".li-main").onclick = async () => {
        // PDFがキャッシュ済みなら openPdfBuffer→Memo.openForPdf が
        // メモも読み込む。未キャッシュ時だけメモ単体を表示
        // （ノートの二重読込・競合を避ける）。
        const ok = await loadPdfFromCache(n.id, n.pdf || n.title);
        if (!ok) {
          Memo.openExisting(n.id);
          els.status.textContent =
            "このPDFは未キャッシュ（「PDFを開く」で一度開くと次回から復元）";
        }
      };
      li.querySelector(".li-del").onclick = async (e) => {
        e.stopPropagation();
        if (!(await appConfirm(`「${n.title}」を削除しますか？\nメモ・しおり・会話履歴も消えます。`))) return;
        await fetch("/api/notes/" + n.id, { method: "DELETE" });
        if (n.id === activeId) {
          pdfDoc = null;
          els.container.innerHTML = '<div id="dropHint"><div class="drop-icon">📄</div><p>PDFをここにドラッグ&ドロップ、または「📂 PDFを開く」</p></div>';
          els.fileName.textContent = "ファイル未選択";
          els.fileName.classList.add("muted");
          els.results.innerHTML = "";
          els.status.textContent = "";
          activeId = null;
        }
        await refresh();
      };
      itemsEl.appendChild(li);
    }
  }

  async function refresh() {
    try {
      const r = await fetch("/api/notes");
      all = r.ok ? await r.json() : [];
    } catch {
      all = [];
    }
    render();
  }

  toggle.onclick = () => setOpen(pane.classList.contains("collapsed"));
  if (collapse) collapse.onclick = () => setOpen(false);
  searchEl.addEventListener("input", render);

  document.addEventListener("pointerdown", (e) => {
    if (pane.classList.contains("collapsed")) return;
    if (e.target.closest("#listPane, #listToggle")) return;
    setOpen(false);
  });

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "b") {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault();
      setOpen(pane.classList.contains("collapsed"));
    }
  });

  document.addEventListener("memos-changed", refresh);
  document.addEventListener("memo-opened", (e) => {
    activeId = e.detail && e.detail.id;
    if (activeId && !all.some((n) => n.id === activeId)) refresh();
    else render();
  });

  setOpen(false); // 既定は閉じる（オーバーレイなので）
  refresh();
})();

/* ---------- レイアウト切替 ---------- */
(() => {
  const btn = document.getElementById("layoutBtn");
  const LAYOUTS = ["standard", "columns"];
  const LABELS = { standard: "⊞", columns: "☰" };
  if (!btn) return;

  // A4縦 = 1:1.414, 16:9スライド = 1.778:1
  const RATIO = { columns: 1 / 1.414, standard: 16 / 9 };
  const RESIZER_W = 13; // memoResizer(5) + resizer(8)

  function calcWidths(layout) {
    const main = document.getElementById("main");
    if (!main) return null;
    const totalW = main.clientWidth;
    const totalH = main.clientHeight;
    const ratio = RATIO[layout] || RATIO.standard;
    const pdfW = Math.round(Math.min(totalH * ratio, totalW * 0.6));

    if (layout === "columns") {
      const sideTotal = totalW - pdfW - RESIZER_W;
      const memoW = Math.round(Math.max(200, Math.min(sideTotal * 0.48, 450)));
      const aiW = Math.max(180, sideTotal - memoW);
      return { memoW, aiW };
    } else {
      const aiW = Math.round(Math.max(280, Math.min(totalW - pdfW - 8, 520)));
      return { aiW };
    }
  }

  function apply(layout) {
    document.body.dataset.layout = layout;
    btn.textContent = LABELS[layout] || "⊞";
    btn.title = layout === "standard" ? "3カラムに切替" : "標準に切替";
    const panel = document.getElementById("memoPanel");
    const memoResizer = document.getElementById("memoResizer");

    if (layout === "columns") {
      if (panel) panel.classList.add("open");
      if (memoResizer) memoResizer.hidden = false;
    } else {
      if (memoResizer) memoResizer.hidden = true;
    }

    const w = calcWidths(layout);
    if (!w) return;
    if (layout === "columns" && panel) {
      panel.style.setProperty("--memo-col-w", w.memoW + "px");
    }
    if (w.aiW) {
      els.sidePane.style.width = w.aiW + "px";
    }
  }

  async function load() {
    try {
      const r = await fetch("/api/layout");
      if (!r.ok) return;
      const { layout } = await r.json();
      if (layout && LAYOUTS.includes(layout)) apply(layout);
    } catch {}
  }

  btn.onclick = () => {
    const cur = document.body.dataset.layout || "standard";
    const next = cur === "standard" ? "columns" : "standard";
    apply(next);
    fetch("/api/layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout: next }),
    }).catch(() => {});
  };

  load();
})();

/* ---------- 設定ダイアログ ---------- */
(() => {
  const btn = document.getElementById("settingsBtn");
  const dialog = document.getElementById("settingsDialog");
  const closeBtn = document.getElementById("settingsClose");
  const saveBtn = document.getElementById("settingsSave");
  const statusEl = document.getElementById("settingsStatus");
  const providerSel = document.getElementById("settingsProvider");
  const keyInput = document.getElementById("settingsApiKey");
  const modelInput = document.getElementById("settingsModel");
  const baseUrlLabel = document.getElementById("settingsBaseUrlLabel");
  const baseUrlInput = document.getElementById("settingsBaseUrl");
  if (!dialog || !btn) return;

  const DEFAULTS = {
    gemini:    { model: "gemini-3.1-flash-lite", base_url: "" },
    openai:    { model: "gpt-4o-mini",           base_url: "https://api.openai.com" },
    anthropic: { model: "claude-sonnet-4-20250514", base_url: "" },
  };
  const HELP = { gemini: "helpGemini", openai: "helpOpenai", anthropic: "helpAnthropic" };

  function updateUI(provider) {
    const d = DEFAULTS[provider] || DEFAULTS.gemini;
    modelInput.placeholder = d.model;
    baseUrlLabel.hidden = provider !== "openai";
    if (d.base_url) baseUrlInput.placeholder = d.base_url;
    for (const [k, id] of Object.entries(HELP)) {
      const el = document.getElementById(id);
      if (el) el.hidden = k !== provider;
    }
  }

  let serverProvider = "";

  async function load() {
    try {
      const r = await fetch("/api/settings");
      if (!r.ok) return;
      const s = await r.json();
      serverProvider = s.provider || "";
      providerSel.value = s.provider || "gemini";
      keyInput.value = "";
      keyInput.placeholder = s.key_set ? "設定済み（変更する場合のみ入力）" : "未設定";
      modelInput.value = s.model || "";
      baseUrlInput.value = s.base_url || "";
      updateUI(providerSel.value);
      statusEl.textContent = "";
    } catch {}
  }

  providerSel.addEventListener("change", () => updateUI(providerSel.value));

  btn.onclick = () => { load(); dialog.showModal(); };
  closeBtn.onclick = () => dialog.close();
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  saveBtn.onclick = async () => {
    const payload = {};
    const pv = providerSel.value;
    if (pv !== serverProvider) payload.provider = pv;
    if (keyInput.value.trim()) payload.api_key = keyInput.value.trim();
    const mv = modelInput.value.trim();
    if (mv) payload.model = mv;
    const bv = baseUrlInput.value.trim();
    if (pv === "openai" && bv) payload.base_url = bv;
    if (!Object.keys(payload).length) {
      statusEl.textContent = "変更がありません";
      return;
    }
    saveBtn.disabled = true;
    statusEl.textContent = "保存中…";
    try {
      const r = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const s = await r.json();
      serverProvider = s.provider || "";
      statusEl.textContent = "保存しました";
      keyInput.value = "";
      keyInput.placeholder = s.key ? "設定済み（変更する場合のみ入力）" : "未設定";
      if (s.model) modelInput.value = s.model;
      if (typeof Ask !== "undefined" && Ask.checkStatus) Ask.checkStatus();
    } catch (e) {
      statusEl.textContent = "⚠️ 保存失敗: " + e.message;
    }
    saveBtn.disabled = false;
  };
})();

/* ---------- 文字サイズ設定 ---------- */
(() => {
  const SLIDERS = [
    { range: "memoSizeRange", val: "memoSizeVal", prop: "--memo-font-size", def: 14 },
    { range: "aiSizeRange",   val: "aiSizeVal",   prop: "--ai-font-size",   def: 14 },
    { range: "askSizeRange",  val: "askSizeVal",  prop: "--ask-font-size",  def: 13.5 },
  ];
  let saveTimer = null;

  function applyAll(sizes) {
    for (const s of SLIDERS) {
      const v = sizes[s.prop] ?? s.def;
      document.documentElement.style.setProperty(s.prop, v + "px");
      const rangeEl = document.getElementById(s.range);
      const valEl = document.getElementById(s.val);
      if (rangeEl) rangeEl.value = v;
      if (valEl) valEl.textContent = v;
    }
  }

  function persist() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const data = {};
      for (const s of SLIDERS) {
        const rangeEl = document.getElementById(s.range);
        if (rangeEl) data[s.prop] = parseFloat(rangeEl.value);
      }
      fetch("/api/font-sizes", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      }).catch(() => {});
    }, 500);
  }

  for (const s of SLIDERS) {
    const rangeEl = document.getElementById(s.range);
    const valEl = document.getElementById(s.val);
    if (!rangeEl) continue;
    rangeEl.addEventListener("input", () => {
      const v = parseFloat(rangeEl.value);
      if (valEl) valEl.textContent = v;
      document.documentElement.style.setProperty(s.prop, v + "px");
      persist();
    });
  }

  (async () => {
    try {
      const r = await fetch("/api/font-sizes");
      if (r.ok) applyAll(await r.json());
    } catch {}
  })();
})();

/* ---------- ウェルカムガイド（初回のみ） ---------- */
(async () => {
  const dialog = document.getElementById("welcomeDialog");
  const startBtn = document.getElementById("welcomeStart");
  if (!dialog || !startBtn) return;
  try {
    const r = await fetch("/api/welcomed");
    if (r.ok && (await r.json()).welcomed) return;
  } catch { return; }
  dialog.showModal();
  startBtn.onclick = () => {
    dialog.close();
    fetch("/api/welcomed", { method: "POST" }).catch(() => {});
  };
})();

/* ---------- 起動時: 最後に開いた PDF をキャッシュから自動復元 ---------- */
(async () => {
  try {
    const r = await fetch("/api/last-pdf");
    if (!r.ok) return;
    const { id, name } = await r.json();
    if (!id) return;
    await loadPdfFromCache(id, name || "document.pdf");
  } catch {}
})();
