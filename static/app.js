const state = {
  dashboard: null,
  token: localStorage.getItem("sample_tracking_token") || "",
  user: null,
  role: null,
  selectedLotId: null,
  selectedInventoryLotId: null,
  selectedDispatchId: null,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { "X-Auth-Token": state.token } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(payload.error || `Request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function statusBadge(value) {
  const v = String(value || "").toLowerCase();
  const tone = v.includes("deliver") || v.includes("approved") || v === "true" || v.includes("submitted")
    ? "good"
    : v.includes("draft") || v.includes("review") || v.includes("transit") || v.includes("pending")
      ? "warn"
      : "bad";
  return `<span class="badge ${tone}">${value}</span>`;
}

function hasAccess(zone) {
  return Boolean(state.dashboard?.access?.[zone]);
}

function showLogin(message = "") {
  // Login is a separate page now.
  if (!location.pathname.endsWith("/login.html")) {
    location.replace("/login.html");
    return;
  }
  const err = $("#login-error");
  if (err) err.textContent = message;
}

function reportClientError(error) {
  const msg = (error && (error.message || String(error))) || "Unknown client error";
  const el = document.getElementById("login-error");
  if (el) el.textContent = msg;
}

window.addEventListener("error", (event) => {
  reportClientError(event.error || event.message);
});

window.addEventListener("unhandledrejection", (event) => {
  reportClientError(event.reason);
});

function showAppShell() {
  // App shell is `/` now.
  if (location.pathname.endsWith("/login.html")) {
    location.replace("/");
    return;
  }
}

function renderSessionBadge() {
  const badge = $("#session-badge");
  if (!badge) return;
  badge.textContent = `${state.user?.name || ""} · ${(state.role || "").toUpperCase()}`;
}

function renderTable(container, columns, rows, options = {}) {
  if (!rows.length) {
    container.innerHTML = `<div class="table-wrap"><div class="empty-state">${options.empty || "No rows found."}</div></div>`;
    return;
  }

  const selectedId = options.selectedId;
  const body = rows.map((row) => {
    const cells = columns.map((column) => {
      const value = typeof column.render === "function" ? column.render(row) : row[column.key] ?? "";
      return `<td>${value}</td>`;
    }).join("");
    const rowId = row.id ?? row.dispatch_id;
    const selected = selectedId && rowId === selectedId ? "selected" : "";
    return `<tr class="${selected}" data-row-id="${rowId}">${cells}</tr>`;
  }).join("");

  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;

  if (options.onSelect) {
    container.querySelectorAll("tbody tr").forEach((rowEl) => {
      rowEl.addEventListener("click", () => options.onSelect(Number(rowEl.dataset.rowId)));
    });
  }
}

function bindModalButtons() {
  document.querySelectorAll("[data-open-modal]").forEach((button) => {
    button.addEventListener("click", () => $(`#${button.dataset.openModal}`).showModal());
  });
  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
}

function populateMetrics(metrics) {
  const cards = [
    metrics.totalLots != null ? `<div class="metric"><strong>${metrics.totalLots}</strong><span>Total lots</span></div>` : "",
    metrics.openLots != null ? `<div class="metric"><strong>${metrics.openLots}</strong><span>Open inventory lots</span></div>` : "",
    metrics.deliveredShipments != null ? `<div class="metric"><strong>${metrics.deliveredShipments}</strong><span>Delivered shipments</span></div>` : "",
    metrics.feedbackPending != null ? `<div class="metric"><strong>${metrics.feedbackPending}</strong><span>Feedback pending</span></div>` : "",
  ].filter(Boolean).join("");
  $("#metrics").innerHTML = `<div class="metric-grid">${cards}</div>`;
}

function applyAccessUI() {
  const access = state.dashboard.access;
  $("#panel-quality").hidden = !access.quality;
  $("#panel-logistics").hidden = !access.logistics;
  $("#panel-marketing").hidden = !access.marketing;
  $("#access-banner").textContent = `${state.user.name} signed in with ${state.role} access`;
  $("#access-banner").className = `access-banner role-${state.role}`;
  $("#analysis-trigger").disabled = !hasAccess("quality") || !state.selectedLotId;
  $("#dispatch-trigger").disabled = !hasAccess("logistics") || !state.selectedInventoryLotId;
  $("#feedback-trigger").disabled = !hasAccess("marketing") || !state.selectedDispatchId;
}

async function loadDashboard() {
  state.dashboard = await api("/api/dashboard");
  state.user = state.dashboard.user;
  state.role = state.dashboard.role;
  renderSessionBadge();
  showAppShell();
  applyAccessUI();
  populateMetrics(state.dashboard.metrics);

  if (hasAccess("quality")) {
    renderTable($("#lots-table"), [
      { label: "Lot", key: "lot_number" },
      { label: "Product", key: "product_name" },
      { label: "Qty", render: (r) => `${r.initial_quantity} ${r.unit_measure}` },
      { label: "Status", render: (r) => statusBadge(r.status) },
      { label: "Analyses", key: "analysis_count" },
    ], state.dashboard.lots, {
      selectedId: state.selectedLotId,
      onSelect: selectLot,
    });
  } else {
    state.selectedLotId = null;
  }

  if (hasAccess("logistics")) {
    renderTable($("#inventory-table"), [
      { label: "Lot", key: "lot_number" },
      { label: "Product", key: "product_name" },
      { label: "Status", render: (r) => statusBadge(r.status) },
      { label: "Project", key: "npd_project_ref" },
      { label: "Created", render: (r) => new Date(r.created_at).toLocaleDateString() },
    ], state.dashboard.inventory, {
      selectedId: state.selectedInventoryLotId,
      onSelect: selectInventoryLot,
    });
  } else {
    state.selectedInventoryLotId = null;
  }

  if (hasAccess("marketing")) {
    renderTable($("#marketing-table"), [
      { label: "Dispatch", key: "dispatch_id" },
      { label: "Lot", key: "lot_number" },
      { label: "Product", key: "product_name" },
      { label: "Customer", key: "customer_name" },
      { label: "Feedback", render: (r) => r.feedback_id ? statusBadge("Submitted") : statusBadge("Pending") },
    ], state.dashboard.marketing, {
      selectedId: state.selectedDispatchId,
      onSelect: selectDispatch,
    });
  } else {
    state.selectedDispatchId = null;
  }

  if (hasAccess("quality") && state.selectedLotId) {
    await loadAnalyses(state.selectedLotId);
  } else {
    $("#analyses-table").innerHTML = `<div class="table-wrap"><div class="empty-state">Select a lot to inspect its test records.</div></div>`;
  }

  if (hasAccess("logistics") && state.selectedInventoryLotId) {
    await loadDispatches(state.selectedInventoryLotId);
  } else {
    $("#dispatches-table").innerHTML = `<div class="table-wrap"><div class="empty-state">Select a lot in inventory to manage dispatches.</div></div>`;
  }

  if (hasAccess("marketing") && state.selectedDispatchId) {
    await loadFeedback(state.selectedDispatchId);
  } else {
    $("#feedback-detail").innerHTML = `<div class="empty-state">Select a delivered shipment to review or log feedback.</div>`;
  }

  applyAccessUI();
}

async function selectLot(id) {
  state.selectedLotId = id;
  $("#analysis-trigger").disabled = false;
  await loadDashboard();
}

async function selectInventoryLot(id) {
  state.selectedInventoryLotId = id;
  $("#dispatch-trigger").disabled = false;
  await loadDashboard();
}

async function selectDispatch(id) {
  state.selectedDispatchId = id;
  $("#feedback-trigger").disabled = false;
  await loadDashboard();
}

async function loadAnalyses(lotId) {
  const row = state.dashboard.lots.find((item) => item.id === lotId);
  $("#analysis-context").textContent = row ? `Analyses for ${row.lot_number} · ${row.product_name}` : "Select a lot to inspect its test records.";
  const analyses = await api(`/api/analyses?lot_id=${lotId}`);
  renderTable($("#analyses-table"), [
    { label: "Date", render: (r) => new Date(r.test_date).toLocaleDateString() },
    { label: "Test", key: "test_type" },
    { label: "Spec", key: "spec_value" },
    { label: "Result", key: "result_value" },
    { label: "Pass", render: (r) => statusBadge(r.is_pass ? "Pass" : "Fail") },
    { label: "Analyst", key: "analyst_name" },
  ], analyses, { empty: "No analyses logged yet." });
}

async function loadDispatches(lotId) {
  const row = state.dashboard.inventory.find((item) => item.id === lotId);
  $("#dispatch-context").textContent = row ? `Shipments for ${row.lot_number} · ${row.product_name}` : "Select a lot in inventory to manage dispatches.";
  const dispatches = await api(`/api/dispatches?lot_id=${lotId}`);
  const container = $("#dispatches-table");
  if (!dispatches.length) {
    container.innerHTML = `<div class="table-wrap"><div class="empty-state">No shipments logged yet.</div></div>`;
    return;
  }
  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Dispatch</th><th>Customer</th><th>Qty</th><th>Courier</th><th>AWB</th><th>Date</th><th>Status</th></tr>
        </thead>
        <tbody>
          ${dispatches.map((row) => `
            <tr>
              <td>${row.id}</td>
              <td>${row.customer_name}</td>
              <td>${row.quantity_sent}</td>
              <td>${row.courier_name}</td>
              <td>${row.awb_number}</td>
              <td>${new Date(row.dispatch_date).toLocaleDateString()}</td>
              <td>
                <select class="inline-select" data-dispatch-id="${row.id}">
                  ${["Dispatched", "In-Transit", "Delivered"].map((status) => `<option ${status === row.delivery_status ? "selected" : ""}>${status}</option>`).join("")}
                </select>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  container.querySelectorAll(".inline-select").forEach((select) => {
    select.addEventListener("change", async () => {
      await api("/api/dispatch-status", {
        method: "PATCH",
        body: JSON.stringify({
          dispatch_id: Number(select.dataset.dispatchId),
          delivery_status: select.value,
        }),
      });
      await loadDashboard();
    });
  });
}

async function loadFeedback(dispatchId) {
  const row = state.dashboard.marketing.find((item) => item.dispatch_id === dispatchId);
  $("#feedback-context").textContent = row ? `Feedback for dispatch ${row.dispatch_id} · ${row.lot_number}` : "Select a delivered shipment to review or log feedback.";
  const feedback = await api(`/api/feedback?dispatch_id=${dispatchId}`);
  $("#feedback-detail").innerHTML = feedback.id ? `
    <div class="feedback-card">
      <p><strong>Rating:</strong> ${feedback.rating}/5</p>
      <p><strong>Marketing:</strong> ${feedback.marketing_person}</p>
      <p><strong>Action Required:</strong> ${feedback.action_required ? "Yes" : "No"}</p>
      <p><strong>Technical Notes:</strong><br>${feedback.technical_notes}</p>
      <p><strong>Next Steps:</strong><br>${feedback.next_steps || "None recorded"}</p>
    </div>
  ` : `<div class="empty-state">No feedback recorded for this delivered shipment.</div>`;
}

function formDataToObject(form) {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

function bindForms() {
  const loginForm = $("#login-form");
  if (loginForm) {
    document.querySelectorAll(".demo-fill").forEach((button) => {
      button.addEventListener("click", () => {
        loginForm.elements.username.value = button.dataset.username;
        loginForm.elements.password.value = button.dataset.password;
        const err = $("#login-error");
        if (err) err.textContent = "";
      });
    });

    loginForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const err = $("#login-error");
      if (err) err.textContent = "";
      const submitButton = $("#login-button");
      const originalLabel = submitButton?.textContent || "Sign In";
      if (!submitButton) return;
      submitButton.disabled = true;
      submitButton.textContent = "Signing in...";
      const payload = formDataToObject(event.target);
      try {
        const result = await api("/api/login", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        state.token = result.token;
        localStorage.setItem("sample_tracking_token", state.token);
        location.assign("/");
      } catch (error) {
        if (err) err.textContent = error.message;
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = originalLabel;
      }
    });
  }

  const lotForm = $("#lot-form");
  if (lotForm) lotForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/api/lots", { method: "POST", body: JSON.stringify(formDataToObject(event.target)) });
    event.target.reset();
    $("#lot-modal").close();
    await loadDashboard();
  });

  const analysisForm = $("#analysis-form");
  if (analysisForm) analysisForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formDataToObject(event.target);
    payload.lot_id = state.selectedLotId;
    payload.is_pass = event.target.elements.is_pass.checked;
    await api("/api/analyses", { method: "POST", body: JSON.stringify(payload) });
    event.target.reset();
    event.target.elements.test_date.value = new Date().toISOString().slice(0, 10);
    $("#analysis-modal").close();
    await loadDashboard();
  });

  const dispatchForm = $("#dispatch-form");
  if (dispatchForm) dispatchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formDataToObject(event.target);
    payload.lot_id = state.selectedInventoryLotId;
    await api("/api/dispatches", { method: "POST", body: JSON.stringify(payload) });
    event.target.reset();
    event.target.elements.dispatch_date.value = new Date().toISOString().slice(0, 10);
    $("#dispatch-modal").close();
    await loadDashboard();
  });

  const feedbackForm = $("#feedback-form");
  if (feedbackForm) feedbackForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formDataToObject(event.target);
    payload.dispatch_id = state.selectedDispatchId;
    payload.action_required = event.target.elements.action_required.checked;
    await api("/api/feedback", { method: "POST", body: JSON.stringify(payload) });
    event.target.reset();
    $("#feedback-modal").close();
    await loadDashboard();
  });

  const logoutButton = $("#logout-button");
  if (logoutButton) logoutButton.addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST", body: JSON.stringify({}) });
    } catch (_) {
      // Ignore logout failures during local token cleanup.
    }
    state.token = "";
    state.user = null;
    state.role = null;
    state.dashboard = null;
    state.selectedLotId = null;
    state.selectedInventoryLotId = null;
    state.selectedDispatchId = null;
    localStorage.removeItem("sample_tracking_token");
    location.assign("/login.html");
  });
}

function setDefaultDates() {
  const today = new Date().toISOString().slice(0, 10);
  const analysisForm = $("#analysis-form");
  if (analysisForm?.elements?.test_date) analysisForm.elements.test_date.value = today;
  const dispatchForm = $("#dispatch-form");
  if (dispatchForm?.elements?.dispatch_date) dispatchForm.elements.dispatch_date.value = today;
}

async function restoreSession() {
  if (!state.token) return showLogin("");
  try {
    await loadDashboard();
  } catch (error) {
    localStorage.removeItem("sample_tracking_token");
    state.token = "";
    showLogin("Session expired. Sign in again.");
  }
}

async function init() {
  bindModalButtons();
  bindForms();
  // Keep `/login.html` isolated: no dashboard loads, no page redirects that can
  // continue running against the login DOM and surface scary JS errors.
  if (location.pathname.endsWith("/login.html")) {
    if (state.token) location.replace("/");
    return;
  }

  setDefaultDates();
  await restoreSession();
}

init().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML("afterbegin", `<div class="empty-state" style="padding:16px">${error.message}</div>`);
});
