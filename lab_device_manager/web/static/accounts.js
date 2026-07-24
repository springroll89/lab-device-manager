const accountState = {
  session: null,
  users: [],
  roleLabels: {},
  creatableRoles: [],
};

function setFormStatus(id, message, type = "error") {
  const status = document.getElementById(id);
  status.textContent = message;
  status.className = `form-status ${type}`;
}

function clearFormStatus(id) {
  const status = document.getElementById(id);
  status.textContent = "";
  status.className = "form-status hidden";
}

function formatAccountTime(value) {
  return value ? new Date(value).toLocaleString() : "尚未登录";
}

async function accountApi(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", accountState.session.csrf_token);
  }
  const response = await fetch(path, {...options, headers});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    location.replace(`/login?next=${encodeURIComponent(location.pathname)}`);
    throw new Error("登录已失效");
  }
  if (response.status === 403 && data.error === "password_change_required") {
    location.replace("/change-password?next=/accounts");
    throw new Error("请先修改密码");
  }
  if (!response.ok) {
    const error = new Error(data.message || data.error || "请求失败");
    error.code = data.error;
    throw error;
  }
  return data;
}

async function loadAccountSession() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    location.replace("/login?next=/accounts");
    return false;
  }
  if (!response.ok) throw new Error("账号信息读取失败");
  accountState.session = await response.json();
  if (accountState.session.must_change_password) {
    location.replace("/change-password?next=/accounts");
    return false;
  }
  if (!accountState.session.can_manage_accounts) {
    location.replace("/experiments");
    return false;
  }
  document.getElementById("permissionSummary").textContent =
    accountState.session.role === "super_admin"
      ? "最高管理员可以创建实验室主管和操作员，并管理所有下级账号。"
      : "实验室主管可以创建、停用和重置操作员账号。";
  return true;
}

function buildRoleOptions() {
  const select = document.getElementById("newRole");
  select.textContent = "";
  for (const role of accountState.creatableRoles) {
    const option = document.createElement("option");
    option.value = role;
    option.textContent = accountState.roleLabels[role] || role;
    select.appendChild(option);
  }
}

function canManageUser(user) {
  if (user.id === accountState.session.user_id) return false;
  if (accountState.session.role === "super_admin") {
    return ["supervisor", "operator"].includes(user.role);
  }
  return user.role === "operator";
}

function actionButton(label, className, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function renderAccounts() {
  const body = document.getElementById("accountRows");
  body.textContent = "";
  for (const user of accountState.users) {
    const row = document.createElement("tr");
    if (!user.is_active) row.classList.add("is-disabled");

    const identity = document.createElement("td");
    const name = document.createElement("strong");
    name.textContent = user.display_name;
    const username = document.createElement("small");
    username.textContent = user.username;
    identity.append(name, username);

    const role = document.createElement("td");
    role.textContent = accountState.roleLabels[user.role] || user.role;

    const status = document.createElement("td");
    const statusText = document.createElement("span");
    statusText.className = `account-status ${user.is_active ? "active" : "inactive"}`;
    statusText.textContent = user.is_active
      ? (user.must_change_password ? "待首次改密" : "正常")
      : "已停用";
    status.appendChild(statusText);

    const lastLogin = document.createElement("td");
    lastLogin.textContent = formatAccountTime(user.last_login_at_ms);

    const actions = document.createElement("td");
    actions.className = "account-actions";
    if (canManageUser(user)) {
      actions.appendChild(actionButton(
        "重置密码",
        "account-text-button",
        () => openResetDialog(user),
      ));
      actions.appendChild(actionButton(
        user.is_active ? "停用" : "启用",
        user.is_active ? "account-danger-button" : "account-text-button",
        () => toggleAccount(user),
      ));
    } else {
      actions.textContent = user.id === accountState.session.user_id
        ? "当前账号"
        : "不可修改";
    }

    row.append(identity, role, status, lastLogin, actions);
    body.appendChild(row);
  }
}

async function loadAccounts() {
  const data = await accountApi("/api/accounts");
  accountState.users = data.users || [];
  accountState.roleLabels = data.role_labels || {};
  accountState.creatableRoles = data.creatable_roles || [];
  buildRoleOptions();
  renderAccounts();
}

async function createAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  clearFormStatus("createAccountStatus");
  const button = document.getElementById("createAccountButton");
  const payload = {
    username: document.getElementById("newUsername").value.trim(),
    display_name: document.getElementById("newDisplayName").value.trim(),
    role: document.getElementById("newRole").value,
    password: document.getElementById("newAccountPassword").value,
  };
  button.disabled = true;
  button.textContent = "正在创建…";
  try {
    await accountApi("/api/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setFormStatus(
      "createAccountStatus",
      `账号 ${payload.username} 已创建，请将临时密码单独告知本人。`,
      "success",
    );
    form.reset();
    buildRoleOptions();
    await loadAccounts();
  } catch (error) {
    const message = error.code === "username_exists"
      ? "这个登录账号已经存在。"
      : error.message;
    setFormStatus("createAccountStatus", message);
  } finally {
    button.disabled = false;
    button.textContent = "创建账号";
  }
}

function openResetDialog(user) {
  clearFormStatus("resetStatus");
  document.getElementById("resetUserId").value = user.id;
  document.getElementById("resetTarget").textContent =
    `${user.display_name} · ${user.username}`;
  document.getElementById("resetPassword").value = "";
  document.getElementById("resetDialog").showModal();
  document.getElementById("resetPassword").focus();
}

async function resetPassword(event) {
  event.preventDefault();
  clearFormStatus("resetStatus");
  const userId = document.getElementById("resetUserId").value;
  const password = document.getElementById("resetPassword").value;
  const button = document.getElementById("confirmReset");
  button.disabled = true;
  try {
    await accountApi(`/api/accounts/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({password}),
    });
    document.getElementById("resetDialog").close();
    await loadAccounts();
  } catch (error) {
    setFormStatus("resetStatus", error.message);
  } finally {
    button.disabled = false;
  }
}

async function toggleAccount(user) {
  const action = user.is_active ? "停用" : "启用";
  if (!window.confirm(`确认${action}账号“${user.display_name}”？`)) return;
  try {
    await accountApi(`/api/accounts/${user.id}/status`, {
      method: "PATCH",
      body: JSON.stringify({is_active: !user.is_active}),
    });
    await loadAccounts();
  } catch (error) {
    setFormStatus("accountListStatus", error.message);
  }
}

document.getElementById("createAccountForm").addEventListener("submit", createAccount);
document.getElementById("refreshAccounts").addEventListener("click", loadAccounts);
document.getElementById("resetPasswordForm").addEventListener("submit", resetPassword);
document.getElementById("cancelReset").addEventListener("click", () => {
  document.getElementById("resetDialog").close();
});

loadAccountSession()
  .then(ready => ready && loadAccounts())
  .catch(error => setFormStatus("accountListStatus", error.message));
