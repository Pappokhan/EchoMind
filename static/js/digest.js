const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || "";

const periodSelector = document.getElementById("periodSelector");
const periodPills = periodSelector ? Array.from(periodSelector.querySelectorAll(".mode-pill")) : [];
const digestModeSelect = document.getElementById("digestModeSelect");
const digestPeriodLabel = document.getElementById("digestPeriodLabel");
const digestBtn = document.getElementById("digestBtn");
const digestBtnLabel = document.getElementById("digestBtnLabel");
const digestErrorMsg = document.getElementById("digestErrorMsg");
const digestResult = document.getElementById("digestResult");
const digestPlaceholder = document.getElementById("digestPlaceholder");
const digestHistory = document.getElementById("digestHistory");

let currentDays = 7;

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function moodColor(score) {
  if (score > 0.15) return "var(--sage)";
  if (score < -0.15) return "var(--coral)";
  return "var(--amber)";
}

periodPills.forEach(pill => {
  pill.addEventListener("click", () => {
    currentDays = Number(pill.dataset.days);
    periodPills.forEach(p => p.classList.toggle("active", p === pill));
    digestPeriodLabel.textContent = pill.querySelector("span").textContent;
  });
});

function renderDigest(data) {
  digestPlaceholder.hidden = true;

  if (data.empty) {
    digestResult.hidden = true;
    digestPlaceholder.hidden = false;
    digestPlaceholder.querySelector("p:last-child").textContent =
      `No entries in the ${data.period_label.toLowerCase()} yet — write a few first.`;
    return;
  }

  digestResult.hidden = false;
  document.getElementById("digestHeadline").textContent = data.headline || "—";
  document.getElementById("digestLatencyTag").textContent =
    `${data.demo_mode ? "demo · " : ""}${Math.round(data.latency_ms)} ms`;
  document.getElementById("digestTrend").textContent = data.mood_trend || "—";
  document.getElementById("digestNarrative").textContent = data.narrative || "";
  document.getElementById("digestFocus").textContent = data.suggested_focus || "";

  const statRow = document.getElementById("digestStatRow");
  const avg = data.avg_sentiment === null || data.avg_sentiment === undefined
    ? "—" : Number(data.avg_sentiment).toFixed(2);
  statRow.innerHTML = `
    <span class="digest-stat">${data.entry_count} <strong>entries</strong></span>
    <span class="digest-stat">avg score <strong>${avg}</strong></span>
    <span class="digest-stat">${escapeHtml(data.period_label)}</span>
  `;

  const themesRow = document.getElementById("digestThemesRow");
  themesRow.innerHTML = "";
  (data.top_themes || []).forEach(t => {
    const chip = document.createElement("span");
    chip.className = "theme-chip";
    chip.textContent = t;
    themesRow.appendChild(chip);
  });
}

async function generateDigest() {
  digestErrorMsg.hidden = true;
  digestBtn.disabled = true;
  digestBtnLabel.textContent = "Synthesizing…";

  try {
    const res = await fetch("/api/digest", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF_TOKEN },
      body: JSON.stringify({ days: currentDays, mode: digestModeSelect.value || null }),
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || "Something went wrong.");
    }

    renderDigest(data);
    loadDigestHistory();
  } catch (err) {
    digestErrorMsg.textContent = err.message;
    digestErrorMsg.hidden = false;
  } finally {
    digestBtn.disabled = false;
    digestBtnLabel.textContent = "Generate digest";
  }
}

digestBtn.addEventListener("click", generateDigest);

function timeAgo(isoString) {
  const diffMs = Date.now() - new Date(isoString).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

async function loadDigestHistory() {
  try {
    const res = await fetch("/api/digests?limit=8");
    const digests = await res.json();

    if (!digests.length) {
      digestHistory.innerHTML = `<p class="muted digest-history-empty">No digests generated yet.</p>`;
      return;
    }

    digestHistory.innerHTML = "";
    digests.forEach(d => {
      const row = document.createElement("div");
      row.className = "entry-row";
      const score = Number(d.avg_sentiment) || 0;
      row.innerHTML = `
        <span class="entry-dot" style="background:${moodColor(score)}"></span>
        <div class="entry-body">
          <div class="entry-meta">
            <span class="entry-mode-tag" title="period">${d.period_days}d</span>
            <span class="entry-mood">${escapeHtml(d.headline || "—")}</span>
            <span>${timeAgo(d.timestamp)}</span>
          </div>
          <p class="entry-text">${escapeHtml(d.narrative || "")}</p>
        </div>
      `;
      digestHistory.appendChild(row);
    });
  } catch (err) {
    digestHistory.innerHTML = `<p class="muted">Couldn't load past digests.</p>`;
  }
}

loadDigestHistory();
