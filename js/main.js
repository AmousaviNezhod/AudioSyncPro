/**
 * Audio Sync Pro - Main controller
 */
(function () {
    var cs = null;
    var fs = null;
    var AudioUtils = null;
    var SyncEngine = null;
    var pathModule = null;
    var hostLoaded = false;
    var runtimeReady = false;
    var runtimeError = "";

    function initRuntime() {
        if (runtimeReady) return true;
        try {
            if (typeof CSInterface !== "undefined") {
                cs = new CSInterface();
            }
        } catch (e) {
            runtimeError = "CEP runtime not available";
            return false;
        }

        try {
            fs = (window.cep && window.cep.fs) ? window.cep.fs : null;
        } catch (e) {
            fs = null;
        }

        try {
            pathModule = requireModule("path");
        } catch (e) {
            runtimeError = e.message || "Node.js not enabled";
            return false;
        }

        try {
            var nodeDir = getExtensionNodeDir();
            AudioUtils = requireModule(pathModule.join(nodeDir, "AudioUtils.js"));
            SyncEngine = requireModule(pathModule.join(nodeDir, "SyncEngine.js"));
        } catch (e) {
            runtimeError = e.message || "Could not load sync engine";
            return false;
        }

        runtimeReady = true;
        return true;
    }

    function requireModule(name) {
        try {
            if (typeof require !== "undefined" && require) {
                return require(name);
            }
        } catch (e) {}
        try {
            if (window.cep_node && window.cep_node.require) {
                return window.cep_node.require(name);
            }
        } catch (e) {}
        throw new Error("Node.js not enabled");
    }

    function getExtensionNodeDir() {
        if (cs && typeof SystemPath !== "undefined") {
            try {
                return cs.getSystemPath(SystemPath.EXTENSION).replace(/\\/g, "/") + "/node";
            } catch (e) {}
        }
        // Fallback for non-CEP test contexts: resolve relative to this script.
        var script = document.currentScript || (document.querySelector && document.querySelector("script[src*='main.js']"));
        if (script && script.src) {
            var base = script.src.split("?")[0].replace(/\/js\/main\.js$/, "");
            return base + "/node";
        }
        return "./node";
    }

    function getHostJsxPath() {
        if (!cs) return "";
        try {
            var base = cs.getSystemPath(SystemPath.EXTENSION);
            if (!base) return "";
            return base.replace(/\\/g, "/").replace(/\/$/, "") + "/jsx/host.jsx";
        } catch (e) {
            return "";
        }
    }

    function loadHostJsx(callback) {
        if (hostLoaded) return callback(true);
        var jsxPath = getHostJsxPath();
        if (!jsxPath || !fs || !fs.readFile) {
            AudioSyncProUI.setStatus("Host bridge unavailable", "error");
            return callback(false);
        }
        var res = fs.readFile(jsxPath, window.cep.encoding.UTF8);
        if (res.err !== fs.NO_ERROR) {
            AudioSyncProUI.setStatus("Could not load JSX bridge: " + jsxPath, "error");
            return callback(false);
        }
        if (!cs || !cs.evalScript) {
            AudioSyncProUI.setStatus("CSInterface not available", "error");
            return callback(false);
        }
        cs.evalScript(res.data || "", function (result) {
            if (result && result.indexOf("EvalScript error") !== -1) {
                AudioSyncProUI.setStatus("JSX bridge load failed: " + result, "error");
                return callback(false);
            }
            hostLoaded = true;
            callback(true);
        });
    }

    function callHost(command, argObj, callback) {
        if (!cs || !cs.evalScript) {
            return callback({ success: false, error: "CEP runtime not available" });
        }
        loadHostJsx(function (loaded) {
            if (!loaded) return callback({ success: false, error: "Host bridge not loaded" });
            var argStr = JSON.stringify(argObj || {});
            var script = command + "(" + JSON.stringify(argStr) + ");";
            cs.evalScript(script, function (result) {
                try {
                    callback(JSON.parse(result));
                } catch (e) {
                    callback({ success: false, error: result || "Host returned invalid response" });
                }
            });
        });
    }

    function log(msg, type) {
        AudioSyncProUI.log(msg, type);
    }

    function getClipsFromTimeline(callback) {
        AudioSyncProUI.setStatus("دریافت لایه‌های انتخاب‌شده...");
        callHost("host.getSelectedClips", {}, function (res) {
            if (!res.success) {
                AudioSyncProUI.setStatus(res.error || "خطا در دریافت کلیپ‌ها", "error");
                AudioSyncProUI.log(res.error, "error");
                callback(null);
                return;
            }
            if (!res.data || !res.data.clips || res.data.clips.length === 0) {
                AudioSyncProUI.setStatus("هیچ کلیپی انتخاب نشده", "error");
                callback(null);
                return;
            }
            callback(res.data.clips);
        });
    }

    function ensureRuntime() {
        if (!initRuntime()) {
            AudioSyncProUI.setStatus(runtimeError || "Runtime not available", "error");
            log(runtimeError || "Runtime not available", "error");
            return false;
        }
        return true;
    }

    function runSync(settings) {
        if (!ensureRuntime()) return;

        AudioSyncProUI.setBusy(true);
        AudioSyncProUI.setProgress(0, "آماده‌سازی");

        getClipsFromTimeline(function (clips) {
            if (!clips) {
                AudioSyncProUI.setBusy(false);
                AudioSyncProUI.hideProgress();
                return;
            }

            log("کلیپ‌های انتخاب‌شده: " + clips.length, "info");
            for (var i = 0; i < clips.length; i++) {
                log((i + 1) + ". " + clips[i].name + " | track " + clips[i].trackIndex + " | start " + clips[i].startSeconds.toFixed(3) + "s");
            }

            SyncEngine.analyzeClips(clips, settings, function (percent, label) {
                AudioSyncProUI.setProgress(percent * 0.6, label);
                log(label, "info");
            }).then(function (results) {
                AudioSyncProUI.setProgress(60, "محاسبه آفست‌ها");
                var offsets = SyncEngine.computeOffsets(results, settings, function (percent, label) {
                    AudioSyncProUI.setProgress(60 + percent * 0.2, label);
                    log(label, "info");
                });

                log("آفست‌ها محاسبه شد:", "info");
                for (var i = 0; i < offsets.length; i++) {
                    log("  " + results[i].name + " -> " + offsets[i].offsetSeconds.toFixed(4) + "s (confidence " + (offsets[i].confidence * 100).toFixed(1) + "%)");
                }

                var plan = SyncEngine.buildPlan(results, offsets, settings);
                AudioSyncProUI.setProgress(80, "اعمال روی تایم‌لاین");
                callHost("host.applyPlan", { operations: plan.operations }, function (res) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.setProgress(100, "تمام");
                    if (!res.success) {
                        AudioSyncProUI.setStatus(res.error || "خطا در اعمال", "error");
                        log(res.error, "error");
                    } else {
                        AudioSyncProUI.setStatus("سینک و نرمالایز انجام شد", "success");
                        log("موفق: " + res.data.moved + " حرکت، " + res.data.gained + " نرمالایز", "success");
                        if (res.data.errors && res.data.errors.length) {
                            for (var e = 0; e < res.data.errors.length; e++) {
                                log(res.data.errors[e], "warn");
                            }
                        }
                    }
                    setTimeout(AudioSyncProUI.hideProgress, 1500);
                });
            }).catch(function (err) {
                AudioSyncProUI.setBusy(false);
                AudioSyncProUI.hideProgress();
                AudioSyncProUI.setStatus(err.message, "error");
                log(err.message, "error");
            });
        });
    }

    function runNormalizeOnly(settings) {
        if (!ensureRuntime()) return;

        AudioSyncProUI.setBusy(true);
        AudioSyncProUI.setProgress(0, "آماده‌سازی");

        getClipsFromTimeline(function (clips) {
            if (!clips) {
                AudioSyncProUI.setBusy(false);
                AudioSyncProUI.hideProgress();
                return;
            }

            var s = {
                ffmpegPath: settings.ffmpegPath,
                sampleRate: settings.sampleRate,
                sampleSeconds: settings.sampleSeconds,
                normalizeAudio: true,
                targetPeak: settings.targetPeak
            };

            SyncEngine.analyzeClips(clips, s, function (percent, label) {
                AudioSyncProUI.setProgress(percent * 0.8, label);
                log(label, "info");
            }).then(function (results) {
                var operations = [];
                for (var i = 0; i < results.length; i++) {
                    if (results[i].gainDb && results[i].gainDb !== 0) {
                        operations.push({
                            type: "gain",
                            id: results[i].id,
                            trackIndex: results[i].trackIndex,
                            clipIndex: results[i].clipIndex,
                            gainDb: results[i].gainDb
                        });
                        log(results[i].name + " -> gain " + results[i].gainDb.toFixed(2) + " dB", "info");
                    }
                }
                AudioSyncProUI.setProgress(80, "اعمال نرمالایز");
                callHost("host.applyPlan", { operations: operations }, function (res) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.setProgress(100, "تمام");
                    if (!res.success) {
                        AudioSyncProUI.setStatus(res.error || "خطا در نرمالایز", "error");
                        log(res.error, "error");
                    } else {
                        AudioSyncProUI.setStatus("نرمالایز انجام شد", "success");
                        log("نرمالایز موفق: " + res.data.gained, "success");
                    }
                    setTimeout(AudioSyncProUI.hideProgress, 1500);
                });
            }).catch(function (err) {
                AudioSyncProUI.setBusy(false);
                AudioSyncProUI.hideProgress();
                AudioSyncProUI.setStatus(err.message, "error");
                log(err.message, "error");
            });
        });
    }

    function init() {
        AudioSyncProUI.init({
            onSync: runSync,
            onNormalize: runNormalizeOnly
        });
        AudioSyncProUI.setStatus("آماده");
        log("Audio Sync Pro بارگذاری شد", "success");

        // In a CEP panel this is a no-op; in a plain browser it keeps the UI usable.
        initRuntime();
        if (!runtimeReady && runtimeError) {
            log(runtimeError, "warn");
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
