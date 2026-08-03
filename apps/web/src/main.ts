import "./index.css";
import "./App.css";
import { cancelTransfer, createApproval, createExpense, createPurchaseOrder, db, dismissSyncIssue, fulfilTransfer, getSyncIssues, loadFromServer, markNotificationRead, pendingSyncCount, receivePurchaseOrder, receiveTransfer, recordReceipt, recordSale, recordTransfer, type Branch, type Notification, type Product, type PurchaseOrder, type StockMovement, type Supplier, type Transfer } from "./db/database";
import { getCurrentUser, login, logout, registerDevice, request, type AuthUser } from "./lib/api";

type View = "overview" | "catalogue" | "receipt" | "transfer" | "sale" | "approval" | "expense" | "ledger" | "purchase" | "notifications" | "users";
const TRANSFER_FULFIL_ROLES = ["owner", "store_keeper", "super_admin"];
const TRANSFER_RECEIVE_ROLES = ["owner", "store_keeper", "shop_manager", "super_admin"];
const VIEW_ROLES: Record<View, string[] | null> = {
  overview: null,
  catalogue: null,
  receipt: ["owner", "store_keeper", "super_admin"],
  transfer: ["owner", "store_keeper", "shop_manager", "super_admin"],
  sale: ["owner", "cashier", "shop_manager", "super_admin"],
  approval: ["owner", "accountant", "super_admin"],
  expense: ["owner", "accountant", "super_admin"],
  purchase: ["owner", "store_keeper", "super_admin"],
  notifications: null,
  ledger: null,
  users: ["owner", "super_admin"],
};
const canSee = (target: View) => !VIEW_ROLES[target] || VIEW_ROLES[target]!.includes(currentUser?.role ?? "");
let view: View = "overview";
let branches: Branch[] = [];
let products: Product[] = [];
let suppliers: Supplier[] = [];
let movements: StockMovement[] = [];
let transfers: Transfer[] = [];
let purchaseOrders: PurchaseOrder[] = [];
let notifications: Notification[] = [];
let adminUsers: AuthUser[] = [];
let currentUser: AuthUser | null = null;
let pendingSync = 0;
const money = new Intl.NumberFormat("en-ZW", { style: "currency", currency: "USD" });
const root = document.querySelector<HTMLDivElement>("#root")!;
const escape = (value: string) => value.replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char] ?? char);
const branch = (kind: Branch["type"]) => branches.find((item) => item.type === kind);
const product = (id: string) => products.find((item) => item.id === id);
const stock = (productId: string, branchId?: string) => movements.filter((item) => item.productId === productId && item.branchId === branchId).reduce((sum, item) => sum + item.quantity, 0);

async function refresh() {
  branches = await db.branches.toArray();
  products = await db.products.toArray();
  suppliers = await db.suppliers.toArray();
  movements = await db.movements.orderBy("createdAt").reverse().toArray();
  transfers = await db.transfers.orderBy("requestedAt").reverse().toArray();
  purchaseOrders = await db.purchaseOrders.orderBy("requestedAt").reverse().toArray();
  notifications = await db.notifications.orderBy("createdAt").reverse().toArray();
  pendingSync = await pendingSyncCount();
  if (canSee("users")) {
    adminUsers = (await request("/users").catch(() => [])) as AuthUser[];
  }
}

async function sync() {
  if (!currentUser) {
    renderLogin();
    return;
  }

  try {
    await loadFromServer();
    await refresh();
    render("Live database connected. Pending transactions were synced.");
  } catch {
    await refresh();
    render("Offline mode: cached data is available and transactions will queue.");
  }
}

async function initialize() {
  try {
    const response = await getCurrentUser();
    currentUser = response.user;
    await registerDevice();
    await sync();
  } catch {
    currentUser = null;
    renderLogin("Please sign in to continue.");
  }
}

function nav() {
  return ([
    ["overview", "Overview"],
    ["catalogue", "Parts & fitment"],
    ["receipt", "Receive stock"],
    ["transfer", "Branch transfer"],
    ["sale", "Record sale"],
    ["approval", "Approvals"],
    ["expense", "Expenses"],
    ["purchase", "Purchasing"],
    ["notifications", "Notifications"],
    ["ledger", "Audit ledger"],
    ["users", "Users"],
  ] as [View, string][]).filter(([id]) => canSee(id)).map(([id, label]) => `<button data-view="${id}" class="${view === id ? "active" : ""}">${label}</button>`).join("");
}

function options(rows: { id: string; name: string }[]) {
  return rows.map((row) => `<option value="${escape(row.id)}">${escape(row.name)}</option>`).join("");
}

function title() {
  return ({
    overview: "Morning control room",
    catalogue: "Parts & vehicle fitment",
    receipt: "Receive supplier stock",
    transfer: "Request, fulfil & receive branch transfers",
    sale: "Record a shop sale",
    approval: "Approval queue",
    expense: "Record operating expense",
    purchase: "Purchasing workflow",
    notifications: "Operator inbox",
    ledger: "Immutable stock ledger",
    users: "User management",
  })[view];
}
function content() {
  const warehouse = branch("warehouse"); const shop = branch("shop");
  if (view === "overview") { const low = products.filter((item) => stock(item.id, shop?.id) <= item.reorderLevel); const sales = movements.filter((item) => item.kind === "sale").reduce((sum, item) => sum + (product(item.productId)?.sellingPrice ?? 0) * -item.quantity, 0); return `<div class="metrics"><section class="metric"><small>Parts catalogued</small><strong>${products.length}</strong><span>with exact vehicle fitment</span></section><section class="metric"><small>Sales recorded</small><strong>${money.format(sales)}</strong><span>from the stock ledger</span></section><section class="metric"><small>Needs replenishment</small><strong>${low.length}</strong><span>at or below shop reorder level</span></section></div><div class="grid"><section class="card"><div class="card-title"><h2>Shop replenishment watch</h2><span>${escape(shop?.name ?? "No shop")}</span></div>${low.map((item) => `<div class="row"><div><strong>${escape(item.name)}</strong><small>${escape(item.fitment)}</small></div><b class="danger">${stock(item.id, shop?.id)} left</b></div>`).join("") || "<p>No low-stock parts.</p>"}</section><section class="card"><div class="card-title"><h2>Transfer evidence</h2><span>${transfers.length} recorded</span></div>${transfers.slice(0, 4).map((item) => `<div class="row"><div><strong>${escape(item.id)} · ${escape(product(item.productId)?.name ?? "Part")}</strong><small>${item.quantity} units</small></div><b class="good">${escape(item.status)}</b></div>`).join("") || "<p>No transfers yet.</p>"}</section><section class="card"><div class="card-title"><h2>Purchasing & inbox</h2><span>${purchaseOrders.filter((order) => order.status === "pending").length} pending orders</span></div><div class="row"><div><strong>${purchaseOrders.filter((order) => order.status === "pending").length} pending purchase orders</strong><small>${notifications.filter((item) => !item.read).length} unread notifications</small></div></div></section></div>`; }
  if (view === "catalogue") return `<section class="card table-card"><div class="card-title"><h2>Fitment-first catalogue</h2><span>${products.length} active parts</span></div><table><thead><tr><th>Part / SKU</th><th>Vehicle fitment</th><th>Warehouse</th><th>Shop</th><th>Sell</th></tr></thead><tbody>${products.map((item) => `<tr><td><strong>${escape(item.name)}</strong><small>${escape(item.brand)} · ${escape(item.sku)}</small></td><td>${escape(item.fitment)}</td><td>${stock(item.id, warehouse?.id)}</td><td>${stock(item.id, shop?.id)}</td><td>${money.format(item.sellingPrice)}</td></tr>`).join("")}</tbody></table></section>`;
  if (view === "receipt") return form("Goods receipt", "receipt-form", `<label>Part<select name="product">${options(products)}</select></label><label>Supplier<select name="supplier">${options(suppliers)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label>`, "Post goods receipt");
  if (view === "transfer") {
    const canFulfil = TRANSFER_FULFIL_ROLES.includes(currentUser?.role ?? "");
    const canReceive = TRANSFER_RECEIVE_ROLES.includes(currentUser?.role ?? "");
    const requested = transfers.filter((item) => item.status === "requested");
    const inTransit = transfers.filter((item) => item.status === "in_transit");
    return `${form("Request a branch transfer", "transfer-form", `<label>Part<select name="product">${options(products)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label><p class="hint">Creates a pending request; stock only moves once fulfilled and received.</p>`, "Request transfer")}<section class="card table-card"><div class="card-title"><h2>Awaiting fulfilment</h2><span>${requested.length} pending</span></div><table><thead><tr><th>Reference</th><th>Part</th><th>Quantity</th><th>Action</th></tr></thead><tbody>${requested.map((item) => `<tr><td>${escape(item.reference)}</td><td>${escape(product(item.productId)?.name ?? "Part")}</td><td>${item.quantity}</td><td>${canFulfil ? `<button class="secondary fulfil-transfer" data-transfer-id="${escape(item.id)}" data-transfer-reference="${escape(item.reference)}" data-transfer-version="${item.version}">Fulfil</button> <button class="secondary cancel-transfer" data-transfer-id="${escape(item.id)}">Cancel</button>` : "—"}</td></tr>`).join("") || "<tr><td colspan=\"4\">No pending requests.</td></tr>"}</tbody></table></section><section class="card table-card"><div class="card-title"><h2>In transit</h2><span>${inTransit.length} awaiting receipt</span></div><table><thead><tr><th>Reference</th><th>Part</th><th>Quantity</th><th>Action</th></tr></thead><tbody>${inTransit.map((item) => `<tr><td>${escape(item.reference)}</td><td>${escape(product(item.productId)?.name ?? "Part")}</td><td>${item.quantity}</td><td>${canReceive ? `<button class="secondary receive-transfer" data-transfer-id="${escape(item.id)}" data-transfer-reference="${escape(item.reference)}" data-transfer-version="${item.version}">Receive</button>` : "—"}</td></tr>`).join("") || "<tr><td colspan=\"4\">Nothing in transit.</td></tr>"}</tbody></table></section>`;
  }
  if (view === "sale") return form("Counter sale", "sale-form", `<label>Part<select name="product">${options(products)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label><p class="hint">Only available shop stock can be issued.</p>`, "Issue sale receipt");
  if (view === "approval") return form("Request an approval", "approval-form", `<label>Type<select name="type"><option>stock adjustment</option><option>price change</option><option>expense</option></select></label><label>Subject<input name="subject" required></label><label>Requester<input name="requester" required></label><label>Approver<input name="approver" required></label><label>Priority<select name="priority"><option>normal</option><option>high</option></select></label>`, "Send for approval");
  if (view === "expense") return form("Record an operating expense", "expense-form", `<label>Branch<select name="branch">${options(branches)}</select></label><label>Category<select name="category"><option>delivery</option><option>utilities</option><option>maintenance</option></select></label><label>Amount (USD)<input name="amount" type="number" min="0.01" step="0.01" required></label><label>Description<input name="description" required></label>`, "Record expense");
  if (view === "purchase") return `<section class="form-card"><p class="eyebrow">Planned replenishment</p><h2>Request a purchase order</h2><form id="purchase-form" class="form"><label>Supplier<select name="supplier">${options(suppliers)}</select></label><label>Branch<select name="branch">${options(branches)}</select></label><label>Part<select name="product">${options(products)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label><label>Unit cost<input name="unit-cost" type="number" min="0.01" step="0.01" value="5" required></label><label>Notes<input name="notes"></label><button class="primary">Create purchase order</button></form></section><section class="card table-card"><div class="card-title"><h2>Open purchase orders</h2><span>${purchaseOrders.filter((order) => order.status === "pending").length} waiting to receive</span></div><table><thead><tr><th>Reference</th><th>Supplier</th><th>Notes</th><th>Status</th><th>Action</th></tr></thead><tbody>${purchaseOrders.map((order) => `<tr><td>${escape(order.reference)}</td><td>${escape(suppliers.find((supplier) => supplier.id === order.supplierId)?.name ?? order.supplierId)}</td><td>${escape(order.notes)}</td><td>${escape(order.status)}</td><td>${order.status === "pending" ? `<button class="secondary receive-order" data-order-id="${escape(order.id)}" data-order-reference="${escape(order.reference)}" data-order-version="${order.version}">Receive</button>` : "—"}</td></tr>`).join("")}</tbody></table></section>`;
  if (view === "notifications") return `<section class="card table-card"><div class="card-title"><h2>Operator inbox</h2><span>${notifications.filter((item) => !item.read).length} unread</span></div>${notifications.map((item) => `<div class="row"><div><strong>${escape(item.title)}</strong><small>${escape(item.body)}</small></div><div>${item.read ? '<b class="good">Read</b>' : `<button class="secondary mark-read" data-notification-id="${escape(item.id)}">Mark read</button>`}</div></div>`).join("") || "<p>No notifications yet.</p>"}</section>`;
  if (view === "users") return `<section class="form-card"><p class="eyebrow">Access control</p><h2>Create an operator account</h2><form id="user-form" class="form"><label>Full name<input name="full_name" required></label><label>Email<input name="email" type="email" required></label><label>Temporary password<input name="password" type="password" required></label><label>Role<select name="role"><option value="owner">Owner</option><option value="accountant">Accountant</option><option value="store_keeper">Store Keeper</option><option value="shop_manager">Shop Manager</option><option value="cashier" selected>Cashier</option><option value="super_admin">Super Admin</option></select></label><label>Branch<select name="branch"><option value="">Unassigned</option>${options(branches)}</select></label><button class="primary">Create user</button></form></section><section class="card table-card"><div class="card-title"><h2>Operator roster</h2><span>${adminUsers.length} accounts</span></div><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Branch</th><th>Status</th></tr></thead><tbody>${adminUsers.map((item) => `<tr><td>${escape(item.full_name)}</td><td>${escape(item.email)}</td><td><span class="pill">${escape(item.role)}</span></td><td>${escape(branches.find((entry) => entry.id === item.branch_id)?.name ?? "—")}</td><td>${item.is_active ? '<b class="good">Active</b>' : '<b class="danger">Disabled</b>'}</td></tr>`).join("")}</tbody></table></section>`;
  return `<section class="card table-card"><div class="card-title"><h2>Movement evidence</h2><span>Append-only · newest first</span></div><table><thead><tr><th>Time</th><th>Part</th><th>Branch</th><th>Event</th><th>Change</th><th>Reference</th></tr></thead><tbody>${movements.map((item) => `<tr><td>${new Date(item.createdAt).toLocaleString()}</td><td>${escape(product(item.productId)?.name ?? "Part")}</td><td>${escape(branches.find((entry) => entry.id === item.branchId)?.name ?? "Branch")}</td><td><span class="pill">${escape(item.kind.replace("_", " "))}</span></td><td class="${item.quantity > 0 ? "positive" : "negative"}">${item.quantity > 0 ? "+" : ""}${item.quantity}</td><td>${escape(item.reference)}</td></tr>`).join("")}</tbody></table></section>`;
}
function form(heading: string, id: string, fields: string, action: string) {
  return `<section class="form-card"><p class="eyebrow">Controlled transaction</p><h2>${heading}</h2><p>Information is persisted in the operational database and safely queued if this device is offline.</p><form id="${id}" class="form">${fields}<button class="primary">${action}</button></form></section>`;
}

function renderLogin(message = "") {
  root.innerHTML = `<main class="shell auth-shell"><section class="card auth-card"><p class="eyebrow">Secure operator sign-in</p><h1>Access the spare parts control room</h1><p>Sign in with your pilot credentials to load the live database and sync transactions.</p><form id="login-form" class="form"><label>Email<input name="email" type="email" required></label><label>Password<input name="password" type="password" required></label><button class="primary">Sign in</button></form>${message ? `<p class="notice">${escape(message)}</p>` : ""}</section></main>`;
  bind();
}

function render(notice = "") {
  if (!currentUser) {
    renderLogin(notice);
    return;
  }

  const syncIssues = getSyncIssues();
  const syncIssuesPanel = syncIssues.length
    ? `<section class="card table-card sync-issues"><div class="card-title"><h2>Sync issues</h2><span>${syncIssues.length} unresolved</span></div>${syncIssues.map((issue) => `<div class="row"><div><strong>${escape(issue.reference)}</strong><small>${escape(issue.message)}</small></div><button class="secondary dismiss-sync-issue" data-issue-id="${escape(issue.id)}">Refresh &amp; dismiss</button></div>`).join("")}</section>`
    : "";
  root.innerHTML = `<main class="shell"><aside class="sidebar"><div class="brand"><span>SP</span><div><strong>SparePilot</strong><small>operations desk</small></div></div><nav>${nav()}</nav><div class="operator"><b>${escape(currentUser.full_name.slice(0, 2).toUpperCase())}</b><div><strong>${escape(currentUser.full_name)}</strong><small>${escape(currentUser.role)} · ${pendingSync ? `${pendingSync} pending` : "synced"}</small></div><button id="logout-button" class="secondary">Sign out</button></div></aside><section class="content"><header><div><p class="eyebrow">Harare pilot · warehouse / retail branch</p><h1>${title()}</h1></div><div class="sync"><i></i>${pendingSync ? `${pendingSync} pending` : "Local-first"}</div></header>${notice ? `<p class="notice">${escape(notice)}</p>` : ""}${syncIssuesPanel}${content()}</section></main>`;
  bind();
}

function value(form: HTMLFormElement, name: string) {
  return new FormData(form).get(name)?.toString() ?? "";
}

function bind() {
  root.querySelector<HTMLFormElement>("#login-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    try {
      await login(value(form, "email"), value(form, "password"));
      const response = await getCurrentUser();
      currentUser = response.user;
      await sync();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Authentication failed";
      renderLogin(message);
    }
  });

  root.querySelector<HTMLButtonElement>("#logout-button")?.addEventListener("click", async () => {
    await logout();
    currentUser = null;
    renderLogin("Signed out.");
  });

  root.querySelectorAll<HTMLButtonElement>("[data-view]").forEach((button) => button.addEventListener("click", () => {
    view = button.dataset.view as View;
    render();
  }));

  root.querySelector<HTMLFormElement>("#receipt-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await recordReceipt(value(form, "product"), branch("warehouse")!.id, Number(value(form, "quantity")), value(form, "supplier"));
    await refresh();
    view = "ledger";
    render("Receipt saved or queued for sync.");
  });

  root.querySelector<HTMLFormElement>("#transfer-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await recordTransfer(value(form, "product"), branch("warehouse")!.id, branch("shop")!.id, Number(value(form, "quantity")));
    await refresh();
    view = "transfer";
    render("Transfer requested or queued for sync.");
  });

  root.querySelectorAll<HTMLButtonElement>(".fulfil-transfer").forEach((button) => button.addEventListener("click", async () => {
    const transferId = button.dataset.transferId;
    if (!transferId) return;
    try {
      await fulfilTransfer(transferId, button.dataset.transferReference ?? transferId, Number(button.dataset.transferVersion ?? 0));
      await refresh();
      view = "transfer";
      render("Transfer fulfilled and stock moved out.");
    } catch (error) {
      await refresh();
      render(error instanceof Error ? error.message : "Could not fulfil transfer.");
    }
  }));

  root.querySelectorAll<HTMLButtonElement>(".receive-transfer").forEach((button) => button.addEventListener("click", async () => {
    const transferId = button.dataset.transferId;
    if (!transferId) return;
    try {
      await receiveTransfer(transferId, button.dataset.transferReference ?? transferId, Number(button.dataset.transferVersion ?? 0));
      await refresh();
      view = "ledger";
      render("Transfer received into stock.");
    } catch (error) {
      await refresh();
      render(error instanceof Error ? error.message : "Could not receive transfer.");
    }
  }));

  root.querySelectorAll<HTMLButtonElement>(".dismiss-sync-issue").forEach((button) => button.addEventListener("click", () => {
    const issueId = button.dataset.issueId;
    if (!issueId) return;
    dismissSyncIssue(issueId);
    render();
  }));

  root.querySelectorAll<HTMLButtonElement>(".cancel-transfer").forEach((button) => button.addEventListener("click", async () => {
    const transferId = button.dataset.transferId;
    if (!transferId) return;
    try {
      await cancelTransfer(transferId);
      await refresh();
      view = "transfer";
      render("Transfer cancelled.");
    } catch (error) {
      render(error instanceof Error ? error.message : "Could not cancel transfer.");
    }
  }));

  root.querySelector<HTMLFormElement>("#sale-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    const selected = product(value(form, "product"));
    if (!selected || !branch("shop")) return;
    await recordSale(selected, branch("shop")!.id, Number(value(form, "quantity")));
    await refresh();
    view = "ledger";
    render("Sale saved or queued for sync.");
  });

  root.querySelector<HTMLFormElement>("#approval-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await createApproval(value(form, "type"), value(form, "subject"), value(form, "requester"), value(form, "approver"), value(form, "priority"));
    render("Approval request sent.");
  });

  root.querySelector<HTMLFormElement>("#expense-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await createExpense(value(form, "branch"), value(form, "category"), Number(value(form, "amount")), value(form, "description"));
    render("Expense recorded.");
  });

  root.querySelector<HTMLFormElement>("#purchase-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await createPurchaseOrder(value(form, "supplier"), value(form, "branch"), value(form, "notes"), [{ productId: value(form, "product"), quantity: Number(value(form, "quantity")), unitCost: Number(value(form, "unit-cost")) }]);
    await refresh();
    view = "purchase";
    render("Purchase order queued or synced.");
  });

  root.querySelector<HTMLFormElement>("#user-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    try {
      await request("/users", { method: "POST", body: JSON.stringify({ full_name: value(form, "full_name"), email: value(form, "email"), password: value(form, "password"), role: value(form, "role"), branch_id: value(form, "branch") || null }) });
      await refresh();
      view = "users";
      render("User account created.");
    } catch (error) {
      render(error instanceof Error ? error.message : "Could not create user.");
    }
  });

  root.querySelectorAll<HTMLButtonElement>(".receive-order").forEach((button) => button.addEventListener("click", async () => {
    const orderId = button.dataset.orderId;
    if (!orderId) return;
    try {
      await receivePurchaseOrder(orderId, button.dataset.orderReference ?? orderId, Number(button.dataset.orderVersion ?? 0));
      await refresh();
      view = "purchase";
      render("Purchase order received into stock.");
    } catch (error) {
      await refresh();
      render(error instanceof Error ? error.message : "Could not receive purchase order.");
    }
  }));

  root.querySelectorAll<HTMLButtonElement>(".mark-read").forEach((button) => button.addEventListener("click", async () => {
    const notificationId = button.dataset.notificationId;
    if (!notificationId) return;
    await markNotificationRead(notificationId);
    await refresh();
    view = "notifications";
    render("Notification marked as read.");
  }));
}

window.addEventListener("online", () => {
  void sync();
});

void initialize();
