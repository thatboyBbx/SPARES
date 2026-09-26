import "./index.css";
import "./App.css";
import { approveApproval, approveExpense, approvePurchaseOrder, cancelTransfer, createApproval, createCustomer, createExpense, createPurchaseOrder, createSupplierPayment, db, dismissSyncIssue, enablePushNotifications, fetchApprovalsSummary, fetchBranchPerformance, fetchDailySales, fetchExpensesSummary, fetchProfitLoss, fetchReplenishmentSuggestions, fulfilTransfer, getSyncIssues, loadFromServer, markNotificationRead, pendingSyncCount, pushNotificationsAvailable, receivePurchaseOrder, receiveTransfer, recordReceipt, recordSale, recordTransfer, rejectApproval, rejectExpense, type Approval, type ApprovalsSummary, type Branch, type BranchPerformance, type Customer, type Expense, type ExpensesSummary, type JournalEntry, type Notification, type Product, type ProfitLoss, type PurchaseOrder, type ReplenishmentSuggestion, type StockMovement, type Supplier, type SupplierPayment, type Transfer } from "./db/database";
import { getCurrentUser, login, logout, registerDevice, request, type AuthUser } from "./lib/api";

type View = "overview" | "catalogue" | "receipt" | "transfer" | "sale" | "approval" | "expense" | "ledger" | "purchase" | "accounting" | "replenishment" | "notifications" | "reports" | "users";
const TRANSFER_FULFIL_ROLES = ["owner", "store_keeper", "super_admin"];
const TRANSFER_RECEIVE_ROLES = ["owner", "store_keeper", "shop_manager", "super_admin"];
const PURCHASE_ORDER_APPROVE_ROLES = ["owner", "accountant", "super_admin"];
const ACCOUNTING_ROLES = ["owner", "accountant", "super_admin"];
const REPLENISHMENT_ROLES = ["owner", "store_keeper", "shop_manager", "super_admin"];
const ANALYTICS_ROLES = ["owner", "accountant", "shop_manager", "super_admin"];
const VIEW_ROLES: Record<View, string[] | null> = {
  overview: null,
  catalogue: null,
  receipt: ["owner", "store_keeper", "super_admin"],
  transfer: ["owner", "store_keeper", "shop_manager", "super_admin"],
  sale: ["owner", "cashier", "shop_manager", "super_admin"],
  approval: ["owner", "accountant", "super_admin"],
  expense: ["owner", "accountant", "super_admin"],
  purchase: ["owner", "store_keeper", "super_admin"],
  accounting: ACCOUNTING_ROLES,
  replenishment: REPLENISHMENT_ROLES,
  notifications: null,
  ledger: null,
  reports: null,
  users: ["owner", "super_admin"],
};
const canSee = (target: View) => !VIEW_ROLES[target] || VIEW_ROLES[target]!.includes(currentUser?.role ?? "");
const hasRole = (roles: string[]) => roles.includes(currentUser?.role ?? "");
let view: View = "overview";
let branches: Branch[] = [];
let products: Product[] = [];
let suppliers: Supplier[] = [];
let movements: StockMovement[] = [];
let transfers: Transfer[] = [];
let purchaseOrders: PurchaseOrder[] = [];
let notifications: Notification[] = [];
let customers: Customer[] = [];
let approvals: Approval[] = [];
let expenses: Expense[] = [];
let supplierPayments: SupplierPayment[] = [];
let journalEntries: JournalEntry[] = [];
let dailySales: Array<{ day: string; sales_count: number; sales_total: number }> = [];
let profitLoss: ProfitLoss | null = null;
let branchPerformance: BranchPerformance[] = [];
let expensesSummary: ExpensesSummary | null = null;
let approvalsSummary: ApprovalsSummary | null = null;
let replenishmentSuggestions: ReplenishmentSuggestion[] = [];
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
  customers = await db.customers.toArray();
  approvals = await db.approvals.toArray();
  expenses = await db.expenses.toArray();
  supplierPayments = await db.supplierPayments.orderBy("createdAt").reverse().toArray();
  journalEntries = await db.journalEntries.orderBy("createdAt").reverse().toArray();
  pendingSync = await pendingSyncCount();
  if (canSee("users")) {
    adminUsers = (await request("/users").catch(() => [])) as AuthUser[];
  }
  dailySales = await fetchDailySales().catch(() => []);
  if (hasRole(ACCOUNTING_ROLES)) {
    profitLoss = await fetchProfitLoss().catch(() => null);
    expensesSummary = await fetchExpensesSummary().catch(() => null);
  }
  if (hasRole(ANALYTICS_ROLES)) {
    branchPerformance = await fetchBranchPerformance().catch(() => []);
    approvalsSummary = await fetchApprovalsSummary().catch(() => null);
  }
  if (hasRole(REPLENISHMENT_ROLES)) {
    replenishmentSuggestions = await fetchReplenishmentSuggestions().catch(() => []);
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
    ["accounting", "Accounting"],
    ["replenishment", "Replenishment"],
    ["notifications", "Notifications"],
    ["ledger", "Audit ledger"],
    ["reports", "Reports & analytics"],
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
    accounting: "Supplier payments & journal ledger",
    replenishment: "Replenishment suggestions",
    notifications: "Operator inbox",
    ledger: "Immutable stock ledger",
    reports: "Reports & analytics",
    users: "User management",
  })[view];
}
function content() {
  const warehouse = branch("warehouse"); const shop = branch("shop");
  if (view === "overview") { const low = products.filter((item) => stock(item.id, shop?.id) <= item.reorderLevel); const sales = movements.filter((item) => item.kind === "sale").reduce((sum, item) => sum + (product(item.productId)?.sellingPrice ?? 0) * -item.quantity, 0); return `<div class="metrics"><section class="metric"><small>Parts catalogued</small><strong>${products.length}</strong><span>with exact vehicle fitment</span></section><section class="metric"><small>Sales recorded</small><strong>${money.format(sales)}</strong><span>from the stock ledger</span></section><section class="metric"><small>Needs replenishment</small><strong>${low.length}</strong><span>at or below shop reorder level</span></section></div><div class="grid"><section class="card"><div class="card-title"><h2>Shop replenishment watch</h2><span>${escape(shop?.name ?? "No shop")}</span></div>${low.map((item) => `<div class="row"><div><strong>${escape(item.name)}</strong><small>${escape(item.fitment)}</small></div><b class="danger">${stock(item.id, shop?.id)} left</b></div>`).join("") || "<p>No low-stock parts.</p>"}</section><section class="card"><div class="card-title"><h2>Transfer evidence</h2><span>${transfers.length} recorded</span></div>${transfers.slice(0, 4).map((item) => `<div class="row"><div><strong>${escape(item.id)} · ${escape(product(item.productId)?.name ?? "Part")}</strong><small>${item.quantity} units</small></div><b class="good">${escape(item.status)}</b></div>`).join("") || "<p>No transfers yet.</p>"}</section><section class="card"><div class="card-title"><h2>Purchasing & inbox</h2><span>${purchaseOrders.filter((order) => order.status !== "received").length} pending orders</span></div><div class="row"><div><strong>${purchaseOrders.filter((order) => order.status !== "received").length} pending purchase orders</strong><small>${notifications.filter((item) => !item.read).length} unread notifications</small></div></div></section></div>`; }
  if (view === "catalogue") return `<section class="card table-card"><div class="card-title"><h2>Fitment-first catalogue</h2><span>${products.length} active parts</span></div><table><thead><tr><th>Part / SKU</th><th>Vehicle fitment</th><th>Warehouse</th><th>Shop</th><th>Sell</th></tr></thead><tbody>${products.map((item) => `<tr><td><strong>${escape(item.name)}</strong><small>${escape(item.brand)} · ${escape(item.sku)}</small></td><td>${escape(item.fitment)}</td><td>${stock(item.id, warehouse?.id)}</td><td>${stock(item.id, shop?.id)}</td><td>${money.format(item.sellingPrice)}</td></tr>`).join("")}</tbody></table></section>`;
  if (view === "receipt") return form("Goods receipt", "receipt-form", `<label>Part<select name="product">${options(products)}</select></label><label>Supplier<select name="supplier">${options(suppliers)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label>`, "Post goods receipt");
  if (view === "transfer") {
    const canFulfil = TRANSFER_FULFIL_ROLES.includes(currentUser?.role ?? "");
    const canReceive = TRANSFER_RECEIVE_ROLES.includes(currentUser?.role ?? "");
    const requested = transfers.filter((item) => item.status === "requested");
    const inTransit = transfers.filter((item) => item.status === "in_transit");
    return `${form("Request a branch transfer", "transfer-form", `<label>Part<select name="product">${options(products)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label><p class="hint">Creates a pending request; stock only moves once fulfilled and received.</p>`, "Request transfer")}<section class="card table-card"><div class="card-title"><h2>Awaiting fulfilment</h2><span>${requested.length} pending</span></div><table><thead><tr><th>Reference</th><th>Part</th><th>Quantity</th><th>Action</th></tr></thead><tbody>${requested.map((item) => `<tr><td>${escape(item.reference)}</td><td>${escape(product(item.productId)?.name ?? "Part")}</td><td>${item.quantity}</td><td>${canFulfil ? `<button class="secondary fulfil-transfer" data-transfer-id="${escape(item.id)}" data-transfer-reference="${escape(item.reference)}" data-transfer-version="${item.version}">Fulfil</button> <button class="secondary cancel-transfer" data-transfer-id="${escape(item.id)}">Cancel</button>` : "—"}</td></tr>`).join("") || "<tr><td colspan=\"4\">No pending requests.</td></tr>"}</tbody></table></section><section class="card table-card"><div class="card-title"><h2>In transit</h2><span>${inTransit.length} awaiting receipt</span></div><table><thead><tr><th>Reference</th><th>Part</th><th>Quantity</th><th>Action</th></tr></thead><tbody>${inTransit.map((item) => `<tr><td>${escape(item.reference)}</td><td>${escape(product(item.productId)?.name ?? "Part")}</td><td>${item.quantity}</td><td>${canReceive ? `<button class="secondary receive-transfer" data-transfer-id="${escape(item.id)}" data-transfer-reference="${escape(item.reference)}" data-transfer-version="${item.version}">Receive</button>` : "—"}</td></tr>`).join("") || "<tr><td colspan=\"4\">Nothing in transit.</td></tr>"}</tbody></table></section>`;
  }
  if (view === "sale") return `${form("Counter sale", "sale-form", `<label>Part<select name="product">${options(products)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label><label>Customer (optional)<select name="customer"><option value="">Walk-in customer</option>${options(customers)}</select></label><p class="hint">Only available shop stock can be issued.</p>`, "Issue sale receipt")}<section class="card table-card"><div class="card-title"><h2>Add a customer</h2><span>${customers.length} on file</span></div><form id="customer-form" class="form"><label>Name<input name="name" required></label><label>Phone<input name="phone"></label><button class="primary">Add customer</button></form></section>`;
  if (view === "approval") return `${form("Request an approval", "approval-form", `<label>Type<select name="type"><option>stock adjustment</option><option>price change</option><option>expense</option></select></label><label>Subject<input name="subject" required></label><label>Approver<input name="approver" required></label><label>Priority<select name="priority"><option>normal</option><option>high</option></select></label>`, "Send for approval")}<section class="card table-card"><div class="card-title"><h2>Approval queue</h2><span>${approvals.filter((item) => item.status === "pending").length} pending</span></div><table><thead><tr><th>Subject</th><th>Requester</th><th>Priority</th><th>Status</th><th>Action</th></tr></thead><tbody>${approvals.map((item) => `<tr><td>${escape(item.subject)}${item.isOverdue ? ' <b class="danger">overdue</b>' : ""}</td><td>${escape(item.requester)}</td><td><span class="pill">${escape(item.priority)}</span></td><td><span class="pill">${escape(item.status)}</span></td><td>${item.status === "pending" ? `<button class="secondary approve-approval" data-approval-id="${escape(item.id)}">Approve</button> <button class="secondary reject-approval" data-approval-id="${escape(item.id)}">Reject</button>` : "—"}</td></tr>`).join("") || "<tr><td colspan=\"5\">No approvals yet.</td></tr>"}</tbody></table></section>`;
  if (view === "expense") return `${form("Record an operating expense", "expense-form", `<label>Branch<select name="branch">${options(branches)}</select></label><label>Category<select name="category"><option>delivery</option><option>utilities</option><option>maintenance</option></select></label><label>Amount (USD)<input name="amount" type="number" min="0.01" step="0.01" required></label><label>Description<input name="description" required></label>`, "Record expense")}<section class="card table-card"><div class="card-title"><h2>Expenses</h2><span>${expenses.filter((item) => item.status === "pending").length} pending</span></div><table><thead><tr><th>Category</th><th>Description</th><th>Amount</th><th>Status</th><th>Action</th></tr></thead><tbody>${expenses.map((item) => `<tr><td>${escape(item.category)}</td><td>${escape(item.description)}</td><td>${money.format(item.amount)}</td><td><span class="pill">${escape(item.status)}</span></td><td>${item.status === "pending" ? `<button class="secondary approve-expense" data-expense-id="${escape(item.id)}">Approve</button> <button class="secondary reject-expense" data-expense-id="${escape(item.id)}">Reject</button>` : "—"}</td></tr>`).join("") || "<tr><td colspan=\"5\">No expenses recorded yet.</td></tr>"}</tbody></table></section>`;
  if (view === "purchase") {
    const canApprovePO = PURCHASE_ORDER_APPROVE_ROLES.includes(currentUser?.role ?? "");
    return `<section class="form-card"><p class="eyebrow">Planned replenishment</p><h2>Request a purchase order</h2><form id="purchase-form" class="form"><label>Supplier<select name="supplier">${options(suppliers)}</select></label><label>Branch<select name="branch">${options(branches)}</select></label><label>Part<select name="product">${options(products)}</select></label><label>Quantity<input name="quantity" type="number" min="1" value="1" required></label><label>Unit cost<input name="unit-cost" type="number" min="0.01" step="0.01" value="5" required></label><label>Notes<input name="notes"></label><button class="primary">Create purchase order</button></form></section><section class="card table-card"><div class="card-title"><h2>Open purchase orders</h2><span>${purchaseOrders.filter((order) => order.status !== "received").length} waiting</span></div><table><thead><tr><th>Reference</th><th>Supplier</th><th>Notes</th><th>Status</th><th>Action</th></tr></thead><tbody>${purchaseOrders.map((order) => {
      const action = order.status === "requested"
        ? (canApprovePO ? `<button class="secondary approve-order" data-order-id="${escape(order.id)}">Approve</button>` : "Awaiting approval")
        : order.status === "approved"
          ? `<button class="secondary receive-order" data-order-id="${escape(order.id)}" data-order-reference="${escape(order.reference)}" data-order-version="${order.version}">Receive</button>`
          : "—";
      return `<tr><td>${escape(order.reference)}</td><td>${escape(suppliers.find((supplier) => supplier.id === order.supplierId)?.name ?? order.supplierId)}</td><td>${escape(order.notes)}</td><td><span class="pill">${escape(order.status)}</span></td><td>${action}</td></tr>`;
    }).join("")}</tbody></table></section>`;
  }
  if (view === "accounting") return `${form("Record a supplier payment", "supplier-payment-form", `<label>Supplier<select name="supplier">${options(suppliers)}</select></label><label>Purchase order (optional)<select name="purchase_order"><option value="">Not linked</option>${purchaseOrders.map((o) => `<option value="${escape(o.id)}">${escape(o.reference)}</option>`).join("")}</select></label><label>Amount (USD)<input name="amount" type="number" min="0.01" step="0.01" required></label><label>Method<select name="method"><option value="bank">Bank transfer</option><option value="cash">Cash</option><option value="mobile_money">Mobile money</option></select></label><label>Notes<input name="notes"></label>`, "Record payment")}<section class="card table-card"><div class="card-title"><h2>Supplier payments</h2><span>${supplierPayments.length} recorded</span></div><table><thead><tr><th>Reference</th><th>Supplier</th><th>Amount</th><th>Method</th><th>Date</th></tr></thead><tbody>${supplierPayments.map((p) => `<tr><td>${escape(p.reference)}</td><td>${escape(suppliers.find((s) => s.id === p.supplierId)?.name ?? p.supplierId)}</td><td>${money.format(p.amount)}</td><td>${escape(p.method)}</td><td>${new Date(p.createdAt).toLocaleDateString()}</td></tr>`).join("") || "<tr><td colspan=\"5\">No supplier payments yet.</td></tr>"}</tbody></table></section><section class="card table-card"><div class="card-title"><h2>Journal ledger</h2><span>${journalEntries.length} entries</span></div><table><thead><tr><th>Date</th><th>Type</th><th>Reference</th><th>Description</th><th>Amount</th></tr></thead><tbody>${journalEntries.map((j) => `<tr><td>${new Date(j.createdAt).toLocaleString()}</td><td><span class="pill">${escape(j.entryType.replace("_", " "))}</span></td><td>${escape(j.reference)}</td><td>${escape(j.description)}</td><td class="${j.amount >= 0 ? "positive" : "negative"}">${j.amount >= 0 ? "+" : ""}${money.format(j.amount)}</td></tr>`).join("") || "<tr><td colspan=\"5\">No journal entries yet.</td></tr>"}</tbody></table></section>`;
  if (view === "replenishment") return `<section class="card table-card"><div class="card-title"><h2>Reorder suggestions</h2><span>${replenishmentSuggestions.filter((r) => r.needs_reorder).length} need reordering</span></div><p class="hint">Heuristic v1: trailing 30-day sales velocity projected against current stock — not machine learning. Real demand forecasting is deferred until there is enough real production data to validate a model against.</p><table><thead><tr><th>Part</th><th>Branch</th><th>Stock</th><th>Reorder level</th><th>30-day sales</th><th>Days of cover</th><th>Suggested reorder</th><th>Status</th></tr></thead><tbody>${replenishmentSuggestions.map((r) => `<tr><td>${escape(r.product_name)}</td><td>${escape(r.branch_name)}</td><td>${r.current_stock}</td><td>${r.reorder_level}</td><td>${r.sales_last_30_days}</td><td>${r.days_of_cover ?? "—"}</td><td>${r.suggested_reorder_quantity}</td><td>${r.urgent ? '<b class="danger">Reorder now</b>' : r.needs_reorder ? '<b class="danger">Low</b>' : '<b class="good">OK</b>'}</td></tr>`).join("") || "<tr><td colspan=\"8\">No data yet.</td></tr>"}</tbody></table></section>`;
  if (view === "notifications") return `${pushNotificationsAvailable() ? '<section class="card"><div class="card-title"><h2>Push notifications</h2></div><p>Get alerted the moment stock runs low or a transfer needs attention.</p><button id="enable-push-button" class="secondary">Enable push notifications</button></section>' : ""}<section class="card table-card"><div class="card-title"><h2>Operator inbox</h2><span>${notifications.filter((item) => !item.read).length} unread</span></div>${notifications.map((item) => `<div class="row"><div><strong>${escape(item.title)}</strong><small>${escape(item.body)}</small></div><div>${item.read ? '<b class="good">Read</b>' : `<button class="secondary mark-read" data-notification-id="${escape(item.id)}">Mark read</button>`}</div></div>`).join("") || "<p>No notifications yet.</p>"}</section>`;
  if (view === "reports") {
    const plCard = profitLoss ? `<section class="card"><div class="card-title"><h2>Profit &amp; loss</h2><span>${profitLoss.start ?? "All time"}</span></div><div class="metrics"><section class="metric"><small>Revenue</small><strong>${money.format(profitLoss.revenue)}</strong></section><section class="metric"><small>Purchase cost</small><strong>${money.format(profitLoss.purchase_cost)}</strong></section><section class="metric"><small>Expenses</small><strong>${money.format(profitLoss.expenses)}</strong></section><section class="metric"><small>Net profit</small><strong class="${profitLoss.net_profit >= 0 ? "positive" : "negative"}">${money.format(profitLoss.net_profit)}</strong></section></div></section>` : "";
    const branchCard = branchPerformance.length ? `<section class="card table-card"><div class="card-title"><h2>Branch performance</h2></div><table><thead><tr><th>Branch</th><th>Sales</th><th>Stock value</th><th>Low stock parts</th></tr></thead><tbody>${branchPerformance.map((b) => `<tr><td>${escape(b.branch_name)}</td><td>${money.format(b.sales_total)} (${b.sales_count})</td><td>${money.format(b.stock_value)}</td><td>${b.low_stock_count}</td></tr>`).join("")}</tbody></table></section>` : "";
    const approvalsCard = approvalsSummary ? `<section class="card"><div class="card-title"><h2>Approval bottlenecks</h2></div><div class="metrics"><section class="metric"><small>Pending</small><strong>${approvalsSummary.pending_count}</strong></section><section class="metric"><small>Overdue</small><strong>${approvalsSummary.overdue_count}</strong></section><section class="metric"><small>Avg resolution</small><strong>${approvalsSummary.avg_resolution_hours != null ? `${approvalsSummary.avg_resolution_hours.toFixed(1)}h` : "—"}</strong></section></div></section>` : "";
    const expenseCard = expensesSummary ? `<section class="card table-card"><div class="card-title"><h2>Expenses by category</h2><span>${money.format(expensesSummary.total_approved)} approved</span></div><table><thead><tr><th>Category</th><th>Status</th><th>Count</th><th>Total</th></tr></thead><tbody>${expensesSummary.by_category.map((row) => `<tr><td>${escape(row.category)}</td><td><span class="pill">${escape(row.status)}</span></td><td>${row.count}</td><td>${money.format(row.total)}</td></tr>`).join("") || "<tr><td colspan=\"4\">No expenses yet.</td></tr>"}</tbody></table></section>` : "";
    const dailySalesCard = `<section class="card table-card"><div class="card-title"><h2>Daily sales</h2><span>${dailySales.length} days recorded</span></div><table><thead><tr><th>Day</th><th>Sales count</th><th>Sales total</th></tr></thead><tbody>${dailySales.map((row) => `<tr><td>${escape(row.day)}</td><td>${row.sales_count}</td><td>${money.format(row.sales_total)}</td></tr>`).join("") || "<tr><td colspan=\"3\">No sales recorded yet.</td></tr>"}</tbody></table></section>`;
    return `${plCard}${branchCard}${approvalsCard}${expenseCard}${dailySalesCard}`;
  }
  if (view === "users") return `<section class="form-card"><p class="eyebrow">Access control</p><h2>Create an operator account</h2><form id="user-form" class="form"><label>Full name<input name="full_name" required></label><label>Email<input name="email" type="email" required></label><label>Temporary password<input name="password" type="password" required></label><label>Role<select name="role"><option value="owner">Owner</option><option value="accountant">Accountant</option><option value="store_keeper">Store Keeper</option><option value="shop_manager">Shop Manager</option><option value="cashier" selected>Cashier</option><option value="super_admin">Super Admin</option></select></label><label>Branch<select name="branch"><option value="">Unassigned</option>${options(branches)}</select></label><button class="primary">Create user</button></form></section><section class="card table-card"><div class="card-title"><h2>Operator roster</h2><span>${adminUsers.length} accounts</span></div><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Branch</th><th>Status</th></tr></thead><tbody>${adminUsers.map((item) => `<tr><td>${escape(item.full_name)}</td><td>${escape(item.email)}</td><td><span class="pill">${escape(item.role)}</span></td><td>${escape(branches.find((entry) => entry.id === item.branch_id)?.name ?? "—")}</td><td>${item.is_active ? '<b class="good">Active</b>' : '<b class="danger">Disabled</b>'}</td></tr>`).join("")}</tbody></table></section>`;
  return `<section class="card table-card"><div class="card-title"><h2>Movement evidence</h2><span>Append-only · newest first</span></div><table><thead><tr><th>Time</th><th>Part</th><th>Branch</th><th>Event</th><th>Change</th><th>Reference</th></tr></thead><tbody>${movements.map((item) => `<tr><td>${new Date(item.createdAt).toLocaleString()}</td><td>${escape(product(item.productId)?.name ?? "Part")}</td><td>${escape(branches.find((entry) => entry.id === item.branchId)?.name ?? "Branch")}</td><td><span class="pill">${escape(item.kind.replace("_", " "))}</span></td><td class="${item.quantity > 0 ? "positive" : "negative"}">${item.quantity > 0 ? "+" : ""}${item.quantity}</td><td>${escape(item.reference)}</td></tr>`).join("")}</tbody></table></section>`;
}
function form(heading: string, id: string, fields: string, action: string) {
  return `<section class="form-card"><p class="eyebrow">Controlled transaction</p><h2>${heading}</h2><p>Information is persisted in the operational database and safely queued if this device is offline.</p><form id="${id}" class="form">${fields}<button class="primary">${action}</button></form></section>`;
}

function renderLogin(message = "") {
  root.innerHTML = `<main class="shell auth-shell"><section class="card auth-card"><p class="eyebrow">Secure operator sign-in</p><h1>Access the spare parts control room</h1><p>Sign in with your pilot credentials to load the live database and sync transactions.</p><form id="login-form" class="form"><label>Email<input name="email" type="email" required></label><label>Password<input name="password" type="password" required></label><button class="primary">Sign in</button></form>${message ? `<p class="notice">${escape(message)}</p>` : ""}</section></main>`;
  enhanceMobileTables();
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
  enhanceMobileTables();
  bind();
}

function value(form: HTMLFormElement, name: string) {
  return new FormData(form).get(name)?.toString() ?? "";
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function confirmAction(message: string) {
  return window.confirm(message);
}

function setWorking(source?: HTMLElement) {
  const button = source instanceof HTMLFormElement
    ? source.querySelector<HTMLButtonElement>('button[type="submit"], button:not([type])')
    : source instanceof HTMLButtonElement ? source : null;
  if (!button) return () => undefined;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  return () => {
    button.disabled = false;
    button.textContent = label;
  };
}

function enhanceMobileTables() {
  root.querySelectorAll<HTMLTableElement>("table").forEach((table) => {
    const labels = Array.from(table.querySelectorAll<HTMLTableCellElement>("thead th")).map((header) => header.textContent?.trim() ?? "");
    table.querySelectorAll<HTMLTableRowElement>("tbody tr").forEach((row) => {
      Array.from(row.cells).forEach((cell, index) => {
        if (labels[index]) cell.dataset.label = labels[index];
      });
    });
  });
}

async function completeAction(action: () => Promise<void>, success: string, nextView: View, failure: string, source?: HTMLElement) {
  const done = setWorking(source);
  try {
    await action();
    await refresh();
    view = nextView;
    render(success);
  } catch (error) {
    await refresh().catch(() => undefined);
    render(errorMessage(error, failure));
  } finally {
    done();
  }
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
    await completeAction(
      () => recordReceipt(value(form, "product"), branch("warehouse")!.id, Number(value(form, "quantity")), value(form, "supplier")),
      "Receipt saved or queued for sync.", "ledger", "Could not save the goods receipt.", form,
    );
  });

  root.querySelector<HTMLFormElement>("#transfer-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await completeAction(
      () => recordTransfer(value(form, "product"), branch("warehouse")!.id, branch("shop")!.id, Number(value(form, "quantity"))),
      "Transfer requested or queued for sync.", "transfer", "Could not request the transfer.", form,
    );
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
    await completeAction(
      () => recordSale(selected, branch("shop")!.id, Number(value(form, "quantity")), value(form, "customer") || null),
      "Sale saved or queued for sync.", "ledger", "Could not record the sale.", form,
    );
  });

  root.querySelector<HTMLFormElement>("#customer-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    try {
      await createCustomer(value(form, "name"), value(form, "phone"));
      await refresh();
      view = "sale";
      render("Customer added.");
    } catch (error) {
      render(error instanceof Error ? error.message : "Could not add customer.");
    }
  });

  root.querySelector<HTMLFormElement>("#approval-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await completeAction(
      () => createApproval(value(form, "type"), value(form, "subject"), currentUser?.full_name ?? "", value(form, "approver"), value(form, "priority")),
      "Approval request sent.", "approval", "Could not send the approval request.", form,
    );
  });

  root.querySelectorAll<HTMLButtonElement>(".approve-approval").forEach((button) => button.addEventListener("click", async () => {
    const approvalId = button.dataset.approvalId;
    if (!approvalId) return;
    if (!confirmAction("Approve this request? This updates the linked workflow.")) return;
    await completeAction(() => approveApproval(approvalId), "Approval approved.", "approval", "Could not approve this request.", button);
  }));

  root.querySelectorAll<HTMLButtonElement>(".reject-approval").forEach((button) => button.addEventListener("click", async () => {
    const approvalId = button.dataset.approvalId;
    if (!approvalId) return;
    if (!confirmAction("Reject this request? This decision cannot be undone from this screen.")) return;
    await completeAction(() => rejectApproval(approvalId), "Approval rejected.", "approval", "Could not reject this request.", button);
  }));

  root.querySelector<HTMLFormElement>("#expense-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await completeAction(
      () => createExpense(value(form, "branch"), value(form, "category"), Number(value(form, "amount")), value(form, "description")),
      "Expense recorded.", "expense", "Could not record the expense.", form,
    );
  });

  root.querySelectorAll<HTMLButtonElement>(".approve-expense").forEach((button) => button.addEventListener("click", async () => {
    const expenseId = button.dataset.expenseId;
    if (!expenseId) return;
    if (!confirmAction("Approve this expense? A journal entry will be posted.")) return;
    await completeAction(() => approveExpense(expenseId), "Expense approved.", "expense", "Could not approve this expense.", button);
  }));

  root.querySelectorAll<HTMLButtonElement>(".reject-expense").forEach((button) => button.addEventListener("click", async () => {
    const expenseId = button.dataset.expenseId;
    if (!expenseId) return;
    if (!confirmAction("Reject this expense? This decision cannot be undone from this screen.")) return;
    await completeAction(() => rejectExpense(expenseId), "Expense rejected.", "expense", "Could not reject this expense.", button);
  }));

  root.querySelector<HTMLFormElement>("#purchase-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await completeAction(
      () => createPurchaseOrder(value(form, "supplier"), value(form, "branch"), value(form, "notes"), [{ productId: value(form, "product"), quantity: Number(value(form, "quantity")), unitCost: Number(value(form, "unit-cost")) }]),
      "Purchase order queued or synced.", "purchase", "Could not create the purchase order.", form,
    );
  });

  root.querySelector<HTMLFormElement>("#supplier-payment-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    if (!confirmAction(`Record a supplier payment of ${money.format(Number(value(form, "amount")))}? This posts a journal entry.`)) return;
    await completeAction(
      () => createSupplierPayment(value(form, "supplier"), value(form, "purchase_order") || null, Number(value(form, "amount")), value(form, "method"), value(form, "notes")),
      "Supplier payment recorded.", "accounting", "Could not record supplier payment.", form,
    );
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

  root.querySelectorAll<HTMLButtonElement>(".approve-order").forEach((button) => button.addEventListener("click", async () => {
    const orderId = button.dataset.orderId;
    if (!orderId) return;
    try {
      await approvePurchaseOrder(orderId);
      await refresh();
      view = "purchase";
      render("Purchase order approved.");
    } catch (error) {
      render(error instanceof Error ? error.message : "Could not approve purchase order.");
    }
  }));

  root.querySelectorAll<HTMLButtonElement>(".receive-order").forEach((button) => button.addEventListener("click", async () => {
    const orderId = button.dataset.orderId;
    if (!orderId) return;
    if (!confirmAction("Receive this order into stock? This posts an inventory and accounting entry.")) return;
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
    await completeAction(() => markNotificationRead(notificationId), "Notification marked as read.", "notifications", "Could not mark this notification as read.", button);
  }));

  root.querySelector<HTMLButtonElement>("#enable-push-button")?.addEventListener("click", async () => {
    try {
      await enablePushNotifications();
      render("Push notifications enabled.");
    } catch (error) {
      render(error instanceof Error ? error.message : "Could not enable push notifications.");
    }
  });
}

window.addEventListener("online", () => {
  void sync();
});

void initialize();
