/**
 * Minimal CSInterface shim for LUT Manager
 * Wraps the CEP runtime APIs injected at window.__adobe_cep__.
 */

function SystemPath() {}
SystemPath.USER_DATA = "userData";
SystemPath.COMMON_FILES = "commonFiles";
SystemPath.MY_DOCUMENTS = "myDocuments";
SystemPath.APPLICATION = "application";
SystemPath.EXTENSION = "extension";
SystemPath.HOST_APPLICATION = "hostApplication";

function CSInterface() {}

CSInterface.prototype.getOSInformation = function () {
    var userAgent = navigator.userAgent || "";
    if ((navigator.platform === "Win32") || (navigator.platform === "Windows")) {
        return "Windows";
    } else if ((navigator.platform === "MacIntel") || (navigator.platform === "Macintosh")) {
        return "Mac OS X";
    }
    return "Unknown Operation System";
};

CSInterface.prototype.getSystemPath = function (pathType) {
    if (!window.__adobe_cep__ || !window.__adobe_cep__.getSystemPath) {
        return "";
    }
    var raw = window.__adobe_cep__.getSystemPath(pathType);
    try {
        raw = decodeURI(raw);
    } catch (e) {}
    var path = String(raw).replace(/^file:\/\/\//, "").replace(/^file:\/\//, "");
    var OSVersion = this.getOSInformation();
    if (OSVersion.indexOf("Windows") !== -1) {
        // Windows CEP returns encoded forward-slash paths like C:/Users/...
        // but Windows APIs prefer backslashes. Normalize below in storage.
        if (path.charAt(0) === "/" && path.charAt(2) === "/") {
            // Remove leading slash from /C:/... patterns.
            path = path.substring(1);
        }
    }
    return path;
};

CSInterface.prototype.evalScript = function (script, callback) {
    if (!window.__adobe_cep__ || !window.__adobe_cep__.evalScript) {
        if (typeof callback === "function") {
            callback("");
        }
        return;
    }
    if (typeof callback !== "function") {
        callback = function () {};
    }
    window.__adobe_cep__.evalScript(script, callback);
};

CSInterface.prototype.getHostEnvironment = function () {
    if (window.__adobe_cep__ && window.__adobe_cep__.getHostEnvironment) {
        return JSON.parse(window.__adobe_cep__.getHostEnvironment());
    }
    return null;
};

CSInterface.prototype.closeExtension = function () {
    if (window.__adobe_cep__ && window.__adobe_cep__.closeExtension) {
        window.__adobe_cep__.closeExtension();
    }
};

CSInterface.prototype.dispatchEvent = function (event) {
    if (window.__adobe_cep__ && window.__adobe_cep__.dispatchEvent) {
        window.__adobe_cep__.dispatchEvent(
            JSON.stringify(event)
        );
    }
};

CSInterface.prototype.addEventListener = function (type, listener, obj) {
    if (!window.__adobe_cep__) return;
    if (!window.__adobe_cep__.addEventListener) return;
    window.__adobe_cep__.addEventListener(type, listener, obj);
};

CSInterface.prototype.removeEventListener = function (type, listener, obj) {
    if (!window.__adobe_cep__) return;
    if (!window.__adobe_cep__.removeEventListener) return;
    window.__adobe_cep__.removeEventListener(type, listener, obj);
};

CSInterface.prototype.openURLInDefaultBrowser = function (url) {
    if (window.cep && window.cep.util && window.cep.util.openURLInDefaultBrowser) {
        return window.cep.util.openURLInDefaultBrowser(url);
    }
    return -1;
};

CSInterface.prototype.getExtensionID = function () {
    var env = this.getHostEnvironment();
    return env && env.extensionId ? env.extensionId : "";
};
