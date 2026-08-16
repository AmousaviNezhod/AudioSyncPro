/**
 * Audio Sync Pro - Main controller (Python bridge)
 *
 * A compiled Python server is launched once via window.cep.process and kept
 * alive in the background. Requests are sent as JSON lines over stdin and
 * responses are read as JSON lines from stdout. The executable is built as a
 * Windows GUI subsystem app so no console window is shown.
 */
(function () {
    var cs = null;
    var fs = null;
    var processApi = null;
    var hostLoaded = false;
    var runtimeReady = false;
    var runtimeError = "";

    var server = {
        pid: null,
        starting: false,
        ready: false,
        stdoutBuffer: "",
        stderrBuffer: "",
        pending: null,
        requestCounter: 0,
        startCallbacks: [],
        startTimeout: null
    };

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
        if (!processApi || typeof processApi.createProcess !== "function") {
            runtimeError = "CEP process API not available";
            return false;
        }

        runtimeReady = true;
        return true;
    }

    function toNativePath(p) {
        if (!p) return p;
        var os = "";
        try { os = cs.getOSInformation(); } catch (e) {}
        if (os && os.indexOf("Windows") !== -1) {
            return p.replace(/\//g, "\\");
        }
        return p.replace(/\\/g, "/");
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

    function getBridgePath() {
        var extBase = cs.getSystemPath(SystemPath.EXTENSION).replace(/\/$/, "");
        var exePath = toNativePath(extBase + "/python/dist/sync_bridge.exe");
        return { exePath: exePath };
    }

    function readFileSafe(path) {
        var res = fs.readFile(path, window.cep.encoding.UTF8);
        if (res.err !== 0) {
            throw new Error("readFile failed (" + res.err + ") for " + path);
        }
        return res.data;
    }

    function loadHostJsx(callback) {
        if (hostLoaded) return callback(true);
        var jsxPath = getHostJsxPath();
        if (!jsxPath || !fs || !fs.readFile) {
            AudioSyncProUI.setStatus("Host bridge unavailable", "error");
            return callback(false);
        }
        var res = fs.readFile(jsxPath, window.cep.encoding.UTF8);
        if (res.err !== 0) {
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

    // === Bridge server management ===

    function onServerStdout(chunk) {
        if (chunk === null || chunk === undefined) return;
        server.stdoutBuffer += chunk;
        var lines = server.stdoutBuffer.split("\n");
        server.stdoutBuffer = lines.pop();
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line) continue;
            try {
                var resp = JSON.parse(line);
                // Startup handshake: any valid JSON response from the freshly
                // spawned server means stdio is alive. We use a ping, but a
                // sync response that arrives before we mark ready is accepted
                // as a sign the server is up.
                if (server.starting && (resp.pong || resp.success === true)) {
                    markServerReady(true);
                }
                if (server.pending) {
                    // Optional request-id correlation, when the server echoes it.
                    if (resp.request_id && server.pending.id &&
                        resp.request_id !== server.pending.id) {
                        log("ردیف درخواست نامعتبر از موتور: " + resp.request_id, "warn");
                        continue;
                    }
                    clearTimeout(server.pending.timeoutId);
                    var cb = server.pending.callback;
                    server.pending = null;
                    cb(resp);
                } else if (!resp.pong) {
                    log("پاسخ غیرمنتظره از موتور: " + line.substring(0, 80), "warn");
                }
            } catch (e) {
                log("خروجی نامعتبر از موتور: " + line.substring(0, 80), "warn");
            }
        }
    }

    function onServerStderr(chunk) {
        if (!chunk) return;
        server.stderrBuffer += chunk;
        var lines = server.stderrBuffer.split("\n");
        server.stderrBuffer = lines.pop();
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line) continue;
            log("[موتور] " + line, "info");
            if (server.starting && line.indexOf("server started") !== -1) {
                markServerReady(true);
            }
        }
    }

    function onServerQuit() {
        if (server.starting) {
            failStart("موتور در حین راه‌اندازی بسته شد");
            return;
        }
        if (server.ready) {
            log("موتور بسته شد", "warn");
        }
        markServerReady(false);
        if (server.pending) {
            var cb = server.pending.callback;
            server.pending = null;
            cb({ success: false, error: "Bridge server quit unexpectedly" });
        }
    }

    function markServerReady(ready) {
        if (ready) {
            if (server.startTimeout) {
                clearTimeout(server.startTimeout);
                server.startTimeout = null;
            }
            server.ready = true;
            server.starting = false;
            AudioSyncProUI.setDot("ready");
            var cbs = server.startCallbacks;
            server.startCallbacks = [];
            for (var i = 0; i < cbs.length; i++) {
                cbs[i](true);
            }
        } else {
            server.ready = false;
            server.starting = false;
            server.pid = null;
            AudioSyncProUI.setDot("");
        }
    }

    function failStart(message) {
        if (server.startTimeout) {
            clearTimeout(server.startTimeout);
            server.startTimeout = null;
        }
        log("شروع موتور ناموفق: " + message, "error");
        if (server.pid !== null) {
            try { processApi.terminate(server.pid); } catch (e) {}
            server.pid = null;
        }
        server.starting = false;
        server.ready = false;
        AudioSyncProUI.setDot("error");
        var cbs = server.startCallbacks;
        server.startCallbacks = [];
        for (var i = 0; i < cbs.length; i++) {
            cbs[i](false);
        }
    }

    function startServer(callback) {
        if (server.ready && server.pid !== null) {
            return callback(true);
        }
        if (server.starting) {
            server.startCallbacks.push(callback);
            return;
        }

        // Kill any stale process.
        if (server.pid !== null) {
            try { processApi.terminate(server.pid); } catch (e) {}
            server.pid = null;
        }
        server.starting = true;
        server.ready = false;
        server.stdoutBuffer = "";
        server.stderrBuffer = "";
        server.pending = null;
        server.startCallbacks = [callback];

        var paths = getBridgePath();
        log("در حال راه‌اندازی موتور...", "info");

        var procRes;
        try {
            procRes = processApi.createProcess(paths.exePath, "--server");
        } catch (e) {
            return failStart("createProcess threw: " + e.message);
        }
        if (!procRes || procRes.err !== 0) {
            return failStart("createProcess returned error " + (procRes ? procRes.err : "no result") + " for " + paths.exePath);
        }

        server.pid = procRes.data;
        try { processApi.stdout(server.pid, onServerStdout); } catch (e) {}
        try { processApi.stderr(server.pid, onServerStderr); } catch (e) {}
        try { processApi.onquit(server.pid, onServerQuit); } catch (e) {}

        // Send an immediate ping. This is the most reliable startup handshake
        // because it confirms both stdin and stdout are connected.
        try {
            var pingLine = JSON.stringify({ action: "ping", request_id: "asp_startup" }) + "\n";
            processApi.stdin(server.pid, pingLine);
        } catch (e) {
            return failStart("could not write startup ping: " + e.message);
        }

        // If we do not get any response within 30 seconds, give up.
        // (The onefile executable may need a few seconds to extract on first run.)
        server.startTimeout = setTimeout(function () {
            failStart("server did not respond to startup ping in time");
        }, 30000);
    }

    function sendRequest(request, callback) {
        if (!server.ready || server.pid === null) {
            callback({ success: false, error: "Bridge server not running" });
            return;
        }

        server.requestCounter += 1;
        var reqId = "req_" + server.requestCounter;
        request.request_id = reqId;

        var timeoutId = setTimeout(function () {
            var cb = server.pending ? server.pending.callback : null;
            server.pending = null;
            if (cb) cb({ success: false, error: "Bridge server timed out" });
        }, 180000);

        server.pending = { id: reqId, callback: callback, timeoutId: timeoutId };

        var line = JSON.stringify(request) + "\n";
        try {
            processApi.stdin(server.pid, line);
        } catch (e) {
            clearTimeout(timeoutId);
            server.pending = null;
            callback({ success: false, error: "Could not write to bridge: " + e.message });
        }
    }

    function callEngine(op, clips, settings, callback) {
        startServer(function (ok) {
            if (!ok) {
                callback({ success: false, error: "Could not start audio engine" });
                return;
            }
            sendRequest({ op: op, clips: clips, settings: settings }, callback);
        });
    }

    // === Workflow ===

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

            callEngine("sync", clips, settings, function (resp) {
                AudioSyncProUI.setProgress(80, "اعمال روی تایم‌لاین");

                if (!resp.success) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.hideProgress();
                    AudioSyncProUI.setStatus(resp.error || "خطا در موتور", "error");
                    log(resp.error || "خطا در موتور", "error");
                    return;
                }

                var operations = resp.operations || (resp.data && resp.data.operations) || [];
                var groups = resp.groups || (resp.data && resp.data.groups) || [];
                log("گروه‌های سینک: " + groups.length, "info");
                callHost("host.applyPlan", { operations: operations }, function (res) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.setProgress(100, "تمام");
                    if (!res.success) {
                        AudioSyncProUI.setStatus(res.error || "خطا در اعمال", "error");
                        log(res.error, "error");
                    } else {
                        AudioSyncProUI.setStatus("سینک انجام شد", "success");
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

            callEngine("normalize", clips, settings, function (resp) {
                AudioSyncProUI.setProgress(80, "اعمال نرمالایز");

                if (!resp.success) {
                    AudioSyncProUI.setBusy(false);
                    AudioSyncProUI.hideProgress();
                    AudioSyncProUI.setStatus(resp.error || "خطا در موتور", "error");
                    log(resp.error || "خطا در موتور", "error");
                    return;
                }

                var operations = resp.operations || (resp.data && resp.data.operations) || [];
                var gained = resp.gained || (resp.data && resp.data.gained) || 0;
                for (var i = 0; i < operations.length; i++) {
                    var op = operations[i];
                    log(op.name + " -> gain " + (op.gainDb || 0).toFixed(2) + " dB", "info");
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

        // Terminate the background bridge when the panel is closed.
        if (typeof window !== "undefined") {
            window.addEventListener("beforeunload", function () {
                if (server.pid !== null) {
                    try { processApi.terminate(server.pid); } catch (e) {}
                    server.pid = null;
                    server.ready = false;
                }
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
