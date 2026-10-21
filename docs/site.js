/* Progressive enhancements only. Reading and navigation never require JavaScript. */
(() => {
  "use strict";
  // Shared resources stay relative to the site, not the current project's page.
  const siteURL = new URL(".", document.currentScript.src);
  const root = document.documentElement;
  root.classList.add("js");
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
  const store = {
    get(key, fallback = null) {
      try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, value); } catch { /* Optional preferences may be unavailable. */ }
    }
  };

  root.dataset.theme = "dark";

  const header = $(".site-header");
  const menuButton = $("[data-menu-toggle]");
  const navigation = $(".main-nav", header);
  let siteMenu;
  if (menuButton && navigation) {
    siteMenu = navigation.cloneNode(true);
    siteMenu.id = "site-menu";
    siteMenu.className = "site-menu";
    siteMenu.setAttribute("aria-label", "Site menu");
    siteMenu.hidden = true;
    header.append(siteMenu);
    menuButton.addEventListener("click", () => {
      siteMenu.hidden = !siteMenu.hidden;
      menuButton.setAttribute("aria-expanded", String(!siteMenu.hidden));
    });
    siteMenu.addEventListener("click", event => { if (event.target.closest("a")) closeMenu(); });
    document.addEventListener("click", event => { if (!header.contains(event.target)) closeMenu(); });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !siteMenu.hidden) { closeMenu(); menuButton.focus(); }
    });
  }
  function closeMenu() {
    if (siteMenu) siteMenu.hidden = true;
    menuButton?.setAttribute("aria-expanded", "false");
  }

  const toast = document.createElement("div");
  toast.className = "toast";
  toast.setAttribute("role", "status");
  toast.setAttribute("aria-live", "polite");
  toast.hidden = true;
  document.body.append(toast);
  let toastTimer;
  function notify(message) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
  }
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch { /* Try the local fallback. */ }
    }
    const focus = document.activeElement;
    const field = document.createElement("textarea");
    field.value = text;
    field.style.cssText = "position:fixed;left:-9999px;top:0";
    field.setAttribute("aria-label", "Command to copy");
    document.body.append(field);
    field.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch { /* Report failure instead of a false success. */ }
    field.remove();
    focus?.focus({ preventScroll: true });
    return copied;
  }
  $$("[data-copy]").forEach(button => button.addEventListener("click", async () => {
    const code = $("pre code", button.closest(".code-block"));
    if (!code) return;
    const copied = await copyText(code.textContent.trimEnd());
    button.textContent = copied ? "Copied" : "Select text";
    notify(copied ? "Copied to clipboard. Review commands before running them." : "Clipboard unavailable. Select the command text and copy it manually.");
    setTimeout(() => { button.textContent = "Copy"; }, 2200);
  }));

  const installCode = $("[data-install-command]");
  const installLaunch = $("[data-install-launch]");
  $$("[data-install-card] input").forEach(input => input.addEventListener("change", () => {
    if (installCode && ["/roms", "/roms2"].includes(input.value)) {
      installCode.textContent = installCode.textContent.replace(/^cd \/roms2?\/tools/m, `cd ${input.value}/tools`);
      if (installLaunch) installLaunch.textContent = installLaunch.textContent.replace(/\/roms2?\/tools/g, `${input.value}/tools`);
    }
  }));

  function setupWalkthrough() {
    const figure = $("[data-walkthrough]");
    if (!figure) return;
    const video = $("video.device-demo", figure);
    const controls = $("[data-demo-controls]", figure);
    const videoHome = video.parentElement;
    const controlsAnchor = document.createComment("walkthrough controls return here");
    controls.after(controlsAnchor);
    const toggle = $("[data-demo-toggle]", controls);
    const sound = $("[data-demo-audio]", controls);
    const expand = $("[data-demo-expand]", controls);
    const captions = $("[data-demo-captions]", controls);
    const position = $(".demo-position", controls);
    const status = $("[data-demo-status]", controls);
    const time = $("[data-demo-time]", controls);
    const sceneList = $("[data-demo-chapters]", controls);
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let wantsPlay = !motion.matches;
    let inView = figure.getBoundingClientRect().top < window.innerHeight;
    let playAttempt = 0;
    let chapters = [];
    let pendingSeek = null;
    let failed = false;
    let audioAvailable = null;
    let dialog;
    let dialogOrigin;
    video.controls = false;
    video.muted = true;
    video.volume = .8;
    controls.hidden = false;
    const timestamp = value => `${Math.floor(Math.max(0, value || 0) / 60)}:${String(Math.floor(Math.max(0, value || 0) % 60)).padStart(2, "0")}`;

    function update() {
      if (failed) {
        status.textContent = "Video unavailable. Use the direct video link below.";
        toggle.textContent = "Unavailable";
        toggle.disabled = position.disabled = sound.disabled = true;
        $("[data-demo-restart]", controls).disabled = true;
        $$("button[data-start]", sceneList).forEach(button => { button.disabled = true; });
        return;
      }
      const duration = Number.isFinite(video.duration) ? video.duration : 78;
      const current = pendingSeek ?? video.currentTime;
      const chapter = chapters.find(item => current >= item.start && current < item.end) || chapters[0];
      toggle.textContent = video.paused ? "Play" : "Pause";
      toggle.setAttribute("aria-label", video.paused ? "Play walkthrough" : "Pause walkthrough");
      sound.textContent = audioAvailable === false ? "Silent" : video.muted ? "Sound off" : "Sound on";
      sound.disabled = audioAvailable === false;
      sound.setAttribute("aria-pressed", String(!video.muted));
      sound.setAttribute("aria-label", video.muted ? "Enable walkthrough sound" : "Mute walkthrough sound");
      time.textContent = `${timestamp(current)} / ${timestamp(duration)}`;
      position.max = String(duration);
      position.value = String(current);
      position.setAttribute("aria-valuetext", `${timestamp(current)} of ${timestamp(duration)}${chapter ? ` / ${chapter.title}` : ""}`);
      const label = `${pendingSeek !== null ? "Loading / " : video.paused ? "Paused / " : ""}${chapter?.title || "227 AI walkthrough"}`;
      if (status.textContent !== label) status.textContent = label;
      $$("button[data-start]", sceneList).forEach(button => {
        if (chapter && Number(button.dataset.start) === chapter.start) button.setAttribute("aria-current", "step");
        else button.removeAttribute("aria-current");
      });
    }
    function synchronizePlayback() {
      const attempt = ++playAttempt;
      if (failed) { video.pause(); return; }
      if (wantsPlay && !document.hidden && (inView || dialog?.open)) {
        video.play().catch(error => {
          if (error.name === "AbortError" || attempt !== playAttempt) return;
          if (video.error || error.name === "NotSupportedError") { mediaFailure(); return; }
          wantsPlay = false;
          status.textContent = "Press Play to start the walkthrough";
          update();
        });
      } else video.pause();
    }
    function seek(value) {
      if (failed) return;
      pendingSeek = value;
      if (Number.isFinite(video.duration)) {
        video.currentTime = Math.max(0, Math.min(video.duration - .01, value));
        pendingSeek = null;
      }
      update();
    }
    function movePlayer(host, controlHost) {
      const at = video.currentTime;
      host.append(video);
      controlHost.append(controls);
      if (Number.isFinite(video.duration)) video.currentTime = at;
      synchronizePlayback();
    }
    function openExpanded() {
      if (dialog?.open) { dialog.close(); return; }
      if (!dialog) {
        dialog = document.createElement("dialog");
        dialog.className = "demo-dialog";
        dialog.setAttribute("aria-labelledby", "expanded-demo-title");
        dialog.innerHTML = `<div class="demo-dialog-header"><h2 id="expanded-demo-title">227 AI / AUTOMATED PC WALKTHROUGH</h2><button type="button" class="dialog-close" aria-label="Close expanded walkthrough">Close</button></div><div class="demo-dialog-player"></div><div class="demo-dialog-controls"></div><p class="demo-dialog-note">Scripted PC demo. Replies and microphone input are simulated; speech is prerecorded.</p>`;
        $(".dialog-close", dialog).addEventListener("click", () => dialog.close());
        dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
        dialog.addEventListener("close", () => {
          const at = video.currentTime;
          videoHome.append(video);
          controlsAnchor.before(controls);
          if (Number.isFinite(video.duration)) video.currentTime = at;
          expand.textContent = "Expand";
          expand.setAttribute("aria-label", "Expand walkthrough");
          synchronizePlayback();
          if (dialogOrigin?.isConnected) dialogOrigin.focus({ preventScroll: true });
        });
        document.body.append(dialog);
      }
      dialogOrigin = document.activeElement;
      dialog.showModal();
      movePlayer($(".demo-dialog-player", dialog), $(".demo-dialog-controls", dialog));
      expand.textContent = "Collapse";
      expand.setAttribute("aria-label", "Return walkthrough to handheld");
      $(".dialog-close", dialog).focus();
    }
    toggle.addEventListener("click", () => { wantsPlay = video.paused; synchronizePlayback(); });
    $("[data-demo-restart]", controls).addEventListener("click", () => { seek(0); wantsPlay = true; synchronizePlayback(); });
    sound.addEventListener("click", () => {
      video.muted = !video.muted;
      if (!video.muted) {
        wantsPlay = true;
        notify("Sound enabled. Read-aloud begins at 0:20; the prerecorded mic-test sample plays at 0:44.");
      }
      synchronizePlayback();
      update();
    });
    captions.addEventListener("click", () => {
      const track = video.textTracks[0];
      if (!track) return;
      const enabled = track.mode !== "showing";
      track.mode = enabled ? "showing" : "hidden";
      captions.textContent = enabled ? "Captions on" : "Captions off";
      captions.setAttribute("aria-pressed", String(enabled));
    });
    expand.addEventListener("click", openExpanded);
    position.addEventListener("input", () => seek(Number(position.value)));
    ["timeupdate", "play", "pause", "loadedmetadata", "seeked"].forEach(name => video.addEventListener(name, update));
    video.addEventListener("loadedmetadata", () => {
      if (pendingSeek !== null) seek(pendingSeek);
    });
    function mediaFailure() {
      failed = true;
      wantsPlay = false;
      video.pause();
      update();
    }
    video.addEventListener("error", mediaFailure);
    $("source", video)?.addEventListener("error", mediaFailure);
    document.addEventListener("visibilitychange", synchronizePlayback);
    motion.addEventListener("change", () => {
      if (motion.matches) { wantsPlay = false; synchronizePlayback(); }
    });
    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(entries => {
        inView = entries[0].isIntersecting;
        synchronizePlayback();
      }, { threshold: .12 });
      observer.observe(figure);
    }
    fetch(new URL("media/previews/walkthrough.json", siteURL))
      .then(response => { if (!response.ok) throw new Error("No scene metadata"); return response.json(); })
      .then(metadata => {
        chapters = Array.isArray(metadata.chapters) ? metadata.chapters.filter(item => typeof item.title === "string" && Number.isFinite(item.start) && Number.isFinite(item.end)) : [];
        for (const chapter of chapters) {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.start = String(chapter.start);
          button.title = chapter.description || chapter.title;
          button.disabled = failed;
          const title = document.createElement("span");
          title.textContent = chapter.title;
          const start = document.createElement("span");
          start.textContent = timestamp(chapter.start);
          button.append(title, start);
          button.addEventListener("click", () => { seek(chapter.start); wantsPlay = true; synchronizePlayback(); });
          sceneList.append(button);
        }
        audioAvailable = Boolean(metadata.hasAudio);
        if (!audioAvailable) {
          video.muted = true;
          sound.disabled = true;
          sound.textContent = "Silent";
        }
        update();
      })
      .catch(() => { $(".demo-chapters", controls).hidden = true; });
    update();
    // Start resource selection after handlers are attached, including cached loads.
    video.load();
    synchronizePlayback();
  }
  setupWalkthrough();

  $$(".table-wrap").forEach(wrapper => {
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", $("caption", wrapper)?.textContent || "Scrollable reference table");
  });
  // A location indicator makes long references easier to scan; it is not a test metric.
  const sideLinks = $$(".docs-sidebar a[href^='#']");
  const sections = $$(".docs-article section[id]");
  if (sideLinks.length && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (!visible) return;
      sideLinks.forEach(link => {
        if (link.hash === `#${visible.target.id}`) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      });
    }, { rootMargin: "-15% 0px -65% 0px", threshold: 0 });
    sections.forEach(section => observer.observe(section));
  }
  if ($(".docs-article")) {
    const progress = document.createElement("div");
    progress.className = "reading-progress";
    progress.setAttribute("aria-hidden", "true");
    document.body.append(progress);
    let scheduled = false;
    const updateProgress = () => {
      const length = root.scrollHeight - window.innerHeight;
      root.style.setProperty("--read-progress", String(length > 0 ? Math.min(1, window.scrollY / length) : 0));
      scheduled = false;
    };
    window.addEventListener("scroll", () => {
      if (!scheduled) { scheduled = true; requestAnimationFrame(updateProgress); }
    }, { passive: true });
    window.addEventListener("resize", updateProgress);
    updateProgress();
  }
  function revealAnchor() {
    if (!location.hash) return;
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (!target) return;
    for (let parent = target.closest("details"); parent; parent = parent.parentElement?.closest("details")) parent.open = true;
    const gallerySection = target.closest("[data-gallery-section]");
    if (gallerySection?.hidden) $(`[data-filter="${gallerySection.dataset.gallerySection}"]`)?.click();
  }
  window.addEventListener("hashchange", revealAnchor);
  revealAnchor();

  const searchDialog = document.createElement("dialog");
  searchDialog.className = "search-dialog";
  searchDialog.setAttribute("aria-labelledby", "search-title");
  searchDialog.innerHTML = `<div class="search-header"><div class="search-field"><label for="documentation-query" id="search-title">SEARCH 1Z99</label><input type="search" id="documentation-query" placeholder="Search projects, apps and build notes..." autocomplete="off" spellcheck="false" aria-describedby="search-help"></div><button type="button" class="dialog-close" aria-label="Close search">Esc</button></div><div class="search-results" aria-label="Search results"></div><div class="search-footer"><span id="search-help" role="status" aria-live="polite">Search runs locally in your browser.</span><label class="shortcut-preference"><input type="checkbox" data-slash-shortcut> Enable / shortcut</label><span>Tab to browse / Enter to open</span></div>`;
  document.body.append(searchDialog);
  const queryInput = $("input", searchDialog);
  const results = $(".search-results", searchDialog);
  const searchStatus = $("#search-help", searchDialog);
  const slashShortcut = $("[data-slash-shortcut]", searchDialog);
  slashShortcut.checked = store.get("227-site-slash-shortcut", "on") !== "off";
  slashShortcut.addEventListener("change", () => store.set("227-site-slash-shortcut", slashShortcut.checked ? "on" : "off"));
  let indexPromise;
  let searchOrigin;
  let searchVersion = 0;
  const normalized = value => value.toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  function emptySearch(message) {
    const p = document.createElement("p");
    p.className = "search-empty";
    p.textContent = message;
    results.replaceChildren(p);
  }
  async function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(new URL("search-index.json", siteURL)).then(response => {
        if (!response.ok) throw new Error("Search index unavailable");
        return response.json();
      }).then(data => {
        if (!Array.isArray(data)) throw new Error("Invalid search index");
        return data.map(item => ({ ...item, normalizedTitle: normalized(item.title), normalizedText: normalized(item.text), normalizedPage: normalized(item.page) }));
      }).catch(error => { indexPromise = null; throw error; });
    }
    return indexPromise;
  }
  async function runSearch() {
    const version = ++searchVersion;
    const query = queryInput.value.trim();
    const words = normalized(query).split(/\s+/).filter(Boolean);
    emptySearch("Loading the local index...");
    let index;
    try { index = await loadIndex(); } catch {
      if (version !== searchVersion) return;
      emptySearch("Search could not load. Try reloading, or use the page navigation.");
      searchStatus.textContent = "Search unavailable. The complete documentation is still readable.";
      return;
    }
    if (version !== searchVersion) return;
    const ranked = index.map(item => {
      const all = `${item.normalizedPage} ${item.normalizedTitle} ${item.normalizedText}`;
      const matches = words.every(word => all.includes(word));
      const score = words.reduce((sum, word) => sum + (item.normalizedTitle.includes(word) ? 12 : 0) + (item.normalizedPage.includes(word) ? 3 : 0) + (item.normalizedText.includes(word) ? 1 : 0), 0);
      return { item, matches, score };
    }).filter(result => !words.length || result.matches).sort((a, b) => b.score - a.score);
    const shown = ranked.slice(0, words.length ? 12 : 7);
    results.replaceChildren();
    if (!shown.length) emptySearch(`No sections found for "${query}". Try a shorter query such as "voice" or "card".`);
    for (const { item } of shown) {
      const destination = new URL(item.url, siteURL);
      // The index is local content, but never treat it as executable markup or an external redirect.
      if (destination.origin !== location.origin || !["http:", "https:", "file:"].includes(destination.protocol)) continue;
      const link = document.createElement("a");
      link.href = destination.href;
      const page = document.createElement("span");
      page.className = "search-result-page";
      page.textContent = item.page;
      const title = document.createElement("strong");
      title.textContent = item.title;
      const excerpt = document.createElement("p");
      const at = words.length ? Math.max(0, item.normalizedText.indexOf(words[0]) - 65) : 0;
      excerpt.textContent = `${at ? "..." : ""}${item.text.slice(at, at + 185)}${item.text.length > at + 185 ? "..." : ""}`;
      link.append(page, title, excerpt);
      link.addEventListener("click", () => searchDialog.close());
      results.append(link);
    }
    searchStatus.textContent = words.length ? `${ranked.length} matching sections. Showing ${shown.length}.` : `${index.length} sections indexed / Popular starting points`;
  }
  function openSearch() {
    if (searchDialog.open) return;
    closeMenu();
    searchOrigin = document.activeElement;
    searchDialog.showModal();
    queryInput.focus();
    runSearch();
  }
  $$("[data-search-open]").forEach(button => button.addEventListener("click", openSearch));
  $(".dialog-close", searchDialog).addEventListener("click", () => searchDialog.close());
  searchDialog.addEventListener("click", event => { if (event.target === searchDialog) searchDialog.close(); });
  searchDialog.addEventListener("close", () => { if (searchOrigin?.isConnected) searchOrigin.focus({ preventScroll: true }); });
  queryInput.addEventListener("input", runSearch);
  queryInput.addEventListener("keydown", event => {
    if (event.key === "ArrowDown") { event.preventDefault(); $("a", results)?.focus(); }
    else if (event.key === "Enter") { const first = $("a", results); if (first) { event.preventDefault(); first.click(); } }
  });
  results.addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    const links = $$("a", results);
    const at = links.indexOf(document.activeElement);
    if (event.key === "ArrowUp" && at <= 0) queryInput.focus();
    else links[Math.min(links.length - 1, at + (event.key === "ArrowDown" ? 1 : -1))]?.focus();
  });
  document.addEventListener("keydown", event => {
    const editing = event.target instanceof HTMLElement && (event.target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName));
    if ((event.key === "/" && slashShortcut.checked && !editing && !event.ctrlKey && !event.metaKey && !event.altKey) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k")) {
      event.preventDefault();
      openSearch();
    }
  });

  let closedForPrint = [];
  window.addEventListener("beforeprint", () => {
    closedForPrint = $$("details:not([open])");
    closedForPrint.forEach(detail => { detail.open = true; });
  });
  window.addEventListener("afterprint", () => {
    closedForPrint.forEach(detail => { detail.open = false; });
    closedForPrint = [];
  });
  const docMeta = $(".docs-article .doc-meta");
  if (docMeta) {
    const printButton = document.createElement("button");
    printButton.className = "print-button";
    printButton.type = "button";
    printButton.textContent = "Print this guide";
    printButton.addEventListener("click", () => window.print());
    docMeta.append(printButton);
  }

  const galleryFigures = $$(".gallery-grid .shot");
  if (galleryFigures.length) {
    $("[data-gallery-tools]").hidden = false;
    $$("[data-filter]").forEach(button => button.addEventListener("click", () => {
      $$("[data-filter]").forEach(other => other.setAttribute("aria-pressed", String(other === button)));
      galleryFigures.forEach(figure => { figure.hidden = button.dataset.filter !== "all" && figure.dataset.category !== button.dataset.filter; });
      $$("[data-gallery-section]").forEach(section => {
        section.hidden = button.dataset.filter !== "all" && section.dataset.gallerySection !== button.dataset.filter;
        if (section.hidden) $$("video", section).forEach(video => video.pause());
      });
    }));
  }
  let imageDialog;
  let imageOrigin;
  function openImage(source, alt, caption) {
    if (!imageDialog) {
      imageDialog = document.createElement("dialog");
      imageDialog.className = "image-dialog";
      imageDialog.setAttribute("aria-label", "Screenshot viewer");
      imageDialog.innerHTML = `<div class="image-dialog-header"><span>1Z99 / CAPTURE VIEWER</span><button type="button" class="dialog-close" aria-label="Close image viewer">Close</button></div><img alt=""><p></p>`;
      $(".dialog-close", imageDialog).addEventListener("click", () => imageDialog.close());
      imageDialog.addEventListener("click", event => { if (event.target === imageDialog) imageDialog.close(); });
      imageDialog.addEventListener("close", () => imageOrigin?.focus({ preventScroll: true }));
      document.body.append(imageDialog);
    }
    const image = $("img", imageDialog);
    image.src = source;
    image.alt = alt;
    $("p", imageDialog).textContent = caption;
    imageOrigin = document.activeElement;
    imageDialog.showModal();
  }
  async function loadCaptures() {
    const figures = $$("[data-shot]");
    if (!figures.length) return;
    const manifests = new Map();
    for (const figure of figures) {
      const path = figure.closest("[data-captures]")?.dataset.captures;
      if (!path) continue;
      let manifest;
      try {
        const manifestURL = new URL(path, siteURL);
        if (manifestURL.origin !== siteURL.origin || !manifestURL.pathname.startsWith(siteURL.pathname)) continue;
        if (!manifests.has(path)) manifests.set(path, fetch(manifestURL).then(response => response.ok ? response.json() : null));
        manifest = await manifests.get(path);
      } catch { continue; /* Leave the labeled placeholder when a manifest is unavailable. */ }
      if (!manifest) continue;
      const entry = manifest[figure.dataset.shot];
      if (!entry?.src || !entry.alt) continue;
      let url;
      try { url = new URL(entry.src, siteURL); } catch { continue; }
      if (url.origin !== siteURL.origin || !url.pathname.startsWith(siteURL.pathname + "media/")) continue;
      const image = document.createElement("img");
      image.className = "shot-image";
      image.alt = entry.alt;
      image.decoding = "async";
      image.width = 640;
      image.height = entry.kind === "photo" ? 800 : 480;
      image.addEventListener("load", async () => {
        try { await image.decode(); } catch { return; }
        const placeholder = $(".shot-placeholder", figure);
        if (!placeholder) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "shot-open";
        button.setAttribute("aria-label", `Enlarge: ${entry.alt}`);
        button.append(image);
        button.addEventListener("click", () => openImage(url.href, entry.alt, $("figcaption", figure)?.textContent || entry.alt));
        placeholder.replaceWith(button);
        figure.dataset.loaded = "true";
        const caption = $("figcaption", figure);
        if (caption && entry.origin === "pc-demo") {
          const source = document.createElement("span");
          source.className = "capture-origin";
          const seconds = Number(entry.time);
          source.textContent = entry.realPlayback ? "PC playback / native capture" : entry.method === "native-render" ? "PC demo / native render" : Number.isFinite(seconds)
            ? `PC demo / ${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`
            : "PC demo";
          caption.append(source);
        }
        updateGalleryCount();
      }, { once: true });
      image.addEventListener("error", () => { /* Leave the labeled placeholder if a configured file is missing. */ }, { once: true });
      image.src = url.href;
    }
  }
  function updateGalleryCount() {
    const label = $("[data-gallery-count]");
    if (!label) return;
    const loaded = $$(".gallery-grid .shot[data-loaded='true']").length;
    const pending = galleryFigures.length - loaded;
    label.textContent = loaded ? `${loaded} ${loaded === 1 ? "image" : "images"}${pending ? ` / ${pending} pending` : ""}` : "Images pending";
  }
  loadCaptures();
})();
