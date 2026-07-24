(function initializeAccountMenu() {
  function addTextElement(parent, tagName, className, text) {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = text;
    parent.appendChild(element);
    return element;
  }

  function addMenuLink(panel, href, label) {
    const link = document.createElement("a");
    link.className = "account-menu-item";
    link.href = href;
    link.textContent = label;
    panel.appendChild(link);
  }

  function renderMenu(host, session) {
    const details = document.createElement("details");
    details.className = "account-menu";

    const summary = document.createElement("summary");
    summary.className = "account-menu-trigger";
    summary.setAttribute(
      "aria-label",
      `当前账号：${session.operator}，打开账号菜单`
    );
    const triggerText = document.createElement("span");
    triggerText.className = "account-menu-trigger-text";
    addTextElement(
      triggerText,
      "strong",
      "account-menu-name",
      session.operator
    );
    addTextElement(
      triggerText,
      "small",
      "account-menu-role",
      session.role_label
    );
    summary.appendChild(triggerText);
    addTextElement(summary, "span", "account-menu-chevron", "⌄");

    const panel = document.createElement("div");
    panel.className = "account-menu-panel";
    const profile = document.createElement("div");
    profile.className = "account-menu-profile";
    addTextElement(profile, "strong", "", session.operator);
    addTextElement(
      profile,
      "span",
      "",
      `${session.role_label} · ${session.username}`
    );
    panel.appendChild(profile);

    addMenuLink(panel, "/change-password", "修改密码");
    if (session.can_manage_accounts) {
      addMenuLink(panel, "/accounts", "账号管理");
    }

    const logoutForm = document.createElement("form");
    logoutForm.method = "post";
    logoutForm.action = "/logout";
    const logoutButton = document.createElement("button");
    logoutButton.className = "account-menu-item account-menu-logout";
    logoutButton.type = "submit";
    logoutButton.textContent = "退出登录";
    logoutForm.appendChild(logoutButton);
    panel.appendChild(logoutForm);

    details.append(summary, panel);
    host.replaceChildren(details);
  }

  function renderUnavailable(host) {
    const link = document.createElement("a");
    link.className = "account-menu-unavailable";
    link.href = `/login?next=${encodeURIComponent(
      location.pathname + location.search
    )}`;
    link.textContent = "重新登录";
    host.replaceChildren(link);
  }

  async function loadMenu(host) {
    host.classList.add("account-menu-host");
    host.setAttribute("aria-live", "polite");
    addTextElement(host, "span", "account-menu-loading", "正在读取账号…");
    try {
      const response = await fetch("/api/session", {
        headers: {Accept: "application/json"},
      });
      if (response.status === 401) {
        location.replace(
          `/login?next=${encodeURIComponent(
            location.pathname + location.search
          )}`
        );
        return;
      }
      if (!response.ok) {
        renderUnavailable(host);
        return;
      }
      const session = await response.json();
      if (
        session.must_change_password &&
        location.pathname !== "/change-password"
      ) {
        location.replace(
          `/change-password?next=${encodeURIComponent(
            location.pathname + location.search
          )}`
        );
        return;
      }
      renderMenu(host, session);
    } catch (_) {
      renderUnavailable(host);
    }
  }

  function closeOtherMenus(event) {
    document.querySelectorAll(".account-menu[open]").forEach(menu => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  }

  function init() {
    document.querySelectorAll("[data-account-menu]").forEach(host => {
      if (host.dataset.accountMenuReady === "true") return;
      host.dataset.accountMenuReady = "true";
      loadMenu(host);
    });
    document.addEventListener("pointerdown", closeOtherMenus);
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      document.querySelectorAll(".account-menu[open]").forEach(menu => {
        menu.removeAttribute("open");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, {once: true});
  } else {
    init();
  }
})();
