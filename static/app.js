/* MV 播放器前端:主页(歌单管理) + 歌单页(播放/排序),hash 路由。 */
"use strict";

const $ = (id) => document.getElementById(id);
const video = $("video");

let playlists = [];      // 服务端最新歌单列表
let songsById = {};      // id → song
let viewPl = null;       // 当前打开的歌单 id(null = 主页)
let playingPl = null;    // 正在播放的歌来自哪个歌单(自动切歌沿用它的顺序)
let currentId = null;    // 正在播放的歌曲 id
let shuffleSet = new Set(); // 开启了随机播放的歌单 id(每个歌单独立开关)
let shuffleOrders = {};     // plId → 随机顺序的歌曲 id 列表(仅显示层,不改服务端真实顺序)
let loop = true;
let dragLi = null;       // 正在拖拽的 <li>

const STATUS_TEXT = {
  pending: ["排队中…", "busy"],
  searching: ["搜索官方MV…", "busy"],
  done: ["✓ 就绪", "ok"],
  no_mv: ["未找到官方MV", "err"],
  failed: ["下载失败", "err"],
};

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function plById(id) { return playlists.find((p) => p.id === id); }

/* ---------- 页面内置对话框(替代 prompt/confirm,不会被浏览器拦截) ---------- */

function openModal({ title, message, input, value, placeholder }) {
  return new Promise((resolve) => {
    const dlg = $("modal");
    const inp = $("modal-input");
    $("modal-title").textContent = title || "";
    $("modal-msg").hidden = !message;
    $("modal-msg").textContent = message || "";
    inp.hidden = !input;
    inp.value = value || "";
    inp.placeholder = placeholder || "";
    let settled = false;
    const done = (val) => {
      if (settled) return;
      settled = true;
      $("modal-form").removeEventListener("submit", onSubmit);
      $("modal-cancel").removeEventListener("click", onCancel);
      dlg.removeEventListener("close", onClose);
      if (dlg.open) dlg.close();
      resolve(val);
    };
    const onSubmit = (e) => {
      e.preventDefault();
      done(input ? (inp.value.trim() || null) : true);
    };
    const onCancel = () => done(null);
    const onClose = () => done(null); // Esc / 点击外部
    $("modal-form").addEventListener("submit", onSubmit);
    $("modal-cancel").addEventListener("click", onCancel);
    dlg.addEventListener("close", onClose);
    dlg.showModal();
    if (input) { inp.focus(); inp.select(); }
  });
}

const askText = (title, value = "", placeholder = "") =>
  openModal({ title, input: true, value, placeholder });
const askConfirm = (title, message = "") =>
  openModal({ title, message });

function copyText(text) {
  const legacy = () => new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    ok ? resolve() : reject(new Error("execCommand failed"));
  });
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).catch(legacy);
  }
  return legacy();
}

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}

function plSongs(plId) {
  const pl = plById(plId);
  return pl ? pl.song_ids.map((id) => songsById[id]).filter(Boolean) : [];
}

// 显示顺序:该歌单开启随机时按其随机序排列(懒生成),否则即原始顺序
function displaySongs(plId) {
  const ss = plSongs(plId);
  if (!shuffleSet.has(plId)) return ss;
  let order = shuffleOrders[plId];
  if (!order) {
    order = ss.map((s) => s.id);
    for (let i = order.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
    // 正在播放的歌若在本歌单里,提到第一位;不在则维持普通随机
    if (currentId && order.includes(currentId)) {
      order = [currentId, ...order.filter((id) => id !== currentId)];
    }
    shuffleOrders[plId] = order;
  }
  const pos = new Map(order.map((id, i) => [id, i]));
  // 洗牌后新加的歌排在末尾(保持稳定)
  return [...ss].sort((a, b) =>
    (pos.has(a.id) ? pos.get(a.id) : 1e9) - (pos.has(b.id) ? pos.get(b.id) : 1e9));
}

function readyQueue(plId) {
  return displaySongs(plId).filter((s) => s.status === "done");
}

function songLabel(s) {
  return s.artist ? `${s.title} - ${s.artist}` : s.title;
}

/* ---------- 路由 ---------- */

function route() {
  const m = location.hash.match(/^#\/p\/([0-9a-f]+)/);
  viewPl = m ? m[1] : null;
  $("home-view").hidden = viewPl !== null;
  $("layout").hidden = viewPl === null;
  renderAll();
}
window.addEventListener("hashchange", route);

$("back-btn").onclick = () => { location.hash = ""; };

async function renamePlaylist(id) {
  const name = await askText("重命名歌单", plById(id)?.name || "");
  if (!name) return;
  const res = await fetch("/api/playlists/" + id, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) toast((await res.json()).error || "重命名失败");
  await refresh();
}

$("pl-rename-btn").onclick = () => { if (viewPl) renamePlaylist(viewPl); };

/* ---------- 主页渲染 ---------- */

let lastHomeKey = "";

function cardHtml(pl) {
  const ss = plSongs(pl.id);
  const done = ss.filter((s) => s.status === "done");
  const thumbs = done.filter((s) => s.thumb).slice(0, 4);
  const cover = thumbs.length
    ? `<div class="cover grid${Math.min(thumbs.length, 4)}">` +
      thumbs.map((s) => `<img src="/media/thumbs/${s.id}.jpg" alt="">`).join("") + `</div>`
    : `<div class="cover empty">🎵</div>`;
  return `<div class="pl-card" data-id="${pl.id}">
    ${cover}
    <div class="pl-info">
      <div class="pl-name">${escapeHtml(pl.name)}</div>
      <div class="pl-sub">${ss.length} 首 · ${done.length} 可播</div>
    </div>
    <div class="pl-ops">
      <button data-op="rename" title="重命名">✎</button>
      <button data-op="delpl" title="删除歌单">✕</button>
    </div>
  </div>`;
}

function renderHome() {
  const key = JSON.stringify(playlists.map((pl) => {
    const ss = plSongs(pl.id);
    return [pl.id, pl.name, ss.length,
            ss.filter((s) => s.status === "done").length,
            ss.filter((s) => s.thumb).slice(0, 4).map((s) => s.id)];
  }));
  if (key !== lastHomeKey) {
    lastHomeKey = key;
    $("pl-grid").innerHTML = playlists.map(cardHtml).join("");
  }
  $("home-empty").hidden = playlists.length > 0;
  const np = $("home-nowplaying");
  if (currentId && songsById[currentId]) {
    np.hidden = false;
    np.textContent = "▶ " + songLabel(songsById[currentId]);
  } else {
    np.hidden = true;
  }
}

$("new-pl-btn").onclick = async () => {
  const name = await askText("新建歌单", "", "歌单名称");
  if (!name) return;
  const res = await fetch("/api/playlists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const pl = await res.json();
  await refresh();
  if (pl.id) location.hash = "#/p/" + pl.id;
};

$("home-nowplaying").onclick = () => {
  if (playingPl) location.hash = "#/p/" + playingPl;
};

$("pl-grid").addEventListener("click", async (e) => {
  const card = e.target.closest(".pl-card");
  if (!card) return;
  const id = card.dataset.id;
  const op = e.target.closest("[data-op]");
  if (op) {
    e.stopPropagation();
    if (op.dataset.op === "rename") {
      await renamePlaylist(id);
    } else if (op.dataset.op === "delpl") {
      const pl = plById(id);
      const yes = await askConfirm(`删除歌单「${pl?.name}」?`,
        "已下载的 MV 若不在其他歌单中也会一并删除。");
      if (!yes) return;
      if (playingPl === id) stopPlayback();
      await fetch("/api/playlists/" + id, { method: "DELETE" });
    }
    await refresh();
    return;
  }
  location.hash = "#/p/" + id;
});

/* ---------- 歌单页渲染 ---------- */

function itemKey(s) {
  return [s.status, Math.round(s.progress || 0), s.id === currentId ? 1 : 0,
          s.thumb ? 1 : 0, shuffleSet.has(viewPl) ? 1 : 0].join("|");
}

function songHtml(s) {
  let statusHtml;
  if (s.status === "downloading") {
    const pct = Math.round(s.progress || 0);
    statusHtml = `<div class="status busy">下载中 ${pct}%</div>
      <div class="bar"><i style="width:${pct}%"></i></div>`;
  } else {
    const [text, cls] = STATUS_TEXT[s.status] || [s.status, ""];
    const extra = s.status === "failed" && s.error ? `:${escapeHtml(s.error)}` : "";
    statusHtml = `<div class="status ${cls}" title="${escapeHtml(s.error || s.video_title || "")}">${text}${extra}</div>`;
  }
  const active = s.status !== "no_mv" && s.status !== "failed";
  const thumb = s.thumb
    ? `<img class="thumb" src="/media/thumbs/${s.id}.jpg" alt="">`
    : `<div class="thumb ph">♪</div>`;
  const ops = [
    // 随机播放时显示的是临时顺序,不允许调序(会把随机序写进真实歌单)
    active && !shuffleSet.has(viewPl) ? `<button data-op="up" title="上移">↑</button><button data-op="down" title="下移">↓</button>` : "",
    !active ? `<button data-op="retry" title="重试">↻</button>` : "",
    `<button data-op="copy" title="复制歌名和歌手">📋</button>`,
    s.status !== "pending" && s.status !== "searching" && s.status !== "downloading"
      ? `<button data-op="seturl" title="手动指定视频链接">🔗</button>` : "",
    `<button data-op="del" title="从歌单移除">✕</button>`,
  ].join("");
  return `<li class="song ${s.status} ${s.id === currentId ? "playing" : ""}"
    data-id="${s.id}" ${active && !shuffleSet.has(viewPl) ? 'draggable="true"' : ""}>
    ${thumb}
    <div class="meta">
      <div class="name" title="${escapeHtml(s.video_title || "")}">${escapeHtml(songLabel(s))}</div>
      ${statusHtml}
    </div>
    <div class="ops">${ops}</div>
  </li>`;
}

// 按 id 逐项同步列表:只替换真正变化的 <li>,不整表重建
function syncList(ul, arr) {
  const existing = new Map();
  for (const li of [...ul.children]) existing.set(li.dataset.id, li);
  const scratch = document.createElement("ul");
  let prev = null;
  for (const s of arr) {
    let li = existing.get(s.id);
    const key = itemKey(s);
    if (!li || li.dataset.key !== key) {
      scratch.innerHTML = songHtml(s);
      const fresh = scratch.firstElementChild;
      fresh.dataset.key = key;
      if (li) li.replaceWith(fresh);
      li = fresh;
    }
    existing.delete(s.id);
    const ref = prev ? prev.nextElementSibling : ul.firstElementChild;
    if (ref !== li) ul.insertBefore(li, ref);
    prev = li;
  }
  for (const li of existing.values()) li.remove();
}

function renderPlaylist() {
  const pl = plById(viewPl);
  if (!pl) { location.hash = ""; return; }
  $("pl-title").textContent = pl.name;

  if (dragLi) return; // 拖拽中不动 DOM

  const active = displaySongs(viewPl).filter((s) => s.status !== "no_mv" && s.status !== "failed");
  const removed = plSongs(viewPl).filter((s) => s.status === "no_mv" || s.status === "failed");

  syncList($("song-list"), active);
  syncList($("removed-list"), removed);

  const ready = active.filter((s) => s.status === "done").length;
  $("ready-count").textContent = ready ? `(${ready} 首可播)` : "";
  $("shuffle-btn").classList.toggle("on", shuffleSet.has(viewPl));
  $("drag-hint").textContent = shuffleSet.has(viewPl) ? "(随机播放中,原顺序已保留)" : "(拖拽可调顺序)";
  $("drag-hint").hidden = active.length < 2;
  $("removed-title").hidden = removed.length === 0;
  $("removed-count").textContent = removed.length ? `(${removed.length})` : "";
  $("empty-hint").style.display = currentId ? "none" : "";
}

function renderAll() {
  if (viewPl === null) renderHome();
  else renderPlaylist();
}

/* ---------- 音量:增益放大 + 响度均衡 ---------- */

const TARGET_DB = -14; // 均衡目标平均响度(取常见 MV 的中位值)
let audioCtx = null;
let gainNode = null;

function ensureAudioGraph() {
  if (audioCtx) {
    if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
    return;
  }
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const src = audioCtx.createMediaElementSource(video);
    gainNode = audioCtx.createGain();
    // 限幅器:只防削波爆音,不压制正常增益(默认压缩参数会把放大的音量压回去)
    const limiter = audioCtx.createDynamicsCompressor();
    limiter.threshold.value = -2;
    limiter.knee.value = 0;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.002;
    limiter.release.value = 0.25;
    src.connect(gainNode);
    gainNode.connect(limiter);
    limiter.connect(audioCtx.destination);
  } catch (e) {
    audioCtx = null;
    gainNode = null;
  }
}

function autoGain(song) {
  if (!song || typeof song.loudness !== "number") return 1;
  const db = Math.max(-10, Math.min(12, TARGET_DB - song.loudness));
  return Math.pow(10, db / 20);
}

function applyVolume() {
  const g = autoGain(songsById[currentId]);
  if (gainNode) {
    gainNode.gain.value = g;
  } else {
    video.volume = Math.min(1, g); // 无 WebAudio 时的降级:最多 100%
  }
}

/* ---------- 播放控制 ---------- */

function stopPlayback() {
  video.pause();
  video.removeAttribute("src");
  currentId = null;
  playingPl = null;
  $("now-playing").textContent = "未在播放";
}

const LANG_NAMES = {
  "zh": "中文", "zh-hans": "简体中文", "zh-hant": "繁體中文", "zh-cn": "简体中文",
  "zh-tw": "繁體中文", "zh-hk": "繁體中文", "en": "English", "en-us": "English",
  "ja": "日本語", "ko": "한국어", "es": "Español", "fr": "Français",
};

function attachSubtitles(s) {
  video.querySelectorAll("track").forEach((t) => t.remove());
  const langs = s.subs || [];
  if (!langs.length) return;
  // 默认开启的语言:中文 > 英文 > 日文 > 第一条
  const pref = ["zh", "en", "ja"];
  let def = null;
  for (const p of pref) {
    def = langs.find((l) => l.toLowerCase().startsWith(p));
    if (def) break;
  }
  if (!def) def = langs[0];
  for (const lang of langs) {
    const tr = document.createElement("track");
    tr.kind = "subtitles";
    tr.srclang = lang;
    tr.label = LANG_NAMES[lang.toLowerCase()] || lang;
    tr.src = "/media/subs/" + encodeURIComponent(`${s.id}.${lang}.vtt`);
    if (lang === def) tr.default = true;
    video.appendChild(tr);
  }
}

function playSong(id, plId) {
  const s = songsById[id];
  if (!s || s.status !== "done") return;
  currentId = id;
  playingPl = plId || viewPl || playingPl;
  ensureAudioGraph();
  applyVolume();
  video.src = "/media/" + encodeURIComponent(s.video_file);
  attachSubtitles(s);
  video.play().catch(() => {});
  $("now-playing").textContent = "正在播放:" + songLabel(s);
  renderAll();
}

function step(dir) {
  const plId = playingPl || viewPl;
  const queue = readyQueue(plId); // 随机播放时 queue 本身已是洗牌后的顺序
  if (!queue.length) return;
  const idx = queue.findIndex((s) => s.id === currentId);
  let nextIdx = idx === -1 ? 0 : idx + dir;
  if (nextIdx >= queue.length) {
    if (!loop) return;
    nextIdx = 0;
  }
  if (nextIdx < 0) nextIdx = queue.length - 1;
  playSong(queue[nextIdx].id, plId);
}

video.addEventListener("ended", () => step(1));
video.addEventListener("play", () => { $("play-btn").textContent = "⏸"; });
video.addEventListener("pause", () => { $("play-btn").textContent = "▶️"; });

$("play-btn").onclick = () => {
  if (!currentId) { step(1); return; }
  video.paused ? video.play() : video.pause();
};
$("next-btn").onclick = () => step(1);
$("prev-btn").onclick = () => step(-1);
$("shuffle-btn").onclick = function () {
  const plId = viewPl || playingPl;
  if (!plId) return;
  delete shuffleOrders[plId]; // 开启时重新洗牌,关闭时丢弃随机序恢复原顺序
  if (shuffleSet.has(plId)) shuffleSet.delete(plId);
  else shuffleSet.add(plId);
  this.classList.toggle("on", shuffleSet.has(plId));
  renderAll();
};
$("loop-btn").onclick = function () {
  loop = !loop;
  this.classList.toggle("on", loop);
};
$("fs-btn").onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else video.requestFullscreen().catch(() => {});
};

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  if ($("modal").open) return; // 对话框打开时不响应播放快捷键
  if (viewPl === null && !currentId) return;
  if (e.code === "Space") { e.preventDefault(); $("play-btn").click(); }
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "ArrowLeft") step(-1);
  else if (e.key.toLowerCase() === "f") $("fs-btn").click();
});

/* ---------- 歌单内操作 ---------- */

$("add-btn").onclick = async () => {
  const text = $("song-input").value.trim();
  if (!text || !viewPl) return;
  $("add-btn").disabled = true;
  try {
    const res = await fetch(`/api/playlists/${viewPl}/songs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (data.error) toast(data.error);
    else if (data.added > 0) toast(`已添加 ${data.added} 首`);
    if (data.added > 0) $("song-input").value = "";
    await refresh();
  } finally {
    $("add-btn").disabled = false;
  }
};

async function commitOrder() {
  const pl = plById(viewPl);
  if (!pl) return;
  const domIds = [...$("song-list").children].map((li) => li.dataset.id);
  const rest = pl.song_ids.filter((id) => !domIds.includes(id));
  await fetch(`/api/playlists/${viewPl}/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_ids: [...domIds, ...rest] }),
  });
  await refresh();
}

async function moveSong(id, delta) {
  if (shuffleSet.has(viewPl)) return; // 随机播放中不允许调序
  const pl = plById(viewPl);
  if (!pl) return;
  const active = plSongs(viewPl)
    .filter((s) => s.status !== "no_mv" && s.status !== "failed")
    .map((s) => s.id);
  const i = active.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= active.length) return;
  [active[i], active[j]] = [active[j], active[i]];
  const rest = pl.song_ids.filter((x) => !active.includes(x));
  await fetch(`/api/playlists/${viewPl}/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_ids: [...active, ...rest] }),
  });
  await refresh();
}

document.addEventListener("click", async (e) => {
  const li = e.target.closest("li.song");
  if (!li) return;
  const id = li.dataset.id;
  const opBtn = e.target.closest("[data-op]");
  if (opBtn) {
    e.stopPropagation();
    const op = opBtn.dataset.op;
    if (op === "del") {
      // 删的是正在播的歌:顺延到它的下一首;最后一首且不循环则停
      let nextAfterDelete = null;
      if (id === currentId) {
        const oldQueue = readyQueue(playingPl || viewPl);
        const idx = oldQueue.findIndex((s) => s.id === id);
        const rest = oldQueue.filter((s) => s.id !== id);
        if (rest.length) {
          if (idx < rest.length) nextAfterDelete = rest[idx];
          else if (loop) nextAfterDelete = rest[0];
        }
        stopPlayback();
      }
      await fetch(`/api/playlists/${viewPl}/songs/${id}`, { method: "DELETE" });
      await refresh();
      if (nextAfterDelete) playSong(nextAfterDelete.id, viewPl);
    } else if (op === "retry") {
      await fetch("/api/songs/" + id + "/retry", { method: "POST" });
      await refresh();
    } else if (op === "copy") {
      const s = songsById[id];
      const text = (s.title + " " + (s.artist || "")).trim();
      copyText(text)
        .then(() => toast("已复制:" + text))
        .catch(() => askText("自动复制被浏览器拦截 — 文字已选中,按 ⌘C 复制", text));
    } else if (op === "seturl") {
      const url = await askText("手动指定视频链接(跳过自动搜索,直接下载)",
        "", "https://www.youtube.com/watch?v=...");
      if (url) {
        if (id === currentId) stopPlayback();
        const res = await fetch("/api/songs/" + id + "/set_url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
        const data = await res.json();
        if (data.error) toast(data.error);
      }
      await refresh();
    } else if (op === "up") {
      await moveSong(id, -1);
    } else if (op === "down") {
      await moveSong(id, 1);
    }
    return;
  }
  if (li.classList.contains("done")) playSong(id, viewPl);
});

/* ---------- 拖拽排序 ---------- */

const songList = $("song-list");

songList.addEventListener("dragstart", (e) => {
  if (shuffleSet.has(viewPl)) return; // 随机播放中不允许拖拽调序
  const li = e.target.closest("li.song");
  if (!li) return;
  dragLi = li;
  li.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  try { e.dataTransfer.setData("text/plain", li.dataset.id); } catch (_) {}
});

songList.addEventListener("dragover", (e) => {
  if (!dragLi) return;
  e.preventDefault();
  const li = e.target.closest("li.song");
  if (!li || li === dragLi) return;
  const r = li.getBoundingClientRect();
  const before = e.clientY < r.top + r.height / 2;
  songList.insertBefore(dragLi, before ? li : li.nextSibling);
});

songList.addEventListener("drop", (e) => e.preventDefault());

songList.addEventListener("dragend", async () => {
  if (!dragLi) return;
  dragLi.classList.remove("dragging");
  dragLi = null;
  await commitOrder();
});

/* ---------- 状态轮询 ---------- */

async function refresh() {
  let data;
  try {
    const res = await fetch("/api/state");
    data = await res.json();
  } catch (err) {
    return; // 服务暂时不可达,下轮再试
  }
  playlists = data.playlists || [];
  songsById = {};
  for (const s of data.songs || []) songsById[s.id] = s;

  // 正在播放的歌不存在了(被其他窗口删除)→ 停
  if (currentId && !songsById[currentId]) stopPlayback();
  else if (currentId) applyVolume(); // 响度数据可能刚补测出来
  // 打开的歌单被删了 → 回主页
  if (viewPl && !plById(viewPl)) { location.hash = ""; return; }
  renderAll();
}

// 初始开关状态与按钮视觉保持一致
$("loop-btn").classList.toggle("on", loop);

setInterval(refresh, 1500);
refresh().then(route);
