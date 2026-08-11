/**
 * Audio Sync Pro - Main controller (Python bridge)
 *
 * A compiled Python server is launched once via window.cep.process and kept
 * alive in the background. Requests are sent as JSON lines over stdin and
 * responses are read as JSON lines from stdout. This avoids spawning a new
 * process (and a new console) for every sync/normalize operation.
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
        workDir: ""
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

    function getBridgePath() {
        var extBase = cs.getSystemPath(SystemPath.EXTENSION).replace(/\/$/, "");
        var exePath = toNativePath(extBase + "/python/dist/sync_bridge.exe");
        var scriptPath = toNativePath(extBase + "/python/sync_bridge.py");
        return { exePath: exePath, scriptPath: scriptPath };
    }

    function flushStdoutBuffer() {
        var lines = server.stdoutBuffer.split("\n");
        server.stdoutBuffer = lines.pop();
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line) continue;
            try {
                var resp = JSON.parse(line);
                if (server.pending) {
                    clearTimeout(server.pending.timeoutId);
                    var cb = server.pending.callback;
                    server.pending = null;
                    cb(resp);
                } else {
                    log("Unexpected server response", "warn");
                }
            } catch (e) {
                log("Bad server JSON: " + line.substring(0, 80), "warn");
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
            if (line) log("[server] " + line, "info");
        }
    }

    function onServerStdout(chunk) {
        if (chunk === null || chunk === undefined) return;
        server.stdoutBuffer += chunk;
        flushStdoutBuffer();
    }

    function startServer(callback) {
        if (server.starting) return;
        if (server.ready && server.pid !== null) {
            return callback(true);
        }

        // Reset state if restarting.
        if (server.pid !== null) {
            try {
                processApi.terminate(server.pid);
            } catch (e) {}
            server.pid = null;
        }
        server.starting = true;
        server.stdoutBuffer = "";
        server.stderrBuffer = "";
        server.pending = null;

        var os = "";
        try {
            os = cs.getOSInformation();
        } catch (e) {}
        var isWindows = os.indexOf("Windows") !== -1;
        var paths = getBridgePath();
        var pythonPath = toNativePath("python");

        function trySpawn(executable, args, onFail) {
            var callArgs = [executable].concat(args);
            var procRes = processApi.createProcess.apply(processApi, callArgs);
            if (procRes.err !== 0) {
                if (onFail) return onFail();
                server.starting = false;
                callback(false);
                return;
            }
            server.pid = procRes.data;
            try {
                processApi.stdout(server.pid, onServerStdout);
            } catch (e) {}
            try {
                processApi.stderr(server.pid, onServerStderr);
            } catch (e) {}

            // Wait briefly for the server to initialize, then mark ready.
            setTimeout(function () {
                var running = processApi.isRunning(server.pid);
                if (running.err === 0 && running.data) {
                    server.starting = false;
                    server.ready = true;
                    callback(true);
                } else {
                    server.starting = false;
                    server.ready = false;
                    try {
                        processApi.terminate(server.pid);
                    } catch (e2) {}
                    server.pid = null;
                    if (onFail) return onFail();
                    callback(false);
                }
            }, 250);
        }

        if (isWindows) {
            trySpawn(paths.exePath, ["--server"], function () {
                log("Compiled bridge failed, trying Python script", "warn");
                trySpawn(pythonPath, [paths.scriptPath, "--server"], function () {
                    server.starting = false;
                    callback(false);
                });
            });
        } else {
            trySpawn(pythonPath, [paths.scriptPath, "--server"], function () {
                server.starting = false;
                callback(false);
            });
        }
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

    function runPythonBridge(op, clips, settings, callback) {
        server.workDir = ensureWorkDir();
        startServer(function (ok) {
            if (!ok) {
                callback({ success: false, error: "Could not start bridge server" });
                return;
            }
            sendRequest({ op: op, clips: clips, settings: settings }, callback);
        });
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

        // Terminate the background bridge when the panel is closed.
        if (typeof window !== "undefined") {
            window.addEventListener("beforeunload", function () {
                if (server.pid !== null) {
                    try {
                        processApi.terminate(server.pid);
                    } catch (e) {}
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
