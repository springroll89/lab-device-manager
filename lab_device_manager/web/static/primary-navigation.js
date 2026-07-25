(function initializePrimaryNavigation() {
  const navigationItems = [
    {
      href: "/",
      label: "设备看板",
      isActive: path =>
        path === "/" || /^\/(?:device|run|sensor)(?:\/|$)/.test(path),
    },
    {
      href: "/experiments",
      label: "实验执行",
      isActive: path => path === "/experiments",
    },
    {
      href: "/inventory",
      label: "物品与库存",
      isActive: path => path === "/inventory",
    },
    {
      href: "/hazardous-waste",
      label: "危废管理",
      isActive: path => path === "/hazardous-waste",
    },
    {
      href: "/measurement-station",
      label: "粘度工位",
      isActive: path => path === "/measurement-station",
    },
  ];

  function renderNavigation(host) {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    host.classList.add("primary-nav");
    host.setAttribute("aria-label", "主功能");

    const links = navigationItems.map(item => {
      const link = document.createElement("a");
      link.className = "primary-nav-link";
      link.href = item.href;
      link.textContent = item.label;
      if (item.isActive(path)) {
        link.classList.add("is-active");
        link.setAttribute("aria-current", "page");
      }
      return link;
    });
    host.replaceChildren(...links);
  }

  function init() {
    document.querySelectorAll("[data-primary-nav]").forEach(host => {
      if (host.dataset.primaryNavReady === "true") return;
      host.dataset.primaryNavReady = "true";
      renderNavigation(host);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, {once: true});
  } else {
    init();
  }
})();
