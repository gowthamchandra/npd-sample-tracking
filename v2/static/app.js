(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = {
    token: localStorage.getItem("st_v2_token") || "",
    me: null,
    dashboard: null,
    selectedLotId: null,
    selectedInventoryLotId: null,
    selectedDispatchId: null,
  };

  function showError(msg) {
    const el = $("#login-error");
    if (el) el.textContent = msg || "";
  }

  window.addEventListener("error", (e) => showError(e?.error?.message || e?.message || "Client error"));
  window.addEventListener("unhandledrejection", (e) => showError(e?.reason?.message || String(e?.reason || "Promise error")));

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        ...(state.token ? { "X-Auth-Token": state.token } : {}),
        ...(opts.headers || {}),
      },
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
    if (!res.ok) {
      const err = new Error(data.error || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function statusBadge(value) {
    const v = String(value || "").toLowerCase();
    const tone =
      v.includes("deliver") || v.includes("approved") || v === "true" || v.includes("submitted")
        ? "good"
        : v.includes("draft") || v.includes("review") || v.includes("transit") || v.includes("pending")
          ? "warn"
          : "bad";
    return `<span class="badge ${tone}">${value}</span>`;
  }

  function tableWrap(html) {
    return `<div class="table">${html}</div>`;
  }

  function renderTable(container, cols, rows, { selectedId, onSelect, empty } = {}) {
    if (!container) return;
    if (!rows?.length) {
      container.innerHTML = tableWrap(`<div class="hint" style="padding:14px">${empty || "No rows found."}</div>`);
      return;
    }
    const thead = `<thead><tr>${cols.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead>`;
    const tbody = `<tbody>${rows.map((r) => {
      const rowId = r.id ?? r.dispatch_id;
      const selected = selectedId && rowId === selectedId ? "selected" : "";
      return `<tr class="${selected}" data-row-id="${rowId}">${cols.map((c) => `<td>${c.render ? c.render(r) : (r[c.key] ?? "")}</td>`).join("")}</tr>`;
    }).join("")}</tbody>`;
    container.innerHTML = tableWrap(`<table>${thead}${tbody}</table>`);
    if (onSelect) {
      container.querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => onSelect(Number(tr.dataset.rowId))));
    }
  }

  function setStats(metrics) {
    const el = $("#stats");
    if (!el) return;
    const cards = [
      metrics.totalLots != null ? ["Total lots", metrics.totalLots] : null,
      metrics.openLots != null ? ["Open lots", metrics.openLots] : null,
      metrics.deliveredShipments != null ? ["Delivered shipments", metrics.deliveredShipments] : null,
      metrics.feedbackPending != null ? ["Feedback pending", metrics.feedbackPending] : null,
    ].filter(Boolean);
    el.innerHTML = cards.map(([label, value]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join("");
  }

  function setSessionText() {
    const el = $("#session");
    if (!el) return;
    if (!state.me) { el.textContent = ""; return; }
    el.textContent = `${state.me.user.name} · ${state.me.role.toUpperCase()}`;
  }

  function hasAccess(zone) {
    return Boolean(state.dashboard?.access?.[zone]);
  }

  function showZones() {
    $("#login-card").hidden = true;
    $("#zones").hidden = false;
    $("#zone-quality").hidden = !hasAccess("quality");
    $("#zone-logistics").hidden = !hasAccess("logistics");
    $("#zone-marketing").hidden = !hasAccess("marketing");
  }

  function showLogin() {
    // On v2 we have separate pages. Login is `/`, app is `/app.html`.
    // If this file is loaded on `/app.html` without a session, redirect.
    if (location.pathname.endsWith("/app.html")) {
      location.replace("/");
      return;
    }
    $("#login-card")?.removeAttribute("hidden");
  }

  async function loadDashboard() {
    if (!location.pathname.endsWith("/app.html")) return;
    state.dashboard = await api("/api/dashboard");
    setStats(state.dashboard.metrics);
    showZones();

    if (hasAccess("quality")) {
      renderTable($("#lots-table"), [
        { label: "Lot", key: "lot_number" },
        { label: "Product", key: "product_name" },
        { label: "Qty", render: (r) => `${r.initial_quantity} ${r.unit_measure}` },
        { label: "Status", render: (r) => statusBadge(r.status) },
        { label: "Analyses", key: "analysis_count" },
      ], state.dashboard.lots, { selectedId: state.selectedLotId, onSelect: selectLot });
    }

    if (hasAccess("logistics")) {
      renderTable($("#inventory-table"), [
        { label: "Lot", key: "lot_number" },
        { label: "Product", key: "product_name" },
        { label: "Status", render: (r) => statusBadge(r.status) },
        { label: "Project", key: "npd_project_ref" },
        { label: "Created", render: (r) => new Date(r.created_at).toLocaleDateString() },
      ], state.dashboard.inventory, { selectedId: state.selectedInventoryLotId, onSelect: selectInventoryLot });
    }

    if (hasAccess("marketing")) {
      renderTable($("#marketing-table"), [
        { label: "Dispatch", key: "dispatch_id" },
        { label: "Lot", key: "lot_number" },
        { label: "Product", key: "product_name" },
        { label: "Customer", key: "customer_name" },
        { label: "Feedback", render: (r) => r.feedback_id ? statusBadge("Submitted") : statusBadge("Pending") },
      ], state.dashboard.marketing, { selectedId: state.selectedDispatchId, onSelect: selectDispatch });
    }

    $("#analysis-btn").disabled = !hasAccess("quality") || !state.selectedLotId;
    $("#dispatch-btn").disabled = !hasAccess("logistics") || !state.selectedInventoryLotId;
    $("#feedback-btn").disabled = !hasAccess("marketing") || !state.selectedDispatchId;

    if (hasAccess("quality") && state.selectedLotId) await loadAnalyses(state.selectedLotId);
    if (hasAccess("logistics") && state.selectedInventoryLotId) await loadDispatches(state.selectedInventoryLotId);
    if (hasAccess("marketing") && state.selectedDispatchId) await loadFeedback(state.selectedDispatchId);
  }

  async function loadAnalyses(lotId) {
    const lot = state.dashboard.lots.find((l) => l.id === lotId);
    $("#analysis-hint").textContent = lot ? `Analyses for ${lot.lot_number} · ${lot.product_name}` : "Select a lot to view analyses.";
    const analyses = await api(`/api/analyses?lot_id=${lotId}`);
    renderTable($("#analyses-table"), [
      { label: "Date", render: (r) => new Date(r.test_date).toLocaleDateString() },
      { label: "Test", key: "test_type" },
      { label: "Spec", key: "spec_value" },
      { label: "Result", key: "result_value" },
      { label: "Pass", render: (r) => statusBadge(r.is_pass ? "Pass" : "Fail") },
      { label: "Analyst", key: "analyst_name" },
    ], analyses, { empty: "No analyses yet." });
  }

  async function loadDispatches(lotId) {
    const lot = state.dashboard.inventory.find((l) => l.id === lotId);
    $("#dispatch-hint").textContent = lot ? `Shipments for ${lot.lot_number} · ${lot.product_name}` : "Select a lot to view shipments.";
    const rows = await api(`/api/dispatches?lot_id=${lotId}`);
    const container = $("#dispatches-table");
    if (!rows.length) {
      container.innerHTML = tableWrap(`<div class="hint" style="padding:14px">No shipments yet.</div>`);
      return;
    }
    container.innerHTML = tableWrap(`
      <table>
        <thead><tr><th>ID</th><th>Customer</th><th>Qty</th><th>Courier</th><th>AWB</th><th>Date</th><th>Status</th></tr></thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${r.id}</td>
              <td>${r.customer_name}</td>
              <td>${r.quantity_sent}</td>
              <td>${r.courier_name}</td>
              <td>${r.awb_number}</td>
              <td>${new Date(r.dispatch_date).toLocaleDateString()}</td>
              <td>
                <select class="inline-select" data-dispatch-id="${r.id}">
                  ${["Dispatched","In-Transit","Delivered"].map((s) => `<option ${s===r.delivery_status?"selected":""}>${s}</option>`).join("")}
                </select>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `);
    container.querySelectorAll(".inline-select").forEach((sel) => {
      sel.addEventListener("change", async () => {
        await api("/api/dispatch-status", { method: "PATCH", body: JSON.stringify({ dispatch_id: Number(sel.dataset.dispatchId), delivery_status: sel.value }) });
        await loadDashboard();
      });
    });
  }

  async function loadFeedback(dispatchId) {
    const row = state.dashboard.marketing.find((d) => d.dispatch_id === dispatchId);
    $("#feedback-hint").textContent = row ? `Feedback for dispatch ${dispatchId} · ${row.lot_number}` : "Select a delivered shipment.";
    const fb = await api(`/api/feedback?dispatch_id=${dispatchId}`);
    $("#feedback-detail").innerHTML = fb.id ? `
      <div class="hint">
        <div style="margin-bottom:10px"><strong style="color:#f5e5bc">Rating</strong> ${fb.rating}/5</div>
        <div style="margin-bottom:10px"><strong style="color:#f5e5bc">Marketing</strong> ${fb.marketing_person}</div>
        <div style="margin-bottom:10px"><strong style="color:#f5e5bc">Action Required</strong> ${fb.action_required ? "Yes" : "No"}</div>
        <div style="margin-bottom:10px"><strong style="color:#f5e5bc">Notes</strong><br/>${fb.technical_notes}</div>
        <div><strong style="color:#f5e5bc">Next Steps</strong><br/>${fb.next_steps || "None"}</div>
      </div>
    ` : `<div class="hint">No feedback yet for this dispatch.</div>`;
  }

  function selectLot(id) {
    state.selectedLotId = id;
    $("#analysis-btn").disabled = false;
    loadDashboard().catch(showError);
  }

  function selectInventoryLot(id) {
    state.selectedInventoryLotId = id;
    $("#dispatch-btn").disabled = false;
    loadDashboard().catch(showError);
  }

  function selectDispatch(id) {
    state.selectedDispatchId = id;
    $("#feedback-btn").disabled = false;
    loadDashboard().catch(showError);
  }

  function formObject(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  function bindDialogs() {
    document.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const dlg = document.getElementById(btn.dataset.open);
        if (dlg) dlg.showModal();
      });
    });
  }

  function setDefaultDates() {
    const today = new Date().toISOString().slice(0, 10);
    const af = $("#analysis-form");
    const df = $("#dispatch-form");
    if (af) af.elements.test_date.value = today;
    if (df) df.elements.dispatch_date.value = today;
  }

  function bindForms() {
    $("#fill-admin")?.addEventListener("click", () => {
      $("#login-form").elements.username.value = "admin";
      $("#login-form").elements.password.value = "Admin@123";
      showError("");
    });

    $("#login-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      showError("");
      const btn = $("#login-btn");
      btn.disabled = true;
      btn.textContent = "Signing In...";
      try {
        const payload = formObject(e.target);
        const res = await api("/api/login", { method: "POST", body: JSON.stringify(payload) });
        state.token = res.token;
        localStorage.setItem("st_v2_token", state.token);
        state.me = { user: res.user, role: res.role, access: res.access };
        setSessionText();
        location.assign("/app.html");
      } catch (err) {
        showError(err.message);
        localStorage.removeItem("st_v2_token");
        state.token = "";
      } finally {
        btn.disabled = false;
        btn.textContent = "Sign In";
      }
    });

    $("#logout")?.addEventListener("click", async () => {
      try { await api("/api/logout", { method: "POST", body: JSON.stringify({}) }); } catch {}
      localStorage.removeItem("st_v2_token");
      state.token = "";
      location.assign("/");
    });

    $("#lot-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = formObject(e.target);
      await api("/api/lots", { method: "POST", body: JSON.stringify(payload) });
      e.target.reset();
      document.getElementById("lot-modal")?.close();
      await loadDashboard();
    });

    $("#analysis-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = formObject(e.target);
      payload.lot_id = state.selectedLotId;
      payload.is_pass = e.target.elements.is_pass.checked;
      await api("/api/analyses", { method: "POST", body: JSON.stringify(payload) });
      e.target.reset();
      setDefaultDates();
      document.getElementById("analysis-modal")?.close();
      await loadDashboard();
    });

    $("#dispatch-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = formObject(e.target);
      payload.lot_id = state.selectedInventoryLotId;
      await api("/api/dispatches", { method: "POST", body: JSON.stringify(payload) });
      e.target.reset();
      setDefaultDates();
      document.getElementById("dispatch-modal")?.close();
      await loadDashboard();
    });

    $("#feedback-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = formObject(e.target);
      payload.dispatch_id = state.selectedDispatchId;
      payload.action_required = e.target.elements.action_required.checked;
      await api("/api/feedback", { method: "POST", body: JSON.stringify(payload) });
      e.target.reset();
      document.getElementById("feedback-modal")?.close();
      await loadDashboard();
    });
  }

  async function restore() {
    if (!state.token) return showLogin();
    try {
      state.me = await api("/api/me");
      setSessionText();
      if (location.pathname.endsWith("/app.html")) {
        await loadDashboard();
      } else {
        location.assign("/app.html");
      }
    } catch (e) {
      localStorage.removeItem("st_v2_token");
      state.token = "";
      state.me = null;
      setSessionText();
      showLogin();
      showError("Session expired. Please sign in again.");
    }
  }

  function boot() {
    bindDialogs();
    bindForms();
    setDefaultDates();
    restore().catch(showError);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
