/**
 * Audio Sync Pro - Main controller (Python bridge)
 *
 * The audio analysis now runs in an external Python process spawned via
 * window.cep.process. Python writes a JSON response that is passed to the
 * ExtendScript host bridge (host.applyPlan).
 */
(function () {
    var cs = null;
    var fs = null;
    var processApi = null;
    var hostLoaded = false;
    var runtimeReady = false;
    var runtimeError = "";

    function initRuntime() {
        if (runtimeReady) return true;
        runtimeError = "";
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
            processApi = (window.cep && window.cep.process) ? window.cep.process : null;
        } catch (e) {
            processApi = null;
        }

        if (!cs) {
            runtimeError = "CSInterface not available";
            return false;
        }
        if (!fs) {
            runtimeError = "CEP fs API not available";
            return false;
        }
        if (!processApi) {
            runtimeError = "CEP process API not available";
            return false;
        }

        runtimeReady = true;
        return true;
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

    function toNativePath(p) {
        if (!p) return p;
        var os = "";
        try {
            os = cs.getOSInformation();
        } catch (e) {}
        if (os && os.indexOf("Windows") !== -1) {
            return p.replace(/\//g, "\\");
        }
        return p.replace(/\\/g, "/");
    }

    function getWorkDir() {
        var base = cs.getSystemPath(SystemPath.USER_DATA);
        if (!base) base = cs.getSystemPath(SystemPath.EXTENSION);
        return toNativePath(base.replace(/\/$/, "")) + toNativePath("/AudioSyncPro");
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

    function writeFileSafe(path, content) {
        var res = fs.writeFile(path, content, window.cep.encoding.UTF8);
        if (res.err !== fs.NO_ERROR) {
            throw new Error("writeFile failed (" + res.err + ") for " + path);
        }
    }

    function readFileSafe(path) {
        var res = fs.readFile(path, window.cep.encoding.UTF8);
        if (res.err !== fs.NO_ERROR) {
            throw new Error("readFile failed (" + res.err + ") for " + path);
        }
        return res.data;
    }

    function ensureWorkDir() {
        var dir = getWorkDir();
        try {
            fs.makedir(dir);
        } catch (e) {}
        return dir;
    }

    function runPythonBridge(op, clips, settings, callback) {
        var workDir = ensureWorkDir();
        var requestPath = workDir + toNativePath("/request.json");
        var responsePath = workDir + toNativePath("/response.json");

        var extBase = cs.getSystemPath(SystemPath.EXTENSION).replace(/\/$/, "");
        var exePath = toNativePath(extBase + "/python/dist/sync_bridge.exe");
        var scriptPath = toNativePath(extBase + "/python/sync_bridge.py");
        var pythonPath = toNativePath(settings.pythonPath || "python");

        var request = {
            op: op,
            clips: clips,
            settings: settings
        };

        try {
            writeFileSafe(requestPath, JSON.stringify(request));
        } catch (e) {
            callback({ success: false, error: e.message });
            return;
        }

        var os = "";
        try {
            os = cs.getOSInformation();
        } catch (e) {}
        var isWindows = os.indexOf("Windows") !== -1;

        function startBridge(executable, args, fallback) {
            var callArgs = [executable].concat(args);
            var procRes = processApi.createProcess.apply(processApi, callArgs);
            if (procRes.err !== 0) {
                if (fallback) return fallback();
                return callback({ success: false, error: "Could not start bridge: " + procRes.err });
            }
            monitorProcess(procRes.data, responsePath, callback);
        }

        if (isWindows) {
            // Prefer the compiled executable on Windows; fall back to the Python script.
            startBridge(exePath, [requestPath, responsePath], function () {
                log("Compiled bridge not available, falling back to Python script", "warn");
                startBridge(pythonPath, [scriptPath, requestPath, responsePath], null);
            });
        } else {
            startBridge(pythonPath, [scriptPath, requestPath, responsePath], null);
        }
    }

    function monitorProcess(pid, responsePath, callback) {
        var stderr = "";
        var finished = false;
        try {
            processApi.stderr(pid, function (chunk) {
                if (chunk) stderr += chunk;
            });
        } catch (e) {}

        var checkTimer = null;
        var timeoutId = null;

        function finish(error, response) {
            if (finished) return;
            finished = true;
            if (checkTimer) clearInterval(checkTimer);
            if (timeoutId) clearTimeout(timeoutId);
            try {
                processApi.terminate(pid);
            } catch (e) {}
            if (error) {
                callback({ success: false, error: error + (stderr ? "\n" + stderr : "") });
            } else {
                callback(response);
            }
        }

        checkTimer = setInterval(function () {
            var running = processApi.isRunning(pid);
            if (running.err === 0 && running.data) {
                return;
            }

            if (running.err !== 0) {
                return finish("process.isRunning error: " + running.err);
            }

            try {
                var data = readFileSafe(responsePath);
                var resp = JSON.parse(data);
                return finish(null, resp);
            } catch (e) {
                return finish("Could not read bridge response: " + e.message);
            }
        }, 200);

        // Safety timeout (120 seconds).
        timeoutId = setTimeout(function () {
            finish("Bridge timed out");
        }, 120000);
    }

    function runSync(settings) {
        if (!ensureRuntime()) return;

        AudioSyncProUI.setBusy(true);
        AudioSyncProUI.setProgress(10, "دریافت کلیپ‌ها...");

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

            AudioSyncProUI.setProgress(30, "تحلیل صدا در Python...");

            runPythonBridge("sync", clips, settings, function (resp) {
                AudioSyncProUI.setProgress(80, "اعمال روی تایم‌لاین");

                if (!resp.success) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.hideProgress();
                    AudioSyncProUI.setStatus(resp.error || "خطا در Python", "error");
                    log(resp.error || "خطا در Python", "error");
                    return;
                }

                log("گروه‌های سینک: " + ((resp.data && resp.data.groups) ? resp.data.groups.length : 0), "info");
                callHost("host.applyPlan", { operations: resp.data.operations }, function (res) {
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
            });
        });
    }

    function runNormalizeOnly(settings) {
        if (!ensureRuntime()) return;

        AudioSyncProUI.setBusy(true);
        AudioSyncProUI.setProgress(10, "دریافت کلیپ‌ها...");

        getClipsFromTimeline(function (clips) {
            if (!clips) {
                AudioSyncProUI.setBusy(false);
                AudioSyncProUI.hideProgress();
                return;
            }

            AudioSyncProUI.setProgress(30, "تحلیل صدا در Python...");

            runPythonBridge("normalize", clips, settings, function (resp) {
                AudioSyncProUI.setProgress(80, "اعمال نرمالایز");

                if (!resp.success) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.hideProgress();
                    AudioSyncProUI.setStatus(resp.error || "خطا در Python", "error");
                    log(resp.error || "خطا در Python", "error");
                    return;
                }

                var operations = (resp.data && resp.data.operations) ? resp.data.operations : [];
                for (var i = 0; i < operations.length; i++) {
                    var op = operations[i];
                    log(op.name + " -> gain " + op.gainDb.toFixed(2) + " dB", "info");
                }

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

        // Load CEP runtime if available; in a plain browser this will fail gracefully.
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
