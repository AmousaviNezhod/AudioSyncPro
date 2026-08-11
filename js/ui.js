/**
 * Audio Sync Pro - UI utilities
 */
var AudioSyncProUI = (function () {
    var els = {};

    var PRESETS = {
        fast: { sampleRate: 8000, sampleSeconds: 10, maxOffset: 5.0, matchThreshold: 0.35, hint: "نرخ نمونه ۸kHz، ۱۰ ثانیه، آستانه ۰.۳۵ — برای پیش‌نمایش سریع" },
        balanced: { sampleRate: 16000, sampleSeconds: 30, maxOffset: 10.0, matchThreshold: 0.45, hint: "نرخ نمونه ۱۶kHz، ۳۰ ثانیه، آستانه ۰.۴۵ — بهترین تعادل" },
        accurate: { sampleRate: 22050, sampleSeconds: 60, maxOffset: 20.0, matchThreshold: 0.55, hint: "نرخ نمونه ۲۲.۰۵kHz، ۶۰ ثانیه، آستانه ۰.۵۵ — بیشترین دقت" },
        custom: null
    };

    function getEls() {
        if (!els.analyzeBtn) {
            els = {
                preset: document.getElementById("preset"),
                presetHint: document.getElementById("presetHint"),
                customSettings: document.getElementById("customSettings"),
                sampleRate: document.getElementById("sampleRate"),
                sampleSeconds: document.getElementById("sampleSeconds"),
                maxOffset: document.getElementById("maxOffset"),
                matchThreshold: document.getElementById("matchThreshold"),
                placeOnTracks: document.getElementById("placeOnTracks"),
                normalizeAudio: document.getElementById("normalizeAudio"),
                targetPeak: document.getElementById("targetPeak"),
                analyzeBtn: document.getElementById("analyzeBtn"),
                normalizeOnlyBtn: document.getElementById("normalizeOnlyBtn"),
                progressPanel: document.getElementById("progressPanel"),
                progressLabel: document.getElementById("progressLabel"),
                progressPercent: document.getElementById("progressPercent"),
                progressFill: document.getElementById("progressFill"),
                logOutput: document.getElementById("logOutput"),
                status: document.getElementById("status"),
                statusDot: document.getElementById("statusDot")
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
        if (type === "error") setDot("error");
        else if (type === "success") setDot("ready");
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

    function getSettings() {
        var e = getEls();
        return {
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

    function applyPreset() {
        var e = getEls();
        var name = e.preset ? e.preset.value : "balanced";
        var cfg = PRESETS[name] || PRESETS["balanced"];
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
        setBusy: setBusy,
        setDot: setDot
    };
})();
