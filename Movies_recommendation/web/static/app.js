"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("status"),
    error: $("error"),
    errorMessage: $("error-message"),
    retry: $("retry"),
    empty: $("empty"),
    grid: $("grid"),
    meta: $("meta"),
    referenceDate: $("reference-date"),
    scoreNote: $("score-note"),
    template: $("card-template"),
  };

  const scoreFormat = new Intl.NumberFormat("fr-FR", {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  });
  const dateFormat = new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "long",
    timeStyle: "medium",
    timeZone: "UTC",
  });

  function show(state) {
    els.status.hidden = state !== "loading";
    els.error.hidden = state !== "error";
    els.empty.hidden = state !== "empty";
    els.grid.hidden = state !== "results";
    els.scoreNote.hidden = state !== "results";
  }

  function extractYear(title) {
    const match = /\((\d{4})\)\s*$/.exec(title || "");
    return match ? match[1] : "";
  }

  // Visuel de remplacement déterministe à partir de l'identifiant du film.
  function posterHues(movieId) {
    const h1 = (Number(movieId) * 47) % 360;
    return [h1, (h1 + 40) % 360];
  }

  function renderCard(item) {
    const node = els.template.content.firstElementChild.cloneNode(true);
    const title = item.title || `Film n° ${item.movieId}`;
    const [h1, h2] = posterHues(item.movieId);

    const poster = node.querySelector(".poster");
    poster.style.setProperty("--h1", String(h1));
    poster.style.setProperty("--h2", String(h2));
    node.querySelector(".poster-rank").textContent = String(item.rank);
    node.querySelector(".poster-year").textContent = extractYear(item.title);

    node.querySelector(".rank").textContent = `N° ${item.rank}`;
    node.querySelector(".title").textContent = title;

    const genres = node.querySelector(".genres");
    const list = Array.isArray(item.genres) ? item.genres : [];
    if (list.length === 0) {
      genres.remove();
    } else {
      for (const genre of list) {
        const li = document.createElement("li");
        li.textContent = genre;
        genres.appendChild(li);
      }
    }

    node.querySelector(".score-value").textContent =
      typeof item.score === "number" ? scoreFormat.format(item.score) : "—";
    return node;
  }

  function renderReferenceDate(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      els.referenceDate.textContent = String(iso);
    } else {
      els.referenceDate.dateTime = iso;
      els.referenceDate.textContent = `${dateFormat.format(date)} UTC`;
    }
    els.meta.hidden = false;
  }

  async function load() {
    show("loading");
    try {
      const response = await fetch("/api/recommendations", {
        headers: { Accept: "application/json" },
      });
      let data = null;
      try {
        data = await response.json();
      } catch (_) {
        data = null;
      }
      if (!response.ok) {
        const detail = data && data.detail ? data.detail : `Erreur HTTP ${response.status}`;
        throw new Error(detail);
      }
      if (!data || !Array.isArray(data.recommendations)) {
        throw new Error("Réponse inattendue du serveur.");
      }

      if (data.reference_date) renderReferenceDate(data.reference_date);

      if (data.recommendations.length === 0) {
        show("empty");
        return;
      }
      const fragment = document.createDocumentFragment();
      for (const item of data.recommendations) fragment.appendChild(renderCard(item));
      els.grid.replaceChildren(fragment);
      show("results");
    } catch (err) {
      els.errorMessage.textContent =
        err instanceof TypeError
          ? "Le serveur est injoignable. Vérifiez que l’application est lancée."
          : err.message;
      show("error");
    }
  }

  els.retry.addEventListener("click", load);
  load();
})();
