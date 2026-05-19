/* Paper Reader — PDF選択 → その場で日本語訳＋解説 */

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
marked.setOptions({ breaks: true });

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
};


/* ---------- ローカルLLM(Ollama) セットアップ確認 ---------- */
(async () => {
  try {
    const r = await fetch("/api/llm-status");
    if (!r.ok) return;
    const s = await r.json();
    if (s.running && s.model_present) return; // 準備OK
    const md = !s.running
      ? "### ⚙️ 初回セットアップ（ローカルLLM）\n\n" +
        "解説はあなたのPC内（Ollama）で生成します。クラウド送信なし・APIキー不要。\n\n" +
        "1. `brew install ollama`（または https://ollama.com/download ）\n" +
        "2. 別ターミナルで `ollama serve`\n" +
        "3. `ollama pull " + s.model + "`（約5GB・初回のみ）\n\n" +
        "準備後にページを再読み込みすると、この案内は消えます。"
      : "### ⚙️ モデル取得が必要\n\n" +
        "`ollama pull " + s.model + "` を実行 → 再読み込みで案内は消えます。";
    els.results.innerHTML = '<div class="card"><div class="body"></div></div>';
    els.results.querySelector(".body").innerHTML = marked.parse(md);
  } catch {}
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
  return Math.min(Math.max(paneW, 360), 900) / base.width;
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
  wrap._pageText = tc.items.map((i) => i.str).join(" ");
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
  clearTimeout(commitTimer); // 保留中のズーム確定をキャンセル（別PDFと混ざらない）
  els.fab.hidden = true;
  els.fileName.textContent = name;
  els.fileName.classList.remove("muted");
  els.status.textContent = "読み込み中…";
  els.container.innerHTML = "";
  Memo.openForPdf(id, name);
  pdfDoc = await pdfjsLib.getDocument({ data: buf }).promise;
  zoom = 1;
  live = 1;
  els.pdfPane.scrollTop = 0;
  localStorage.setItem("lastPdfId", id);
  localStorage.setItem("lastPdfName", name);
  await renderAll();
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

/* ---------- 選択検知 ---------- */
function currentSelection() {
  const sel = window.getSelection();
  const text = sel ? sel.toString().trim() : "";
  if (!text || sel.rangeCount === 0) return null;
  const node = sel.anchorNode;
  const elNode = node && (node.nodeType === 1 ? node : node.parentElement);
  const wrap = elNode && elNode.closest(".pageWrap");
  if (!wrap) return null;
  const rect = sel.getRangeAt(0).getBoundingClientRect();
  return { text, rect, context: (wrap._pageText || "").slice(0, 1600) };
}

document.addEventListener("mouseup", () => {
  const s = currentSelection();
  if (!s) {
    els.fab.hidden = true;
    return;
  }
  els.fab.style.left = Math.max(8, s.rect.left) + "px";
  els.fab.style.top = s.rect.bottom + 8 + "px";
  els.fab.hidden = false;
  els.fab._payload = s;
});

document.addEventListener("mousedown", (e) => {
  if (e.target !== els.fab) els.fab.hidden = true;
});
els.pdfPane.addEventListener("scroll", () => (els.fab.hidden = true));

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
  els.results.prepend(card);
  els.sidePane.scrollTop = 0;

  let acc = "";
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
      body.innerHTML = marked.parse(acc);
      els.sidePane.scrollTop = 0;
    }
  } catch (e) {
    body.innerHTML = marked.parse(`> ⚠️ ${e.message}`);
  }
  card.classList.remove("loading");

  card.querySelector(".copyBtn").onclick = (ev) => {
    navigator.clipboard.writeText(`> ${text}\n\n${acc}`);
    ev.target.textContent = "コピーしました ✓";
    setTimeout(() => (ev.target.textContent = "コピー(原文+解説)"), 1500);
  };
}

/* ---------- サイドペイン幅リサイズ ---------- */
let dragging = false;
els.resizer.addEventListener("mousedown", () => {
  dragging = true;
  document.body.style.userSelect = "none";
});
document.addEventListener("mousemove", (e) => {
  if (!dragging) return;
  const w = Math.min(Math.max(window.innerWidth - e.clientX, 280), 720);
  els.sidePane.style.width = w + "px";
});
document.addEventListener("mouseup", () => {
  dragging = false;
  document.body.style.userSelect = "";
});

/* ---------- メモ（論文ごとの自分用まとめ） ---------- */
async function sha256Id(buf) {
  const h = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(h)]
    .slice(0, 8)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

const Memo = (() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("memoPanel");
  const toggle = $("memoToggle");
  const titleEl = $("memoTitle");
  const bodyEl = $("memoBody");
  const previewEl = $("memoPreview");
  const previewBtn = $("memoPreviewBtn");
  const statusEl = $("memoStatus");

  // DOM が古い（キャッシュ食い違い等）場合でも本体を巻き込まない
  if (!panel || !toggle || !titleEl || !bodyEl) {
    console.warn("[memo] UI要素が見つからないためメモ機能を無効化（ハードリロード推奨）");
    return { openForPdf() {}, openExisting() {}, setOpen() {} };
  }

  const emit = (name, detail) =>
    document.dispatchEvent(new CustomEvent(name, { detail }));

  let cur = null; // {id, title, pdf}
  let dirty = false;
  let saveTimer = null;
  let preview = false;

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
  }
  const setStatus = (t) => (statusEl.textContent = t);
  function setEditable(on) {
    titleEl.disabled = bodyEl.disabled = !on;
    $("memoSaveBtn").disabled = $("memoDeleteBtn").disabled = !on;
  }

  function load(note) {
    cur = { id: note.id, title: note.title || "", pdf: note.pdf || "" };
    titleEl.value = cur.title;
    bodyEl.value = note.body || "";
    dirty = false;
    setEditable(true);
    if (preview) renderPreview();
    setStatus(note.updated ? "保存済み " + fmtDate(note.updated) : "新規メモ");
    emit("memo-opened", { id: cur.id });
  }
  function blank(id, pdfName) {
    cur = { id, title: stripExt(pdfName), pdf: pdfName };
    titleEl.value = cur.title;
    bodyEl.value = "";
    dirty = false;
    setEditable(true);
    if (preview) renderPreview();
    setStatus("新規メモ（入力すると自動保存）");
    emit("memo-opened", { id: cur.id });
  }

  async function openForPdf(id, pdfName) {
    try {
      const r = await fetch("/api/notes/" + id);
      if (r.ok) load(await r.json());
      else blank(id, pdfName);
    } catch {
      blank(id, pdfName);
    }
  }
  async function openExisting(id) {
    setOpen(true);
    try {
      const r = await fetch("/api/notes/" + id);
      if (r.ok) load(await r.json());
    } catch {}
  }

  function markDirty() {
    if (!cur) return;
    dirty = true;
    setStatus("編集中…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1200);
    if (preview) renderPreview();
  }
  async function save() {
    if (!cur || !dirty) return;
    clearTimeout(saveTimer);
    setStatus("保存中…");
    try {
      const r = await fetch("/api/notes/" + cur.id, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: titleEl.value.trim() || "Untitled",
          body: bodyEl.value,
          pdf: cur.pdf || null,
        }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      cur.title = d.title;
      dirty = false;
      setStatus("保存済み " + fmtDate(d.updated));
      emit("memos-changed");
    } catch (e) {
      setStatus("⚠️ 保存失敗: " + e.message);
    }
  }

  function renderPreview() {
    previewEl.innerHTML = marked.parse(
      bodyEl.value || "_（まだ何も書かれていません）_"
    );
  }
  function setPreview(on) {
    preview = on;
    bodyEl.hidden = on;
    previewEl.hidden = !on;
    previewBtn.textContent = on ? "編集に戻る" : "プレビュー";
    if (on) renderPreview();
  }

  toggle.onclick = () => setOpen(!panel.classList.contains("open"));
  $("memoClose").onclick = () => setOpen(false);
  titleEl.addEventListener("input", markDirty);
  bodyEl.addEventListener("input", markDirty);
  $("memoSaveBtn").onclick = save;
  previewBtn.onclick = () => setPreview(!preview);
  $("memoDeleteBtn").onclick = async () => {
    if (!cur || !confirm("このメモを削除しますか？")) return;
    await fetch("/api/notes/" + cur.id, { method: "DELETE" });
    cur = null;
    titleEl.value = bodyEl.value = "";
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
  if (localStorage.getItem("memoOpen") === "1") setOpen(true);

  return { openForPdf, openExisting, setOpen };
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
        if (!confirm(`「${n.title}」を削除しますか？`)) return;
        await fetch("/api/notes/" + n.id, { method: "DELETE" });
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

  document.addEventListener("memos-changed", refresh);
  document.addEventListener("memo-opened", (e) => {
    activeId = e.detail && e.detail.id;
    if (activeId && !all.some((n) => n.id === activeId)) refresh();
    else render();
  });

  setOpen(localStorage.getItem("listOpen") !== "0"); // 既定は開く
  refresh();
})();

/* ---------- 起動時: 最後に開いた PDF をキャッシュから自動復元 ---------- */
(async () => {
  const id = localStorage.getItem("lastPdfId");
  if (!id) return;
  const name = localStorage.getItem("lastPdfName") || "document.pdf";
  await loadPdfFromCache(id, name); // 未キャッシュなら何もしない（drop hint のまま）
})();
