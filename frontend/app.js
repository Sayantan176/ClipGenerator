/**
 * ClipGenerator — frontend/app.js
 * Handles all client-side logic:
 *  - Drag-and-drop / click-to-browse file upload
 *  - XMLHttpRequest upload with real-time progress bar
 *  - Polling /api/jobs/{jobId} every 3 s until done or error
 *  - Rendering the clips gallery with video players and landscape + portrait download buttons
 *  - Managing multiple jobs (stored in sessionStorage)
 */

'use strict';

/* ─────────────────────────── Constants ─────────────────────────── */

const POLL_INTERVAL_MS  = 2500;   // 2.5 s between status polls
const MAX_FILE_SIZE_MB  = 2048;   // 2 GB client-side guard
const ACCEPTED_TYPES    = ['video/mp4', 'video/quicktime', 'video/x-matroska',
                            'video/webm', 'video/x-msvideo', 'video/mpeg'];

/* Status → human-readable label + CSS class */
const STATUS_META = {
  queued:       { label: '⏳ Queued',        cls: 'status-queued'       },
  transcribing: { label: '🎙️ Transcribing Audio',  cls: 'status-transcribing' },
  analyzing:    { label: '🧠 AI Analyzing Hooks',  cls: 'status-analyzing'    },
  clipping:     { label: '✂️ Cutting & Subtitling', cls: 'status-clipping'     },
  done:         { label: '✅ Clips Ready!',         cls: 'status-done'         },
  error:        { label: '❌ Failed',              cls: 'status-error'        },
};

/* ─────────────────────────── State ─────────────────────────── */

/** @type {Map<string, number>} */
const pollTimers = new Map();

/** @type {Map<string, object>} */
let sessionJobs = new Map();

/* ─────────────────────────── DOM refs ─────────────────────────── */

let dropZone, fileInput, browseBtn, uploadBtn,
    progressSection, progressBar, progressText,
    statusSection, currentJobStatus, currentJobFile,
    clipsSection, clipsGrid, jobListSection, jobList,
    toastContainer;

/* ─────────────────────────── Initialisation ─────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  dropZone         = document.getElementById('drop-zone');
  fileInput        = document.getElementById('file-input');
  browseBtn        = document.getElementById('browse-btn');
  uploadBtn        = document.getElementById('upload-btn');
  progressSection  = document.getElementById('progress-section');
  progressBar      = document.getElementById('progress-bar');
  progressText     = document.getElementById('progress-text');
  statusSection    = document.getElementById('status-section');
  currentJobStatus = document.getElementById('current-job-status');
  currentJobFile   = document.getElementById('current-job-file');
  clipsSection     = document.getElementById('clips-section');
  clipsGrid        = document.getElementById('clips-grid');
  jobListSection   = document.getElementById('job-list-section');
  jobList          = document.getElementById('job-list');
  toastContainer   = document.getElementById('toast-container');

  bindUploadEvents();
  restoreSession();
});

/* ─────────────────────────── Upload events ─────────────────────────── */

function bindUploadEvents() {
  browseBtn.addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) handleFileSelected(fileInput.files[0]);
  });

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });

  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('drag-over');
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelected(file);
  });

  uploadBtn.addEventListener('click', startUpload);
}

function handleFileSelected(file) {
  if (file.type && !ACCEPTED_TYPES.includes(file.type)) {
    showToast(`Note: Ensure this is a valid video file.`, 'info');
  }

  if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
    showToast(`File is too large (max ${MAX_FILE_SIZE_MB} MB).`, 'error');
    return;
  }

  uploadBtn._selectedFile = file;
  uploadBtn.disabled = false;

  const label = dropZone.querySelector('.drop-label');
  if (label) label.textContent = `Selected: ${file.name}`;
  dropZone.classList.add('has-file');

  showToast(`File ready: ${file.name}`, 'info');
}

function startUpload() {
  const file = uploadBtn._selectedFile;
  if (!file) {
    showToast('Please select a video file first.', 'warning');
    return;
  }

  uploadBtn.disabled = true;
  showProgressSection();
  setProgress(0, 'Uploading video…');

  const formData = new FormData();
  formData.append('file', file);

  const xhr = new XMLHttpRequest();

  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      setProgress(pct, `Uploading… ${pct}%`);
    }
  });

  xhr.addEventListener('load', () => {
    if (xhr.status === 200 || xhr.status === 201) {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        showToast('Server returned an invalid response.', 'error');
        resetUploadUI();
        return;
      }

      const jobId = data.job_id;
      if (!jobId) {
        showToast('Server did not return a job ID.', 'error');
        resetUploadUI();
        return;
      }

      setProgress(100, 'Upload complete! Starting AI analysis…');
      showToast(`Video uploaded! Processing job started.`, 'success');

      registerJob(jobId, file.name);
      beginPolling(jobId);
      renderJobList();

      uploadBtn._selectedFile = null;
      fileInput.value = '';
      const label = dropZone.querySelector('.drop-label');
      if (label) label.textContent = 'Drag & drop a video file here, or click Browse';
      dropZone.classList.remove('has-file');

      setTimeout(() => {
        progressSection.style.display = 'none';
      }, 2000);

    } else {
      let msg = `Upload failed (HTTP ${xhr.status}).`;
      try {
        const err = JSON.parse(xhr.responseText);
        if (err.detail) msg = err.detail;
      } catch { /* ignore */ }
      showToast(msg, 'error');
      resetUploadUI();
    }
  });

  xhr.addEventListener('error', () => {
    showToast('Network error during upload. Is the server running?', 'error');
    resetUploadUI();
  });

  xhr.open('POST', '/api/upload');
  xhr.send(formData);
}

/* ─────────────────────────── Polling ─────────────────────────── */

function beginPolling(jobId) {
  if (pollTimers.has(jobId)) return;

  const timerId = setInterval(() => pollJob(jobId), POLL_INTERVAL_MS);
  pollTimers.set(jobId, timerId);
  pollJob(jobId);
}

function stopPolling(jobId) {
  const timerId = pollTimers.get(jobId);
  if (timerId !== undefined) {
    clearInterval(timerId);
    pollTimers.delete(jobId);
  }
}

async function pollJob(jobId) {
  let data;
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) return;
    data = await res.json();
  } catch (err) {
    return;
  }

  updateJobState(jobId, data);

  const latestJobId = getLatestJobId();
  if (jobId === latestJobId) {
    renderCurrentJobStatus(data);
  }

  renderJobList();

  if (data.status === 'done') {
    stopPolling(jobId);
    showToast('All clips generated successfully!', 'success');
    await fetchAndRenderClips(jobId);
  } else if (data.status === 'error') {
    stopPolling(jobId);
    if (jobId === latestJobId) {
      showToast(`Processing failed: ${data.error || 'Unknown error'}`, 'error');
    }
  }
}

async function fetchAndRenderClips(jobId) {
  let rawData;
  try {
    const res = await fetch(`/api/jobs/${jobId}/clips`);
    if (!res.ok) return;
    rawData = await res.json();
  } catch (err) {
    console.warn('[clips] fetch error:', err);
    return;
  }

  const clips = Array.isArray(rawData) ? rawData : (rawData?.clips || []);

  const job = sessionJobs.get(jobId);
  if (job) {
    job.clips = clips;
    saveSession();
  }

  if (jobId === getLatestJobId()) {
    renderClipsGallery(clips, jobId);
  }
}

/* ─────────────────────────── Session persistence ─────────────────────────── */

function registerJob(jobId, filename) {
  sessionJobs.set(jobId, {
    jobId,
    filename,
    status: 'queued',
    error: null,
    clips: [],
  });
  saveSession();
}

function updateJobState(jobId, apiData) {
  const existing = sessionJobs.get(jobId) || { jobId, filename: '', clips: [] };
  sessionJobs.set(jobId, {
    ...existing,
    status: apiData.status,
    error:  apiData.error || null,
  });
  saveSession();
}

function saveSession() {
  try {
    sessionStorage.setItem('cg_jobs', JSON.stringify([...sessionJobs.entries()]));
  } catch { /* ignore */ }
}

function restoreSession() {
  try {
    const raw = sessionStorage.getItem('cg_jobs');
    if (!raw) return;
    sessionJobs = new Map(JSON.parse(raw));
  } catch {
    sessionJobs = new Map();
    return;
  }

  if (sessionJobs.size === 0) return;

  renderJobList();

  for (const [jobId, job] of sessionJobs) {
    if (job.status !== 'done' && job.status !== 'error') {
      beginPolling(jobId);
    }
  }

  const latestId = getLatestJobId();
  if (latestId) {
    const job = sessionJobs.get(latestId);
    if (job) {
      renderCurrentJobStatus(job);
      if (job.status === 'done' && job.clips && job.clips.length > 0) {
        renderClipsGallery(job.clips, latestId);
      } else if (job.status === 'done') {
        fetchAndRenderClips(latestId);
      }
    }
  }
}

function getLatestJobId() {
  if (sessionJobs.size === 0) return null;
  const keys = [...sessionJobs.keys()];
  return keys[keys.length - 1];
}

/* ─────────────────────────── UI Rendering ─────────────────────────── */

function showProgressSection() {
  progressSection.style.display = 'block';
}

function setProgress(pct, label) {
  progressBar.style.width = `${pct}%`;
  progressText.textContent = label;
}

function resetUploadUI() {
  uploadBtn.disabled = false;
  progressSection.style.display = 'none';
  setProgress(0, '');
}

function renderCurrentJobStatus(jobData) {
  statusSection.style.display = 'block';
  statusSection.classList.add('visible');

  const meta = STATUS_META[jobData.status] || { label: jobData.status, cls: '' };
  const filename = jobData.filename || '';

  currentJobFile.textContent   = filename ? `File: ${filename}` : '';
  currentJobStatus.className   = `job-status-badge ${meta.cls}`;
  currentJobStatus.textContent = meta.label;

  if (jobData.status === 'error' && jobData.error) {
    currentJobStatus.textContent += ` (${jobData.error})`;
  }
}

function renderJobList() {
  if (sessionJobs.size === 0) {
    jobListSection.style.display = 'none';
    return;
  }

  jobListSection.style.display = 'block';
  jobList.innerHTML = '';

  const entries = [...sessionJobs.entries()].reverse();

  for (const [jobId, job] of entries) {
    const meta = STATUS_META[job.status] || { label: job.status, cls: '' };

    const li = document.createElement('li');
    li.className = 'job-list-item';
    li.dataset.jobId = jobId;

    li.innerHTML = `
      <span class="job-list-filename" title="${escHtml(job.filename)}">${escHtml(truncate(job.filename, 32))}</span>
      <span class="job-list-status ${meta.cls}">${meta.label}</span>
    `;

    li.addEventListener('click', () => onJobListClick(jobId));
    jobList.appendChild(li);
  }
}

async function onJobListClick(jobId) {
  const job = sessionJobs.get(jobId);
  if (!job) return;

  renderCurrentJobStatus(job);

  if (job.status === 'done') {
    if (job.clips && job.clips.length > 0) {
      renderClipsGallery(job.clips, jobId);
    } else {
      await fetchAndRenderClips(jobId);
    }
  } else if (job.status !== 'error') {
    if (!pollTimers.has(jobId)) beginPolling(jobId);
    clipsSection.style.display = 'none';
  } else {
    clipsSection.style.display = 'none';
  }

  const snapshot = { ...job };
  sessionJobs.delete(jobId);
  sessionJobs.set(jobId, snapshot);
  saveSession();
  renderJobList();
}

function renderClipsGallery(clips, jobId) {
  clipsSection.style.display = 'block';
  clipsGrid.innerHTML = '';

  const clipList = Array.isArray(clips) ? clips : (clips?.clips || []);

  if (!clipList || clipList.length === 0) {
    clipsGrid.innerHTML = '<p class="no-clips">No clips were generated for this video.</p>';
    return;
  }

  for (const clip of clipList) {
    const card = buildClipCard(clip, jobId);
    clipsGrid.appendChild(card);
  }
}

function buildClipCard(clip, jobId) {
  const duration = formatDuration(clip.start_sec, clip.end_sec);
  const card = document.createElement('div');
  card.className = 'clip-card';

  // Video preview URL (portrait preferred for mobile/social media view, landscape fallback)
  const videoSrc = clip.stream_portrait || `/api/clips/${clip.id}/stream/portrait`;
  const reasonText = clip.reason ? escHtml(clip.reason) : 'Selected for its strong opening hook and compelling message.';

  card.innerHTML = `
    <div class="clip-video-wrap" style="position: relative; width: 100%; border-radius: 8px 8px 0 0; overflow: hidden; background: #000;">
      <video controls preload="metadata" playsinline style="width: 100%; max-height: 420px; display: block; object-fit: contain;">
        <source src="${videoSrc}" type="video/mp4">
        <source src="${clip.stream_landscape || ('/api/clips/' + clip.id + '/stream/landscape')}" type="video/mp4">
        Your browser does not support HTML5 video preview.
      </video>
    </div>
    <div class="clip-body" style="padding: 16px 18px; display: flex; flex-direction: column; flex: 1;">
      <div class="clip-header" style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 10px;">
        <h3 class="clip-title" style="margin: 0; font-size: 1.05rem; font-weight: 700; color: #fff;">${escHtml(clip.title)}</h3>
        <span class="clip-duration" style="background: rgba(255,255,255,0.12); padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; font-family: var(--font-mono); font-weight: 600; white-space: nowrap;">${duration}</span>
      </div>

      <!-- Dedicated AI Reasoning Statement Section -->
      <div class="clip-reasoning-box">
        <div class="clip-reasoning-header">
          <span>💡 Why This Moment Was Clipped</span>
          <span class="clip-reasoning-tag">AI Rationale</span>
        </div>
        <div class="clip-reason-text">${reasonText}</div>
      </div>

      <div class="clip-timestamps" style="margin-bottom: 14px; font-size: 0.8rem; color: var(--text-muted); font-family: var(--font-mono);">
        <span>⏱ Timestamp: ${formatSeconds(clip.start_sec)} → ${formatSeconds(clip.end_sec)}</span>
      </div>

      <div class="clip-actions" style="display: flex; gap: 10px; margin-top: auto;">
        <a class="btn btn-landscape"
           href="/api/clips/${clip.id}/download/landscape"
           download="${sanitiseFilename(clip.title)}_landscape.mp4"
           title="Download landscape (16:9) YouTube format"
           style="flex: 1; padding: 10px 12px; font-size: 0.85rem;">
          📥 16:9 Landscape
        </a>
        <a class="btn btn-portrait"
           href="/api/clips/${clip.id}/download/portrait"
           download="${sanitiseFilename(clip.title)}_portrait_9x16.mp4"
           title="Download portrait (9:16) Reels / TikTok format"
           style="flex: 1; padding: 10px 12px; font-size: 0.85rem; background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%); border-color: #7c3aed;">
          📱 9:16 Portrait
        </a>
      </div>
    </div>
  `;

  return card;
}

/* ─────────────────────────── Toast notifications ─────────────────────────── */

function showToast(message, type = 'info', durationMs = 4000) {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;

  toastContainer.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('toast-visible'));

  setTimeout(() => {
    toast.classList.remove('toast-visible');
    toast.addEventListener('transitionend', () => toast.remove(), { once: true });
  }, durationMs);
}

/* ─────────────────────────── Utility helpers ─────────────────────────── */

function escHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function truncate(str, maxLen) {
  if (!str) return '';
  return str.length > maxLen ? str.slice(0, maxLen - 1) + '…' : str;
}

function formatSeconds(totalSeconds) {
  const s   = Math.round(totalSeconds);
  const m   = Math.floor(s / 60);
  const sec = s % 60;
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

function formatDuration(start, end) {
  const diff = Math.max(0, Math.round(end - start));
  const m    = Math.floor(diff / 60);
  const s    = diff % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

function sanitiseFilename(name) {
  if (!name) return 'clip';
  return name.replace(/[^a-zA-Z0-9_\- ]/g, '_').replace(/\s+/g, '_').slice(0, 80);
}
