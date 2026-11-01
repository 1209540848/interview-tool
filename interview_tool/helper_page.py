# -*- coding: utf-8 -*-
"""helper_page.py — 给朋友手机浏览器用的内嵌单页（无外部 CDN、无第三方依赖）。

页面只做三件事：显示本机推送的问题/截图、把朋友的文字答案 POST 回本机、长轮询
等待新消息。所有用户内容一律走 textContent / DOM API 渲染，绝不拼 innerHTML，
防止朋友端输入被当成 HTML 执行（房间 token 只防陌生人，不防恶意文本）。
"""

PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#11151c">
<title>面试互助</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #11151c; --card: #1a202b; --line: #2a3342;
    --fg: #e8ecf2; --muted: #8d98aa; --subtle: #667085;
    --accent: #82afff; --warm: #e6b673; --ok: #9dd6a4; --err: #ef8f8f;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
  body {
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
          "PingFang SC", "Microsoft YaHei UI", sans-serif;
    padding: 0 12px calc(24px + env(safe-area-inset-bottom));
  }
  header {
    position: sticky; top: 0; z-index: 5;
    display: flex; align-items: center; gap: 8px;
    margin: 0 -12px; padding: 10px 14px calc(10px + env(safe-area-inset-top));
    background: rgba(17, 21, 28, .94); backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line);
  }
  header h1 { font-size: 16px; margin: 0; font-weight: 700; letter-spacing: .02em; }
  #dot { width: 8px; height: 8px; border-radius: 50%; background: var(--subtle); flex: none; }
  #dot.on { background: var(--ok); box-shadow: 0 0 8px rgba(157, 214, 164, .7); }
  #state { font-size: 12px; color: var(--muted); }
  #online { font-size: 12px; color: var(--subtle); margin-left: auto; }
  #empty { color: var(--subtle); text-align: center; padding: 56px 20px; font-size: 14px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    margin: 12px 0; padding: 12px 13px; box-shadow: 0 1px 0 rgba(255,255,255,.03) inset;
  }
  .card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .badge {
    font-size: 12px; font-weight: 700; color: var(--warm);
    background: rgba(230, 182, 115, .12); border: 1px solid rgba(230, 182, 115, .22);
    border-radius: 999px; padding: 1px 8px;
  }
  .when { margin-left: auto; font-size: 11px; color: var(--subtle); font-variant-numeric: tabular-nums; }
  .qtext { margin: 2px 0 8px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; }
  .shot { display: block; width: 100%; height: auto; border-radius: 10px;
          border: 1px solid var(--line); margin: 6px 0 10px; background: #0b0e13; }
  .reply { display: flex; flex-direction: column; gap: 8px; }
  textarea {
    width: 100%; min-height: 68px; resize: vertical; font: inherit; font-size: 16px;
    color: var(--fg); background: #121822; border: 1px solid var(--line);
    border-radius: 10px; padding: 9px 10px; outline: none;
  }
  textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(130,175,255,.16); }
  .row { display: flex; align-items: center; gap: 10px; }
  .send {
    margin-left: auto; font: inherit; font-size: 14px; font-weight: 700;
    color: #0d1117; background: var(--accent); border: 0; border-radius: 10px;
    padding: 9px 22px; cursor: pointer;
  }
  .send:disabled { opacity: .55; }
  .send-state { font-size: 12px; color: var(--muted); }
  .send-state.ok { color: var(--ok); }
  .send-state.err { color: var(--err); }
  .hint { color: var(--subtle); font-size: 12px; text-align: center; margin-top: 28px; }
</style>
</head>
<body>
<header>
  <span id="dot"></span>
  <h1>面试互助</h1>
  <span id="state">连接中…</span>
  <span id="online"></span>
</header>
<div id="empty">等待面试官问题…<br><span style="font-size:12px">保持本页打开，息屏后亮屏会自动重连</span></div>
<main id="list"></main>
<p class="hint">只有持有本次房间链接的人能看到内容 · Ctrl/⌘ + Enter 快速发送</p>
<script>
(function () {
  "use strict";
  var TOKEN = new URLSearchParams(location.search).get("t") || "";
  var CLIENT = (function () {
    try {
      var v = localStorage.getItem("helper-client");
      if (!v) {
        v = (window.crypto && crypto.randomUUID)
          ? crypto.randomUUID()
          : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
        localStorage.setItem("helper-client", v);
      }
      return v;
    } catch (e) {
      return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
    }
  })();

  var list = document.getElementById("list");
  var empty = document.getElementById("empty");
  var dot = document.getElementById("dot");
  var state = document.getElementById("state");
  var online = document.getElementById("online");
  var since = 0;
  var seen = Object.create(null);
  var backoff = 1000;
  var cardCount = 0;

  function setConnected(ok) {
    dot.className = ok ? "on" : "";
    state.textContent = ok ? "已连接" : "重连中…";
    state.style.color = ok ? "var(--ok)" : "var(--muted)";
  }

  function fmtTime(ts) {
    return ts && ts.length >= 16 ? ts.slice(11, 16) : "";
  }

  function addCard(ev) {
    if (!ev || seen[ev.seq]) return;
    seen[ev.seq] = true;
    cardCount += 1;
    empty.style.display = "none";

    var card = document.createElement("article");
    card.className = "card";

    var head = document.createElement("div");
    head.className = "card-head";
    var badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = ev.kind === "shot" ? "📸 截图" : ("Q#" + ev.seq);
    var when = document.createElement("span");
    when.className = "when";
    when.textContent = fmtTime(ev.ts);
    head.appendChild(badge);
    head.appendChild(when);
    card.appendChild(head);

    if (ev.text) {
      var p = document.createElement("p");
      p.className = "qtext";
      p.textContent = ev.text;
      card.appendChild(p);
    }

    if (ev.image) {
      var img = document.createElement("img");
      img.className = "shot";
      img.loading = "lazy";
      img.alt = "面试官屏幕截图";
      img.src = ev.image + "?t=" + encodeURIComponent(TOKEN);
      card.appendChild(img);
    }

    var box = document.createElement("div");
    box.className = "reply";
    var ta = document.createElement("textarea");
    ta.rows = 3;
    ta.placeholder = "帮 TA 想答案…（Ctrl/⌘ + Enter 发送）";
    var row = document.createElement("div");
    row.className = "row";
    var btn = document.createElement("button");
    btn.className = "send";
    btn.type = "button";
    btn.textContent = "发送";
    var st = document.createElement("span");
    st.className = "send-state";
    var pendingCid = null;

    async function send() {
      var text = ta.value.trim();
      if (!text) { ta.focus(); return; }
      if (!pendingCid) {
        pendingCid = CLIENT + "-" + Date.now().toString(36) + "-" +
          Math.random().toString(16).slice(2, 8);
      }
      btn.disabled = true;
      st.textContent = "发送中…";
      st.className = "send-state";
      try {
        var r = await fetch("/api/answer?t=" + encodeURIComponent(TOKEN), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ qid: ev.id, text: text, client_id: pendingCid })
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        st.textContent = "已发送 ✓";
        st.className = "send-state ok";
        ta.value = "";
        pendingCid = null;
      } catch (e) {
        st.textContent = "发送失败，可点发送重试（不会重复）";
        st.className = "send-state err";
      }
      btn.disabled = false;
    }

    btn.addEventListener("click", send);
    ta.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); send(); }
    });
    row.appendChild(btn);
    row.appendChild(st);
    box.appendChild(ta);
    box.appendChild(row);
    card.appendChild(box);
    list.appendChild(card);
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  async function once(timeoutSec) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, (timeoutSec + 10) * 1000);
    try {
      var url = "/api/poll?t=" + encodeURIComponent(TOKEN) +
        "&since=" + since + "&timeout=" + timeoutSec +
        "&client=" + encodeURIComponent(CLIENT);
      var r = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
      if (r.status === 403) throw new Error("房间链接无效");
      if (!r.ok) throw new Error("HTTP " + r.status);
      var data = await r.json();
      setConnected(true);
      backoff = 1000;
      (data.events || []).forEach(addCard);
      if (typeof data.now === "number" && data.now > since) since = data.now;
      online.textContent = data.online > 0 ? ("· " + data.online + " 台在线") : "";
    } finally {
      clearTimeout(timer);
    }
  }

  async function loop() {
    for (;;) {
      try {
        await once(25);
      } catch (e) {
        setConnected(false);
        await sleep(backoff);
        backoff = Math.min(Math.round(backoff * 1.7), 10000);
      }
    }
  }

  (async function boot() {
    try {
      var r = await fetch("/api/ping?t=" + encodeURIComponent(TOKEN) +
        "&client=" + encodeURIComponent(CLIENT), { cache: "no-store" });
      if (r.status === 403) {
        state.textContent = "链接无效";
        state.style.color = "var(--err)";
        return;
      }
      if (r.ok) setConnected(true);
    } catch (e) { /* 由长轮询继续重试 */ }
    loop();
  })();
})();
</script>
</body>
</html>
"""
