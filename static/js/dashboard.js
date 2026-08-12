const CHART_TEXT = "#b7b3ac";
const CHART_GRID = "rgba(237,233,223,0.08)";
const AMBER = "#e7b75f";
const SAGE = "#82b89a";
const CORAL = "#e2775c";

Chart.defaults.color = CHART_TEXT;
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.borderColor = CHART_GRID;

// BUG FIX: the README advertises a "live dashboard" that "polls
// /api/metrics", but nothing ever called loadMetrics() more than once, so
// numbers were frozen at page-load. Added a refresh interval below.
// Doing that safely requires tracking each Chart instance so it can be
// destroyed before the next render — Chart.js throws "Canvas is already
// in use" if you call `new Chart()` again on a canvas that already has a
// live chart attached to it, which is what naively re-running the render
// functions on every poll would have hit.
const charts = {};

function renderChart(canvasId, config) {
  if (charts[canvasId]) {
    charts[canvasId].destroy();
  }
  const ctx = document.getElementById(canvasId);
  charts[canvasId] = new Chart(ctx, config);
  return charts[canvasId];
}

function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

let metricsInFlight = false;

async function loadMetrics() {
  if (metricsInFlight) return; // avoid overlapping polls if a request is slow
  metricsInFlight = true;
  let m;
  try {
    const res = await fetch("/api/metrics");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    m = await res.json();
  } catch (err) {
    console.error("Failed to load metrics:", err);
    return;
  } finally {
    metricsInFlight = false;
  }

  document.getElementById("statTotal").textContent = m.total_requests;
  document.getElementById("statSuccess").textContent = `${m.success_rate}%`;
  document.getElementById("statLatency").textContent = `${m.avg_latency_ms} ms`;
  document.getElementById("statP95").textContent = `${m.p95_latency_ms} ms`;
  document.getElementById("statEntries").textContent = m.total_entries;
  document.getElementById("statSentiment").textContent =
    m.avg_sentiment === null ? "—" : m.avg_sentiment;

  document.getElementById("metaModel").textContent = m.demo_mode ? "rule-based-fallback" : m.model_name;
  document.getElementById("metaPrompt").textContent = m.prompt_version;
  document.getElementById("metaMode").textContent = m.demo_mode ? "Demo (no API key)" : "Live";

  renderSentimentChart(m.sentiment_trend);
  renderRequestsChart(m.requests_over_time);
  renderMoodChart(m.mood_distribution);
  renderModeChart(m.mode_distribution);
  renderThemeCloud(m.top_themes);
}

function renderSentimentChart(trend) {
  // BUG FIX: this branch used to call insertAdjacentHTML(..., "") — that
  // inserts an empty string, so the "no data yet" case silently rendered
  // an empty chart with no explanation. Show an actual empty state instead.
  const card = document.getElementById("sentimentChart").closest(".chart-card");
  let emptyMsg = card.querySelector(".chart-empty-msg");
  if (!trend.length) {
    if (!emptyMsg) {
      emptyMsg = document.createElement("p");
      emptyMsg.className = "muted chart-empty-msg";
      emptyMsg.textContent = "No entries yet — write a few to see your trend.";
      card.appendChild(emptyMsg);
    }
  } else if (emptyMsg) {
    emptyMsg.remove();
  }

  renderChart("sentimentChart", {
    type: "line",
    data: {
      labels: trend.map(t => fmtDate(t.timestamp)),
      datasets: [{
        label: "Sentiment",
        data: trend.map(t => t.sentiment_score),
        borderColor: AMBER,
        backgroundColor: "rgba(231,183,95,0.12)",
        fill: true,
        tension: 0.35,
        pointRadius: 3,
        pointBackgroundColor: AMBER,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { min: -1, max: 1, grid: { color: CHART_GRID } },
        x: { grid: { display: false } }
      }
    }
  });
}

function renderRequestsChart(perDay) {
  renderChart("requestsChart", {
    type: "bar",
    data: {
      labels: perDay.map(d => fmtDate(d.date)),
      datasets: [{
        label: "Requests",
        data: perDay.map(d => d.count),
        backgroundColor: "rgba(130,184,154,0.55)",
        borderRadius: 6,
        maxBarThickness: 28,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: CHART_GRID } },
        x: { grid: { display: false } }
      }
    }
  });
}

function renderMoodChart(dist) {
  const palette = [AMBER, SAGE, CORAL, "#7f8ccb", "#c98fd1", "#6fc2c9", "#d1a56f", "#9bb3e0"];
  renderChart("moodChart", {
    type: "doughnut",
    data: {
      labels: dist.map(d => d.mood),
      datasets: [{
        data: dist.map(d => d.count),
        backgroundColor: dist.map((_, i) => palette[i % palette.length]),
        borderColor: "#151b2e",
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, padding: 14 } } }
    }
  });
}

const MODE_NAMES = {
  journal: "Journal", brainstorm: "Brainstorm", writing: "Writing & Messages",
  decision: "Decision Helper", study: "Study Notes", general: "General",
};

function renderModeChart(dist) {
  const palette = [AMBER, SAGE, CORAL, "#7f8ccb", "#c98fd1", "#6fc2c9", "#d1a56f", "#9bb3e0"];
  renderChart("modeChart", {
    type: "doughnut",
    data: {
      labels: dist.map(d => MODE_NAMES[d.mode] || d.mode),
      datasets: [{
        data: dist.map(d => d.count),
        backgroundColor: dist.map((_, i) => palette[i % palette.length]),
        borderColor: "#151b2e",
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, padding: 14 } } }
    }
  });
}

function renderThemeCloud(themes) {
  const el = document.getElementById("themeCloud");
  if (!themes.length) {
    el.innerHTML = `<p class="muted">No themes yet — write a few entries first.</p>`;
    return;
  }
  el.innerHTML = "";
  themes.forEach(t => {
    const tag = document.createElement("span");
    tag.className = "theme-tag";
    tag.style.fontSize = `${0.72 + Math.min(t.count, 5) * 0.05}rem`;
    tag.textContent = `${t.theme} · ${t.count}`;
    el.appendChild(tag);
  });
}

loadMetrics();
// Live dashboard: refresh in the background every 15s. Pause while the tab
// is hidden so we're not burning requests/DB reads for nobody.
const POLL_MS = 15000;
let pollHandle = setInterval(loadMetrics, POLL_MS);
document.addEventListener("visibilitychange", () => {
  clearInterval(pollHandle);
  if (!document.hidden) {
    loadMetrics();
    pollHandle = setInterval(loadMetrics, POLL_MS);
  }
});
