const MEMORY_CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || "";

function escapeMemoryHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

async function loadMemory() {
  const list = document.getElementById("memory-list");
  const empty = document.getElementById("memory-empty");
  if (!list) return;

  try {
    const res = await fetch("/api/memory");
    if (!res.ok) return;
    const data = await res.json();
    const facts = data.facts || [];

    list.innerHTML = "";
    empty.hidden = facts.length > 0;

    facts.forEach(f => {
      const li = document.createElement("li");
      li.className = "memory-item";
      li.innerHTML = `
        <span class="memory-item-text"><span class="memory-item-key">${escapeMemoryHtml(f.key)}</span>${escapeMemoryHtml(f.value)}</span>
        <button type="button" class="memory-item-forget" data-key="${escapeMemoryHtml(f.key)}">Forget</button>
      `;
      list.appendChild(li);
    });

    list.querySelectorAll(".memory-item-forget").forEach(btn => {
      btn.addEventListener("click", () => forgetMemory(btn.dataset.key));
    });
  } catch (err) {
    // Non-critical section of the profile page — fail silently rather
    // than blocking the rest of the page from rendering.
  }
}

async function forgetMemory(key) {
  try {
    const res = await fetch(`/api/memory/${encodeURIComponent(key)}`, {
      method: "DELETE",
      headers: { "X-CSRFToken": MEMORY_CSRF_TOKEN },
    });
    if (res.ok) loadMemory();
  } catch (err) {
    // ignore — the list simply won't update
  }
}

document.addEventListener("DOMContentLoaded", loadMemory);
