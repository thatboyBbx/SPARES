export type View = "overview" | "catalogue" | "receipt" | "transfer" | "sale" | "approval" | "expense" | "ledger" | "purchase" | "accounting" | "replenishment" | "notifications" | "reports" | "users";

const VIEW_ROLES: Record<View, string[] | null> = {
  overview: null,
  catalogue: null,
  receipt: ["owner", "store_keeper", "super_admin"],
  transfer: ["owner", "store_keeper", "shop_manager", "super_admin"],
  sale: ["owner", "cashier", "shop_manager", "super_admin"],
  approval: ["owner", "accountant", "super_admin"],
  expense: ["owner", "accountant", "super_admin"],
  purchase: ["owner", "store_keeper", "super_admin"],
  accounting: ["owner", "accountant", "super_admin"],
  replenishment: ["owner", "store_keeper", "shop_manager", "super_admin"],
  notifications: null,
  ledger: null,
  reports: null,
  users: ["owner", "super_admin"],
};

export function canRoleSeeView(role: string, target: View) {
  const allowedRoles = VIEW_ROLES[target];
  return !allowedRoles || allowedRoles.includes(role);
}
