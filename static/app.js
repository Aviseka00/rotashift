/* global FullCalendar */

const state = {
  token: localStorage.getItem("rs_token"),
  user: null,
  calendar: null,
  departments: [],
  shiftLegend: {},
  adminShortcutsWired: false,
};

const regState = {
  role: "employee",
  /** Assume enabled until /api/meta/registration responds (avoids race before fetch completes). */
  meta: {
    manager_registration_enabled: true,
    admin_registration_enabled: true,
  },
};

function $(id) {
  return document.getElementById(id);
}

/** Normalize request path so Bearer / 401 handling works with relative or absolute URLs. */
function apiPathname(path) {
  try {
    return new URL(path, window.location.origin).pathname;
  } catch {
    return String(path).split("?")[0] || path;
  }
}

function show(el, on) {
  if (!el) return;
  el.classList.toggle("hidden", !on);
}

async function api(path, opts = {}) {
  const pathname = apiPathname(path);
  const headers = { ...(opts.headers || {}) };
  if (!(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const method = String(opts.method || "GET").toUpperCase();
  const skipAuthHeader =
    pathname === "/api/auth/login" ||
    pathname === "/api/auth/register" ||
    (pathname === "/api/departments" && method === "GET") ||
    pathname.startsWith("/api/meta/");
  if (state.token && !skipAuthHeader) {
    headers.Authorization = `Bearer ${state.token}`;
  }
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) {
    const text401 = await r.text();
    let data401 = null;
    try {
      data401 = text401 ? JSON.parse(text401) : null;
    } catch {
      data401 = null;
    }
    const detail401 =
      data401 && typeof data401 === "object" && data401.detail !== undefined
        ? Array.isArray(data401.detail)
          ? data401.detail.map((x) => x.msg || JSON.stringify(x)).join("; ")
          : String(data401.detail)
        : null;
    // Legacy: login used 401; now 400. Still treat auth credential paths as "not a stale session".
    if (pathname === "/api/auth/login" || pathname === "/api/auth/register") {
      throw new Error(detail401 || "Invalid credentials");
    }
    logout();
    throw new Error(
      detail401 ||
        "Saved login no longer matches this server — sign in again. (New database or changed ROTASHIFT_SECRET_KEY invalidates the old token.)",
    );
  }
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    let msg = r.statusText || `HTTP ${r.status}`;
    if (data && typeof data === "object") {
      if (data.detail !== undefined) {
        const d = data.detail;
        msg = Array.isArray(d)
          ? d
              .map((x) =>
                typeof x === "string" ? x : x.msg || x.message || JSON.stringify(x),
              )
              .join("; ")
          : String(d);
      } else if (data.message) {
        msg = String(data.message);
      }
    }
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return data;
}

function logout() {
  state.token = null;
  state.user = null;
  localStorage.removeItem("rs_token");
  show($("auth-section"), true);
  show($("app-section"), false);
  show($("dashboard-banner"), false);
  show($("logout-btn"), false);
  $("user-slot").textContent = "";
  if (state.calendar) {
    state.calendar.destroy();
    state.calendar = null;
  }
  state.adminShortcutsWired = false;
}

function setToken(token, user) {
  state.token = token;
  state.user = user;
  localStorage.setItem("rs_token", token);
}

async function loadMeta() {
  const sh = await api("/api/meta/shifts");
  state.shiftLegend = { ...(sh.shifts || {}) };
  const fillNonTimed = {
    L: { label: "Leave", description: "Leave" },
    WO: { label: "Week off", description: "Week off" },
  };
  Object.entries(fillNonTimed).forEach(([k, v]) => {
    if (!state.shiftLegend[k]) state.shiftLegend[k] = v;
  });
  const leg = $("shift-legend");
  leg.innerHTML = "";
  Object.entries(state.shiftLegend).forEach(([code, info]) => {
    const span = document.createElement("span");
    span.className = "legend-item";
    const dot = document.createElement("span");
    dot.className = `dot ${code.toLowerCase()}`;
    const strong = document.createElement("strong");
    strong.textContent = code;
    const tail = document.createElement("span");
    if (info.start && info.end) {
      tail.textContent = ` ${info.start}–${info.end}`;
    } else {
      tail.textContent = ` ${info.description || info.label || ""}`;
    }
    span.appendChild(dot);
    span.appendChild(document.createTextNode(" "));
    span.appendChild(strong);
    span.appendChild(tail);
    leg.appendChild(span);
  });
  ["chg-from", "chg-to"].forEach((id) => {
    const sel = $(id);
    sel.innerHTML = "";
    Object.keys(state.shiftLegend).forEach((c) => {
      const inf = state.shiftLegend[c];
      if (!inf || !inf.start || !inf.end) return;
      const o = document.createElement("option");
      o.value = c;
      o.textContent = `${c} (${inf.start}–${inf.end})`;
      sel.appendChild(o);
    });
  });
  fillMgrAssignShiftSelect();
}

function shiftOptionLabel(code) {
  const c = String(code).toUpperCase();
  const inf = state.shiftLegend?.[c];
  if (inf?.start && inf?.end) return `${c} · ${inf.start}–${inf.end}`;
  if (inf?.description) return `${c} · ${inf.description}`;
  if (inf?.label) return `${c} · ${inf.label}`;
  return c;
}

function fillMgrAssignShiftSelect() {
  const sel = $("mgr-assign-shift");
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = "";
  matrixShiftCodes().forEach((code) => {
    const o = document.createElement("option");
    o.value = code;
    o.textContent = shiftOptionLabel(code);
    sel.appendChild(o);
  });
  if (cur && [...sel.options].some((x) => x.value === cur)) sel.value = cur;
}

async function loadDepartments() {
  const data = await api("/api/departments");
  state.departments = data.departments || [];
  const selects = [
    "reg-dept",
    "cal-dept",
    "table-dept",
    "bulk-dept",
    "mgr-roster-dept",
    "admin-add-dept",
    "admin-user-dept-filter",
    "admin-records-dept",
    "tasks-admin-dept",
    "infovalley-admin-dept",
    "infovally-admin-dept",
  ];
  selects.forEach((id) => {
    const sel = $(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent =
      id === "reg-dept"
        ? "Select department…"
        : id === "admin-user-dept-filter" || id === "admin-records-dept"
          ? "All departments"
          : "—";
    sel.appendChild(empty);
    state.departments.forEach((d) => {
      const o = document.createElement("option");
      if (id === "reg-dept") {
        o.value = (d.name || "").toLowerCase();
        o.textContent = d.name || o.value;
      } else {
        o.value = d.id;
        o.textContent = d.name;
      }
      sel.appendChild(o);
    });
    if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
  });
}

/**
 * @param {{ downgradeRole?: boolean }} [options]
 *   If `downgradeRole` is false, we do not change `regState.role` to match server policy.
 *   (When true, a user who had chosen Manager/Admin is moved to Team member if the server
 *   has disabled that path — used when opening Register, never right before Submit, or the
 *   chosen role could be wiped and registration would succeed as employee.)
 */
const FALLBACK_DEPT_NAMES = ["rota", "cholera", "malaria", "shigella"];

function ensureRegisterDeptOptionsFromMeta() {
  const sel = $("reg-dept");
  if (!sel) return;
  const fromApi = state.departments?.length > 0;
  const hasChoice = [...sel.options].some((o) => o.value);
  if (fromApi || hasChoice) return;
  const names = regState.meta?.default_department_names?.length
    ? regState.meta.default_department_names
    : FALLBACK_DEPT_NAMES;
  sel.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Select department…";
  sel.appendChild(empty);
  names.forEach((n) => {
    const v = String(n || "")
      .trim()
      .toLowerCase();
    if (!v) return;
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    sel.appendChild(o);
  });
}

async function loadRegistrationMeta(options = {}) {
  const downgradeRole = options.downgradeRole !== false;
  try {
    const r = await fetch("/api/meta/registration");
    if (!r.ok) throw new Error("bad status");
    regState.meta = await r.json();
  } catch {
    /* Keep optimistic defaults so Manager/Admin are not blocked if the meta request races or fails once. */
    regState.meta = {
      manager_registration_enabled: true,
      admin_registration_enabled: true,
      default_department_names: FALLBACK_DEPT_NAMES,
    };
  }
  if (!Array.isArray(regState.meta.default_department_names) || !regState.meta.default_department_names.length) {
    regState.meta.default_department_names = FALLBACK_DEPT_NAMES;
  }
  syncRegRoleAfterMeta({ downgradeRole });
  updateRegistrationFormUi();
  ensureRegisterDeptOptionsFromMeta();
}

function syncRegRoleAfterMeta(opts = {}) {
  const downgradeRole = opts.downgradeRole !== false;
  const { meta } = regState;
  if (downgradeRole) {
    if (regState.role === "manager" && !meta.manager_registration_enabled) regState.role = "employee";
    if (regState.role === "admin" && !meta.admin_registration_enabled) regState.role = "employee";
  }
  document.querySelectorAll("#reg-role-tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.role === regState.role);
  });
}

function updateRegistrationFormUi() {
  const { meta } = regState;
  const mgrBtn = document.querySelector('#reg-role-tabs button[data-role="manager"]');
  const admBtn = document.querySelector('#reg-role-tabs button[data-role="admin"]');
  if (mgrBtn) mgrBtn.disabled = !meta.manager_registration_enabled;
  if (admBtn) admBtn.disabled = !meta.admin_registration_enabled;

  const panel = $("reg-invite-panel");
  const hint = $("reg-code-hint");
  const heading = $("reg-invite-heading");
  const lead = $("reg-invite-lead");
  const label = $("reg-code-label");
  const codeInput = $("reg-code");

  panel.classList.remove("manager-panel", "admin-panel");

  if (regState.role === "employee") {
    show(panel, false);
    hint.textContent = "";
    if (codeInput) codeInput.value = "";
    return;
  }

  // Manager & Administrator: always show the invite-code panel with role-specific copy
  show(panel, true);

  if (regState.role === "manager") {
    panel.classList.add("manager-panel");
    heading.textContent = "Manager registration — invite code";
    lead.textContent =
      "Enter the manager invite code below. This is required to create a manager account for your department.";
    label.textContent = "Manager invite code";
    hint.textContent = meta.manager_registration_enabled
      ? "Use the code issued by your organisation. If you do not have one, ask your administrator."
      : "Manager self-registration is not enabled on this server. Contact your IT administrator.";
  } else if (regState.role === "admin") {
    panel.classList.add("admin-panel");
    heading.textContent = "Administrator registration — invite code";
    lead.textContent =
      "Enter the administrator invite code below. This is required to create an organisation administrator account.";
    label.textContent = "Administrator invite code";
    hint.textContent = meta.admin_registration_enabled
      ? "Use the code issued by your organisation. Keep it confidential."
      : "Administrator self-registration is not enabled on this server. Contact your IT administrator.";
  }
}

function updateDashboardBanner() {
  const role = state.user?.role || "employee";
  const banner = $("dashboard-banner");
  const title = $("dashboard-title");
  const sub = $("dashboard-sub");
  if (!banner || !title || !sub) return;
  banner.classList.remove("employee", "manager", "admin");
  banner.classList.add(role);
  title.textContent =
    role === "employee"
      ? "Employee dashboard"
      : role === "manager"
        ? "Manager dashboard"
        : "Administrator dashboard";
  sub.textContent =
    role === "employee"
      ? "Schedule shows your department rota. My Kanban is your team’s shared task board (priorities & owners). Use My requests for leave and shift changes."
      : role === "manager"
        ? "Use the tabs: Schedule · Manage shifts · Approvals · Approval log (department request history) · Info-valley · My Kanban (team tasks & priorities)."
        : "Tabs: Departments · People · Approvals · Activity · My Kanban (per-department board) · Schedule · Manage shifts.";
  show(banner, true);
}

function initCalendar() {
  const el = $("calendar");
  if (!el) return;
  if (typeof FullCalendar === "undefined" || !FullCalendar.Calendar) {
    el.innerHTML =
      '<p class="error">The calendar could not load (FullCalendar blocked or offline). Allow <strong>cdn.jsdelivr.net</strong> in your browser or network, then refresh. You can still use the table and other tabs.</p>';
    state.calendar = null;
    return;
  }
  if (state.calendar) {
    state.calendar.destroy();
  }
  state.calendar = new FullCalendar.Calendar(el, {
    initialView: "dayGridMonth",
    height: "auto",
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "dayGridMonth,timeGridWeek,listWeek",
    },
    events(info, successCallback, failureCallback) {
      (async () => {
        try {
          const params = new URLSearchParams({
            start: info.startStr.slice(0, 10),
            end: info.endStr.slice(0, 10),
          });
          if (state.user.role === "admin") {
            const sel = $("cal-dept").value;
            if (!sel) {
              successCallback([]);
              return;
            }
            params.set("department_id", sel);
          }
          const data = await api(`/api/shifts/calendar?${params}`);
          successCallback(data.events || []);
        } catch (e) {
          failureCallback(e);
        }
      })();
    },
    eventDidMount(info) {
      const k = info.event.extendedProps.kind;
      if (k === "leave") {
        info.el.style.opacity = "0.35";
      }
    },
  });
  state.calendar.render();
}

function setDefaultTableRange() {
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - today.getDay());
  const end = new Date(start);
  end.setDate(start.getDate() + 13);
  $("table-start").value = start.toISOString().slice(0, 10);
  $("table-end").value = end.toISOString().slice(0, 10);
}

function rosterShiftCodeOrder(codes) {
  const preferred = ["A", "B", "C", "G", "L", "WO"];
  const upper = (codes || []).map((c) => String(c).toUpperCase());
  const seen = new Set(upper);
  const out = [];
  preferred.forEach((c) => {
    if (seen.has(c)) {
      out.push(c);
      seen.delete(c);
    }
  });
  [...seen].sort().forEach((c) => out.push(c));
  return out;
}

/** Always include L and WO even if an older server only returned timed bands in shift legend. */
function matrixShiftCodes() {
  const canonical = ["A", "B", "C", "G", "L", "WO"];
  const serverKeys = Object.keys(state.shiftLegend || {}).map((c) => String(c).toUpperCase());
  const merged = new Set([...canonical, ...serverKeys]);
  return rosterShiftCodeOrder([...merged]);
}

function canEditMatrixCell(employeeId) {
  const role = state.user?.role;
  if (!role) return false;
  if (role === "manager" || role === "admin") return true;
  if (role === "employee") {
    const mine = String(state.user.employee_id || "").toUpperCase();
    return mine && String(employeeId || "").toUpperCase() === mine;
  }
  return false;
}

function paintMatrixDataCell(td, code, editable) {
  td.replaceChildren();
  td.className = "";
  td.classList.add("matrix-data-cell");
  const c = (code || "").trim().toUpperCase();
  td.dataset.shiftCode = c;
  if (c) {
    td.textContent = c;
    td.classList.add(`cell-${c.toLowerCase()}`);
  } else {
    td.textContent = "—";
  }
  if (editable) {
    td.classList.add("matrix-cell-editable");
    td.title = "Tap to choose A, B, C, G, L (leave), or WO (week off)";
  }
}

function restoreOpenMatrixCellEditor() {
  const sel = document.querySelector("#matrix-body select.matrix-cell-select, #matrix-cards select.matrix-cell-select");
  if (!sel) return;
  const td = sel.closest("td");
  if (td) paintMatrixDataCell(td, td.dataset.shiftCode, true);
}

async function saveMatrixCellShift(td, shiftCode) {
  const emp = td.dataset.employeeId;
  const date = td.dataset.date;
  if (!emp || !date) return;
  if (state.user.role === "employee") {
    const mine = String(state.user.employee_id || "").toUpperCase();
    if (String(emp).toUpperCase() !== mine) throw new Error("You can only edit your own row.");
    await api("/api/shifts/mine", {
      method: "POST",
      body: JSON.stringify({ date, shift_code: shiftCode }),
    });
  } else {
    const payload = {
      assignments: [{ employee_id: emp, date, shift_code: shiftCode }],
    };
    if (state.user.role === "admin") {
      const dept = $("table-dept")?.value;
      if (!dept) throw new Error("Choose a department (admin).");
      payload.department_id = dept;
    }
    await api("/api/shifts/bulk", { method: "POST", body: JSON.stringify(payload) });
  }
  if (state.calendar) state.calendar.refetchEvents();
  await refreshTable();
}

function openMatrixCellEditor(td) {
  if (!td || td.classList.contains("sticky")) return;
  if (!canEditMatrixCell(td.dataset.employeeId)) return;
  restoreOpenMatrixCellEditor();
  const current = (td.dataset.shiftCode || "").trim().toUpperCase();
  const sel = document.createElement("select");
  sel.className = "matrix-cell-select";
  sel.setAttribute("aria-label", "Shift for this day");
  matrixShiftCodes().forEach((code) => {
    const o = document.createElement("option");
    o.value = code;
    o.textContent = shiftOptionLabel(code);
    sel.appendChild(o);
  });
  if (current && [...sel.options].some((o) => o.value === current)) sel.value = current;
  else sel.selectedIndex = 0;

  td.replaceChildren();
  td.appendChild(sel);
  sel.focus();

  let committed = false;
  sel.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      sel.blur();
    }
  });
  sel.addEventListener("change", async () => {
    committed = true;
    const code = sel.value;
    try {
      await saveMatrixCellShift(td, code);
    } catch (e) {
      alert(e.message || String(e));
      paintMatrixDataCell(td, td.dataset.shiftCode, true);
    }
  });
  sel.addEventListener("blur", () => {
    setTimeout(() => {
      if (committed) return;
      if (td.querySelector("select.matrix-cell-select")) paintMatrixDataCell(td, td.dataset.shiftCode, true);
    }, 0);
  });
}

function handleMatrixDataCellClick(ev) {
  const td = ev.target.closest("td.matrix-data-cell");
  if (!td || !td.dataset.date || !td.dataset.employeeId) return;
  if (!canEditMatrixCell(td.dataset.employeeId)) return;
  const inMain = $("matrix-body")?.contains(td);
  const inCards = $("matrix-cards")?.contains(td);
  if (!inMain && !inCards) return;
  if (td.classList.contains("sticky")) return;
  if (td.querySelector("select.matrix-cell-select")) return;
  if (ev.target.closest("select")) return;
  openMatrixCellEditor(td);
}

function initMatrixTableCellEditor() {
  const tbody = $("matrix-body");
  if (tbody && tbody.dataset.editDelegation !== "1") {
    tbody.dataset.editDelegation = "1";
    tbody.addEventListener("click", handleMatrixDataCellClick);
  }
  const cards = $("matrix-cards");
  if (cards && cards.dataset.editDelegation !== "1") {
    cards.dataset.editDelegation = "1";
    cards.addEventListener("click", handleMatrixDataCellClick);
  }
}

function formatMatrixDayLabel(isoDate) {
  if (!isoDate || isoDate.length < 10) return isoDate || "";
  try {
    const d = new Date(`${isoDate.slice(0, 10)}T12:00:00`);
    if (Number.isNaN(d.getTime())) return isoDate.slice(5);
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  } catch {
    return isoDate.slice(5);
  }
}

function formatMatrixWeekdayShort(isoDate) {
  if (!isoDate || isoDate.length < 10) return "";
  try {
    const d = new Date(`${isoDate.slice(0, 10)}T12:00:00`);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, { weekday: "short" });
  } catch {
    return "";
  }
}

function buildMatrixMobileCards(data) {
  const root = $("matrix-cards");
  if (!root) return;
  root.innerHTML = "";
  const dates = data.dates || [];
  const rows = data.rows || [];
  if (!dates.length) {
    root.innerHTML = "";
    return;
  }
  if (!rows.length) {
    root.innerHTML = '<p class="hint">No team members in this roster range.</p>';
    return;
  }
  rows.forEach((row) => {
    const canEditMatrix = canEditMatrixCell(row.employee_id);
    const article = document.createElement("article");
    article.className = "roster-person-card";
    const head = document.createElement("header");
    head.className = "roster-person-card-head";
    const nameEl = document.createElement("strong");
    nameEl.className = "roster-person-name";
    nameEl.textContent = row.full_name || "";
    const idSpan = document.createElement("span");
    idSpan.className = "badge roster-person-id";
    idSpan.textContent = row.employee_id || "";
    head.appendChild(nameEl);
    head.appendChild(idSpan);
    article.appendChild(head);
    const tbl = document.createElement("table");
    tbl.className = "matrix roster-mobile-table";
    const tb = document.createElement("tbody");
    dates.forEach((d) => {
      const tr = document.createElement("tr");
      const tdD = document.createElement("td");
      tdD.className = "roster-mobile-date";
      tdD.textContent = formatMatrixDayLabel(d);
      const tdS = document.createElement("td");
      tdS.dataset.date = d;
      tdS.dataset.employeeId = row.employee_id;
      paintMatrixDataCell(tdS, row.cells[d] || "", canEditMatrix);
      tr.appendChild(tdD);
      tr.appendChild(tdS);
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    article.appendChild(tbl);
    root.appendChild(article);
  });
}

async function refreshTable() {
  const params = new URLSearchParams({
    start: $("table-start").value,
    end: $("table-end").value,
  });
  if (state.user.role === "admin") {
    const sel = $("table-dept").value;
    if (!sel) {
      $("matrix-head").innerHTML = "";
      $("matrix-body").innerHTML = "";
      buildMatrixMobileCards({ dates: [], rows: [] });
      return;
    }
    params.set("department_id", sel);
  }
  const data = await api(`/api/shifts/table?${params}`);
  if (data.shift_legend && typeof data.shift_legend === "object") {
    state.shiftLegend = { ...state.shiftLegend, ...data.shift_legend };
    const fillNonTimed = {
      L: { label: "Leave", description: "Leave" },
      WO: { label: "Week off", description: "Week off" },
    };
    Object.entries(fillNonTimed).forEach(([k, v]) => {
      if (!state.shiftLegend[k]) state.shiftLegend[k] = v;
    });
    fillMgrAssignShiftSelect();
  }
  const thead = $("matrix-head");
  const tbody = $("matrix-body");
  const tbl = $("matrix-table");
  thead.innerHTML = "";
  tbody.innerHTML = "";
  if (tbl) {
    let cap = tbl.querySelector("caption");
    if (!cap) {
      cap = document.createElement("caption");
      tbl.insertBefore(cap, tbl.firstChild);
    }
    cap.className = "matrix-caption";
    cap.textContent = data.department_name
      ? `Shift roster — ${data.department_name} (rows: people · columns: dates)`
      : "Shift roster (rows: people · columns: dates)";
  }
  const hr = document.createElement("tr");
  const h0 = document.createElement("th");
  h0.classList.add("sticky");
  h0.setAttribute("scope", "col");
  h0.textContent = data.department_name ? `${data.department_name} — staff` : "Staff";
  hr.appendChild(h0);
  (data.dates || []).forEach((d) => {
    const th = document.createElement("th");
    th.setAttribute("scope", "col");
    const dateL = document.createElement("span");
    dateL.className = "matrix-th-date";
    dateL.textContent = d.slice(5);
    const dow = document.createElement("span");
    dow.className = "matrix-th-dow";
    dow.textContent = formatMatrixWeekdayShort(d);
    th.appendChild(dateL);
    th.appendChild(dow);
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  (data.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.classList.add("sticky");
    td0.setAttribute("scope", "row");
    td0.textContent = `${row.full_name} (${row.employee_id})`;
    tr.appendChild(td0);
    const canEditMatrix = canEditMatrixCell(row.employee_id);
    (data.dates || []).forEach((d) => {
      const td = document.createElement("td");
      td.dataset.date = d;
      td.dataset.employeeId = row.employee_id;
      const code = row.cells[d];
      paintMatrixDataCell(td, code || "", canEditMatrix);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  buildMatrixMobileCards(data);
}

async function refreshManagerQueues() {
  if (!state.user || !["manager", "admin"].includes(state.user.role)) return;
  const [lv, ch] = await Promise.all([api("/api/requests/leave"), api("/api/requests/shift-change")]);

  if (state.user.role === "admin") {
    const summary = $("admin-pending-summary");
    if (summary) {
      const lp = (lv.requests || []).filter((r) => r.status === "pending").length;
      const sp = (ch.requests || []).filter((r) => r.status === "pending").length;
      summary.innerHTML =
        lp + sp === 0
          ? '<span class="hint">No pending leave or shift-change approvals.</span>'
          : `<strong>${lp}</strong> leave · <strong>${sp}</strong> shift change awaiting approval — open the <strong>Approvals</strong> tab.`;
    }
  }

  const box = $("mgr-leave-list");
  box.innerHTML = "";
  (lv.requests || []).forEach((r) => {
    if (r.status !== "pending") return;
    const div = document.createElement("div");
    div.className = "req-item pending";
    const deptBadge =
      state.user.role === "admin" && r.department_name
        ? `<span class="badge">${escapeHtml(r.department_name)}</span> `
        : "";
    div.innerHTML = `<div>${deptBadge}<strong>${r.full_name}</strong> <span class="badge">${r.employee_id}</span></div>
      <div>${r.start_date} → ${r.end_date}</div><div class="hint">${escapeHtml(r.reason || "")}</div>`;
    if (state.user.role === "manager" || state.user.role === "admin") {
      const actions = document.createElement("div");
      actions.className = "req-actions";
      actions.innerHTML = `<button type="button" class="btn" data-id="${r.id}" data-kind="leave" data-status="approved">Approve</button>
        <button type="button" class="btn secondary" data-id="${r.id}" data-kind="leave" data-status="rejected">Reject</button>`;
      div.appendChild(actions);
    }
    box.appendChild(div);
  });
  box.querySelectorAll('button[data-kind="leave"]').forEach((btn) => {
    btn.addEventListener("click", () => decideLeave(btn.dataset.id, btn.dataset.status));
  });

  const cbox = $("mgr-change-list");
  cbox.innerHTML = "";
  (ch.requests || []).forEach((r) => {
    if (r.status !== "pending") return;
    const div = document.createElement("div");
    div.className = "req-item pending";
    const deptChg =
      state.user.role === "admin" && r.department_name
        ? `<span class="badge">${escapeHtml(r.department_name)}</span> `
        : "";
    div.innerHTML = `<div>${deptChg}<strong>${r.full_name}</strong> <span class="badge">${r.employee_id}</span></div>
      <div>${r.date}: ${r.from_shift} → ${r.to_shift}</div><div class="hint">${escapeHtml(r.reason || "")}</div>`;
    const actions = document.createElement("div");
    actions.className = "req-actions";
    actions.innerHTML = `<button type="button" class="btn" data-id="${r.id}" data-kind="chg" data-status="approved">Approve</button>
      <button type="button" class="btn secondary" data-id="${r.id}" data-kind="chg" data-status="rejected">Reject</button>`;
    div.appendChild(actions);
    cbox.appendChild(div);
  });
  cbox.querySelectorAll('button[data-kind="chg"]').forEach((btn) => {
    btn.addEventListener("click", () => decideChange(btn.dataset.id, btn.dataset.status));
  });
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtShortDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return String(iso);
  }
}

function showEmployeeRequestNotice(message, kind) {
  const el = $("emp-request-notice");
  if (!el) return;
  el.classList.remove("hidden", "success", "error");
  if (!message) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = message;
  el.classList.add(kind === "error" ? "error" : "success");
  if (kind === "success") {
    setTimeout(() => {
      if (el.textContent === message) {
        el.classList.add("hidden");
        el.textContent = "";
        el.classList.remove("success", "error");
      }
    }, 12000);
  }
}

async function refreshEmployeeRequestLog() {
  if (!state.user || state.user.role !== "employee") return;
  const box = $("emp-request-log");
  if (!box) return;
  box.innerHTML = "";
  try {
    const [lv, ch] = await Promise.all([api("/api/requests/leave"), api("/api/requests/shift-change")]);
    const rows = [];
    (lv.requests || []).forEach((r) => {
      rows.push({
        kind: "leave",
        sort: r.created_at || "",
        detail: `${r.start_date} → ${r.end_date}`,
        reason: r.reason || "",
        status: r.status,
        created_at: r.created_at,
        decided_at: r.decided_at,
      });
    });
    (ch.requests || []).forEach((r) => {
      rows.push({
        kind: "shift_change",
        sort: r.created_at || "",
        detail: `${r.date}: shift ${r.from_shift} → ${r.to_shift}`,
        reason: r.reason || "",
        status: r.status,
        created_at: r.created_at,
        decided_at: r.decided_at,
      });
    });
    rows.sort((a, b) => String(b.sort).localeCompare(String(a.sort)));

    if (rows.length === 0) {
      box.innerHTML = '<p class="hint">No requests yet. Submit leave or a shift change above.</p>';
      return;
    }

    const wrap = document.createElement("div");
    wrap.className = "table-scroll emp-log-table-wrap";
    const tbl = document.createElement("table");
    tbl.className = "matrix log-table";
    tbl.innerHTML =
      "<thead><tr><th>Type</th><th>Details</th><th>Submitted</th><th>Status</th><th>Decision time</th></tr></thead>";
    const tb = document.createElement("tbody");
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      const typeLabel = r.kind === "leave" ? "Leave" : "Shift change";
      const st = (r.status || "pending").toLowerCase();
      const badgeClass =
        st === "approved" ? "status-approved" : st === "rejected" ? "status-rejected" : "status-pending";
      tr.innerHTML = `<td>${typeLabel}</td><td>${escapeHtml(r.detail)}<br/><span class="hint">${escapeHtml(r.reason || "—")}</span></td><td>${escapeHtml(fmtShortDateTime(r.created_at))}</td><td><span class="badge ${badgeClass}">${escapeHtml(st)}</span></td><td>${escapeHtml(fmtShortDateTime(r.decided_at))}</td>`;
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    wrap.appendChild(tbl);
    box.appendChild(wrap);
  } catch (e) {
    box.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

async function decideLeave(id, status) {
  await api(`/api/requests/leave/${id}/decide`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  await refreshManagerQueues();
  if (state.calendar) state.calendar.refetchEvents();
  await refreshTable();
  if (state.user.role === "admin") await refreshAdminRequestLog();
  if (state.user.role === "manager") {
    await refreshManagerRequestLog();
    await refreshManagerInlineApprovalsLog();
    $("mgr-approvals-activity-log")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

async function decideChange(id, status) {
  await api(`/api/requests/shift-change/${id}/decide`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  await refreshManagerQueues();
  if (state.calendar) state.calendar.refetchEvents();
  await refreshTable();
  if (state.user.role === "admin") await refreshAdminRequestLog();
  if (state.user.role === "manager") {
    await refreshManagerRequestLog();
    await refreshManagerInlineApprovalsLog();
    $("mgr-approvals-activity-log")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

async function refreshManagerRoster() {
  if (!state.user || !["manager", "admin"].includes(state.user.role)) return;
  const tbody = $("mgr-roster-body");
  const selMember = $("mgr-assign-member");
  const msg = $("mgr-assign-msg");
  if (!tbody || !selMember) return;

  tbody.innerHTML = "";
  selMember.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = "Select team member…";
  selMember.appendChild(ph);

  let url = "/api/users";
  if (state.user.role === "admin") {
    const did = $("mgr-roster-dept") && $("mgr-roster-dept").value;
    if (!did) {
      if (msg) msg.textContent = "Choose a department above to load its team.";
      return;
    }
    url += `?department_id=${encodeURIComponent(did)}`;
  }

  let data;
  try {
    data = await api(url);
  } catch (e) {
    if (msg) msg.textContent = e.message;
    return;
  }

  const users = data.users || [];
  users.forEach((u) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(u.employee_id)}</td><td>${escapeHtml(u.full_name)}</td><td>${escapeHtml(u.role)}</td>`;
    tbody.appendChild(tr);
    const o = document.createElement("option");
    o.value = u.employee_id;
    o.textContent = `${u.full_name} (${u.employee_id})`;
    selMember.appendChild(o);
  });

  if (msg) {
    msg.textContent =
      users.length === 0
        ? "No users found for this department."
        : `${users.length} people — pick one below to assign a shift.`;
  }
}

function requestLogStatusBadge(st) {
  const s = (st || "pending").toLowerCase();
  const cls =
    s === "approved" ? "status-approved" : s === "rejected" ? "status-rejected" : "status-pending";
  return `<span class="badge ${cls}">${escapeHtml(s)}</span>`;
}

/** @param {{ requests?: unknown[] }} lv @param {{ requests?: unknown[] }} ch */
function renderRequestActivityTables(lv, ch, leaveBox, shiftBox, showDeptColumn) {
  if (!leaveBox || !shiftBox) return;
  const deptTh = showDeptColumn ? "<th>Dept</th>" : "";

  const tblL = document.createElement("table");
  tblL.className = "matrix log-table";
  tblL.innerHTML = `<thead><tr>${deptTh}<th>Employee</th><th>Dates</th><th>Submitted</th><th>Status</th><th>Decision</th><th>Approved / rejected by</th></tr></thead>`;
  const tbL = document.createElement("tbody");
  (lv.requests || []).forEach((r) => {
    const tr = document.createElement("tr");
    const dec = r.decided_by_name
      ? `${escapeHtml(r.decided_by_name)} (${escapeHtml(r.decided_by_employee_id || "")})`
      : "—";
    const det = `${escapeHtml(r.start_date)} → ${escapeHtml(r.end_date)}`;
    const deptCell = showDeptColumn ? `<td>${escapeHtml(r.department_name || "—")}</td>` : "";
    tr.innerHTML = `${deptCell}<td>${escapeHtml(r.full_name)}<br/><span class="hint">${escapeHtml(r.employee_id)}</span></td><td>${det}<br/><span class="hint">${escapeHtml(r.reason || "")}</span></td><td>${escapeHtml(fmtShortDateTime(r.created_at))}</td><td>${requestLogStatusBadge(r.status)}</td><td>${escapeHtml(fmtShortDateTime(r.decided_at))}</td><td>${dec}</td>`;
    tbL.appendChild(tr);
  });
  tblL.appendChild(tbL);
  leaveBox.innerHTML = "";
  if ((lv.requests || []).length === 0) {
    leaveBox.innerHTML = '<p class="hint">No leave requests match this filter.</p>';
  } else {
    leaveBox.appendChild(tblL);
  }

  const tblS = document.createElement("table");
  tblS.className = "matrix log-table";
  tblS.innerHTML = `<thead><tr>${deptTh}<th>Employee</th><th>Change</th><th>Submitted</th><th>Status</th><th>Decision</th><th>Approved / rejected by</th></tr></thead>`;
  const tbS = document.createElement("tbody");
  (ch.requests || []).forEach((r) => {
    const tr = document.createElement("tr");
    const dec = r.decided_by_name
      ? `${escapeHtml(r.decided_by_name)} (${escapeHtml(r.decided_by_employee_id || "")})`
      : "—";
    const det = `${escapeHtml(r.date)}: ${escapeHtml(r.from_shift)} → ${escapeHtml(r.to_shift)}`;
    const deptCell = showDeptColumn ? `<td>${escapeHtml(r.department_name || "—")}</td>` : "";
    tr.innerHTML = `${deptCell}<td>${escapeHtml(r.full_name)}<br/><span class="hint">${escapeHtml(r.employee_id)}</span></td><td>${det}<br/><span class="hint">${escapeHtml(r.reason || "")}</span></td><td>${escapeHtml(fmtShortDateTime(r.created_at))}</td><td>${requestLogStatusBadge(r.status)}</td><td>${escapeHtml(fmtShortDateTime(r.decided_at))}</td><td>${dec}</td>`;
    tbS.appendChild(tr);
  });
  tblS.appendChild(tbS);
  shiftBox.innerHTML = "";
  if ((ch.requests || []).length === 0) {
    shiftBox.innerHTML = '<p class="hint">No shift change requests match this filter.</p>';
  } else {
    shiftBox.appendChild(tblS);
  }
}

/** Load department-scoped leave + shift-change history for managers (same API as Approval log tab). */
async function fetchAndRenderManagerDeptRequestLog(leaveBox, shiftBox, showDeptColumn) {
  if (state.user?.role !== "manager") return;
  if (!leaveBox || !shiftBox) return;
  leaveBox.innerHTML = '<p class="hint">Loading…</p>';
  shiftBox.innerHTML = "";
  try {
    const [lv, ch] = await Promise.all([api("/api/requests/leave"), api("/api/requests/shift-change")]);
    renderRequestActivityTables(lv, ch, leaveBox, shiftBox, showDeptColumn);
  } catch (e) {
    leaveBox.innerHTML = `<p class="error">${escapeHtml(e.message || String(e))}</p>`;
    shiftBox.innerHTML = "";
  }
}

async function refreshManagerRequestLog() {
  await fetchAndRenderManagerDeptRequestLog($("mgr-records-leave"), $("mgr-records-shift"), true);
}

async function refreshManagerInlineApprovalsLog() {
  await fetchAndRenderManagerDeptRequestLog($("mgr-approvals-leave-log"), $("mgr-approvals-shift-log"), true);
}

async function refreshAdminRequestLog() {
  if (state.user.role !== "admin") return;
  const dept = $("admin-records-dept")?.value || "";
  const q = dept ? `?department_id=${encodeURIComponent(dept)}` : "";
  const leaveBox = $("admin-records-leave");
  const shiftBox = $("admin-records-shift");
  if (!leaveBox || !shiftBox) return;
  leaveBox.innerHTML = '<p class="hint">Loading…</p>';
  shiftBox.innerHTML = "";
  try {
    const [lv, ch] = await Promise.all([
      api(`/api/requests/leave${q}`),
      api(`/api/requests/shift-change${q}`),
    ]);
    renderRequestActivityTables(lv, ch, leaveBox, shiftBox, true);
  } catch (e) {
    leaveBox.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

async function refreshAdminDeptList() {
  if (state.user.role !== "admin") return;
  const box = $("admin-dept-list");
  if (!box) return;
  box.innerHTML = "";
  try {
    const data = await api("/api/departments");
    const tbl = document.createElement("table");
    tbl.className = "matrix";
    tbl.innerHTML = "<thead><tr><th>Department</th><th></th></tr></thead>";
    const tb = document.createElement("tbody");
    (data.departments || []).forEach((d) => {
      const tr = document.createElement("tr");
      const tdN = document.createElement("td");
      tdN.textContent = d.name;
      const tdAct = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn danger";
      btn.textContent = "Delete department";
      btn.addEventListener("click", async () => {
        if (
          !confirm(
            `Delete department "${d.name}"? Users in this department will no longer be linked to a department.`,
          )
        )
          return;
        await api(`/api/departments/${d.id}`, { method: "DELETE" });
        $("dept-msg").textContent = `Removed department ${d.name}.`;
        await loadDepartments();
        await refreshAdminDeptList();
        await refreshAdminUsers();
        await refreshAdminRequestLog().catch(() => {});
      });
      tdAct.appendChild(btn);
      tr.appendChild(tdN);
      tr.appendChild(tdAct);
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    box.appendChild(tbl);
  } catch (e) {
    box.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

async function refreshAdminUsers() {
  if (state.user?.role !== "admin") return;
  const wrap = $("admin-users");
  if (!wrap) return;
  const filt = $("admin-user-dept-filter")?.value || "";
  let url = "/api/users";
  if (filt) url += `?department_id=${encodeURIComponent(filt)}`;
  const data = await api(url);
  wrap.innerHTML = "";
  const tbl = document.createElement("table");
  tbl.className = "matrix";
  tbl.innerHTML =
    "<thead><tr><th>Employee ID</th><th>Name</th><th>Department</th><th>Set role</th><th>User actions</th></tr></thead>";
  const tb = document.createElement("tbody");
  const myId = String(state.user.id || "");
  (data.users || []).forEach((u) => {
    const tr = document.createElement("tr");

    const tdEmp = document.createElement("td");
    tdEmp.textContent = u.employee_id;

    const tdName = document.createElement("td");
    tdName.textContent = u.full_name;

    const tdDept = document.createElement("td");
    tdDept.textContent = u.department_name || "—";

    const tdRole = document.createElement("td");
    tdRole.className = "admin-user-role-cell";
    const roleWrap = document.createElement("div");
    roleWrap.className = "admin-user-role-wrap";
    const sel = document.createElement("select");
    ["employee", "manager", "admin"].forEach((r) => {
      const o = document.createElement("option");
      o.value = r;
      o.textContent = r;
      if (u.role === r) o.selected = true;
      sel.appendChild(o);
    });
    const btnSave = document.createElement("button");
    btnSave.type = "button";
    btnSave.className = "btn secondary admin-save-btn";
    btnSave.textContent = "Save";
    btnSave.addEventListener("click", async () => {
      await api(`/api/users/${u.id}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role: sel.value }),
      });
      await refreshAdminUsers();
      await refreshManagerRoster().catch(() => {});
    });
    roleWrap.appendChild(sel);
    roleWrap.appendChild(btnSave);
    tdRole.appendChild(roleWrap);

    const tdDel = document.createElement("td");
    tdDel.className = "admin-user-actions-cell";
    if (String(u.id) !== myId) {
      const actionWrap = document.createElement("div");
      actionWrap.className = "admin-user-actions-wrap";
      const btnReset = document.createElement("button");
      btnReset.type = "button";
      btnReset.className = "btn admin-reset-btn";
      btnReset.textContent = "Reset password";
      btnReset.addEventListener("click", async () => {
        const pw = prompt(`Enter a new temporary password for ${u.full_name} (${u.employee_id}):`);
        if (pw === null) return;
        const next = String(pw).trim();
        if (next.length < 6) {
          alert("Password must be at least 6 characters.");
          return;
        }
        await api(`/api/users/${u.id}/password`, {
          method: "PATCH",
          body: JSON.stringify({ password: next }),
        });
        alert(`Password reset for ${u.full_name} (${u.employee_id}). Share it securely.`);
      });
      actionWrap.appendChild(btnReset);

      const btnDel = document.createElement("button");
      btnDel.type = "button";
      btnDel.className = "btn danger";
      btnDel.textContent = "Delete";
      btnDel.addEventListener("click", async () => {
        if (!confirm(`Remove ${u.full_name} (${u.employee_id}) from the system?`)) return;
        await api(`/api/users/${u.id}`, { method: "DELETE" });
        await refreshAdminUsers();
        await refreshManagerRoster().catch(() => {});
      });
      actionWrap.appendChild(btnDel);
      tdDel.appendChild(actionWrap);
    } else {
      tdDel.innerHTML = '<span class="hint">—</span>';
    }

    tr.appendChild(tdEmp);
    tr.appendChild(tdName);
    tr.appendChild(tdDept);
    tr.appendChild(tdRole);
    tr.appendChild(tdDel);
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}

function mountScheduleForRole(role) {
  const block = $("schedule-block");
  if (!block) return;
  let mount = $("emp-sched-mount");
  if (role === "manager") mount = $("mgr-sched-mount");
  if (role === "admin") mount = $("adm-sched-mount");
  if (mount) mount.appendChild(block);
}

const TASK_COLUMNS = [
  { id: "todo", label: "To do" },
  { id: "in_progress", label: "In progress" },
  { id: "done", label: "Done" },
];

const TASK_TABLE_COL_ORDER = { todo: 0, in_progress: 1, done: 2 };

function taskTableStatusLabel(column) {
  if (column === "todo") return "TO DO";
  if (column === "in_progress") return "IN PROGRESS";
  if (column === "done") return "DONE";
  return String(column || "—").toUpperCase();
}

function sortedTasksForTable(tasks) {
  return [...(tasks || [])].sort((a, b) => {
    const ca = TASK_TABLE_COL_ORDER[a.column] ?? 99;
    const cb = TASK_TABLE_COL_ORDER[b.column] ?? 99;
    if (ca !== cb) return ca - cb;
    return (Number(b.priority) || 0) - (Number(a.priority) || 0);
  });
}

async function onTaskTableActionClick(ev) {
  const move = ev.target.closest("[data-task-move]");
  const del = ev.target.closest("[data-task-del]");
  if (move) {
    const id = move.getAttribute("data-task-move");
    const col = move.getAttribute("data-col");
    try {
      await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ column: col }) });
      await refreshTasksBoard();
    } catch (e) {
      alert(e.message || String(e));
    }
    return;
  }
  if (del) {
    if (!confirm("Delete this task?")) return;
    const id = del.getAttribute("data-task-del");
    try {
      await api(`/api/tasks/${id}`, { method: "DELETE" });
      await refreshTasksBoard();
    } catch (e) {
      alert(e.message || String(e));
    }
  }
}

function buildTaskTableRow(task) {
  const pri = Math.min(5, Math.max(1, Number(task.priority) || 3));
  const col = TASK_TABLE_COL_ORDER[task.column] !== undefined ? task.column : "todo";
  const assigneeHtml =
    task.assignee_names && task.assignee_names.length
      ? task.assignee_names.map((n) => escapeHtml(n)).join(", ")
      : '<span class="kanban-table-muted">—</span>';
  const rawDesc = (task.description || "").trim();
  const descHtml =
    rawDesc.length > 0
      ? `<div class="kanban-table-desc">${escapeHtml(rawDesc.slice(0, 140))}${rawDesc.length > 140 ? "…" : ""}</div>`
      : "";
  const canEdit = tasksCanEdit();
  const actionsHtml = canEdit
    ? `<td class="kanban-table-td-actions">${TASK_COLUMNS.map((c) =>
        c.id === task.column
          ? ""
          : `<button type="button" class="kanban-table-act" data-task-move="${escapeHtml(task.id)}" data-col="${c.id}">${escapeHtml(c.label)}</button>`,
      ).join(" ")}<button type="button" class="kanban-table-act kanban-table-act-del" data-task-del="${escapeHtml(task.id)}">Delete</button></td>`
    : "";
  const tr = document.createElement("tr");
  tr.className = `kanban-table-tr kanban-table-tr--${col}`;
  tr.innerHTML = `
    <td class="kanban-table-td-status"><span class="kanban-status-pill kanban-status-pill--${col}">${escapeHtml(taskTableStatusLabel(task.column))}</span></td>
    <td class="kanban-table-td-pri"><span class="kanban-pri-pill kanban-pri-pill-${pri}">P${pri}</span></td>
    <td class="kanban-table-td-title"><strong>${escapeHtml(task.title)}</strong>${descHtml}</td>
    <td class="kanban-table-td-people">${assigneeHtml}</td>
    ${actionsHtml}`;
  return tr;
}

function mountTasksModule(role) {
  const block = $("tasks-module-block");
  if (!block) return;
  let mount = $("emp-tasks-mount");
  if (role === "manager") mount = $("mgr-tasks-mount");
  if (role === "admin") mount = $("adm-tasks-mount");
  if (mount) mount.appendChild(block);
}

function mountInfoValleyModule(role) {
  const block = $("infovalley-module-block") || $("infovally-module-block");
  if (!block) return;
  let mount = $("emp-infovalley-mount") || $("emp-infovally-mount");
  if (role === "manager") mount = $("mgr-infovalley-mount") || $("mgr-infovally-mount");
  if (role === "admin") mount = $("adm-infovalley-mount") || $("adm-infovally-mount");
  if (mount) mount.appendChild(block);
}

function infovalleyScopeDeptId() {
  if (!state.user) return null;
  if (state.user.role === "admin") {
    return $("infovalley-admin-dept")?.value || $("infovally-admin-dept")?.value || null;
  }
  return state.user.department_id || null;
}

function updateInfoValleyUiForRole() {
  const role = state.user?.role;
  const hint = $("infovalley-hint") || $("infovally-hint");
  const adminWrap = $("infovalley-admin-dept-wrap") || $("infovally-admin-dept-wrap");
  if (adminWrap) show(adminWrap, role === "admin");
  if (hint) {
    hint.textContent =
      role === "admin"
        ? "Pick a department and review or add daily activities. Everyone in that department can comment."
        : "Department-wide day-wise activity log. Everyone here can add entries and comments.";
  }
}

function formatInfoValleyDate(isoDate) {
  if (!isoDate || isoDate.length < 10) return isoDate || "";
  try {
    const d = new Date(`${isoDate.slice(0, 10)}T12:00:00`);
    return d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  } catch {
    return isoDate;
  }
}

function renderInfoValleyTable(entries) {
  const root = $("infovalley-table") || $("infovally-table");
  if (!root) return;
  root.innerHTML = "";
  const list = entries || [];
  if (!list.length) {
    root.innerHTML = '<p class="hint">No activity posted yet for this department/day range.</p>';
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "table-scroll infovalley-scroll";
  const tbl = document.createElement("table");
  tbl.className = "matrix infovalley-table";
  tbl.innerHTML = `<thead><tr>
    <th>Date</th>
    <th>Activity details</th>
    <th>Comments</th>
  </tr></thead>`;
  const tb = document.createElement("tbody");
  list.forEach((item) => {
    const who = item.created_by || {};
    const cAt = item.created_at ? new Date(item.created_at).toLocaleString() : "—";
    const commentsHtml = (item.comments || [])
      .map((c) => {
        const by = c.created_by || {};
        const ts = c.created_at ? new Date(c.created_at).toLocaleString() : "";
        return `<div class="infovalley-comment-item"><strong>${escapeHtml(by.full_name || "?")} (${escapeHtml(by.employee_id || "?")})</strong><div>${escapeHtml(c.comment || "")}</div><div class="hint">${escapeHtml(ts)}</div></div>`;
      })
      .join("");
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="infovalley-date-cell"><strong>${escapeHtml(formatInfoValleyDate(item.activity_date))}</strong><div class="hint">${escapeHtml(item.activity_date || "")}</div></td>
      <td>
        <div class="infovalley-activity-title">${escapeHtml(item.title || "")}</div>
        <div class="infovalley-activity-meta hint">By ${escapeHtml(who.full_name || "?")} (${escapeHtml(who.employee_id || "?")}) · ${escapeHtml(cAt)}</div>
        <div class="infovalley-activity-details">${escapeHtml(item.details || "")}</div>
      </td>
      <td>
        <div class="infovalley-comments-wrap">${commentsHtml || '<span class="hint">No comments yet.</span>'}</div>
        <div class="row infovalley-comment-row">
          <div><input data-infovalley-comment-input="${escapeHtml(item.id)}" maxlength="1200" placeholder="Add comment..." /></div>
          <div style="flex: 0 0 auto"><button type="button" class="btn secondary" data-infovalley-comment-btn="${escapeHtml(item.id)}">Comment</button></div>
        </div>
      </td>`;
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
  root.appendChild(wrap);
}

/** GET /api/activities — retry with trailing slash if the server/proxy only exposes /api/activities/ */
async function fetchActivitiesJson(path) {
  try {
    return await api(path);
  } catch (e) {
    const st = e && e.status;
    const msg = (e && e.message) || "";
    const notFound = st === 404 || /not\s*found/i.test(msg) || msg === "Not Found";
    if (!notFound) throw e;
    const q = path.indexOf("?");
    const alt = q >= 0 ? `/api/activities/?${path.slice(q + 1)}` : "/api/activities/";
    if (alt === path) throw e;
    return await api(alt);
  }
}

/** POST create activity — same trailing-slash retry as tasks. */
async function postActivityCreate(body) {
  const opts = { method: "POST", body: JSON.stringify(body) };
  try {
    return await api("/api/activities", opts);
  } catch (e) {
    if (e && e.status === 404) {
      return await api("/api/activities/", opts);
    }
    throw e;
  }
}

async function refreshInfoValleyBoard() {
  const root = $("infovalley-table") || $("infovally-table");
  if (!root || !state.user) return;
  const deptId = infovalleyScopeDeptId();
  if (state.user.role === "admin" && !deptId) {
    root.innerHTML = '<p class="hint">Choose a department to open Info-valley.</p>';
    return;
  }
  let path = "/api/activities";
  if (state.user.role === "admin") {
    path += `?department_id=${encodeURIComponent(deptId)}`;
  }
  root.innerHTML = '<p class="hint">Loading activities…</p>';
  try {
    const data = await fetchActivitiesJson(path);
    renderInfoValleyTable(data.entries || []);
  } catch (e) {
    root.innerHTML = `<p class="error">${escapeHtml(e.message || String(e))}</p>`;
  }
}

function tasksCanEdit() {
  return state.user && ["manager", "admin"].includes(state.user.role);
}

function setTasksKanbanBanner(message, kind) {
  const wrap = $("tasks-kanban-banner");
  const msg = $("tasks-kanban-banner-msg");
  if (!wrap || !msg) return;
  wrap.classList.remove("info", "error");
  if (!message) {
    wrap.classList.add("hidden");
    msg.textContent = "";
    return;
  }
  msg.textContent = message;
  wrap.classList.remove("hidden");
  wrap.classList.add(kind === "error" ? "error" : "info");
}

function updateTasksBoardUiForRole() {
  const role = state.user?.role;
  const hint = $("tasks-board-hint");
  const adminWrap = $("tasks-admin-dept-wrap");
  const createPanel = $("tasks-create-panel");
  if (hint) {
    hint.textContent =
      role === "employee"
        ? "The task table (top) lists your department’s work in strong colors by status and priority — managers update rows below."
        : role === "manager"
          ? "Use the high-contrast table first, then add or change tasks below. Priority 5 = most urgent; rows sort by status then priority."
          : "Pick a department, then read the table. Everyone in that department sees the same rows; you and managers can add tasks under the table.";
  }
  if (adminWrap) show(adminWrap, role === "admin");
  if (createPanel) show(createPanel, role === "manager" || role === "admin");
}

async function loadTaskAssigneeOptions() {
  const sel = $("task-new-assignees");
  if (!sel || !tasksCanEdit()) return;
  const role = state.user.role;
  let url = "/api/users";
  if (role === "admin") {
    const did = $("tasks-admin-dept")?.value;
    if (!did) {
      sel.innerHTML = "";
      return;
    }
    url += `?department_id=${encodeURIComponent(did)}`;
  }
  try {
    const data = await api(url);
    sel.innerHTML = "";
    (data.users || []).forEach((u) => {
      const o = document.createElement("option");
      o.value = u.employee_id;
      o.textContent = `${u.full_name} (${u.employee_id})`;
      sel.appendChild(o);
    });
  } catch {
    sel.innerHTML = "";
  }
}

function renderKanbanFromTasks(tasks) {
  const root = $("tasks-kanban");
  if (!root) return;
  root.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "kanban-table-scroll";
  const tbl = document.createElement("table");
  tbl.className = "kanban-table";
  const canEdit = tasksCanEdit();
  const theadRow = `<tr>
    <th scope="col">Status</th>
    <th scope="col">Priority</th>
    <th scope="col">Title</th>
    <th scope="col">Responsible</th>
    ${canEdit ? '<th scope="col">Actions</th>' : ""}
  </tr>`;
  tbl.innerHTML = `<thead>${theadRow}</thead>`;
  const tb = document.createElement("tbody");
  const sorted = sortedTasksForTable(tasks);
  if (!sorted.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = canEdit ? 5 : 4;
    td.className = "kanban-table-empty";
    td.innerHTML =
      canEdit && (state.user.role !== "admin" || $("tasks-admin-dept")?.value)
        ? "No tasks in this table yet — add one in <strong>New task</strong> below."
        : "No tasks in this table yet.";
    tr.appendChild(td);
    tb.appendChild(tr);
  } else {
    sorted.forEach((t) => tb.appendChild(buildTaskTableRow(t)));
    if (canEdit) {
      tb.addEventListener("click", onTaskTableActionClick);
    }
  }
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
  root.appendChild(wrap);
}

/** Load task list; retries with trailing slash if the server/proxy only exposes /api/tasks/ */
async function fetchTaskBoardJson(path) {
  try {
    return await api(path);
  } catch (e) {
    const st = e && e.status;
    const msg = (e && e.message) || "";
    const notFound = st === 404 || /not\s*found/i.test(msg) || msg === "Not Found";
    if (!notFound) throw e;
    const q = path.indexOf("?");
    const alt = q >= 0 ? `/api/tasks/?${path.slice(q + 1)}` : "/api/tasks/";
    if (alt === path) throw e;
    return await api(alt);
  }
}

/** POST create task — same trailing-slash retry as the list endpoint. */
async function postTaskCreate(payload) {
  const opts = { method: "POST", body: JSON.stringify(payload) };
  try {
    return await api("/api/tasks", opts);
  } catch (e) {
    if (e && e.status === 404) {
      return await api("/api/tasks/", opts);
    }
    throw e;
  }
}

function tasksKanbanFailureBanner(e) {
  const st = e && e.status;
  const msg = (e && e.message) || String(e);
  const healthUrl = `${window.location.origin}/api/tasks/health`;
  const host = window.location.hostname || "";
  const isLocal = host === "127.0.0.1" || host === "localhost" || host === "::1";
  const localHint = isLocal
    ? " Local dev: stop the old server (Ctrl+C in the terminal), then from the project folder run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 — you must see the startup line “My Kanban API enabled” in that terminal. Open this app only at http://127.0.0.1:8000/ (not Live Server on another port)."
    : "";
  if (!st && (msg.includes("Failed to fetch") || msg.includes("NetworkError"))) {
    return {
      text: `Network error while loading the board (${msg}). Check your connection and that this site’s URL matches your RotaShift server.${isLocal ? " " + localHint : ""}`,
      kind: "error",
    };
  }
  if (st === 404 || /not\s*found/i.test(msg) || msg === "Not Found") {
    return {
      text: `My Kanban API returned HTTP 404 at ${healthUrl.replace("/health", "")}. ${isLocal ? localHint : "Redeploy the latest code from GitHub, then open " + healthUrl + ' — expect {"ok":true,"kanban":true}. If it does, hard-refresh this page (Ctrl+Shift+R).'}`,
      kind: "info",
    };
  }
  if (st === 401) {
    return { text: "Session expired — sign in again, then reopen My Kanban.", kind: "error" };
  }
  return { text: msg || `Request failed (${st || "?"})`, kind: "error" };
}

async function refreshTasksBoard() {
  const root = $("tasks-kanban");
  if (!root || !state.user) return;
  const role = state.user.role;
  let path = "/api/tasks";
  if (role === "admin") {
    const did = $("tasks-admin-dept")?.value;
    if (!did) {
      setTasksKanbanBanner("", "");
      root.innerHTML =
        '<p class="my-kanban-empty">Choose a department above to open its <strong>My Kanban</strong> board.</p>';
      return;
    }
    path += `?department_id=${encodeURIComponent(did)}`;
  }
  setTasksKanbanBanner("", "");
  root.innerHTML = '<p class="my-kanban-loading">Loading board…</p>';
  try {
    const data = await fetchTaskBoardJson(path);
    renderKanbanFromTasks(data.tasks || []);
  } catch (e) {
    const { text, kind } = tasksKanbanFailureBanner(e);
    setTasksKanbanBanner(text, kind);
    renderKanbanFromTasks([]);
  }
}

function mountShiftPanels(role) {
  const sm = $("panel-shift-mgmt");
  const ap = $("panel-mgr-approvals");
  const park = $("parked-mgr-panels");
  if (!sm || !ap) return;
  if (role === "employee") {
    if (park) {
      park.appendChild(sm);
      park.appendChild(ap);
    }
    return;
  }
  if (role === "manager") {
    const ms = $("mgr-shift-slot");
    const mas = $("mgr-approval-slot");
    if (ms) ms.appendChild(sm);
    if (mas) mas.appendChild(ap);
    return;
  }
  if (role === "admin") {
    const ads = $("adm-shift-slot");
    if (ads) ads.appendChild(sm);
    const apTab = $("adm-approval-tab-slot");
    if (apTab) apTab.appendChild(ap);
  }
}

function activateDashTab(dashId, tabId) {
  document.querySelectorAll(`[data-dash="${dashId}"].dash-tab`).forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tabId);
  });
  document.querySelectorAll(`[data-dash="${dashId}"].dash-pane`).forEach((p) => {
    show(p, p.dataset.tab === tabId);
  });
  if (tabId === "schedule" && state.calendar) {
    setTimeout(() => state.calendar.updateSize(), 150);
  }
  if (dashId === "employee" && tabId === "requests" && state.user?.role === "employee") {
    refreshEmployeeRequestLog();
  }
  if (dashId === "admin" && tabId === "records" && state.user?.role === "admin") {
    refreshAdminRequestLog();
  }
  if (dashId === "admin" && tabId === "approvals" && state.user?.role === "admin") {
    refreshManagerQueues();
  }
  if (dashId === "manager" && tabId === "approvals") {
    refreshManagerQueues();
    refreshManagerInlineApprovalsLog().catch(() => {});
  }
  if (dashId === "manager" && tabId === "records" && state.user?.role === "manager") {
    refreshManagerRequestLog();
  }
  if (dashId === "admin" && tabId === "org" && state.user?.role === "admin") {
    refreshAdminDeptList();
  }
  if (tabId === "tasks") {
    updateTasksBoardUiForRole();
    loadTaskAssigneeOptions().catch(() => {});
    refreshTasksBoard().catch(() => {});
  }
  if (tabId === "infovalley") {
    updateInfoValleyUiForRole();
    refreshInfoValleyBoard().catch(() => {});
  }
}

function applyRoleVisibility() {
  const role = state.user?.role || "employee";
  show($("dash-employee"), role === "employee");
  show($("dash-manager"), role === "manager");
  show($("dash-admin"), role === "admin");
  show($("admin-dash-overview"), role === "admin");
  show($("admin-cal-ctl"), role === "admin");
  show($("admin-table-ctl"), role === "admin");
  show($("bulk-dept-row"), role === "admin");
  show($("mgr-admin-dept-row"), role === "admin");
  show($("emp-schedule-quick"), role === "employee");
  updateTasksBoardUiForRole();
  updateInfoValleyUiForRole();

  const calHint = $("cal-scope-hint");
  if (calHint) {
    calHint.textContent =
      role === "admin"
        ? "Choose a department to load its shared calendar."
        : "You see everyone in your department — shifts and approved leave.";
  }
  const apprHint = $("approvals-scope-hint");
  if (apprHint) {
    apprHint.textContent =
      role === "admin"
        ? "Pending leave and shift-change requests from every department. Approve or reject here. Full history is under the Activity tab."
        : role === "manager"
          ? "Pending requests from your department only. After each action, the full audit trail updates in the Department activity log on this page and on the Approval log tab."
          : "";
  }
  const mgrActLog = $("mgr-approvals-activity-log");
  if (mgrActLog) show(mgrActLog, role === "manager");

  const apprHist = $("approvals-history-lead");
  if (apprHist) {
    if (role === "manager") {
      apprHist.textContent =
        "Every decision is saved below in the department activity log, and on the Approval log tab (same data).";
      apprHist.classList.remove("hidden");
    } else if (role === "admin") {
      apprHist.textContent =
        "Every decision is saved. Open the Activity tab for organisation-wide leave and shift-change history.";
      apprHist.classList.remove("hidden");
    } else {
      apprHist.textContent = "";
      apprHist.classList.add("hidden");
    }
  }
  updateDashboardBanner();
  if (role === "employee") activateDashTab("employee", "schedule");
  if (role === "manager") activateDashTab("manager", "schedule");
  if (role === "admin") activateDashTab("admin", "org");
}

document.querySelectorAll(".dash-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    activateDashTab(btn.dataset.dash, btn.dataset.tab);
  });
});

async function bootAuthenticated() {
  const me = await api("/api/auth/me");
  state.user = me;
  show($("auth-section"), false);
  show($("app-section"), true);
  show($("logout-btn"), true);
  $("user-slot").innerHTML = `<span class="badge ${me.role}">${me.role}</span> <strong>${escapeHtml(
    me.full_name,
  )}</strong> · ${escapeHtml(me.employee_id)} · ${escapeHtml(me.department_name || "")}`;

  const softFail = (label, err) => {
    console.error(`[RotaShift boot] ${label}:`, err);
  };

  try {
    await loadMeta();
  } catch (e) {
    softFail("loadMeta", e);
  }
  try {
    await loadDepartments();
  } catch (e) {
    softFail("loadDepartments", e);
  }

  mountScheduleForRole(state.user.role);
  mountShiftPanels(state.user.role);
  mountTasksModule(state.user.role);
  mountInfoValleyModule(state.user.role);

  if (state.user.role === "admin") {
    const pick = $("cal-dept");
    if (pick) {
      pick.onchange = () => {
        state.calendar?.refetchEvents();
      };
      if (!pick.value && state.departments[0]) pick.value = state.departments[0].id;
    }
    const tableDept = $("table-dept");
    if (tableDept) {
      tableDept.onchange = () => refreshTable();
      if (!tableDept.value && state.departments[0]) tableDept.value = state.departments[0].id;
    }
    const mgrDept = $("mgr-roster-dept");
    if (mgrDept && state.departments[0] && !mgrDept.value) mgrDept.value = state.departments[0].id;
    if (mgrDept) {
      mgrDept.onchange = () => {
        refreshManagerRoster();
      };
    }
    const taskDept = $("tasks-admin-dept");
    if (taskDept && state.departments[0] && !taskDept.value) taskDept.value = state.departments[0].id;
    if (taskDept) {
      taskDept.onchange = () => {
        loadTaskAssigneeOptions().catch(() => {});
        refreshTasksBoard().catch(() => {});
      };
    }
    const infoDept = $("infovalley-admin-dept") || $("infovally-admin-dept");
    if (infoDept && state.departments[0] && !infoDept.value) infoDept.value = state.departments[0].id;
    if (infoDept) {
      infoDept.onchange = () => {
        refreshInfoValleyBoard().catch(() => {});
      };
    }
  }

  applyRoleVisibility();

  if ($("mgr-assign-date") && !$("mgr-assign-date").value) {
    $("mgr-assign-date").value = new Date().toISOString().slice(0, 10);
  }
  const infoDateEl = $("infovalley-date") || $("infovally-date");
  if (infoDateEl && !infoDateEl.value) {
    infoDateEl.value = new Date().toISOString().slice(0, 10);
  }
  if (state.user.role === "manager" && $("mgr-roster-hint")) {
    $("mgr-roster-hint").textContent = `Everyone registered under your department (${escapeHtml(me.department_name || "")}).`;
  }
  setDefaultTableRange();
  try {
    initCalendar();
  } catch (e) {
    softFail("initCalendar", e);
    const calEl = $("calendar");
    if (calEl) {
      calEl.innerHTML = `<p class="error">Calendar failed to start: ${escapeHtml(e.message || String(e))}</p>`;
    }
    state.calendar = null;
  }

  const refreshSafe = async (label, fn) => {
    try {
      await fn();
    } catch (e) {
      softFail(label, e);
    }
  };
  await refreshSafe("refreshTable", () => refreshTable());
  await refreshSafe("refreshManagerRoster", () => refreshManagerRoster());
  await refreshSafe("refreshManagerQueues", () => refreshManagerQueues());
  await refreshSafe("refreshAdminUsers", () => refreshAdminUsers());
  await refreshSafe("refreshEmployeeRequestLog", () => refreshEmployeeRequestLog());
  await refreshSafe("refreshAdminDeptList", () => refreshAdminDeptList());
  await refreshSafe("refreshAdminRequestLog", () => refreshAdminRequestLog());
  await refreshSafe("refreshManagerRequestLog", () => refreshManagerRequestLog());
  await refreshSafe("refreshManagerInlineApprovalsLog", () => refreshManagerInlineApprovalsLog());
  await refreshSafe("refreshInfoValleyBoard", () => refreshInfoValleyBoard());

  const tr = $("table-refresh");
  if (tr) tr.onclick = () => refreshTable();

  $("mgr-records-refresh")?.addEventListener("click", () => refreshManagerRequestLog());

  const auf = $("admin-user-dept-filter");
  if (auf) auf.onchange = () => refreshAdminUsers();
  const ard = $("admin-records-dept");
  if (ard) ard.onchange = () => refreshAdminRequestLog();

  if (!state.adminShortcutsWired && state.user.role === "admin") {
    state.adminShortcutsWired = true;
    $("admin-go-schedule")?.addEventListener("click", () => activateDashTab("admin", "schedule"));
    $("admin-go-people")?.addEventListener("click", () => activateDashTab("admin", "people"));
    $("admin-go-approvals")?.addEventListener("click", () => activateDashTab("admin", "approvals"));
    $("admin-go-records")?.addEventListener("click", () => activateDashTab("admin", "records"));
    $("admin-go-shifts")?.addEventListener("click", () => activateDashTab("admin", "shifts"));
  }
}

async function tryRestoreSession() {
  if (!state.token) return;
  try {
    await bootAuthenticated();
  } catch (e) {
    logout();
    const le = $("login-err");
    if (le && e?.message) {
      le.textContent = e.message;
      le.classList.remove("hidden");
    }
  }
}

/* Auth UI */
$("tab-login").addEventListener("click", () => {
  $("tab-login").classList.add("active");
  $("tab-register").classList.remove("active");
  show($("panel-login"), true);
  show($("panel-register"), false);
  const ler = $("login-err");
  if (ler) {
    ler.classList.add("hidden");
    ler.textContent = "";
  }
});
$("tab-register").addEventListener("click", () => {
  $("tab-register").classList.add("active");
  $("tab-login").classList.remove("active");
  show($("panel-login"), false);
  show($("panel-register"), true);
  $("reg-err")?.classList.add("hidden");
  (async () => {
    try {
      await loadDepartments();
    } catch {
      /* e.g. cold start / network; department list may come from registration meta */
    }
    await loadRegistrationMeta();
  })();
});

document.querySelectorAll("#reg-role-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    regState.role = btn.dataset.role;
    document.querySelectorAll("#reg-role-tabs button").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    updateRegistrationFormUi();
  });
});

const regCodeToggle = $("reg-code-toggle");
const regCodeInput = $("reg-code");
if (regCodeToggle && regCodeInput) {
  regCodeToggle.addEventListener("click", () => {
    const isPwd = regCodeInput.type === "password";
    regCodeInput.type = isPwd ? "text" : "password";
    regCodeToggle.textContent = isPwd ? "Hide" : "Show";
  });
}

$("login-btn").addEventListener("click", async () => {
  $("login-err").classList.add("hidden");
  try {
    const body = {
      employee_id: $("login-emp").value.trim(),
      password: $("login-pass").value,
    };
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
    setToken(data.access_token, data.user);
    try {
      await bootAuthenticated();
      $("app-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (bootErr) {
      logout();
      throw bootErr;
    }
  } catch (e) {
    $("login-err").textContent = e.message;
    $("login-err").classList.remove("hidden");
  }
});

$("register-btn").addEventListener("click", async () => {
  const regBtn = $("register-btn");
  $("reg-err").classList.add("hidden");
  const prevLabel = regBtn?.textContent;
  if (regBtn) {
    regBtn.disabled = true;
    regBtn.textContent = "Creating account…";
  }
  try {
    await loadRegistrationMeta({ downgradeRole: false });

    const deptName = ($("reg-dept").value || "").trim().toLowerCase();
    if (!deptName) throw new Error("Select a department.");
    if (regState.role === "manager" && !regState.meta.manager_registration_enabled) {
      throw new Error(
        "Manager registration is not enabled on this server. Set ROTASHIFT_REGISTER_CODE_MANAGER in .env or environment and restart.",
      );
    }
    if (regState.role === "admin" && !regState.meta.admin_registration_enabled) {
      throw new Error(
        "Administrator registration is not enabled on this server. Set ROTASHIFT_REGISTER_CODE_ADMIN in .env or environment and restart.",
      );
    }
    const empId = $("reg-emp").value.trim();
    if (!empId) throw new Error("Enter an employee ID.");
    const fullName = $("reg-name").value.trim();
    if (!fullName) throw new Error("Enter your full name.");
    const pw = $("reg-pass").value;
    if (!pw || pw.length < 6) throw new Error("Password must be at least 6 characters.");
    const body = {
      employee_id: empId,
      password: pw,
      full_name: fullName,
      department_name: deptName,
      role: regState.role,
    };
    if (regState.role === "manager" || regState.role === "admin") {
      const code = $("reg-code")?.value?.trim() || "";
      if (!code) {
        throw new Error("Enter your organisation invite code in the highlighted section above.");
      }
      body.registration_code = code;
    }
    const data = await api("/api/auth/register", { method: "POST", body: JSON.stringify(body) });
    setToken(data.access_token, data.user);
    try {
      await bootAuthenticated();
      $("app-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (bootErr) {
      logout();
      throw bootErr;
    }
  } catch (e) {
    $("reg-err").textContent = e.message;
    $("reg-err").classList.remove("hidden");
  } finally {
    if (regBtn) {
      regBtn.disabled = false;
      regBtn.textContent = prevLabel || "Create account";
    }
  }
});

$("logout-btn").addEventListener("click", () => logout());

/* Employee */
$("leave-submit").addEventListener("click", async () => {
  try {
    if (!$("leave-start").value || !$("leave-end").value) {
      showEmployeeRequestNotice("Please choose start and end dates for leave.", "error");
      return;
    }
    const res = await api("/api/requests/leave", {
      method: "POST",
      body: JSON.stringify({
        start_date: $("leave-start").value,
        end_date: $("leave-end").value,
        reason: $("leave-reason").value,
      }),
    });
    showEmployeeRequestNotice(
      `Leave request submitted successfully. Reference id: ${res.id}. Status: ${res.status ?? "pending"} — your manager will review it. You can track it in the log below.`,
      "success",
    );
    $("leave-reason").value = "";
    await refreshEmployeeRequestLog();
    await refreshManagerQueues();
  } catch (e) {
    showEmployeeRequestNotice(e.message, "error");
  }
});

$("chg-submit").addEventListener("click", async () => {
  try {
    if (!$("chg-date").value) {
      showEmployeeRequestNotice("Please choose the date for the shift change.", "error");
      return;
    }
    const res = await api("/api/requests/shift-change", {
      method: "POST",
      body: JSON.stringify({
        date: $("chg-date").value,
        from_shift: $("chg-from").value,
        to_shift: $("chg-to").value,
        reason: $("chg-reason").value,
      }),
    });
    showEmployeeRequestNotice(
      `Shift change request submitted successfully. Reference id: ${res.id}. Status: ${res.status ?? "pending"} — your manager will review it. You can track it in the log below.`,
      "success",
    );
    $("chg-reason").value = "";
    await refreshEmployeeRequestLog();
    await refreshManagerQueues();
  } catch (e) {
    showEmployeeRequestNotice(e.message, "error");
  }
});

$("emp-log-refresh").addEventListener("click", () => {
  refreshEmployeeRequestLog();
});

$("mgr-roster-refresh").addEventListener("click", () => {
  refreshManagerRoster().catch((e) => {
    $("mgr-assign-msg").textContent = e.message;
  });
});

$("mgr-assign-submit").addEventListener("click", async () => {
  $("mgr-assign-msg").textContent = "";
  const emp = $("mgr-assign-member").value.trim();
  const date = $("mgr-assign-date").value;
  const shift = $("mgr-assign-shift").value;
  if (!emp) {
    $("mgr-assign-msg").textContent = "Select a team member.";
    return;
  }
  if (!date) {
    $("mgr-assign-msg").textContent = "Pick a date.";
    return;
  }
  const payload = {
    assignments: [{ employee_id: emp, date, shift_code: shift }],
  };
  if (state.user.role === "admin") {
    payload.department_id = $("mgr-roster-dept").value;
    if (!payload.department_id) {
      $("mgr-assign-msg").textContent = "Choose a department first.";
      return;
    }
  }
  $("mgr-assign-msg").textContent = "Saving…";
  try {
    const res = await api("/api/shifts/bulk", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("mgr-assign-msg").textContent = `Saved. (${res.upserted || 0} assignment(s))`;
    if (state.calendar) state.calendar.refetchEvents();
    await refreshTable();
  } catch (e) {
    $("mgr-assign-msg").textContent = e.message;
  }
});

/* Bulk */
$("bulk-submit").addEventListener("click", async () => {
  const raw = $("bulk-lines").value.trim();
  const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const assignments = [];
  const errs = [];
  lines.forEach((line, i) => {
    const parts = line.split(",").map((x) => x.trim());
    if (parts.length < 3) {
      errs.push(`Line ${i + 1}: need EMPLOYEE_ID,YYYY-MM-DD,CODE`);
      return;
    }
    assignments.push({
      employee_id: parts[0],
      date: parts[1],
      shift_code: parts[2].toUpperCase(),
    });
  });
  if (errs.length) {
    $("bulk-msg").textContent = errs.join(" ");
    return;
  }
  const payload = { assignments };
  if (state.user.role === "admin") {
    payload.department_id = $("bulk-dept").value;
    if (!payload.department_id) {
      $("bulk-msg").textContent = "Select department for bulk assignment.";
      return;
    }
  }
  $("bulk-msg").textContent = "Saving…";
  const res = await api("/api/shifts/bulk", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  $("bulk-msg").textContent = `Saved ${res.upserted} rows. ${(res.errors || []).length ? JSON.stringify(res.errors) : ""}`;
  $("bulk-lines").value = "";
  if (state.calendar) state.calendar.refetchEvents();
  await refreshTable();
});

/* Admin — create user */
$("admin-add-submit").addEventListener("click", async () => {
  const msg = $("admin-add-msg");
  msg.textContent = "";
  try {
    const deptId = $("admin-add-dept").value;
    if (!deptId) throw new Error("Select a department.");
    await api("/api/users", {
      method: "POST",
      body: JSON.stringify({
        employee_id: $("admin-add-emp").value.trim(),
        full_name: $("admin-add-name").value.trim(),
        password: $("admin-add-pass").value,
        department_id: deptId,
        role: $("admin-add-role").value,
      }),
    });
    msg.textContent = "User created. They can log in with their Employee ID and password.";
    $("admin-add-emp").value = "";
    $("admin-add-name").value = "";
    $("admin-add-pass").value = "";
    await refreshAdminUsers();
  } catch (e) {
    msg.textContent = e.message;
  }
});

/* Admin */
$("create-dept-btn").addEventListener("click", async () => {
  const msg = $("dept-msg");
  const name = $("new-dept-name").value.trim();
  if (!name) return;
  if (msg) msg.textContent = "";
  try {
    await api("/api/departments", { method: "POST", body: JSON.stringify({ name }) });
    $("new-dept-name").value = "";
    if (msg) msg.textContent = "Department created.";
    await loadDepartments();
    await refreshAdminDeptList();
  } catch (e) {
    if (msg) msg.textContent = e.message || String(e);
  }
});

$("tasks-refresh-btn")?.addEventListener("click", () => {
  refreshTasksBoard().catch((e) => alert(e.message || String(e)));
});

$("tasks-kanban-banner-dismiss")?.addEventListener("click", () => {
  setTasksKanbanBanner("", "");
});

$("task-create-btn")?.addEventListener("click", async () => {
  const msg = $("task-form-msg");
  if (msg) msg.textContent = "";
  const title = $("task-new-title")?.value?.trim();
  if (!title) {
    if (msg) msg.textContent = "Enter a title.";
    return;
  }
  const body = {
    title,
    description: $("task-new-desc")?.value || "",
    column: $("task-new-column")?.value || "todo",
    priority: parseInt($("task-new-priority")?.value || "3", 10),
    assignee_employee_ids: [...($("task-new-assignees")?.selectedOptions || [])].map((o) => o.value),
  };
  if (state.user.role === "admin") {
    body.department_id = $("tasks-admin-dept")?.value;
    if (!body.department_id) {
      if (msg) msg.textContent = "Pick a department for this task.";
      return;
    }
  }
  try {
    await postTaskCreate(body);
    $("task-new-title").value = "";
    $("task-new-desc").value = "";
    if (msg) msg.textContent = "Task added.";
    await refreshTasksBoard();
    await loadTaskAssigneeOptions();
  } catch (e) {
    if (msg) msg.textContent = e.message || String(e);
  }
});

$("infovalley-refresh")?.addEventListener("click", () => {
  refreshInfoValleyBoard().catch((e) => alert(e.message || String(e)));
});
$("infovally-refresh")?.addEventListener("click", () => {
  refreshInfoValleyBoard().catch((e) => alert(e.message || String(e)));
});

async function submitInfoValleyActivity() {
  const msg = $("infovalley-msg") || $("infovally-msg");
  if (msg) msg.textContent = "";
  const titleEl = $("infovalley-activity-title") || $("infovalley-title") || $("infovally-title");
  const detailsEl = $("infovalley-details") || $("infovally-details");
  const dateEl = $("infovalley-date") || $("infovally-date");
  const title = titleEl?.value?.trim() || "";
  const details = detailsEl?.value?.trim() || "";
  const activityDate = dateEl?.value || "";
  if (!activityDate || !title || !details) {
    if (msg) msg.textContent = "Fill date, title, and details.";
    return;
  }
  const body = { activity_date: activityDate, title, details };
  if (state.user?.role === "admin") {
    body.department_id = infovalleyScopeDeptId();
    if (!body.department_id) {
      if (msg) msg.textContent = "Pick a department first.";
      return;
    }
  }
  try {
    await postActivityCreate(body);
    if (titleEl) titleEl.value = "";
    if (detailsEl) detailsEl.value = "";
    if (msg) msg.textContent = "Activity posted.";
    await refreshInfoValleyBoard();
  } catch (e) {
    if (msg) msg.textContent = e.message || String(e);
  }
}

async function handleInfoValleyCommentClick(ev) {
  const btn = ev.target.closest("[data-infovalley-comment-btn]");
  if (!btn) return;
  const id = btn.getAttribute("data-infovalley-comment-btn");
  const input = document.querySelector(`[data-infovalley-comment-input="${id}"]`);
  const comment = input?.value?.trim() || "";
  if (!comment) return;
  try {
    await api(`/api/activities/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ comment }),
    });
    input.value = "";
    await refreshInfoValleyBoard();
  } catch (e) {
    alert(e.message || String(e));
  }
}

$("infovalley-submit")?.addEventListener("click", submitInfoValleyActivity);
$("infovally-submit")?.addEventListener("click", submitInfoValleyActivity);
$("infovalley-table")?.addEventListener("click", handleInfoValleyCommentClick);
$("infovally-table")?.addEventListener("click", handleInfoValleyCommentClick);

$("export-btn").addEventListener("click", async () => {
  const data = await api("/api/admin/export");
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `rotashift-export-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

$("admin-records-refresh")?.addEventListener("click", () => refreshAdminRequestLog());

$("emp-go-leave")?.addEventListener("click", () => {
  activateDashTab("employee", "requests");
  requestAnimationFrame(() => {
    $("leave-request-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

$("emp-go-shift-change")?.addEventListener("click", () => {
  activateDashTab("employee", "requests");
  requestAnimationFrame(() => {
    $("shift-change-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

initMatrixTableCellEditor();

tryRestoreSession();
if (!state.token) {
  loadDepartments().catch(() => {});
  loadRegistrationMeta().catch(() => {});
}
