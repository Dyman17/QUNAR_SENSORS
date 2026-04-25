(() => {
  const POLL_MS = 2000;
  const TIME_ZONE = "Asia/Qyzylorda"; // UTC+5
  const LOCALE = "ru-RU";
  const CAM_ONLINE_SEC = 10;
  const CHAT_STORE_KEY = "aiDosChatV1";

  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const el = byId(id);
    if (!el) return;
    el.textContent = value;
  };

  const fmt = (v) => {
    if (v === null || v === undefined || v === "") return "--";
    return String(v);
  };

  const fmtAgo = (seconds) => {
    if (seconds === null || seconds === undefined) return "--";
    if (seconds < 60) return `${seconds} сек назад`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
    return `${Math.floor(seconds / 3600)} ч назад`;
  };

  const splitDateTime = (iso) => {
    if (!iso) return { date: "--", time: "--" };
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return { date: "--", time: "--" };

    try {
      const date = new Intl.DateTimeFormat(LOCALE, {
        timeZone: TIME_ZONE,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(d);
      const time = new Intl.DateTimeFormat(LOCALE, {
        timeZone: TIME_ZONE,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(d);
      return { date, time };
    } catch {
      // Fallback: apply +5 hours manually to UTC.
      const plus5 = new Date(d.getTime() + 5 * 60 * 60 * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      const date = `${pad(plus5.getUTCDate())}.${pad(plus5.getUTCMonth() + 1)}.${plus5.getUTCFullYear()}`;
      const time = `${pad(plus5.getUTCHours())}:${pad(plus5.getUTCMinutes())}:${pad(plus5.getUTCSeconds())}`;
      return { date, time };
    }
  };

  const fmtCmd = (v) => {
    if (v === null || v === undefined) return "--";
    const n = Number(v);
    if (n === 1) return "ON (1)";
    if (n === 0) return "OFF (0)";
    return String(v);
  };

  const setStatus = (online, seconds) => {
    const badge = byId("status-badge");
    if (!badge) return;
    badge.textContent = online ? "Онлайн" : "Оффлайн";
    badge.classList.toggle("ok", Boolean(online));
    badge.classList.toggle("warn", !online);
    setText("status-ago", `(${fmtAgo(seconds)})`);
  };

  const setCamStatus = (online, seconds) => {
    const badge = byId("cam-status-badge");
    if (!badge) return;
    badge.textContent = online ? "Video online" : "No video";
    badge.classList.toggle("ok", Boolean(online));
    badge.classList.toggle("warn", !online);
    setText("cam-status-ago", `(${fmtAgo(seconds)})`);
  };

  async function poll() {
    if (!byId("status-badge") && !byId("v-device-id")) return;
    try {
      const res = await fetch("/api/current", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const receivedAt = data.last_received_at || null;
      const packet = data.packet || null;
      const normalized = data.normalized_fields || null;
      const computed = data.computed || null;

      if (!packet) {
        setText("has-packet", "Пакеты ещё не приходили.");
        setStatus(false, null);
        return;
      }

      setText("has-packet", "Пакеты приходят.");
      setText("received-at", fmt(receivedAt));
      const dt = splitDateTime(receivedAt);
      setText("received-date", dt.date);
      setText("received-time", dt.time);

      const now = Date.now();
      const last = receivedAt ? Date.parse(receivedAt) : NaN;
      const seconds = Number.isFinite(last) ? Math.max(0, Math.floor((now - last) / 1000)) : null;
      const online = seconds !== null && seconds <= 30;
      setStatus(online, seconds);

      setText("v-device-id", fmt(packet.device_id));
      setText("v-token", fmt(packet.device_token));

      setText("v-t1", fmt(packet.temperature1));
      setText("v-h1", fmt(packet.humidity1));
      setText("v-t2", fmt(packet.temperature2));
      setText("v-h2", fmt(packet.humidity2));
      setText("v-soil", fmt(packet.soil));
      setText("v-light1", fmt(packet.light1));
      setText("v-light2", fmt(packet.light2));
      setText("v-r1", fmt(packet.relay1_state));
      setText("v-r2", fmt(packet.relay2_state));
      setText("v-rssi", fmt(packet.wifi_rssi));
      setText("v-heap", fmt(packet.free_heap));
      setText("v-uptime", fmt(packet.uptime_sec));
      setText("v-fw", fmt(packet.firmware_version));

      if (computed && computed.relay_state) {
        setText("cmd-r1", fmtCmd(computed.relay_state.relay1_command));
        setText("cmd-r2", fmtCmd(computed.relay_state.relay2_command));
      }

      if (normalized) {
        const pre = byId("normalized-json");
        if (pre) pre.textContent = JSON.stringify(normalized, null, 2);
      }
    } catch (e) {
      setText("has-packet", "Ошибка обновления. Проверь логи Render.");
      setStatus(false, null);
    }
  }

  const reloadCam = () => {
    const img = byId("cam-img");
    if (!img) return;
    img.src = `/api/video/stream.mjpeg?t=${Date.now()}`;
  };

  async function pollVideo() {
    if (!byId("cam-status-badge")) return;

    try {
      const res = await fetch("/api/video/status", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const receivedAt = data.last_received_at || null;
      setText("cam-received-at", fmt(receivedAt));
      setText("cam-bytes", fmt(data.bytes));

      if (!data.has_frame) {
        setText("cam-has-frame", "Waiting for the first frame...");
        setCamStatus(false, null);
        return;
      }

      const now = Date.now();
      const last = receivedAt ? Date.parse(receivedAt) : NaN;
      const seconds = Number.isFinite(last) ? Math.max(0, Math.floor((now - last) / 1000)) : null;
      const online = seconds !== null && seconds <= CAM_ONLINE_SEC;
      setCamStatus(online, seconds);
      setText("cam-has-frame", "Frames received.");
    } catch (e) {
      setCamStatus(false, null);
      setText("cam-has-frame", "Video status error. Check server logs.");
    }
  }

  const getAiToken = () => {
    const el = byId("ai-token");
    if (!el) return "";
    return String(el.value || "").trim();
  };

  const aiHeaders = () => {
    const token = getAiToken();
    return token ? { "X-AI-Token": token } : {};
  };

  const setBusy = (id, busy) => {
    const el = byId(id);
    if (!el) return;
    el.disabled = Boolean(busy);
  };

  const readChat = () => {
    try {
      const raw = localStorage.getItem(CHAT_STORE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  };

  const writeChat = (messages) => {
    try {
      localStorage.setItem(CHAT_STORE_KEY, JSON.stringify(messages.slice(-40)));
    } catch {}
  };

  const renderChat = () => {
    const log = byId("ai-chat-log");
    if (!log) return;
    log.innerHTML = "";
    const messages = readChat();
    for (const m of messages) appendChat(m.role, m.content, false);
    log.scrollTop = log.scrollHeight;
  };

  const clearChat = () => {
    try {
      localStorage.removeItem(CHAT_STORE_KEY);
    } catch {}
    renderChat();
  };

  async function runAiAnalyze() {
    if (!byId("ai-analyze-btn")) return;
    setBusy("ai-analyze-btn", true);
    setText("ai-analyze-status", "Запрос...");
    try {
      const res = await fetch("/api/ai/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...aiHeaders() },
        body: "{}",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      const cards = byId("ai-analyze-cards");
      if (cards) {
        cards.hidden = false;
        const a = data.analysis || {};
        const items = [
          { k: "Score", v: a.score },
          { k: "Verdict", v: a.verdict },
          { k: "Confidence", v: a.confidence },
        ];
        cards.innerHTML = items
          .map((it) => `<div class="ai-card"><div class="ai-card__k">${it.k}</div><div class="ai-card__v">${String(it.v ?? "--")}</div></div>`)
          .join("");
      }

      const out = byId("ai-analyze-out");
      if (out) out.textContent = JSON.stringify(data, null, 2);
      setText("ai-analyze-status", "Готово.");
    } catch (e) {
      setText("ai-analyze-status", `Ошибка: ${e.message || e}`);
    } finally {
      setBusy("ai-analyze-btn", false);
    }
  }

  const appendChat = (role, text, scroll = true) => {
    const log = byId("ai-chat-log");
    if (!log) return;

    const wrap = document.createElement("div");
    const meta = document.createElement("div");
    meta.className = "chat-meta";
    meta.textContent = role === "user" ? "Ты" : "AI";

    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    bubble.textContent = String(text || "");

    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    log.appendChild(wrap);
    if (scroll) log.scrollTop = log.scrollHeight;
  };

  async function sendAiChat() {
    const input = byId("ai-chat-input");
    if (!input) return;

    const msg = String(input.value || "").trim();
    if (!msg) return;

    input.value = "";
    appendChat("user", msg);
    const messages = readChat();
    messages.push({ role: "user", content: msg });
    writeChat(messages);

    setBusy("ai-chat-send", true);
    setText("ai-chat-status", "Запрос...");
    try {
      const res = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...aiHeaders() },
        body: JSON.stringify({ messages }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      appendChat("assistant", data.reply || "");
      messages.push({ role: "assistant", content: String(data.reply || "") });
      writeChat(messages);
      setText("ai-chat-status", "Готово.");
    } catch (e) {
      appendChat("assistant", `Ошибка: ${e.message || e}`);
      setText("ai-chat-status", "Ошибка.");
    } finally {
      setBusy("ai-chat-send", false);
    }
  }

  window.addEventListener("load", () => {
    if (byId("status-badge") || byId("v-device-id")) {
      poll();
      setInterval(poll, POLL_MS);
    }

    const reload = byId("cam-reload");
    if (reload) reload.addEventListener("click", reloadCam);
    const img = byId("cam-img");
    if (img) img.addEventListener("error", () => setText("cam-has-frame", "Stream error. Try Reload stream."));

    if (byId("cam-status-badge")) {
      pollVideo();
      setInterval(pollVideo, POLL_MS);
    }

    const tokenEl = byId("ai-token");
    if (tokenEl) {
      try {
        tokenEl.value = localStorage.getItem("aiToken") || "";
      } catch {}
      tokenEl.addEventListener("input", () => {
        try {
          localStorage.setItem("aiToken", tokenEl.value || "");
        } catch {}
      });
    }

    const analyzeBtn = byId("ai-analyze-btn");
    if (analyzeBtn) analyzeBtn.addEventListener("click", runAiAnalyze);

    const analyzeClear = byId("ai-analyze-clear");
    if (analyzeClear)
      analyzeClear.addEventListener("click", () => {
        const out = byId("ai-analyze-out");
        if (out) out.textContent = "{}";
        const cards = byId("ai-analyze-cards");
        if (cards) {
          cards.hidden = true;
          cards.innerHTML = "";
        }
        setText("ai-analyze-status", "—");
      });

    const sendBtn = byId("ai-chat-send");
    if (sendBtn) sendBtn.addEventListener("click", sendAiChat);

    const chatInput = byId("ai-chat-input");
    if (chatInput) {
      chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendAiChat();
        }
      });
    }

    const clearBtn = byId("ai-chat-clear");
    if (clearBtn) clearBtn.addEventListener("click", clearChat);

    const chips = document.querySelectorAll("[data-suggest]");
    chips.forEach((btn) => {
      btn.addEventListener("click", () => {
        const val = btn.getAttribute("data-suggest") || "";
        const el = byId("ai-chat-input");
        if (!el) return;
        el.value = val;
        el.focus();
      });
    });

    if (byId("ai-chat-log")) renderChat();
  });
})();
