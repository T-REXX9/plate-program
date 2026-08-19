(() => {
  "use strict";

  const switchers = () => Array.from(document.querySelectorAll(".controller-switcher"));
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

  function closeMenu(switcher, restoreFocus = false) {
    const trigger = switcher.querySelector(".controller-trigger");
    const menu = switcher.querySelector(".controller-menu");
    if (!trigger || !menu) return;
    trigger.setAttribute("aria-expanded", "false");
    menu.hidden = true;
    if (restoreFocus) trigger.focus();
  }

  function closeOtherMenus(activeSwitcher) {
    switchers().forEach((switcher) => {
      if (switcher !== activeSwitcher) closeMenu(switcher);
    });
  }

  function toggleMenu(switcher) {
    const trigger = switcher.querySelector(".controller-trigger");
    const menu = switcher.querySelector(".controller-menu");
    if (!trigger || !menu || trigger.disabled) return;
    const opening = menu.hidden;
    closeOtherMenus(switcher);
    trigger.setAttribute("aria-expanded", String(opening));
    menu.hidden = !opening;
    if (opening) menu.querySelector('[role="option"][aria-selected="true"]')?.focus();
  }

  function buildOption(controller, selectedId) {
    const option = document.createElement("button");
    option.className = "controller-option";
    option.type = "button";
    option.setAttribute("role", "option");
    option.dataset.controllerUid = controller.controller_id;
    option.setAttribute("aria-selected", String(controller.controller_id === selectedId));

    const dot = document.createElement("span");
    dot.className = `controller-option-dot ${controller.controller_online ? "online" : "offline"}`;
    dot.setAttribute("aria-hidden", "true");

    const copy = document.createElement("span");
    copy.className = "controller-option-copy";
    const name = document.createElement("strong");
    name.textContent = controller.display_name;
    const meta = document.createElement("small");
    meta.textContent = `${typeLabel(controller)} · ${controller.controller_id}`;
    copy.append(name, meta);

    const state = document.createElement("span");
    state.className = "controller-option-state";
    state.textContent = controller.controller_online ? "Online" : "Offline";

    const check = document.createElement("span");
    check.className = "controller-check";
    check.setAttribute("aria-hidden", "true");
    check.textContent = "✓";
    option.append(dot, copy, state, check);
    return option;
  }

  function render(payload) {
    const selected = payload.controllers.find(
      (controller) => controller.controller_id === payload.selected_controller_id,
    );
    switchers().forEach((switcher) => {
      const select = switcher.querySelector(".controller-select");
      const trigger = switcher.querySelector(".controller-trigger");
      const dot = switcher.querySelector(".controller-live-dot");
      const name = switcher.querySelector(".controller-current-name");
      const meta = switcher.querySelector(".controller-meta");
      const options = switcher.querySelector(".controller-options");
      const rename = switcher.querySelector(".controller-rename");

      if (select) {
        select.replaceChildren();
        payload.controllers.forEach((controller) => {
          const option = new Option(controller.display_name, controller.controller_id);
          option.selected = controller.controller_id === payload.selected_controller_id;
          select.add(option);
        });
        select.disabled = payload.controllers.length === 0;
      }
      if (trigger) trigger.disabled = payload.controllers.length === 0;
      if (dot) dot.className = `controller-live-dot ${selected?.controller_online ? "online" : "offline"}`;
      if (name) name.textContent = selected?.display_name || "No controllers connected";
      if (meta) {
        meta.textContent = selected
          ? `${typeLabel(selected)} · ${selected.controller_id}`
          : "Waiting for a controller";
      }
      if (options) {
        options.replaceChildren();
        if (payload.controllers.length) {
          payload.controllers.forEach((controller) =>
            options.append(buildOption(controller, payload.selected_controller_id)),
          );
        } else {
          const empty = document.createElement("div");
          empty.className = "controller-empty";
          empty.textContent = "No controllers have checked in yet.";
          options.append(empty);
        }
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
      // Keep the last known state if a refresh is interrupted.
    }
  }

  async function selectController(switcher, controllerUid) {
    const trigger = switcher.querySelector(".controller-trigger");
    if (trigger) trigger.disabled = true;
    closeMenu(switcher);
    try {
      await post("/controllers/select", { controller_uid: controllerUid }, switcher);
      window.location.reload();
    } catch (error) {
      window.alert(error.message);
      if (trigger) trigger.disabled = false;
    }
  }

  switchers().forEach((switcher) => {
    switcher.querySelector(".controller-trigger")?.addEventListener("click", () => toggleMenu(switcher));

    switcher.querySelector(".controller-options")?.addEventListener("click", (event) => {
      const option = event.target.closest(".controller-option");
      if (option) selectController(switcher, option.dataset.controllerUid);
    });

    switcher.querySelector(".controller-menu")?.addEventListener("keydown", (event) => {
      const options = Array.from(switcher.querySelectorAll(".controller-option"));
      const index = options.indexOf(document.activeElement);
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu(switcher, true);
      } else if (event.key === "ArrowDown" && options.length) {
        event.preventDefault();
        options[(index + 1) % options.length].focus();
      } else if (event.key === "ArrowUp" && options.length) {
        event.preventDefault();
        options[(index - 1 + options.length) % options.length].focus();
      } else if (event.key === "Home" && options.length) {
        event.preventDefault();
        options[0].focus();
      } else if (event.key === "End" && options.length) {
        event.preventDefault();
        options.at(-1).focus();
      }
    });

    switcher.querySelector(".controller-rename")?.addEventListener("click", async () => {
      const select = switcher.querySelector(".controller-select");
      const current = select?.selectedOptions[0]?.textContent || "";
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

  document.addEventListener("click", (event) => {
    switchers().forEach((switcher) => {
      if (!switcher.contains(event.target)) closeMenu(switcher);
    });
  });

  refresh();
  window.setInterval(refresh, 5000);
})();
