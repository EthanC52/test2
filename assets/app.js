"use strict";

const TOKEN_ORDER = ["eurcv", "usdcv"];
const charts = [];

const integerFormatter = new Intl.NumberFormat("fr-FR", {
  maximumFractionDigits: 0,
});

function formatCurrency(value, currency) {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency,
    maximumFractionDigits: numeric < 1000 ? 2 : 0,
  }).format(numeric);
}

function formatDate(isoString, runStatus = "complete") {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "Mise à jour inconnue";
  const prefix = runStatus === "complete" ? "Mis à jour" : "Tentative de mise à jour";
  return `${prefix} le ${new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)}`;
}


function snapshotTimestamp(item) {
  if (!item || typeof item !== "object") return null;
  const value = item.timestamp || item.date;
  return typeof value === "string" && value.trim() ? value : null;
}

function formatHistoryTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const hasTime = value.includes("T");
  return new Intl.DateTimeFormat("fr-FR", hasTime
    ? { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }
    : { dateStyle: "short", timeZone: "UTC" }
  ).format(date);
}

function availabilityText(token) {
  const configured = Number(token.configured_chain_count || 0);
  const fresh = Number(token.fresh_chain_count || 0);
  const stale = Number(token.stale_chain_count || 0);
  if (token.status === "complete") return `${fresh}/${configured} chaînes actualisées`;
  if (token.status === "partial") {
    return `${fresh}/${configured} chaînes actualisées · ${stale} valeur(s) conservée(s)`;
  }
  if (token.status === "stale") return "Dernières valeurs connues";
  return "Données actuellement indisponibles";
}

function cardTemplate(tokenId, token) {
  const largest = token.largest_holder_chain;
  const largestText = largest
    ? `Plus grande base : ${largest.label} · ${integerFormatter.format(largest.holders)}`
    : "Plus grande base indisponible";
  const holders = Number.isFinite(Number(token.holders))
    ? integerFormatter.format(Number(token.holders))
    : "—";

  return `
    <article class="token-card" data-status="${token.status || "unknown"}">
      <header class="card-header">
        <div>
          <h2 class="token-symbol">${token.symbol}</h2>
          <p class="token-name">${token.name}</p>
        </div>
        <span class="currency-pill">${token.currency}</span>
      </header>

      <div class="source-health">${availabilityText(token)}</div>

      <section class="kpis">
        <div class="kpi">
          <span class="kpi-label">Holders actuels</span>
          <strong class="kpi-value">${holders}</strong>
          <span class="kpi-detail">${largestText}</span>
        </div>
        <div class="kpi">
          <span class="kpi-label">Supply courante</span>
          <strong class="kpi-value">${formatCurrency(token.supply, token.currency)}</strong>
          <span class="kpi-detail">Valeur nominale dans la devise d'ancrage</span>
        </div>
      </section>

      <section class="chart-section">
        <div class="chart-title">
          <h3>Capitalisation nominale historique</h3>
          <span>${token.currency} · snapshots de supply</span>
        </div>
        <div class="chart-wrap" id="chart-wrap-${tokenId}">
          <canvas id="chart-${tokenId}" aria-label="Historique de ${token.symbol}"></canvas>
        </div>
      </section>
    </article>
  `;
}

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function loadOptionalJson(path) {
  try {
    return await loadJson(path);
  } catch (error) {
    console.warn(error);
    return null;
  }
}

function showChartFallback(tokenId, message) {
  const wrap = document.getElementById(`chart-wrap-${tokenId}`);
  if (!wrap) return;
  wrap.innerHTML = `<p class="chart-fallback">${message}</p>`;
}

function renderChart(tokenId, token, history) {
  const snapshots = Array.isArray(history?.snapshots) ? history.snapshots : [];
  const cleanSnapshots = snapshots
    .map((item) => ({
      timestamp: snapshotTimestamp(item),
      supply: Number(item?.supply),
    }))
    .filter((item) => item.timestamp && Number.isFinite(item.supply))
    .sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  if (!cleanSnapshots.length) {
    showChartFallback(tokenId, "Historique indisponible, données courantes conservées.");
    return false;
  }
  if (typeof window.Chart !== "function") {
    showChartFallback(tokenId, "La librairie graphique est indisponible.");
    return false;
  }

  const context = document.getElementById(`chart-${tokenId}`);
  const labels = cleanSnapshots.map((item) => formatHistoryTimestamp(item.timestamp));
  const values = cleanSnapshots.map((item) => item.supply);

  charts.push(
    new Chart(context, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: `Capitalisation nominale (${token.currency})`,
            data: values,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 12,
            tension: 0.22,
            fill: true,
            borderColor: tokenId === "eurcv" ? "#54e6b1" : "#7ea7ff",
            backgroundColor:
              tokenId === "eurcv" ? "rgba(84, 230, 177, 0.10)" : "rgba(126, 167, 255, 0.10)",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => formatCurrency(item.parsed.y, token.currency),
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: "#8293aa", maxTicksLimit: 7 },
          },
          y: {
            beginAtZero: false,
            grid: { color: "rgba(148, 163, 184, 0.10)" },
            ticks: {
              color: "#8293aa",
              callback: (value) =>
                new Intl.NumberFormat("fr-FR", {
                  notation: "compact",
                  maximumFractionDigits: 1,
                }).format(value),
            },
          },
        },
      },
    }),
  );
  return true;
}

async function init() {
  const dashboard = document.getElementById("dashboard");
  const errorBox = document.getElementById("error");
  const statusDot = document.querySelector(".status-dot");

  try {
    const current = await loadJson("data/current.json");
    const availableIds = TOKEN_ORDER.filter((id) => current.tokens?.[id]);
    if (!availableIds.length) throw new Error("Aucun token dans data/current.json");

    const histories = await Promise.all(
      availableIds.map((id) => loadOptionalJson(`data/history/${id}.json`)),
    );

    dashboard.innerHTML = availableIds
      .map((id) => cardTemplate(id, current.tokens[id]))
      .join("");
    document.getElementById("last-update").textContent = formatDate(
      current.generated_at,
      current.run_status,
    );

    let missingHistory = false;
    availableIds.forEach((id, index) => {
      if (!renderChart(id, current.tokens[id], histories[index])) missingHistory = true;
    });

    const sourceErrorCount = Array.isArray(current.source_errors)
      ? current.source_errors.length
      : 0;
    if (current.run_status !== "complete" || missingHistory) {
      statusDot?.classList.add("degraded");
      errorBox.hidden = false;
      errorBox.className = "warning";
      const sourceText = sourceErrorCount
        ? `${sourceErrorCount} source(s) ont conservé leur dernière valeur connue.`
        : "Certaines données ne sont pas disponibles.";
      const historyText = missingHistory
        ? " Une courbe manquante n'empêche pas l'affichage des données courantes."
        : "";
      errorBox.textContent = `${sourceText}${historyText}`;
    }
  } catch (error) {
    console.error(error);
    statusDot?.classList.add("failed");
    errorBox.hidden = false;
    errorBox.className = "error";
    errorBox.textContent =
      "Données courantes indisponibles. L'historique présent dans les JSON n'a pas été modifié.";
    document.getElementById("last-update").textContent = "Données indisponibles";
  }
}

document.addEventListener("DOMContentLoaded", init);
