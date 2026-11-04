"use strict";

/*
 * Module livres : affichage de 5 livres aléatoires et COLLECTE DES INTERACTIONS.
 *
 * Trajet d'une interaction :
 *   clic sur un bouton [data-action] dans une carte [data-book-id]
 *     -> handleBookAction()        (listener délégué, attaché une seule fois)
 *     -> performStateAction() / openBook()
 *     -> sendBookEvent(bookId, eventType)   POST /api/book-events
 *     -> backend : record_book_event()      INSERT INTO book_events
 *     -> la réponse contient l'état confirmé {liked, dismissed}
 *     -> renderBookState() met à jour toutes les vues de ce livre.
 *
 * La session n'est jamais envoyée par le JS : c'est le cookie HttpOnly
 * « books_session » que le navigateur joint automatiquement.
 */

(function () {
  const $ = (id) => document.getElementById(id);

  const els = {
    refresh: $("refresh"),
    discoveryStatus: $("discovery-status"),
    discoveryError: $("discovery-error"),
    discoveryErrorMessage: $("discovery-error-message"),
    discoveryRetry: $("discovery-retry"),
    discoveryEmpty: $("discovery-empty"),
    discoveryPartial: $("discovery-partial"),
    grid: $("book-grid"),
    cardTemplate: $("book-card-template"),

    recoStatus: $("reco-status"),
    recoError: $("reco-error"),
    recoErrorMessage: $("reco-error-message"),
    recoRetry: $("reco-retry"),
    recoEmpty: $("reco-empty"),
    recoEmptyMessage: $("reco-empty-message"),
    recoGrid: $("reco-grid"),

    searchForm: $("search-form"),
    searchInput: $("search-input"),
    searchStatus: $("search-status"),
    searchError: $("search-error"),
    searchErrorMessage: $("search-error-message"),
    searchRetry: $("search-retry"),
    searchSummary: $("search-summary"),
    searchResults: $("search-results"),
    searchPagination: $("search-pagination"),
    searchPrev: $("search-prev"),
    searchNext: $("search-next"),
    searchPageInfo: $("search-page-info"),
    searchRowTemplate: $("search-row-template"),

    dialog: $("book-dialog"),
    dialogLoading: $("dialog-loading"),
    dialogError: $("dialog-error"),
    dialogErrorMessage: $("dialog-error-message"),
    dialogContent: $("dialog-content"),
  };

  /* ------------------------------------------------------------------ état */

  // État CONFIRMÉ par le backend pour chaque livre : {liked, dismissed}.
  const bookStates = new Map();
  // Livres dont une action est en cours d'envoi (boutons désactivés).
  const inFlight = new Set();
  // Dernier échec par livre : {eventType, message} -> affiche « Réessayer ».
  const failures = new Map();
  // request_id des actions non confirmées, réutilisés lors d'une reprise.
  //   clé "<bookId>:state" pour like/unlike/dismiss/undismiss
  //   clé "<bookId>:open"  pour open
  const pendingRequestIds = new Map();

  const MAX_NETWORK_ATTEMPTS = 3;

  /* ------------------------------------------------------------- outils */

  function newRequestId() {
    if (window.crypto && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    // crypto.randomUUID n'existe qu'en contexte sécurisé (https, localhost).
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  class HttpError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      data = null;
    }
    if (!response.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = "Requête invalide.";
      throw new HttpError(detail || `Erreur HTTP ${response.status}`, response.status);
    }
    return data;
  }

  function errorMessage(err) {
    return err instanceof TypeError
      ? "Le serveur est injoignable. Vérifiez que l’application est lancée."
      : err.message;
  }

  /* ====================================================================
   * ENVOI D'UN ÉVÉNEMENT — point d'entrée unique vers le backend.
   *
   * Corps envoyé : {book_id, event_type, request_id}. La session vient du
   * cookie. Le même request_id est réutilisé :
   *   - pour les tentatives automatiques après une coupure réseau ;
   *   - pour le bouton « Réessayer » de la même action.
   * Le backend ignore un request_id déjà enregistré (pas de doublon).
   * ==================================================================== */
  async function sendBookEvent(bookId, eventType) {
    const key = `${bookId}:${eventType === "open" ? "open" : "state"}`;
    let pending = pendingRequestIds.get(key);
    if (!pending || pending.eventType !== eventType) {
      // Nouvelle action (ou action différente de celle en échec) : nouvel id.
      pending = { eventType, requestId: newRequestId() };
      pendingRequestIds.set(key, pending);
    }
    const body = JSON.stringify({
      book_id: bookId,
      event_type: eventType,
      request_id: pending.requestId,
    });

    for (let attempt = 1; ; attempt += 1) {
      try {
        const data = await requestJson("/api/book-events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        pendingRequestIds.delete(key); // confirmé : le prochain clic aura un nouvel id
        return data; // {request_id, book_id, event_type, duplicate, state}
      } catch (err) {
        const networkError = err instanceof TypeError;
        const retryable = networkError || err.status >= 500;
        if (retryable && attempt < MAX_NETWORK_ATTEMPTS) {
          await delay(500 * attempt);
          continue; // même body, donc même request_id
        }
        if (!retryable) pendingRequestIds.delete(key); // 4xx : réessayer ne changera rien
        throw err;
      }
    }
  }

  /* ====================================================================
   * DÉTECTION DES INTERACTIONS — un listener délégué par conteneur.
   *
   * Les cartes sont recréées à chaque tirage, mais le listener est posé sur
   * le conteneur (#book-grid, #search-results, #book-dialog) une seule fois
   * à l'initialisation : aucun gestionnaire n'est dupliqué.
   * Seul le bouton cliqué compte (closest("button[data-action]")) : un clic
   * sur « J’aime » n'ouvre jamais la fiche, et n'est pas compté comme « open ».
   * ==================================================================== */
  function handleBookAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button || !event.currentTarget.contains(button) || button.disabled) return;

    const action = button.dataset.action;
    if (action === "close") {
      els.dialog.close();
      return;
    }

    const holder = button.closest("[data-book-id]");
    const bookId = holder && holder.dataset.bookId;
    if (!bookId) return;

    switch (action) {
      case "open":
        openBook(bookId);
        break;
      case "like":
      case "unlike":
      case "dismiss":
      case "undismiss":
        performStateAction(bookId, action);
        break;
      case "retry": {
        const failure = failures.get(bookId);
        if (!failure) return;
        if (failure.eventType === "open") recordOpen(bookId);
        else performStateAction(bookId, failure.eventType);
        break;
      }
      default:
        break;
    }
  }

  // like / unlike / dismiss / undismiss : on attend la confirmation du backend
  // avant d'afficher le nouvel état.
  async function performStateAction(bookId, eventType) {
    if (inFlight.has(bookId)) return;
    inFlight.add(bookId);
    failures.delete(bookId);
    renderBookState(bookId);
    try {
      const result = await sendBookEvent(bookId, eventType);
      bookStates.set(bookId, result.state);
      scheduleRecommendations(); // le profil a changé : recalcul
    } catch (err) {
      failures.set(bookId, { eventType, message: errorMessage(err) });
    } finally {
      inFlight.delete(bookId);
      renderBookState(bookId);
    }
  }

  // « open » est envoyé seulement APRÈS le chargement réussi de la fiche.
  async function recordOpen(bookId) {
    if (failures.get(bookId)?.eventType === "open") failures.delete(bookId);
    renderBookState(bookId);
    try {
      await sendBookEvent(bookId, "open");
      scheduleRecommendations(); // une ouverture compte aussi dans le profil
    } catch (err) {
      // Une autre action en échec garde la priorité d'affichage.
      if (!failures.has(bookId)) {
        failures.set(bookId, { eventType: "open", message: errorMessage(err) });
      }
    }
    renderBookState(bookId);
  }

  /* ------------------------------------------------------- rendu commun */

  const LABELS = {
    like: { text: "J’aime", title: "Ajouter un j’aime" },
    unlike: { text: "♥ Aimé", title: "Retirer le j’aime" },
    dismiss: { text: "Pas intéressé", title: "Écarter ce livre des prochains tirages" },
    undismiss: { text: "Écarté · annuler", title: "Annuler le rejet" },
  };
  const FAILURE_TEXT = {
    open: "L’ouverture de la fiche n’a pas été enregistrée.",
    like: "Le « j’aime » n’a pas été enregistré.",
    unlike: "Le retrait du « j’aime » n’a pas été enregistré.",
    dismiss: "Le rejet n’a pas été enregistré.",
    undismiss: "L’annulation du rejet n’a pas été enregistrée.",
  };

  function setActionButton(button, action, pressed, busy) {
    button.dataset.action = action;
    button.textContent = busy ? "Envoi…" : LABELS[action].text;
    button.title = LABELS[action].title;
    button.setAttribute("aria-pressed", String(pressed));
    button.disabled = busy;
  }

  // Met à jour toutes les vues d'un livre (carte, fiche ouverte) à partir
  // de l'état confirmé, de l'envoi en cours et du dernier échec.
  function renderBookState(bookId) {
    const state = bookStates.get(bookId) || { liked: false, dismissed: false };
    const busy = inFlight.has(bookId);
    const failure = failures.get(bookId);

    for (const holder of document.querySelectorAll(`[data-book-id="${CSS.escape(bookId)}"]`)) {
      holder.classList.toggle("is-liked", state.liked);
      holder.classList.toggle("is-dismissed", state.dismissed);

      const like = holder.querySelector('[data-role="like"]');
      if (like) setActionButton(like, state.liked ? "unlike" : "like", state.liked, busy);
      const dismiss = holder.querySelector('[data-role="dismiss"]');
      if (dismiss) setActionButton(dismiss, state.dismissed ? "undismiss" : "dismiss", state.dismissed, busy);

      const badge = holder.querySelector(".book-badge");
      if (badge) {
        badge.hidden = !(state.liked || state.dismissed);
        badge.textContent = state.liked ? "Vous aimez ce livre" : "Écarté des prochains tirages";
      }

      const feedback = holder.querySelector(".book-feedback");
      if (feedback) {
        if (failure) {
          const text = document.createElement("span");
          text.textContent = `${FAILURE_TEXT[failure.eventType]} ${failure.message}`;
          const retry = document.createElement("button");
          retry.type = "button";
          retry.className = "link-button";
          retry.dataset.action = "retry";
          retry.textContent = "Réessayer";
          feedback.replaceChildren(text, " ", retry);
          feedback.hidden = false;
        } else {
          feedback.replaceChildren();
          feedback.hidden = true;
        }
      }
    }
  }

  function initials(title) {
    return (title || "?")
      .split(/\s+/)
      .filter((w) => /[\p{L}\p{N}]/u.test(w))
      .slice(0, 2)
      .map((w) => w.match(/[\p{L}\p{N}]/u)[0].toUpperCase())
      .join("");
  }

  // Couverture, ou visuel de remplacement si l'image manque ou échoue.
  function fillCover(slot, book) {
    const fallback = () => {
      const div = document.createElement("div");
      div.className = "cover-fallback";
      const hue = Number(String(book.book_id).slice(-6)) % 360;
      div.style.setProperty("--hue", String(hue));
      div.textContent = initials(book.title);
      div.setAttribute("aria-hidden", "true");
      slot.replaceChildren(div);
    };
    if (!book.thumbnail) {
      fallback();
      return;
    }
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", fallback, { once: true });
    img.src = book.thumbnail;
    slot.replaceChildren(img);
  }

  const joinList = (list) => (Array.isArray(list) ? list.join(", ") : "");

  /* -------------------------------------------------- sélection aléatoire */

  let lastDiscoveryRequest = "load";

  function showDiscovery(state) {
    els.discoveryStatus.hidden = state !== "loading";
    els.discoveryError.hidden = state !== "error";
    els.discoveryEmpty.hidden = state !== "empty";
    els.grid.hidden = state !== "results";
    if (state !== "results") els.discoveryPartial.hidden = true;
    els.refresh.disabled = state === "loading";
  }

  function renderCard(book) {
    const node = els.cardTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.bookId = book.book_id; // book_id exact du catalogue
    fillCover(node.querySelector(".cover-slot"), book);
    node.querySelector(".book-category").textContent = joinList(book.categories) || "Non classé";
    node.querySelector(".book-title").textContent = book.title;
    node.querySelector(".book-authors").textContent = joinList(book.authors) || "Auteur inconnu";
    node.querySelector('[data-action="open"]').setAttribute("aria-label", `Voir le livre : ${book.title}`);
    if (typeof book.score === "number") {
      const score = node.querySelector(".book-score");
      score.textContent = `Similarité ${scoreFormat.format(book.score)}`;
      score.hidden = false;
    }
    return node;
  }

  const scoreFormat = new Intl.NumberFormat("fr-FR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  /* ------------------------------------------- recommandations TF-IDF */

  const RECO_TOP_K = 4;
  let recoToken = 0;
  let recoTimer = null;

  function showReco(state) {
    els.recoStatus.hidden = state !== "loading";
    els.recoError.hidden = state !== "error";
    els.recoEmpty.hidden = state !== "empty";
    // Pendant un recalcul, on garde les anciennes cartes visibles.
    if (state !== "loading") els.recoGrid.hidden = state !== "results";
  }

  // Plusieurs clics rapprochés -> un seul recalcul.
  function scheduleRecommendations() {
    clearTimeout(recoTimer);
    recoTimer = setTimeout(loadRecommendations, 250);
  }

  async function loadRecommendations() {
    const token = ++recoToken;
    showReco("loading");
    try {
      const data = await requestJson(`/api/books/recommendations?top_k=${RECO_TOP_K}`);
      if (token !== recoToken) return; // une réponse plus récente est attendue

      if (data.status === "cold_start" || data.status === "no_candidates" || data.count === 0) {
        els.recoEmptyMessage.textContent = data.status === "cold_start"
          ? "Aimez ou ouvrez au moins un livre pour obtenir des recommandations."
          : "Aucun autre livre similaire à proposer pour l’instant.";
        showReco("empty");
        return;
      }
      const fragment = document.createDocumentFragment();
      for (const book of data.recommendations) fragment.appendChild(renderCard(book));
      els.recoGrid.replaceChildren(fragment);
      for (const book of data.recommendations) renderBookState(book.book_id);
      showReco("results");
    } catch (err) {
      if (token !== recoToken) return;
      if (err.status === 401) {
        els.recoEmptyMessage.textContent = "Aimez ou ouvrez au moins un livre pour obtenir des recommandations.";
        showReco("empty");
        return;
      }
      els.recoErrorMessage.textContent = errorMessage(err);
      showReco("error");
    }
  }

  async function loadDiscovery(mode) {
    lastDiscoveryRequest = mode;
    showDiscovery("loading");
    try {
      const data = mode === "refresh"
        ? await requestJson("/api/books/discovery/refresh", { method: "POST" })
        : await requestJson("/api/books/discovery");

      if (!data || !Array.isArray(data.items)) throw new Error("Réponse inattendue du serveur.");
      if (data.items.length === 0) {
        showDiscovery("empty");
        return;
      }
      const fragment = document.createDocumentFragment();
      for (const book of data.items) {
        bookStates.set(book.book_id, book.state);
        fragment.appendChild(renderCard(book));
      }
      // Remplace les cartes ; le listener délégué de #book-grid reste unique.
      els.grid.replaceChildren(fragment);
      for (const book of data.items) renderBookState(book.book_id);

      els.discoveryPartial.hidden = data.count >= data.requested_size;
      els.discoveryPartial.textContent =
        `Seulement ${data.count} livre(s) disponible(s) : les autres ont été écartés.`;
      showDiscovery("results");
    } catch (err) {
      els.discoveryErrorMessage.textContent = errorMessage(err);
      showDiscovery("error");
    }
  }

  /* ----------------------------------------------------------- recherche */

  const search = { query: "", page: 1, pageSize: 10 };

  function showSearch(state) {
    els.searchStatus.hidden = state !== "loading";
    els.searchError.hidden = state !== "error";
    const hasResults = state === "results";
    els.searchResults.hidden = !hasResults;
    els.searchPagination.hidden = !hasResults;
    els.searchSummary.hidden = !(hasResults || state === "empty");
  }

  function renderSearchRow(book) {
    const node = els.searchRowTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.bookId = book.book_id;
    fillCover(node.querySelector(".cover-slot"), book);
    const year = book.published_year ? ` (${book.published_year})` : "";
    node.querySelector(".search-row-title").textContent = book.title + year;
    node.querySelector(".book-authors").textContent = joinList(book.authors) || "Auteur inconnu";
    node.querySelector('[data-action="open"]').setAttribute("aria-label", `Voir le livre : ${book.title}`);
    return node;
  }

  async function runSearch(page) {
    search.page = page;
    showSearch("loading");
    const params = new URLSearchParams({
      query: search.query,
      page: String(page),
      page_size: String(search.pageSize),
    });
    try {
      const data = await requestJson(`/api/books?${params}`);
      if (data.total === 0) {
        els.searchSummary.textContent = `Aucun livre ne correspond à « ${search.query} ».`;
        showSearch("empty");
        return;
      }
      els.searchResults.replaceChildren(...data.items.map(renderSearchRow));
      els.searchSummary.textContent = search.query
        ? `${data.total} livre(s) pour « ${search.query} »`
        : `${data.total} livres dans le catalogue`;
      els.searchPageInfo.textContent = `Page ${data.page} / ${data.total_pages}`;
      els.searchPrev.disabled = data.page <= 1;
      els.searchNext.disabled = data.page >= data.total_pages;
      showSearch("results");
    } catch (err) {
      els.searchErrorMessage.textContent = errorMessage(err);
      showSearch("error");
    }
  }

  /* --------------------------------------------------------- fiche livre */

  let dialogToken = 0;

  function showDialog(state) {
    els.dialogLoading.hidden = state !== "loading";
    els.dialogError.hidden = state !== "error";
    els.dialogContent.hidden = state !== "content";
  }

  function renderDetail(book) {
    const c = els.dialogContent;
    fillCover(c.querySelector(".cover-slot"), book);
    c.querySelector(".dialog-categories").textContent = joinList(book.categories) || "Non classé";
    c.querySelector(".dialog-title").textContent = book.title;
    const subtitle = c.querySelector(".dialog-subtitle");
    subtitle.textContent = book.subtitle || "";
    subtitle.hidden = !book.subtitle;
    c.querySelector(".dialog-authors").textContent = joinList(book.authors) || "Auteur inconnu";
    // textContent : la description n'est jamais interprétée comme du HTML.
    c.querySelector(".dialog-description").textContent = book.description || "Pas de description.";

    const meta = [
      ["Année", book.published_year],
      ["Pages", book.num_pages],
      ["Note moyenne", book.average_rating != null ? `${book.average_rating.toFixed(2)} / 5` : null],
      ["Nombre de notes", book.ratings_count != null ? book.ratings_count.toLocaleString("fr-FR") : null],
      ["ISBN-13", book.isbn13],
      ["ISBN-10", book.isbn10],
    ];
    const dl = c.querySelector(".dialog-meta");
    dl.replaceChildren();
    for (const [label, value] of meta) {
      if (value === null || value === undefined || value === "") continue;
      const wrap = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = String(value);
      wrap.append(dt, dd);
      dl.appendChild(wrap);
    }
  }

  async function openBook(bookId) {
    const token = ++dialogToken;
    els.dialog.dataset.bookId = bookId; // la fiche est aussi une vue [data-book-id]
    showDialog("loading");
    if (!els.dialog.open) els.dialog.showModal();
    try {
      const book = await requestJson(`/api/books/${encodeURIComponent(bookId)}`);
      if (token !== dialogToken) return; // une autre fiche a été ouverte entre-temps
      bookStates.set(bookId, book.state);
      renderDetail(book);
      showDialog("content");
      renderBookState(bookId);
      recordOpen(bookId); // enregistré seulement après un chargement réussi
    } catch (err) {
      if (token !== dialogToken) return;
      els.dialogErrorMessage.textContent = errorMessage(err);
      showDialog("error");
    }
  }

  /* -------------------------------------------------------- initialisation */

  // Listeners délégués : attachés UNE fois, jamais dans une fonction de rendu.
  els.grid.addEventListener("click", handleBookAction);
  els.recoGrid.addEventListener("click", handleBookAction);
  els.searchResults.addEventListener("click", handleBookAction);
  els.dialog.addEventListener("click", handleBookAction);

  // Clic sur le fond de la modale : fermeture.
  els.dialog.addEventListener("click", (event) => {
    if (event.target === els.dialog) els.dialog.close();
  });

  els.refresh.addEventListener("click", () => loadDiscovery("refresh"));
  els.discoveryRetry.addEventListener("click", () => loadDiscovery(lastDiscoveryRequest));

  els.searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    search.query = els.searchInput.value.trim();
    runSearch(1);
  });
  els.recoRetry.addEventListener("click", loadRecommendations);
  els.searchRetry.addEventListener("click", () => runSearch(search.page));
  els.searchPrev.addEventListener("click", () => runSearch(search.page - 1));
  els.searchNext.addEventListener("click", () => runSearch(search.page + 1));

  // La sélection crée le cookie de session ; les recommandations viennent après.
  loadDiscovery("load").then(loadRecommendations);
})();
