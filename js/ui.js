/**
 * Audio Sync Pro - UI controller
 */
var AudioSyncProUI = (function () {
    var els = {};

    var SYNC_PRESETS = {
        fast: { sampleRate: 8000, sampleSeconds: 20, maxOffset: 10.0, matchThreshold: 0.35, hint: "۸kHz · ۲۰s · آستانه ۰.۳۵ — پیش‌نمایش سریع" },
        balanced: { sampleRate: 16000, sampleSeconds: 40, maxOffset: 30.0, matchThreshold: 0.40, hint: "۱۶kHz · ۴۰s · آستانه ۰.۴۰ — تعادل سرعت و دقت" },
        accurate: { sampleRate: 22050, sampleSeconds: 80, maxOffset: 60.0, matchThreshold: 0.45, hint: "۲۲.۰۵kHz · ۸۰s · آستانه ۰.۴۵ — بیشترین دقت" },
        custom: null
    };

    function getEls() {
        if (!els.syncBtn) {
            els = {
                preset: document.getElementById("preset"),
                presetHint: document.getElementById("presetHint"),
                customSettings: document.getElementById("customSettings"),
                sampleRate: document.getElementById("sampleRate"),
                sampleSeconds: document.getElementById("sampleSeconds"),
                maxOffset: document.getElementById("maxOffset"),
                matchThreshold: document.getElementById("matchThreshold"),
                placeOnTracks: document.getElementById("placeOnTracks"),
                targetPeak: document.getElementById("targetPeak"),
                normMode: document.getElementById("normMode"),
                syncBtn: document.getElementById("syncBtn"),
                normalizeBtn: document.getElementById("normalizeBtn"),
                progressPanel: document.getElementById("progressPanel"),
                progressLabel: document.getElementById("progressLabel"),
                progressPercent: document.getElementById("progressPercent"),
                progressFill: document.getElementById("progressFill"),
                logOutput: document.getElementById("logOutput"),
                statusChip: document.getElementById("statusChip"),
                statusDot: document.getElementById("statusDot"),
                statusLabel: document.getElementById("statusLabel"),
                footerStatus: document.getElementById("footerStatus")
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

    function toPersianDigits(str) {
        return String(str || "").replace(/\d/g, function (d) {
            return "۰۱۲۳۴۵۶۷۸۹"[parseInt(d, 10)];
        });
    }

    function setDot(state) {
        var e = getEls();
        e.statusDot.classList.remove("ready", "error");
        if (state === "ready") e.statusDot.classList.add("ready");
        else if (state === "error") e.statusDot.classList.add("error");
    }

    function setStatus(message, type) {
        var e = getEls();
        e.footerStatus.textContent = message;
        e.footerStatus.className = "footer-status " + (type || "");
        e.statusLabel.textContent = message;

        if (type === "error") {
            setDot("error");
            e.statusChip.style.borderColor = "rgba(255, 90, 101, 0.35)";
        } else if (type === "success") {
            setDot("ready");
            e.statusChip.style.borderColor = "rgba(0, 212, 170, 0.35)";
        } else if (type === "warn") {
            setDot("");
            e.statusChip.style.borderColor = "rgba(240, 198, 116, 0.35)";
        } else {
            setDot("");
            e.statusChip.style.borderColor = "";
        }
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

    function setProgress(percent, label) {
        var e = getEls();
        e.progressPanel.style.display = "block";
        percent = Math.max(0, Math.min(100, Math.round(percent)));
        e.progressFill.style.width = percent + "%";
        e.progressPercent.textContent = toPersianDigits(percent) + "٪";
        if (label) e.progressLabel.textContent = label;
    }

    function hideProgress() {
        var e = getEls();
        e.progressPanel.style.display = "none";
    }

    function getSyncSettings() {
        var e = getEls();
        return {
            sampleRate: parseInt(e.sampleRate.value, 10) || 16000,
            sampleSeconds: parseFloat(e.sampleSeconds.value) || 40,
            placeOnTracks: e.placeOnTracks.checked,
            normalizeAudio: false,
            matchThreshold: parseFloat(e.matchThreshold.value) || 0.40,
            targetPeak: parseFloat(e.targetPeak.value) || -1.0,
            maxOffset: parseFloat(e.maxOffset.value) || 30.0
        };
    }

    function getNormalizeSettings() {
        var e = getEls();
        return {
            sampleRate: 16000,
            sampleSeconds: 60,
            placeOnTracks: false,
            normalizeAudio: true,
            matchThreshold: 0.45,
            targetPeak: parseFloat(e.targetPeak.value) || -1.0,
            maxOffset: 10.0,
            normMode: e.normMode ? e.normMode.value : "peak"
        };
    }

    function getSettings() {
        return getSyncSettings();
    }

    function setBusy(busy) {
        var e = getEls();
        e.syncBtn.disabled = busy;
        e.normalizeBtn.disabled = busy;
    }

    function applyPreset() {
        var e = getEls();
        var name = e.preset ? e.preset.value : "balanced";
        var cfg = SYNC_PRESETS[name] || SYNC_PRESETS["balanced"];
        var custom = name === "custom";

        if (custom) {
            e.customSettings.classList.remove("hidden");
            e.presetHint.textContent = "مقادیر سفارشی فعال هستند";
            e.sampleRate.disabled = false;
            e.sampleSeconds.disabled = false;
            e.maxOffset.disabled = false;
            e.matchThreshold.disabled = false;
            return;
        }

        e.customSettings.classList.add("hidden");
        if (cfg && cfg.hint) e.presetHint.textContent = cfg.hint;

        e.sampleRate.disabled = true;
        e.sampleSeconds.disabled = true;
        e.maxOffset.disabled = true;
        e.matchThreshold.disabled = true;

        if (cfg) {
            e.sampleRate.value = String(cfg.sampleRate);
            e.sampleSeconds.value = String(cfg.sampleSeconds);
            e.maxOffset.value = String(cfg.maxOffset);
            e.matchThreshold.value = String(cfg.matchThreshold);
        }
    }

    function init(handlers) {
        var e = getEls();
        if (e.preset) {
            e.preset.addEventListener("change", applyPreset);
            applyPreset();
        }
        e.syncBtn.addEventListener("click", function () {
            if (handlers.onSync) handlers.onSync(getSyncSettings());
        });
        e.normalizeBtn.addEventListener("click", function () {
            if (handlers.onNormalize) handlers.onNormalize(getNormalizeSettings());
        });
    }

    return {
        init: init,
        log: log,
        setStatus: setStatus,
        setProgress: setProgress,
        hideProgress: hideProgress,
        getSettings: getSettings,
        getSyncSettings: getSyncSettings,
        getNormalizeSettings: getNormalizeSettings,
        setBusy: setBusy,
        setDot: setDot
    };
})();
