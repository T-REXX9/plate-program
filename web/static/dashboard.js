(() => {
  "use strict";

  const POLL_INTERVAL_MS = 2000;
  let timer = null;
  let syncing = false;
  let latestEvent = null;
  let frameKind = window.localStorage.getItem("plate-frame-kind") === "raw"
    ? "raw"
    : "annotated";

  const byId = (id) => document.getElementById(id);
  const text = (id, value) => {
    const element = byId(id);
    if (element) element.textContent = value;
  };
  const titleCase = (value) => {
    const clean = String(value || "");
    return clean ? clean.charAt(0).toUpperCase() + clean.slice(1) : "";
  };
  const indicator = (id, state) => {
    const element = byId(id);
    if (element) element.className = `indicator ${state}`;
  };
  const signal = (id, stateId, color, label) => {
    const lamp = byId(id);
    if (lamp) lamp.className = `signal-lamp ${color}`;
    text(stateId, label);
  };
  const statusBadge = (value) => {
    const badge = document.createElement("span");
    badge.className = `status ${String(value || "").toLowerCase()}`;
    badge.textContent = value || "—";
    return badge;
  };

  function showNotification(message, category) {
    let stack = document.querySelector(".flash-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "flash-stack";
      stack.setAttribute("aria-live", "polite");
      document.body.append(stack);
    }
    const notification = document.createElement("div");
    notification.className = `flash ${category}`;
    notification.textContent = message;
    stack.append(notification);
    const dismiss = () => {
      if (notification.classList.contains("is-dismissing")) return;
      notification.classList.add("is-dismissing");
      window.setTimeout(() => notification.remove(), 220);
    };
    const timer = window.setTimeout(dismiss, 4000);
    notification.addEventListener("mouseenter", () => {
      window.clearTimeout(timer);
      dismiss();
    }, { once: true });
  }

  const formatDuration = (milliseconds) => {
    const value = Number(milliseconds || 0);
    return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value} ms`;
  };

  function updateFrameSelector() {
    const selector = byId("frame-selector");
    if (selector) selector.hidden = Boolean(latestEvent && !latestEvent.has_image);
    document.querySelectorAll(".frame-option").forEach((button) => {
      const selected = button.dataset.frameKind === frameKind;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    if (latestEvent && !latestEvent.has_image) {
      text("latest-photo-title", "Latest RFID access");
      text("latest-photo-subtitle", "RFID decision received from a camera-less controller");
      return;
    }
    text("latest-photo-title", frameKind === "raw" ? "Latest raw frame" : "Latest annotated frame");
    text(
      "latest-photo-subtitle",
      frameKind === "raw"
        ? "Original camera view before plate annotation"
        : "Vehicle frame with the detected plate marked",
    );
  }

  function showSelectedFrame() {
    updateFrameSelector();
    if (!latestEvent || !latestEvent.has_image) return;
    const frame = byId("latest-photo-frame");
    const image = byId("latest-photo");
    if (!frame || !image) return;
    const eventId = String(latestEvent.id);
    const imageVersion = String(latestEvent.image_version || latestEvent.id);
    const imageUrl = latestEvent.frame_urls?.[frameKind] || latestEvent.image_url;
    const displayKey = `${eventId}:${frameKind}:${imageVersion}`;
    if (frame.dataset.displayKey !== displayKey && imageUrl) {
      frame.dataset.displayKey = displayKey;
      image.src = `${imageUrl}?v=${encodeURIComponent(imageVersion)}`;
    }
    image.alt = frameKind === "raw"
      ? `Raw vehicle frame for plate ${latestEvent.plate_number}`
      : `Annotated vehicle frame for plate ${latestEvent.plate_number}`;
  }

  function updateLatest(event, timing) {
    const frame = byId("latest-photo-frame");
    const image = byId("latest-photo");
    const details = byId("latest-photo-details");
    const placeholder = byId("latest-photo-placeholder");
    const decision = byId("latest-decision");
    const accessLed = byId("latest-access-led");
    const timingLabel = byId("latest-timing");
    const placeholderTitle = byId("latest-placeholder-title");
    const placeholderMessage = byId("latest-placeholder-message");
    const viewer = byId("latest-viewer-wrap");
    if (!frame || !image || !details || !placeholder || !decision) return;

    if (!event) {
      latestEvent = null;
      frame.hidden = true;
      if (viewer) viewer.hidden = true;
      details.hidden = true;
      decision.hidden = true;
      placeholder.hidden = false;
      if (placeholderTitle) placeholderTitle.textContent = "No access event yet";
      if (placeholderTitle) placeholderTitle.className = "";
      if (placeholderMessage) placeholderMessage.textContent = "The first plate or RFID access event will appear here automatically.";
      if (timingLabel) timingLabel.hidden = true;
      if (accessLed) {
        accessLed.className = "access-led neutral";
        accessLed.setAttribute("aria-label", "No access result yet");
        accessLed.title = "No access result yet";
      }
      return;
    }

    latestEvent = event;
    showSelectedFrame();
    text("latest-plate", event.plate_number);
    text("latest-rfid", `RFID ${event.rfid_number || "not read"}`);
    text("latest-owner", event.owner_name || "Unregistered vehicle");
    text("latest-time", event.local_time);
    if (timingLabel) {
      timingLabel.hidden = !timing;
      if (timing) {
        timingLabel.textContent = `${formatDuration(timing.total_ms)} total`;
        timingLabel.title = `Frames ${formatDuration(timing.frames_ms)} · YOLO ${formatDuration(timing.yolo_ms)} · OCR ${formatDuration(timing.ocr_ms)} · Upload ${formatDuration(timing.server_ms)}`;
      }
    }
    decision.className = `status ${String(event.decision || "").toLowerCase()}`;
    decision.textContent = event.decision;
    decision.hidden = false;
    if (accessLed) {
      const ledState = event.decision === "authorized" ? "authorized" : "denied";
      accessLed.className = `access-led ${ledState}`;
      accessLed.setAttribute("aria-label", `Latest access ${event.decision}`);
      accessLed.title = `Latest access: ${event.decision}`;
    }
    const hasImage = Boolean(event.has_image);
    if (viewer) viewer.hidden = !hasImage;
    frame.hidden = !hasImage;
    details.hidden = false;
    placeholder.hidden = hasImage;
    if (!hasImage) {
      const authorized = event.decision === "authorized";
      if (placeholderTitle) {
        placeholderTitle.textContent = authorized ? event.plate_number : "ACCESS DENIED";
        placeholderTitle.className = authorized ? "rfid-plate-display" : "rfid-denied-display";
      }
      if (placeholderMessage) {
        placeholderMessage.textContent = authorized
          ? `Authorized RFID · ${event.owner_name || "Registered vehicle"}`
          : `RFID ${event.rfid_number || "unknown"} was denied`;
      }
    }
    updateFrameSelector();
  }

  function updateCaptureButton(system) {
    const button = byId("camera-capture-button");
    if (!button) return;
    const busy = ["queued", "active"].includes(system.detector_state);
    const unavailable = system.controller_type === "rfid" || !system.controller_online;
    button.disabled = busy || unavailable;
    button.textContent = busy ? "Capturing…" : (unavailable ? "Camera unavailable" : "Capture plate");
  }

  function updateRecent(events) {
    const body = byId("recent-events-body");
    if (!body) return;
    body.replaceChildren();

    if (!events.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      const empty = document.createElement("div");
      const heading = document.createElement("strong");
      const note = document.createElement("span");
      cell.colSpan = 5;
      empty.className = "empty-state";
      heading.textContent = "No access events yet";
      note.textContent = "Recognition events will appear here automatically.";
      empty.append(heading, note);
      cell.append(empty);
      row.append(cell);
      body.append(row);
      return;
    }

    events.forEach((event) => {
      const row = document.createElement("tr");
      const timeCell = document.createElement("td");
      const plateCell = document.createElement("td");
      const ownerCell = document.createElement("td");
      const vehicleCell = document.createElement("td");
      const decisionCell = document.createElement("td");
      const plate = document.createElement("span");
      timeCell.className = "nowrap";
      timeCell.textContent = event.local_time;
      plate.className = "plate";
      plate.textContent = event.plate_number;
      const rfid = document.createElement("small");
      rfid.className = "cell-note";
      rfid.textContent = `RFID ${event.rfid_number || "—"}`;
      plateCell.append(plate, rfid);
      ownerCell.textContent = event.owner_name || "Unknown vehicle";
      vehicleCell.textContent = event.vehicle || "—";
      decisionCell.append(statusBadge(event.decision));
      row.className = `decision-row ${String(event.decision || "").toLowerCase()}`;
      row.append(timeCell, plateCell, ownerCell, vehicleCell, decisionCell);
      body.append(row);
    });
  }

  function updateDaily(days) {
    const grid = byId("daily-activity");
    if (!grid) return;
    grid.replaceChildren();
    if (!days.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "Daily summaries will appear after the first recognition event.";
      grid.append(empty);
      return;
    }
    days.forEach((day) => {
      const card = document.createElement("div");
      const date = document.createElement("strong");
      const total = document.createElement("span");
      const detail = document.createElement("small");
      const bar = document.createElement("span");
      card.className = "day-card";
      bar.className = "day-bar";
      bar.style.setProperty("--day-total", String(day.total_events || 1));
      card.title = `${day.total_events} total · ${day.authorized_count} authorized · ${day.denied_count} denied`;
      date.textContent = day.event_date;
      total.textContent = day.total_events;
      detail.textContent = "";
      card.append(bar, date, total, detail);
      grid.append(card);
    });
  }

  function render(data) {
    text("metric-active-vehicles", data.summary.active_vehicles);
    text("metric-events-today", data.summary.events_today);
    text("metric-authorized-today", data.summary.authorized_today);
    text("metric-denied-today", data.summary.denied_today);
    const online = Boolean(data.system.controller_online);
    const moving = ["opening", "closing", "fault"].includes(data.system.gate_state);
    signal("controller-lamp", "controller-state", online ? "green" : "red", online ? "Connected" : "Offline");
    signal("camera-lamp", "camera-state", online && data.system.camera_running ? "green" : "red", online && data.system.camera_running ? "Detected" : "Unavailable");
    signal("loop-lamp", "loop-state", online ? (data.system.loop_active ? "green" : "red") : "off", online ? (data.system.loop_active ? "Vehicle present" : "Clear") : "Unknown");
    signal("ir-lamp", "ir-state", online ? (data.system.ir_blocked ? "red" : "green") : "off", online ? (data.system.ir_blocked ? "Blocked" : "Clear") : "Unknown");
    signal("barrier-lamp", "barrier-state", online ? (moving ? "amber" : (data.system.barrier_open ? "green" : "red")) : "off", online ? titleCase(String(data.system.gate_state || "unknown").replaceAll("_", " ")) : "Unknown");
    signal("traffic-lamp", "traffic-state", online ? (data.system.traffic_green ? "green" : "red") : "off", online ? (data.system.traffic_green ? "GO" : "STOP") : "Unknown");
    signal("plate-result-lamp", "plate-result-state", online ? (data.system.plate_unrecognized ? "red" : "green") : "off", online ? (data.system.plate_unrecognized ? "Not recognized" : "Ready") : "Unknown");
    text("hardware-updated", data.system.controller_seen_at || "Waiting for controller");
    updateCaptureButton(data.system);
    updateLatest(data.latest_event, data.latest_timing);
    updateRecent(data.recent_events);
    updateDaily(data.daily);
  }

  function schedule() {
    window.clearTimeout(timer);
    if (!document.hidden) timer = window.setTimeout(sync, POLL_INTERVAL_MS);
  }

  function setupCaptureForm() {
    const form = byId("camera-capture-form");
    const button = byId("camera-capture-button");
    if (!form || !button) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      button.textContent = "Capturing…";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
        });
        const result = await response.json();
        showNotification(result.message, result.success ? "success" : "error");
        await sync();
      } catch (error) {
        button.disabled = false;
        button.textContent = "Capture plate";
        showNotification("The capture request could not be sent.", "error");
        console.warn(error);
      }
    });
  }

  function setupFrameSelector() {
    document.querySelectorAll(".frame-option").forEach((button) => {
      button.addEventListener("click", () => {
        frameKind = button.dataset.frameKind === "raw" ? "raw" : "annotated";
        window.localStorage.setItem("plate-frame-kind", frameKind);
        showSelectedFrame();
      });
    });
    updateFrameSelector();
  }

  async function sync() {
    if (syncing || document.hidden) return schedule();
    syncing = true;
    try {
      const response = await fetch("/api/dashboard", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (response.redirected || response.status === 401) {
        window.location.assign(response.url || "/login");
        return;
      }
      if (!response.ok) throw new Error(`Dashboard sync failed: ${response.status}`);
      render(await response.json());
      text("sync-status", "Connected");
      byId("sync-status")?.classList.remove("error");
      document.body.dataset.lastSyncAt = String(Date.now());
    } catch (error) {
      text("sync-status", "Reconnecting…");
      byId("sync-status")?.classList.add("error");
      console.warn(error);
    } finally {
      syncing = false;
      schedule();
    }
  }

  document.addEventListener("visibilitychange", () => {
    window.clearTimeout(timer);
    if (!document.hidden) sync();
  });
  setupCaptureForm();
  setupFrameSelector();
  sync();
})();
