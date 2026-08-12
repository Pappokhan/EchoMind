const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || "";

const entryEl = document.getElementById("entry");
const charCountEl = document.getElementById("charCount");
const analyzeBtn = document.getElementById("analyzeBtn");
const btnLabel = document.getElementById("btnLabel");
const errorMsg = document.getElementById("errorMsg");
const resultCard = document.getElementById("resultCard");
const placeholderCard = document.getElementById("placeholderCard");
const placeholderText = document.getElementById("placeholderText");

const moodBadge = document.getElementById("moodBadge");
const latencyTag = document.getElementById("latencyTag");
const sentimentFill = document.getElementById("sentimentFill");
const sentimentValue = document.getElementById("sentimentValue");
const themesRow = document.getElementById("themesRow");
const reflectionText = document.getElementById("reflectionText");
const actionText = document.getElementById("actionText");
const recentList = document.getElementById("recentList");

const entryLabel = document.getElementById("entryLabel");
const scoreLabel = document.getElementById("scoreLabel");
const summaryLabel = document.getElementById("summaryLabel");
const actionLabel = document.getElementById("actionLabel");
const heroEyebrow = document.getElementById("heroEyebrow");
const heroSub = document.getElementById("heroSub");
const modeSelector = document.getElementById("modeSelector");
const modePills = modeSelector ? Array.from(modeSelector.querySelectorAll(".mode-pill")) : [];

let currentMode = "journal";
let currentListeningLabel = "Listening…";
let currentButtonLabel = "Reflect on this";

const MODE_ICONS = {};
modePills.forEach(p => { MODE_ICONS[p.dataset.mode] = p.querySelector(".mode-icon").textContent; });

function applyMode(pill) {
  if (!pill) return;
  currentMode = pill.dataset.mode;
  currentListeningLabel = pill.dataset.listeningLabel;
  currentButtonLabel = pill.dataset.buttonLabel;

  modePills.forEach(p => {
    const active = p === pill;
    p.classList.toggle("active", active);
    p.setAttribute("aria-selected", active ? "true" : "false");
  });

  entryLabel.textContent = pill.dataset.entryLabel;
  entryEl.placeholder = pill.dataset.placeholder;
  btnLabel.textContent = pill.dataset.buttonLabel;
  scoreLabel.textContent = pill.dataset.scoreLabel;
  summaryLabel.textContent = pill.dataset.summaryLabel;
  actionLabel.textContent = pill.dataset.actionLabel;
  heroEyebrow.textContent = pill.dataset.tagline;
  heroSub.textContent = pill.dataset.description;
  if (placeholderText) {
    placeholderText.textContent = `Your ${pill.dataset.summaryLabel.toLowerCase()} will echo back here.`;
  }
}

modePills.forEach(pill => {
  pill.addEventListener("click", () => {
    applyMode(pill);
    loadRecentEntries();
  });
});

entryEl.addEventListener("input", () => {
  charCountEl.textContent = entryEl.value.length;
});

// BUG FIX: recentList and renderResult below build markup with template
// strings containing values that come from the model (mood, themes) or the
// user's own entry text. The old code only escaped "<" in entry_text,
// which still let "&", '"' and model-generated text break out of
// attributes/entities. Escape everything routed through innerHTML.
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

function renderResult(data) {
  placeholderCard.hidden = true;
  resultCard.hidden = false;

  moodBadge.textContent = data.mood || "—";
  // Transparency on what informed this read: RAG hits from the writer's
  // own past entries + saved cross-session memory (see /api/analyze's
  // rag_entries_used / memory_facts_used — populated by
  // llm_service._build_context_block).
  const contextBits = [];
  if (data.rag_entries_used) contextBits.push(`${data.rag_entries_used} past entr${data.rag_entries_used === 1 ? "y" : "ies"}`);
  if (data.memory_facts_used) contextBits.push(`${data.memory_facts_used} memor${data.memory_facts_used === 1 ? "y" : "ies"}`);
  const contextNote = contextBits.length ? ` · informed by ${contextBits.join(" + ")}` : "";
  latencyTag.textContent = `${data.demo_mode ? "demo · " : ""}${Math.round(data.latency_ms)} ms${contextNote}`;

  const score = Number(data.sentiment_score) || 0;
  const pct = Math.abs(score) * 50;
  sentimentFill.style.width = `${pct}%`;
  sentimentFill.style.left = score >= 0 ? "50%" : `${50 - pct}%`;
  sentimentFill.style.background = moodColor(score);
  sentimentValue.textContent = score.toFixed(2);

  themesRow.innerHTML = "";
  (data.themes || []).forEach(t => {
    const chip = document.createElement("span");
    chip.className = "theme-chip";
    chip.textContent = t;
    themesRow.appendChild(chip);
  });

  reflectionText.textContent = data.reflection || "";
  actionText.textContent = data.suggested_action || "";
}

async function analyzeEntry() {
  const text = entryEl.value.trim();
  errorMsg.hidden = true;
  if (!text) {
    errorMsg.textContent = "Write something first — even a sentence is enough.";
    errorMsg.hidden = false;
    return;
  }

  analyzeBtn.disabled = true;
  btnLabel.textContent = currentListeningLabel;

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF_TOKEN },
      body: JSON.stringify({ entry_text: text, mode: currentMode }),
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || "Something went wrong.");
    }

    renderResult(data);
    entryEl.value = "";
    charCountEl.textContent = "0";
    loadRecentEntries();
    loadStreak();
  } catch (err) {
    errorMsg.textContent = err.message;
    errorMsg.hidden = false;
  } finally {
    analyzeBtn.disabled = false;
    btnLabel.textContent = currentButtonLabel;
  }
}

analyzeBtn.addEventListener("click", analyzeEntry);
entryEl.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyzeEntry();
});

function timeAgo(isoString) {
  const diffMs = Date.now() - new Date(isoString).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

async function loadRecentEntries() {
  try {
    const res = await fetch("/api/entries?limit=8");
    const entries = await res.json();

    if (!entries.length) {
      recentList.innerHTML = `<p class="muted">No entries yet — write your first one above.</p>`;
      return;
    }

    recentList.innerHTML = "";
    entries.forEach(e => {
      const row = document.createElement("div");
      row.className = "entry-row";
      const score = Number(e.sentiment_score) || 0;
      const icon = MODE_ICONS[e.mode] || "📝";
      row.innerHTML = `
        <span class="entry-dot" style="background:${moodColor(score)}"></span>
        <div class="entry-body">
          <div class="entry-meta">
            <span class="entry-mode-tag" title="${escapeHtml(e.mode || 'journal')}">${icon}</span>
            <span class="entry-mood">${escapeHtml(e.mood || "—")}</span>
            <span>${timeAgo(e.timestamp)}</span>
          </div>
          <p class="entry-text">${escapeHtml(e.entry_text)}</p>
        </div>
      `;
      recentList.appendChild(row);
    });
  } catch (err) {
    recentList.innerHTML = `<p class="muted">Couldn't load recent entries.</p>`;
  }
}

const streakBadge = document.getElementById("streakBadge");
const streakText = document.getElementById("streakText");

async function loadStreak() {
  if (!streakBadge) return;
  try {
    const res = await fetch("/api/streak");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const s = await res.json();

    if (!s.total_active_days) {
      streakBadge.hidden = true;
      return;
    }

    if (s.current_streak > 0) {
      const days = s.current_streak === 1 ? "day" : "days";
      streakText.textContent = s.wrote_today
        ? `${s.current_streak}-${days} streak — written today`
        : `${s.current_streak}-${days} streak — write today to keep it going`;
    } else {
      streakText.textContent = `Streak broken — write today to start a new one`;
    }
    streakBadge.hidden = false;
  } catch (err) {
    streakBadge.hidden = true;
  }
}

// Initialize from the server-rendered default (or first) active pill.
applyMode(modePills.find(p => p.classList.contains("active")) || modePills[0]);
loadRecentEntries();
loadStreak();
