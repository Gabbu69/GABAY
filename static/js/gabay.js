(() => {
  "use strict";

  const root = document.documentElement;
  const banner = document.querySelector("#offline-banner");
  const navToggle = document.querySelector(".nav-toggle");
  const nav = document.querySelector("#primary-nav");

  const updateNetworkState = () => {
    const online = navigator.onLine;
    root.dataset.onlineState = online ? "online" : "offline";
    if (banner) banner.hidden = online;
    document.querySelectorAll("[data-offline-only]").forEach((element) => {
      element.hidden = online;
    });
    document.querySelectorAll("[data-online-required] button[type='submit']").forEach((button) => {
      button.disabled = !online;
      button.setAttribute("aria-disabled", String(!online));
    });
  };

  updateNetworkState();
  window.addEventListener("online", updateNetworkState);
  window.addEventListener("offline", updateNetworkState);

  if (navToggle && nav) {
    navToggle.addEventListener("click", () => {
      const expanded = navToggle.getAttribute("aria-expanded") === "true";
      navToggle.setAttribute("aria-expanded", String(!expanded));
      nav.classList.toggle("is-open", !expanded);
    });
    nav.addEventListener("click", (event) => {
      if (event.target.closest("a")) {
        navToggle.setAttribute("aria-expanded", "false");
        nav.classList.remove("is-open");
      }
    });
  }

  document.querySelectorAll("[data-retry]").forEach((button) => {
    button.addEventListener("click", () => window.location.reload());
  });

  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(() => {
        // The public site remains fully usable when installation is unavailable.
      });
    });
  }
})();
