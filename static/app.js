(() => {
  const POLL_MS = 2000;

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
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  };

  const setStatus = (online, seconds) => {
    const badge = byId("status-badge");
    if (!badge) return;
    badge.textContent = online ? "Online" : "Offline";
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
        setText("has-packet", "No packet received yet.");
        setStatus(false, null);
        return;
      }

      setText("has-packet", "Receiving packets.");
      setText("received-at", fmt(receivedAt));

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
        setText("cmd-r1", fmt(computed.relay_state.relay1_command));
        setText("cmd-r2", fmt(computed.relay_state.relay2_command));
      }

      if (normalized) {
        const pre = byId("normalized-json");
        if (pre) pre.textContent = JSON.stringify(normalized, null, 2);
      }
    } catch (e) {
      setText("has-packet", "Polling error. Check service logs.");
      setStatus(false, null);
    }
  }

  window.addEventListener("load", () => {
    poll();
    setInterval(poll, POLL_MS);
  });
})();
