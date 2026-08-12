const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || "";

const chatWindow = document.getElementById("chatWindow");
const chatLoading = document.getElementById("chatLoading");
const chatEmpty = document.getElementById("chatEmpty");
const chatSuggestions = document.getElementById("chatSuggestions");
const chatErrorMsg = document.getElementById("chatErrorMsg");
const chatInput = document.getElementById("chatInput");
const chatCharCount = document.getElementById("chatCharCount");
const chatSendBtn = document.getElementById("chatSendBtn");
const chatClearBtn = document.getElementById("chatClearBtn");
const chatHeaderMeta = document.getElementById("chatHeaderMeta");
const chatHistoryBadge = document.getElementById("chatHistoryBadge");
const chatHistoryBadgeText = document.getElementById("chatHistoryBadgeText");
const chatScrollBtn = document.getElementById("chatScrollBtn");
const chatConfirmOverlay = document.getElementById("chatConfirmOverlay");
const chatConfirmCancel = document.getElementById("chatConfirmCancel");
const chatConfirmOk = document.getElementById("chatConfirmOk");

const chatImageInput = document.getElementById("chatImageInput");
const chatPdfInput = document.getElementById("chatPdfInput");
const chatAttachImageBtn = document.getElementById("chatAttachImageBtn");
const chatAttachPdfBtn = document.getElementById("chatAttachPdfBtn");
const chatAttachPreview = document.getElementById("chatAttachPreview");
const chatAttachPreviewThumb = document.getElementById("chatAttachPreviewThumb");
const chatAttachPreviewName = document.getElementById("chatAttachPreviewName");
const chatAttachRemoveBtn = document.getElementById("chatAttachRemoveBtn");

const CHAR_LIMIT = 2000;
const CHAR_WARN_AT = 1800;
const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024; // keep in sync with config.MAX_ATTACHMENT_SIZE_BYTES

let sending = false;
let lastRenderedRole = null;   // for consecutive-message grouping
let lastRenderedDayKey = null; // for day dividers
let userIsNearBottom = true;   // whether to auto-scroll on new content
let savedMessageCount = 0;     // running total for the history badge
let pendingAttachment = null;  // { file, kind: "image"|"pdf" } selected but not yet sent

function bumpHistoryBadge(by) {
  savedMessageCount += by;
  chatHistoryBadge.hidden = savedMessageCount <= 0;
  chatHistoryBadgeText.textContent = `${savedMessageCount} message${savedMessageCount === 1 ? "" : "s"} saved`;
  chatHistoryBadge.removeAttribute("title");
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function timeLabel(isoString) {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function dayKey(isoString) {
  return new Date(isoString).toDateString();
}

function dayDividerLabel(isoString) {
  const d = new Date(isoString);
  const now = new Date();
  const oneDay = 24 * 60 * 60 * 1000;
  const startOfDay = x => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / oneDay);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], sameYear ? { month: "long", day: "numeric" } : { month: "long", day: "numeric", year: "numeric" });
}

function isNearBottom() {
  return chatWindow.scrollHeight - chatWindow.scrollTop - chatWindow.clientHeight < 120;
}

function scrollToBottom(force) {
  if (force || userIsNearBottom) {
    chatWindow.scrollTop = chatWindow.scrollHeight;
    chatScrollBtn.hidden = true;
  }
}

chatWindow.addEventListener("scroll", () => {
  userIsNearBottom = isNearBottom();
  chatScrollBtn.hidden = userIsNearBottom;
});

chatScrollBtn.addEventListener("click", () => {
  userIsNearBottom = true;
  scrollToBottom(true);
});

function maybeInsertDayDivider(isoString) {
  const key = dayKey(isoString);
  if (key === lastRenderedDayKey) return;
  lastRenderedDayKey = key;
  lastRenderedRole = null; // a new day always starts a fresh visual group
  const divider = document.createElement("div");
  divider.className = "chat-day-divider";
  divider.innerHTML = `<span>${escapeHtml(dayDividerLabel(isoString))}</span>`;
  chatWindow.appendChild(divider);
}

function contextNote(meta) {
  if (!meta) return "";
  const bits = [];
  if (meta.ragEntriesUsed) bits.push(`${meta.ragEntriesUsed} past entr${meta.ragEntriesUsed === 1 ? "y" : "ies"}`);
  if (meta.memoryFactsUsed) bits.push(`${meta.memoryFactsUsed} memor${meta.memoryFactsUsed === 1 ? "y" : "ies"}`);
  return bits.length ? `Drew on ${bits.join(" + ")}` : "";
}

// Renders an attachment inside a bubble. `attachment` is either a server
// object ({kind, mime, name, url}) from history/the send response, or a
// local-only preview object ({kind, name, objectUrl}) for the instant the
// user's own message appears, before the server round-trip returns a URL.
function attachmentMarkup(attachment) {
  if (!attachment) return "";
  const url = attachment.url || attachment.objectUrl || "";
  const name = escapeHtml(attachment.name || "attachment");
  if (attachment.kind === "image") {
    return `<a class="chat-attachment chat-attachment-image" href="${url}" target="_blank" rel="noopener">
      <img src="${url}" alt="${name}" loading="lazy">
    </a>`;
  }
  return `<a class="chat-attachment chat-attachment-pdf" href="${url}" target="_blank" rel="noopener">
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M5 2.5h6l4 4V17a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 17V3a.5.5 0 0 1 .5-.5z"/>
      <path d="M11 2.5V6a.5.5 0 0 0 .5.5H15"/>
    </svg>
    <span>${name}</span>
  </a>`;
}

// Appends one message bubble. `role` is "user" or "assistant"; `meta` is an
// optional {ragEntriesUsed, memoryFactsUsed} object, only present for a
// reply that was just received live (not for reloaded history). `attachment`
// is optional (see attachmentMarkup above).
function appendBubble(role, content, isoString, meta, attachment) {
  chatEmpty.hidden = true;
  maybeInsertDayDivider(isoString);

  const grouped = role === lastRenderedRole;
  lastRenderedRole = role;

  const row = document.createElement("div");
  row.className = `chat-row chat-row-${role}${grouped ? " chat-row-grouped" : ""}`;

  const avatar = role === "assistant"
    ? `<span class="chat-avatar chat-avatar-brand" aria-hidden="true">
         <svg width="14" height="14" viewBox="0 0 30 30">
           <circle cx="15" cy="15" r="3.2" fill="currentColor"/>
           <circle cx="15" cy="15" r="8" fill="none" stroke="currentColor" stroke-opacity="0.55" stroke-width="1.6"/>
         </svg>
       </span>`
    : `<span class="chat-avatar chat-avatar-spacer" aria-hidden="true"></span>`;

  const note = contextNote(meta);

  row.innerHTML = `
    ${avatar}
    <div class="chat-bubble chat-bubble-${role}">
      ${attachmentMarkup(attachment)}
      ${content ? '<p class="chat-bubble-text"></p>' : ""}
      <span class="chat-bubble-foot">
        ${note ? `<span class="chat-bubble-note">${escapeHtml(note)}</span>` : ""}
        <span class="chat-bubble-time">${escapeHtml(timeLabel(isoString))}</span>
      </span>
    </div>
  `;
  const textEl = row.querySelector(".chat-bubble-text");
  if (textEl) textEl.textContent = content;
  chatWindow.appendChild(row);
  return row;
}

function appendTypingBubble() {
  maybeInsertDayDivider(new Date().toISOString());
  const grouped = "assistant" === lastRenderedRole;
  const row = document.createElement("div");
  row.className = `chat-row chat-row-assistant chat-typing-row${grouped ? " chat-row-grouped" : ""}`;
  row.innerHTML = `
    <span class="chat-avatar chat-avatar-brand" aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 30 30">
        <circle cx="15" cy="15" r="3.2" fill="currentColor"/>
        <circle cx="15" cy="15" r="8" fill="none" stroke="currentColor" stroke-opacity="0.55" stroke-width="1.6"/>
      </svg>
    </span>
    <div class="chat-bubble chat-bubble-assistant chat-bubble-typing">
      <span class="chat-typing-dots"><span></span><span></span><span></span></span>
    </div>
  `;
  chatWindow.appendChild(row);
  scrollToBottom();
  return row;
}

function autoResize() {
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 160)}px`;
}

function fileKindFor(file) {
  if (file.type === "application/pdf") return "pdf";
  if (file.type.startsWith("image/")) return "image";
  return null;
}

function setPendingAttachment(file) {
  const kind = fileKindFor(file);
  if (!kind) {
    chatErrorMsg.textContent = "Attachments must be an image (PNG, JPEG, WEBP, GIF) or a PDF.";
    chatErrorMsg.hidden = false;
    return;
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    chatErrorMsg.textContent = `Attachments are limited to ${Math.round(MAX_ATTACHMENT_BYTES / (1024 * 1024))}MB.`;
    chatErrorMsg.hidden = false;
    return;
  }
  chatErrorMsg.hidden = true;
  clearPendingAttachment(); // release any previous object URL first
  pendingAttachment = { file, kind };
  renderAttachPreview();
  updateComposerState();
}

function renderAttachPreview() {
  if (!pendingAttachment) {
    chatAttachPreview.hidden = true;
    chatAttachPreviewThumb.innerHTML = "";
    return;
  }
  const { file, kind } = pendingAttachment;
  chatAttachPreview.hidden = false;
  chatAttachPreviewName.textContent = file.name;
  if (kind === "image") {
    pendingAttachment.objectUrl = URL.createObjectURL(file);
    chatAttachPreviewThumb.innerHTML = `<img src="${pendingAttachment.objectUrl}" alt="">`;
  } else {
    chatAttachPreviewThumb.innerHTML = `<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M5 2.5h6l4 4V17a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 17V3a.5.5 0 0 1 .5-.5z"/>
      <path d="M11 2.5V6a.5.5 0 0 0 .5.5H15"/>
    </svg>`;
  }
}

function clearPendingAttachment() {
  if (pendingAttachment?.objectUrl) URL.revokeObjectURL(pendingAttachment.objectUrl);
  pendingAttachment = null;
  chatImageInput.value = "";
  chatPdfInput.value = "";
  renderAttachPreview();
}

chatAttachImageBtn.addEventListener("click", () => chatImageInput.click());
chatAttachPdfBtn.addEventListener("click", () => chatPdfInput.click());
chatImageInput.addEventListener("change", () => {
  if (chatImageInput.files[0]) setPendingAttachment(chatImageInput.files[0]);
});
chatPdfInput.addEventListener("change", () => {
  if (chatPdfInput.files[0]) setPendingAttachment(chatPdfInput.files[0]);
});
chatAttachRemoveBtn.addEventListener("click", () => {
  clearPendingAttachment();
  updateComposerState();
});

function updateComposerState() {
  const len = chatInput.value.length;
  chatCharCount.textContent = len;
  chatCharCount.classList.toggle("char-count-warn", len >= CHAR_WARN_AT && len < CHAR_LIMIT);
  chatCharCount.classList.toggle("char-count-limit", len >= CHAR_LIMIT);
  chatSendBtn.disabled = sending || (chatInput.value.trim().length === 0 && !pendingAttachment);
}

chatInput.addEventListener("input", () => {
  updateComposerState();
  autoResize();
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

function formatFirstMessageDate(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}

async function loadHistory() {
  try {
    const res = await fetch("/api/chat/history?limit=100");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    chatLoading.hidden = true;

    const messages = data.messages || [];
    const totalCount = data.total_count ?? messages.length;
    savedMessageCount = totalCount;

    if (!messages.length) {
      chatEmpty.hidden = false;
      chatClearBtn.hidden = true;
      chatHistoryBadge.hidden = true;
    } else {
      chatEmpty.hidden = true;
      chatClearBtn.hidden = false;
      messages.forEach(m => appendBubble(m.role, m.content, m.created_at, null, m.attachment));
      scrollToBottom(true);

      chatHistoryBadge.hidden = false;
      chatHistoryBadgeText.textContent = `${totalCount} message${totalCount === 1 ? "" : "s"} saved`;
      if (messages.length < totalCount) { /* older turns exist beyond what's loaded */
        chatHistoryBadge.title = `Showing your most recent ${messages.length} of ${totalCount} messages`;
      } else {
        chatHistoryBadge.removeAttribute("title");
      }
    }

    if (chatHeaderMeta) {
      if (data.day_count > 1) {
        const since = formatFirstMessageDate(data.first_message_at);
        chatHeaderMeta.textContent = since
          ? `Continuing your conversation from ${since} — ${data.day_count} days and counting. EchoMind remembers what you told it.`
          : `You've talked across ${data.day_count} days so far — EchoMind remembers what you told it.`;
      } else if (data.day_count === 1) {
        chatHeaderMeta.textContent = "Remembers what you've told it — saved facts and past entries carry in automatically.";
      }
    }
  } catch (err) {
    chatLoading.hidden = true;
    chatErrorMsg.textContent = "Couldn't load your conversation history.";
    chatErrorMsg.hidden = false;
  }
}

async function sendMessage(retryText, retryAttachment) {
  const text = retryText !== undefined ? retryText : chatInput.value.trim();
  const attachment = retryAttachment !== undefined ? retryAttachment : pendingAttachment;
  chatErrorMsg.hidden = true;
  if ((!text && !attachment) || sending) return;

  sending = true;
  chatSendBtn.disabled = true;

  const localAttachmentPreview = attachment
    ? { kind: attachment.kind, name: attachment.file.name, objectUrl: attachment.objectUrl || URL.createObjectURL(attachment.file) }
    : null;
  // Ownership of the blob URL passes to localAttachmentPreview (and to
  // `attachment` for a retry) — null it out on pendingAttachment so
  // clearPendingAttachment() below doesn't revoke a URL still on screen.
  if (attachment) attachment.objectUrl = localAttachmentPreview.objectUrl;
  const userRow = appendBubble("user", text, new Date().toISOString(), null, localAttachmentPreview);
  userRow.dataset.rawText = text;
  scrollToBottom(true);

  if (retryText === undefined) {
    chatInput.value = "";
    // Reset composer without revoking the blob URL — its ownership just
    // passed to localAttachmentPreview (see above), which is still on screen.
    pendingAttachment = null;
    chatImageInput.value = "";
    chatPdfInput.value = "";
    renderAttachPreview();
    updateComposerState();
    autoResize();
  }

  const typingRow = appendTypingBubble();

  try {
    let res;
    if (attachment) {
      const formData = new FormData();
      formData.append("message", text);
      formData.append("attachment", attachment.file, attachment.file.name);
      res = await fetch("/api/chat", {
        method: "POST",
        headers: { "X-CSRFToken": CSRF_TOKEN },
        body: formData,
      });
    } else {
      res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF_TOKEN },
        body: JSON.stringify({ message: text }),
      });
    }
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || "Something went wrong.");
    }

    // Swap the local preview for the server-persisted attachment (with its
    // durable URL) now that the round trip succeeded.
    if (data.attachment) {
      const link = userRow.querySelector(".chat-attachment");
      if (link) link.setAttribute("href", data.attachment.url);
      const img = userRow.querySelector(".chat-attachment-image img");
      if (img) img.src = data.attachment.url;
    }
    if (localAttachmentPreview?.objectUrl) URL.revokeObjectURL(localAttachmentPreview.objectUrl);

    typingRow.remove();
    appendBubble("assistant", data.reply, new Date().toISOString(), {
      ragEntriesUsed: data.rag_entries_used,
      memoryFactsUsed: data.memory_facts_used,
    });
    chatClearBtn.hidden = false;
    bumpHistoryBadge(2); // the user turn + this reply, both now persisted
    scrollToBottom();
  } catch (err) {
    typingRow.remove();
    markRowFailed(userRow, text, attachment);
    chatErrorMsg.textContent = err.message;
    chatErrorMsg.hidden = false;
  } finally {
    sending = false;
    updateComposerState();
    chatInput.focus();
  }
}

function markRowFailed(row, text, attachment) {
  row.classList.add("chat-row-failed");
  const bubble = row.querySelector(".chat-bubble");
  if (bubble.querySelector(".chat-retry-btn")) return;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "chat-retry-btn";
  retry.textContent = "Failed to send · Retry";
  retry.addEventListener("click", () => {
    row.remove();
    lastRenderedRole = null; // force a fresh group/day-divider check on retry
    sendMessage(text, attachment);
  });
  bubble.appendChild(retry);
}

chatSendBtn.addEventListener("click", () => sendMessage());

chatSuggestions?.querySelectorAll(".chat-suggestion-chip").forEach(chip => {
  chip.addEventListener("click", () => sendMessage(chip.textContent.trim()));
});

function openConfirm() {
  chatConfirmOverlay.hidden = false;
  chatConfirmOk.focus();
}
function closeConfirm() {
  chatConfirmOverlay.hidden = true;
}

chatClearBtn.addEventListener("click", openConfirm);
chatConfirmCancel.addEventListener("click", closeConfirm);
chatConfirmOverlay.addEventListener("click", (e) => {
  if (e.target === chatConfirmOverlay) closeConfirm();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !chatConfirmOverlay.hidden) closeConfirm();
});

chatConfirmOk.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/chat", {
      method: "DELETE",
      headers: { "X-CSRFToken": CSRF_TOKEN },
    });
    if (!res.ok) throw new Error("Couldn't clear the conversation.");
    chatWindow.querySelectorAll(".chat-row, .chat-day-divider").forEach(el => el.remove());
    lastRenderedRole = null;
    lastRenderedDayKey = null;
    chatEmpty.hidden = false;
    chatClearBtn.hidden = true;
    chatHistoryBadge.hidden = true;
  } catch (err) {
    chatErrorMsg.textContent = err.message;
    chatErrorMsg.hidden = false;
  } finally {
    closeConfirm();
  }
});

updateComposerState();
loadHistory();
