(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const text = (id, value) => { const element = byId(id); if (element) element.textContent = value; };
  const titleCase = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const signal = (lampId, stateId, color, label) => {
    const lamp = byId(lampId);
    if (lamp) lamp.className = `signal-lamp ${color}`;
    text(stateId, label);
  };
  const notify = (message, category) => {
    let stack = document.querySelector(".flash-stack");
    if (!stack) { stack = document.createElement("div"); stack.className = "flash-stack"; document.body.append(stack); }
    const item = document.createElement("div");
    item.className = `flash ${category}`;
    item.textContent = message;
    stack.append(item);
    window.setTimeout(() => { item.classList.add("is-dismissing"); window.setTimeout(() => item.remove(), 220); }, 4000);
  };
  let controlsAvailable = false;
  let serialBusy = false;
  const setControlAvailability = () => {
    document.querySelectorAll(".diagnostic-command").forEach((button) => { button.disabled = !controlsAvailable; });
    const send = byId("serial-send");
    if (send) send.disabled = !controlsAvailable || serialBusy;
  };
  const terminalLine = (value = "") => {
    const terminal = byId("serial-terminal");
    if (!terminal) return;
    terminal.textContent += `${terminal.textContent ? "\n" : ""}${value}`;
    terminal.scrollTop = terminal.scrollHeight;
  };
  const timestamp = () => new Date().toLocaleTimeString([], { hour12: false });
  const render = (system) => {
    const online = Boolean(system.controller_online);
    const rfidOnly = system.controller_type === "rfid";
    const moving = ["opening", "closing", "fault"].includes(system.gate_state);
    signal("controller-lamp", "controller-state", online ? "green" : "red", online ? "Connected" : "Offline");
    text("reader-status-icon", rfidOnly ? "◎" : "◉");
    text("reader-status-label", rfidOnly ? "RFID reader" : "Camera");
    signal(
      "camera-lamp",
      "camera-state",
      rfidOnly ? (online ? "green" : "red") : (online && system.camera_running ? "green" : "red"),
      rfidOnly ? (online ? "Active" : "Offline") : (online && system.camera_running ? "Detected" : "Unavailable"),
    );
    signal("loop-lamp", "loop-state", online ? (system.loop_active ? "green" : "red") : "off", online ? (system.loop_active ? "Vehicle present" : "Clear") : "Unknown");
    signal("ir-lamp", "ir-state", online ? (system.ir_blocked ? "red" : "green") : "off", online ? (system.ir_blocked ? "Blocked" : "Clear") : "Unknown");
    signal("barrier-lamp", "barrier-state", online ? (moving ? "amber" : (system.barrier_open ? "green" : "red")) : "off", online ? titleCase(system.gate_state) : "Unknown");
    signal("traffic-lamp", "traffic-state", online ? (system.traffic_green ? "green" : "red") : "off", online ? (system.traffic_green ? "GO" : "STOP") : "Unknown");
    signal("plate-result-lamp", "plate-result-state", online ? (system.plate_unrecognized ? "red" : "green") : "off", online ? (system.plate_unrecognized ? "Not recognized" : "Ready") : "Unknown");
    text("hardware-updated", system.controller_seen_at || "Waiting for controller");
    controlsAvailable = online && system.controller_type === "plate" && system.gate_state !== "disabled";
    setControlAvailability();
  };
  const sync = async () => {
    try {
      const response = await fetch("/api/dashboard", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Status ${response.status}`);
      render((await response.json()).system);
      text("hardware-sync", "Live");
      byId("hardware-sync")?.classList.remove("error");
    } catch (error) {
      text("hardware-sync", "Reconnecting…");
      byId("hardware-sync")?.classList.add("error");
      console.warn(error);
    } finally {
      window.setTimeout(sync, 1000);
    }
  };
  document.querySelectorAll(".diagnostic-command").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm(button.dataset.confirm || "Send this hardware command?")) return;
      const buttons = [...document.querySelectorAll(".diagnostic-command")];
      buttons.forEach((item) => { item.disabled = true; });
      const body = new FormData();
      body.append("csrf_token", byId("hardware-csrf")?.value || "");
      body.append("command", button.dataset.command || "");
      try {
        const response = await fetch("/hardware/command", { method: "POST", body, headers: { Accept: "application/json" } });
        const result = await response.json();
        notify(result.message || "Controller command completed.", result.success ? "success" : "error");
      } catch (error) {
        notify("The hardware command could not be sent.", "error");
        console.warn(error);
      }
    });
  });
  const pollSerialResult = async (commandId) => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 300));
      const response = await fetch(`/api/hardware/commands/${commandId}`, { cache: "no-store", headers: { Accept: "application/json" } });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Status ${response.status}`);
      if (["completed", "failed"].includes(result.status)) return result;
      text("serial-terminal-state", result.status === "active" ? "Reading…" : "Queued…");
    }
    throw new Error("Timed out while waiting for the controller response.");
  };
  byId("serial-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!controlsAvailable || serialBusy) return;
    if (!window.confirm("Confirm the lane is clear. Send this command directly to the RFID reader?")) return;
    const form = event.currentTarget;
    const body = new FormData(form);
    body.append("csrf_token", byId("hardware-csrf")?.value || "");
    serialBusy = true;
    setControlAvailability();
    text("serial-terminal-state", "Sending…");
    terminalLine("");
    terminalLine(`[${timestamp()}] > ${byId("serial-command")?.value || ""}`);
    terminalLine(`PORT /dev/serial0  ${body.get("baud")} ${body.get("data_bits")}${body.get("parity")}${body.get("stop_bits")}  timeout=${body.get("timeout_ms")}ms`);
    try {
      const response = await fetch("/hardware/serial", { method: "POST", body, headers: { Accept: "application/json" } });
      const queued = await response.json();
      if (!response.ok || !queued.success) throw new Error(queued.message || `Status ${response.status}`);
      terminalLine(`TX HEX: ${queued.tx_hex}`);
      const result = await pollSerialResult(queued.command_id);
      if (result.response_data) terminalLine(result.response_data.trimEnd());
      terminalLine(`[${timestamp()}] ${result.status.toUpperCase()}: ${result.result_message || "No message"}`);
      text("serial-terminal-state", result.status === "completed" ? "Complete" : "Failed");
    } catch (error) {
      terminalLine(`[${timestamp()}] ERROR: ${error.message}`);
      text("serial-terminal-state", "Error");
    } finally {
      serialBusy = false;
      setControlAvailability();
    }
  });
  document.querySelectorAll(".serial-preset").forEach((button) => {
    button.addEventListener("click", () => {
      const command = byId("serial-command");
      const mode = byId("serial-mode");
      const timeout = byId("serial-timeout");
      if (command) command.value = button.dataset.serialCommand || "";
      if (mode) mode.value = "hex";
      if (timeout && button.dataset.serialTimeout) timeout.value = button.dataset.serialTimeout;
      document.querySelectorAll(".serial-preset").forEach((item) => item.classList.remove("selected"));
      button.classList.add("selected");
      text("serial-preset-help", `${button.dataset.serialName || "RFID command"} loaded. Review it, then press Send command.`);
      command?.focus();
    });
  });
  byId("serial-command")?.addEventListener("input", () => {
    document.querySelectorAll(".serial-preset").forEach((item) => item.classList.remove("selected"));
  });
  byId("serial-clear")?.addEventListener("click", () => {
    const terminal = byId("serial-terminal");
    if (terminal) terminal.textContent = "";
    text("serial-terminal-state", "Ready");
  });
  sync();
})();
