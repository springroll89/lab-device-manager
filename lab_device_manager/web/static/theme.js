(function initializeTheme() {
  const STORAGE_KEY = "puricore-color-theme";
  const root = document.documentElement;

  function normalize(theme) {
    return theme === "dark" ? "dark" : "light";
  }

  function storedTheme() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function syncToggle(button) {
    const current = normalize(root.dataset.theme);
    const nextLabel = current === "dark" ? "浅色模式" : "深色模式";
    button.textContent = current === "dark" ? `☀ ${nextLabel}` : `☾ ${nextLabel}`;
    button.setAttribute("aria-label", `切换到${nextLabel}`);
    button.setAttribute("aria-pressed", String(current === "dark"));
    button.title = `切换到${nextLabel}`;
  }

  function syncAllToggles() {
    document.querySelectorAll("[data-theme-toggle]").forEach(syncToggle);
  }

  function syncThemeImages() {
    const selected = normalize(root.dataset.theme);
    document.querySelectorAll(
      'img[data-theme-logo], img[src$="/logo.png"], img[src$="/logo-light.png"]'
    ).forEach(image => {
      image.dataset.themeLogo = "true";
      image.src = selected === "dark"
        ? "/static/logo.png"
        : "/static/logo-light.png";
    });
  }

  function apply(theme, persist = true) {
    const selected = normalize(theme);
    root.dataset.theme = selected;
    root.style.colorScheme = selected;
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, selected);
      } catch (_) {}
    }
    syncAllToggles();
    syncThemeImages();
    root.dispatchEvent(new CustomEvent("puricore:themechange", {
      detail: {theme: selected},
    }));
  }

  function enhanceToggle(button) {
    if (!button || button.dataset.themeReady === "true") return;
    button.dataset.themeReady = "true";
    button.dataset.themeToggle = "";
    button.addEventListener("click", () => {
      apply(root.dataset.theme === "dark" ? "light" : "dark");
    });
    syncToggle(button);
  }

  function mountStandaloneToggle() {
    if (document.querySelector("[data-account-menu], [data-theme-toggle]")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-toggle-floating";
    document.body.appendChild(button);
    enhanceToggle(button);
  }

  apply(storedTheme() || "light", false);
  window.PuricoreTheme = {apply, enhanceToggle};

  function init() {
    document.querySelectorAll("[data-theme-toggle]").forEach(enhanceToggle);
    syncThemeImages();
    mountStandaloneToggle();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, {once: true});
  } else {
    init();
  }
})();
