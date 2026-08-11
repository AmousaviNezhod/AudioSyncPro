/**
 * Audio Sync Pro - UI utilities
 */
var AudioSyncProUI = (function () {
    var els = {};

    function getEls() {
        if (!els.ffmpegPath) {
            els = {
                pythonPath: document.getElementById("pythonPath"),
                ffmpegPath: document.getElementById("ffmpegPath"),
                sampleRate: document.getElementById("sampleRate"),
                sampleSeconds: document.getElementById("sampleSeconds"),
                placeOnTracks: document.getElementById("placeOnTracks"),
                normalizeAudio: document.getElementById("normalizeAudio"),
                matchThreshold: document.getElementById("matchThreshold"),
                targetPeak: document.getElementById("targetPeak"),
                maxOffset: document.getElementById("maxOffset"),
                analyzeBtn: document.getElementById("analyzeBtn"),
                normalizeOnlyBtn: document.getElementById("normalizeOnlyBtn"),
                progressPanel: document.getElementById("progressPanel"),
                progressLabel: document.getElementById("progressLabel"),
                progressPercent: document.getElementById("progressPercent"),
                progressFill: document.getElementById("progressFill"),
                logOutput: document.getElementById("logOutput"),
                status: document.getElementById("status")
            };
        }
        return els;
    }

    function escapeHtml(str) {
        return String(str || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function log(message, type) {
        var e = getEls();
        type = type || "info";
        var entry = document.createElement("div");
        entry.className = "log-entry " + type;
        entry.textContent = "[" + new Date().toLocaleTimeString("fa-IR") + "] " + message;
        e.logOutput.appendChild(entry);
        e.logOutput.scrollTop = e.logOutput.scrollHeight;
    }

    function setStatus(message, type) {
        var e = getEls();
        e.status.textContent = message;
        e.status.className = "status " + (type || "");
    }

    function setProgress(percent, label) {
        var e = getEls();
        e.progressPanel.style.display = "block";
        percent = Math.max(0, Math.min(100, Math.round(percent)));
        e.progressFill.style.width = percent + "%";
        e.progressPercent.textContent = percent + "%";
        if (label) e.progressLabel.textContent = label;
    }

    function hideProgress() {
        var e = getEls();
        e.progressPanel.style.display = "none";
    }

    function getSettings() {
        var e = getEls();
        return {
            pythonPath: e.pythonPath.value.trim() || "python",
            ffmpegPath: e.ffmpegPath.value.trim() || "ffmpeg",
            sampleRate: parseInt(e.sampleRate.value, 10) || 16000,
            sampleSeconds: parseFloat(e.sampleSeconds.value) || 30,
            placeOnTracks: e.placeOnTracks.checked,
            normalizeAudio: e.normalizeAudio.checked,
            matchThreshold: parseFloat(e.matchThreshold.value) || 0.45,
            targetPeak: parseFloat(e.targetPeak.value) || -1.0,
            maxOffset: parseFloat(e.maxOffset.value) || 10.0
        };
    }

    function setBusy(busy) {
        var e = getEls();
        e.analyzeBtn.disabled = busy;
        e.normalizeOnlyBtn.disabled = busy;
    }

    function init(handlers) {
        var e = getEls();
        e.analyzeBtn.addEventListener("click", function () {
            if (handlers.onSync) handlers.onSync(getSettings());
        });
        e.normalizeOnlyBtn.addEventListener("click", function () {
            if (handlers.onNormalize) handlers.onNormalize(getSettings());
        });
    }

    return {
        init: init,
        log: log,
        setStatus: setStatus,
        setProgress: setProgress,
        hideProgress: hideProgress,
        getSettings: getSettings,
        setBusy: setBusy
    };
})();
