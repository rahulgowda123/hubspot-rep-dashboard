/**
 * Hotjar session recording + heatmaps.
 *
 * Plain <script> (no bundler in this app), loaded from templates/index.html BEFORE
 * static/script.js so the hj() queue exists before any dashboard code could call it.
 *
 * The Site ID is NOT hardcoded here. app.py renders it into window.__APP_CONFIG__ on
 * every request from the HOTJAR_SITE_ID environment variable, so the packaged .exe is
 * reconfigured by editing the .env next to it -- no rebuild, no Python, no toolchain.
 */
(function () {
  "use strict";

  var SCRIPT_ID = "hotjar-snippet";

  // Snippet version Hotjar expects in both _hjSettings and the script URL. Bumping this
  // is Hotjar's call, not ours -- it changes only when they ship a new loader contract.
  var SNIPPET_VERSION = 6;

  // Treated as "not set": missing, non-string, blank, and the "__PLACEHOLDER__" shape a
  // packaging step might leave behind -- an unsubstituted placeholder must fall through
  // to "off", never be sent to Hotjar as if it were a real ID.
  function resolveSiteId() {
    var cfg = (typeof window !== "undefined" && window.__APP_CONFIG__) || {};
    var raw = cfg.hotjarSiteId;
    if (typeof raw !== "string") return "";
    var trimmed = raw.trim();
    if (!trimmed || /^__.*__$/.test(trimmed)) return "";
    return trimmed;
  }

  var HOTJAR_SITE_ID = resolveSiteId();

  function isHotjarEnabled() {
    return Boolean(HOTJAR_SITE_ID);
  }

  /**
   * Injects the Hotjar snippet. No-ops when no site ID is configured, which is the
   * normal state for a local run and for any deploy that has not opted in.
   *
   * Idempotent on purpose: the dashboard re-renders views in place and this file could
   * be loaded twice by a future template change. Two copies of the snippet would open
   * two recordings for one page view.
   *
   * @returns {boolean} true only when this call actually injected the script.
   */
  function initHotjar() {
    if (!isHotjarEnabled()) return false;
    if (typeof window === "undefined" || typeof document === "undefined") return false;
    if (document.getElementById(SCRIPT_ID)) return false;

    // A non-numeric ID would silently request hotjar-NaN.js and fail with nothing in the
    // console pointing at the cause. Say so instead -- a typo'd ID and a deliberately
    // disabled Hotjar should not look identical to whoever is debugging.
    if (!/^\d+$/.test(HOTJAR_SITE_ID)) {
      console.warn(
        '[analytics] Ignoring HOTJAR_SITE_ID="' + HOTJAR_SITE_ID + '": a Hotjar Site ID is ' +
          'digits only (e.g. "6765855"). Find it under Settings -> Sites & Organizations ' +
          "in Hotjar. Recording is off."
      );
      return false;
    }

    // The queue has to exist before the remote script loads, so calls made during the
    // first render are replayed instead of dropped on the floor.
    window.hj = window.hj || function () {
      (window.hj.q = window.hj.q || []).push(arguments);
    };
    // Number, not string: Hotjar's own snippet emits hjid as a numeric literal and the
    // remote script reads this value back. The digits-only guard above means Number()
    // cannot produce NaN here.
    window._hjSettings = { hjid: Number(HOTJAR_SITE_ID), hjsv: SNIPPET_VERSION };

    var script = document.createElement("script");
    script.id = SCRIPT_ID;
    script.async = true;
    script.src =
      "https://static.hotjar.com/c/hotjar-" + HOTJAR_SITE_ID + ".js?sv=" + SNIPPET_VERSION;
    document.head.appendChild(script);
    return true;
  }

  // Exposed for debugging from the browser console (Analytics.isHotjarEnabled()) and so a
  // future change has a named entry point instead of re-implementing the guards.
  window.Analytics = { initHotjar: initHotjar, isHotjarEnabled: isHotjarEnabled };

  // Entry point. This app has no JS bundle to hook, so the <script> tag IS the entry
  // point. No-ops when HOTJAR_SITE_ID is unset, which is the default.
  initHotjar();
})();
