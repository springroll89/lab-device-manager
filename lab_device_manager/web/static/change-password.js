let passwordSession = null;

function passwordStatus(message, type = "error") {
  const status = document.getElementById("passwordStatus");
  status.textContent = message;
  status.className = `form-status ${type}`;
}

function safeNextPath() {
  const requested = new URLSearchParams(location.search).get("next");
  if (requested && requested.startsWith("/") && !requested.startsWith("//")) {
    return requested;
  }
  return passwordSession?.can_manage_accounts ? "/accounts" : "/experiments";
}

async function loadPasswordSession() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    return;
  }
  if (!response.ok) throw new Error("账号信息读取失败");
  passwordSession = await response.json();
  document.getElementById("currentAccount").textContent =
    `${passwordSession.operator} · ${passwordSession.username}`;
  document.getElementById("passwordIntro").textContent =
    passwordSession.must_change_password
      ? "这是首次登录或密码刚被重置，请先设置新密码。"
      : "修改后请使用新密码登录。";
}

async function submitPassword(event) {
  event.preventDefault();
  const currentPassword = document.getElementById("currentPassword").value;
  const newPassword = document.getElementById("newPassword").value;
  const confirmPassword = document.getElementById("confirmPassword").value;
  const button = document.getElementById("passwordSubmit");
  if (!currentPassword || !newPassword || !confirmPassword) {
    passwordStatus("请完整填写三个密码输入框。");
    return;
  }
  if (newPassword !== confirmPassword) {
    passwordStatus("两次输入的新密码不一致。");
    document.getElementById("confirmPassword").focus();
    return;
  }
  if (newPassword.length < 8 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword)) {
    passwordStatus("新密码至少 8 位，并同时包含字母和数字。");
    document.getElementById("newPassword").focus();
    return;
  }
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const response = await fetch("/api/account/password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": passwordSession.csrf_token,
      },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data.error === "current_password_incorrect"
        ? "当前密码不正确。"
        : data.message || "密码修改失败，请重试。";
      passwordStatus(message);
      return;
    }
    passwordStatus("密码已修改，正在进入系统…", "success");
    setTimeout(() => location.replace(safeNextPath()), 500);
  } catch (_) {
    passwordStatus("无法连接本地服务，请稍后重试。");
  } finally {
    button.disabled = false;
    button.textContent = "保存新密码";
  }
}

async function logoutCurrentAccount() {
  await fetch("/logout", {method: "POST"});
  location.replace("/login");
}

document.getElementById("passwordForm").addEventListener("submit", submitPassword);
document.getElementById("passwordLogout").addEventListener("click", logoutCurrentAccount);
loadPasswordSession().catch(error => passwordStatus(error.message));
