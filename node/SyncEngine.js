/**
 * Audio Sync Pro - Sync engine
 * Orchestrates ffmpeg extraction, cross-correlation, and normalization analysis.
 */

function requireSafe(name) {
    try {
        if (typeof window !== 'undefined' && window.cep_node && window.cep_node.require) {
            return window.cep_node.require(name);
        }
        return require(name);
    } catch (e) {
        throw new Error("Could not load Node module '" + name + "': " + e.message);
    }
}

var path = requireSafe('path');
var AudioUtils = requireSafe(path.join(__dirname, 'AudioUtils.js'));

var SyncEngine = SyncEngine || {};

(function () {
    /**
     * Analyze a list of media files and return their waveforms and volume stats.
     * clips: [{id, mediaPath, startSeconds}]
     * settings: { ffmpegPath, sampleRate, sampleSeconds, normalizeAudio, targetPeak }
     * onProgress(percent, label)
     */
    function analyzeClips(clips, settings, onProgress) {
        if (!clips || clips.length === 0) {
            return Promise.reject(new Error("No clips provided"));
        }
        if (settings.ffmpegPath !== 'ffmpeg' && !AudioUtils.spawnFfmpeg) {
            return Promise.reject(new Error("AudioUtils not loaded"));
        }

        onProgress = onProgress || function () {};
        var results = [];
        var total = clips.length * (settings.normalizeAudio ? 2 : 1) + 1;
        var done = 0;

        function step(label) {
            done++;
            onProgress((done / total) * 100, label);
        }

        return clips.reduce(function (promise, clip) {
            return promise.then(function () {
                return AudioUtils.extractAudio(settings.ffmpegPath, clip.mediaPath, settings.sampleRate, settings.sampleSeconds)
                    .then(function (samples) {
                        var result = {
                            id: clip.id,
                            mediaPath: clip.mediaPath,
                            startSeconds: clip.startSeconds || 0,
                            trackIndex: clip.trackIndex,
                            clipIndex: clip.clipIndex,
                            isAudio: clip.isAudio,
                            samples: samples,
                            gainDb: 0
                        };
                        step("استخراج صدا: " + clip.name);

                        if (settings.normalizeAudio) {
                            return AudioUtils.detectVolume(settings.ffmpegPath, clip.mediaPath, settings.sampleSeconds)
                                .then(function (vol) {
                                    result.maxVolume = vol.maxVolume;
                                    result.meanVolume = vol.meanVolume;
                                    result.gainDb = AudioUtils.gainForNormalization(vol.maxVolume, settings.targetPeak);
                                    step("آنالیز صدا: " + clip.name);
                                    results.push(result);
                                });
                        } else {
                            results.push(result);
                        }
                    });
            });
        }, Promise.resolve()).then(function () {
            step("آنالیز کامل شد");
            return results;
        });
    }

    /**
     * Compute offsets of each clip relative to the reference (first clip).
     * Returns array of { id, offsetSeconds, confidence, gainDb }.
     */
    function computeOffsets(results, settings, onProgress) {
        onProgress = onProgress || function () {};
        if (!results || results.length === 0) return [];

        var ref = results[0];
        var refSamples = ref.samples;
        var offsets = [{
            id: ref.id,
            offsetSeconds: 0,
            confidence: 1,
            gainDb: ref.gainDb || 0
        }];

        for (var i = 1; i < results.length; i++) {
            var target = results[i];
            var corr = AudioUtils.crossCorrelate(refSamples, target.samples, settings.sampleRate, settings.maxOffset);
            var confidence = Math.max(0, Math.min(1, corr.peakValue));
            offsets.push({
                id: target.id,
                offsetSeconds: corr.offsetSeconds,
                confidence: confidence,
                gainDb: target.gainDb || 0
            });
            onProgress(((i + 1) / results.length) * 100, "سینک " + (i + 1) + "/" + results.length);
        }

        return offsets;
    }

    /**
     * Build a plan to send to ExtendScript host.
     * Returns { operations: [...], referenceId: id }
     * operations include { type:'move', id, trackIndex, clipIndex, newStartSeconds, newTrackIndex, gainDb }
     */
    function buildPlan(results, offsets, settings) {
        var ref = results[0];
        var refStart = ref.startSeconds || 0;
        var operations = [];

        // Compute raw aligned starts relative to the reference clip's timeline position.
        // A positive offset means the target is delayed, so shift it earlier by that amount.
        var newStarts = [];
        var minStart = Infinity;
        for (var i = 0; i < results.length; i++) {
            var off = offsets[i];
            var start = refStart - off.offsetSeconds;
            newStarts.push(start);
            if (start < minStart) minStart = start;
        }

        // Shift every clip right so the earliest starts at timeline zero while preserving sync.
        var shift = 0;
        if (minStart < 0) {
            shift = -minStart;
        }

        for (var i = 0; i < results.length; i++) {
            var r = results[i];
            var off = offsets[i];
            var newStart = newStarts[i] + shift;
            if (newStart < 0) newStart = 0;

            operations.push({
                type: 'move',
                id: r.id,
                trackIndex: r.trackIndex,
                clipIndex: r.clipIndex,
                newStartSeconds: newStart,
                newTrackIndex: settings.placeOnTracks ? i : r.trackIndex,
                mediaPath: r.mediaPath,
                isAudio: r.isAudio,
                gainDb: (settings.normalizeAudio ? (off.gainDb || 0) : 0)
            });
        }

        return { operations: operations, referenceId: ref.id };
    }

    SyncEngine.analyzeClips = analyzeClips;
    SyncEngine.computeOffsets = computeOffsets;
    SyncEngine.buildPlan = buildPlan;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SyncEngine;
    }
})();
