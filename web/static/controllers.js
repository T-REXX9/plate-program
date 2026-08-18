(() => {
  "use strict";

  const switchers = () => Array.from(document.querySelectorAll(".controller-switcher"));
  const labelFor = (controller) =>
    `${controller.controller_online ? "●" : "○"} ${controller.display_name}`;
  const typeLabel = (controller) =>
    controller.controller_type === "rfid" ? "RFID only" : "Plate + RFID";

  async function post(path, values, switcher) {
    const body = new URLSearchParams(values);
    body.set("csrf_token", switcher.dataset.csrfToken || "");
    const response = await fetch(path, {
      method: "POST",
      body,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || `Request failed (${response.status})`);
    }
    return payload;
  }

  function render(payload) {
    const selected = payload.controllers.find(
      (controller) => controller.controller_id === payload.selected_controller_id,
    );
    switchers().forEach((switcher) => {
      const select = switcher.querySelector(".controller-select");
      const dot = switcher.querySelector(".controller-live-dot");
      const meta = switcher.querySelector(".controller-meta");
      const rename = switcher.querySelector(".controller-rename");
      if (select) {
        const previous = select.value;
        select.replaceChildren();
        payload.controllers.forEach((controller) => {
          const option = new Option(labelFor(controller), controller.controller_id);
          option.selected = controller.controller_id === payload.selected_controller_id;
          select.add(option);
        });
        select.disabled = payload.controllers.length === 0;
        if (!payload.selected_controller_id && previous) select.value = previous;
      }
      if (dot) dot.className = `controller-live-dot ${selected?.controller_online ? "online" : "offline"}`;
      if (meta) {
        meta.textContent = selected
          ? `${typeLabel(selected)} · ${selected.controller_id}`
          : "Waiting for a controller";
      }
      if (rename) rename.disabled = !selected;
    });
  }

  async function refresh() {
    if (!switchers().length) return;
    try {
      const response = await fetch("/api/controllers", { headers: { Accept: "application/json" } });
      if (response.ok) render(await response.json());
    } catch (_) {
      // The selected controller remains usable from the server-rendered state.
    }
  }

  switchers().forEach((switcher) => {
    switcher.querySelector(".controller-select")?.addEventListener("change", async (event) => {
      const select = event.currentTarget;
      select.disabled = true;
      try {
        await post("/controllers/select", { controller_uid: select.value }, switcher);
        window.location.reload();
      } catch (error) {
        window.alert(error.message);
        select.disabled = false;
      }
    });
    switcher.querySelector(".controller-rename")?.addEventListener("click", async () => {
      const select = switcher.querySelector(".controller-select");
      const current = select?.selectedOptions[0]?.textContent.replace(/^[●○]\s*/, "") || "";
      const displayName = window.prompt("Name this controller:", current);
      if (displayName === null) return;
      try {
        await post(
          "/controllers/name",
          { controller_uid: select.value, display_name: displayName },
          switcher,
        );
        await refresh();
      } catch (error) {
        window.alert(error.message);
      }
    });
  });

  refresh();
  window.setInterval(refresh, 5000);
})();
