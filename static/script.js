// ---------- Language code -> friendly name ----------
const LANG_NAMES = {
  en: "English", vi: "Vietnamese", ja: "Japanese", ko: "Korean",
  zh: "Chinese", "zh-cn": "Chinese", "zh-tw": "Chinese (TW)",
  es: "Spanish", fr: "French", de: "German", ru: "Russian",
  pt: "Portuguese", it: "Italian", th: "Thai", id: "Indonesian",
  ar: "Arabic", hi: "Hindi", tr: "Turkish", nl: "Dutch",
  pl: "Polish", sv: "Swedish", uk: "Ukrainian", he: "Hebrew",
  fa: "Persian", ms: "Malay", tl: "Tagalog", bn: "Bengali",
  ta: "Tamil", te: "Telugu", ur: "Urdu", cs: "Czech",
  el: "Greek", hu: "Hungarian", ro: "Romanian", no: "Norwegian",
  da: "Danish", fi: "Finnish",
};

function langLabel(code) {
  if (!code || code === "unknown") return "unknown";
  const base = String(code).toLowerCase();
  return LANG_NAMES[base] || code;
}

// ---------- DOM ----------
const tabs = document.querySelectorAll(".tab");
const panels = {
  url: document.getElementById("panel-url"),
  image: document.getElementById("panel-image"),
};
const statusCard = document.getElementById("status-card");
const statusMsg = document.getElementById("status-msg");
const resultCard = document.getElementById("result-card");
const resultText = document.getElementById("result-text");
const langBadge = document.getElementById("lang-badge");
const typeBadge = document.getElementById("type-badge");
const errorCard = document.getElementById("error-card");
const errorMsg = document.getElementById("error-msg");

const urlInput = document.getElementById("url");
const imageInput = document.getElementById("image");
const dzFilename = document.getElementById("dz-filename");
const dropzone = document.getElementById("dropzone");

// ---------- Tab switching ----------
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => {
      t.classList.remove("active");
      t.setAttribute("aria-selected", "false");
    });
    tab.classList.add("active");
    tab.setAttribute("aria-selected", "true");
    const active = tab.dataset.tab;
    Object.entries(panels).forEach(([k, panel]) => {
      panel.classList.toggle("hidden", k !== active);
    });
  });
});

// ---------- Dropzone UX ----------
imageInput.addEventListener("change", () => {
  const f = imageInput.files[0];
  if (f) {
    dzFilename.textContent = `${f.name} · ${(f.size / 1024).toFixed(0)} KB`;
  }
});

["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, () => dropzone.classList.remove("drag"))
);

// ---------- Show/hide helpers ----------
function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }
function reset() {
  hide(statusCard);
  hide(resultCard);
  hide(errorCard);
}

// ---------- Job polling ----------
async function pollJob(jobId) {
  while (true) {
    const res = await fetch(`/api/status/${jobId}`);
    if (!res.ok) {
      throw new Error(`Status check failed (${res.status})`);
    }
    const data = await res.json();
    if (data.status === "done") return data.result;
    if (data.status === "error") throw new Error(data.error || "Unknown error");
    statusMsg.textContent = data.progress || "Processing…";
    await new Promise((r) => setTimeout(r, 1200));
  }
}

function setBusy(isBusy) {
  document.querySelectorAll(".btn.primary").forEach((b) => {
    b.disabled = isBusy;
  });
}

// ---------- Actions ----------
async function runTranscribe() {
  const url = urlInput.value.trim();
  if (!url) {
    urlInput.focus();
    return;
  }
  reset();
  show(statusCard);
  statusMsg.textContent = "Submitting…";
  setBusy(true);
  try {
    const res = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    const result = await pollJob(data.job_id);
    renderResult(result);
  } catch (e) {
    hide(statusCard);
    show(errorCard);
    errorMsg.textContent = e.message || String(e);
  } finally {
    setBusy(false);
  }
}

async function runOCR() {
  const f = imageInput.files[0];
  if (!f) {
    imageInput.click();
    return;
  }
  const langs = document.getElementById("ocr-langs").value.trim() || "en,vi";
  reset();
  show(statusCard);
  statusMsg.textContent = "Uploading…";
  setBusy(true);
  try {
    const form = new FormData();
    form.append("image", f);
    form.append("langs", langs);
    const res = await fetch("/api/ocr", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    const result = await pollJob(data.job_id);
    renderResult(result);
  } catch (e) {
    hide(statusCard);
    show(errorCard);
    errorMsg.textContent = e.message || String(e);
  } finally {
    setBusy(false);
  }
}

function renderResult(result) {
  hide(statusCard);
  show(resultCard);
  const text = (result && result.text) || "";
  resultText.value = text || "(no text extracted)";
  langBadge.textContent = langLabel(result && result.language);
  typeBadge.textContent = (result && result.type) || "?";
  // Smooth scroll to result
  resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- Button handlers ----------
document.getElementById("submit-url").addEventListener("click", runTranscribe);
document.getElementById("submit-image").addEventListener("click", runOCR);

// Submit with Enter in URL field
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") runTranscribe();
});

document.getElementById("copy-btn").addEventListener("click", async (e) => {
  try {
    await navigator.clipboard.writeText(resultText.value);
    const btn = e.currentTarget;
    const original = btn.textContent;
    btn.textContent = "copied ✓";
    setTimeout(() => { btn.textContent = original; }, 1500);
  } catch {
    // Fallback
    resultText.select();
    document.execCommand("copy");
  }
});

document.getElementById("download-btn").addEventListener("click", () => {
  const blob = new Blob([resultText.value], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `transcript_${Date.now()}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
});

document.getElementById("reset-btn").addEventListener("click", () => {
  reset();
  urlInput.value = "";
  imageInput.value = "";
  dzFilename.textContent = "PNG · JPG · WebP · up to 200MB";
  urlInput.focus();
});
