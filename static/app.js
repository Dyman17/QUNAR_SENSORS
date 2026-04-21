(() => {
  const POLL_MS = 2000;
  const TIME_ZONE = "Asia/Qyzylorda"; // UTC+5
  const LOCALE = "ru-RU";

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

  async function poll() {
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

  window.addEventListener("load", () => {
    poll();
    setInterval(poll, POLL_MS);
  });
})();
