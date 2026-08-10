/**
 * Audio Sync Pro - Sync engine
 * Orchestrates ffmpeg extraction, cross-correlation, and normalization analysis.
 * Groups selected clips by audio correlation: matching clips are stacked on
 * separate tracks, while leftovers are sequenced end-to-end on one track.
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
     * clips: [{id, mediaPath, startSeconds, durationSeconds}]
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
                            name: clip.name || clip.id,
                            startSeconds: clip.startSeconds || 0,
                            durationSeconds: clip.durationSeconds || 0,
                            trackIndex: clip.trackIndex,
                            clipIndex: clip.clipIndex,
                            isAudio: clip.isAudio,
                            samples: samples,
                            gainDb: 0
                        };
                        step("استخراج صدا: " + result.name);

                        if (settings.normalizeAudio) {
                            return AudioUtils.detectVolume(settings.ffmpegPath, clip.mediaPath, settings.sampleSeconds)
                                .then(function (vol) {
                                    result.maxVolume = vol.maxVolume;
                                    result.meanVolume = vol.meanVolume;
                                    result.gainDb = AudioUtils.gainForNormalization(vol.maxVolume, settings.targetPeak);
                                    step("آنالیز صدا: " + result.name);
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
     * Find connected components (groups) of clips whose audio correlates above
     * the threshold. Returns { groups: [...], orphans: [...] }.
     *
     * group: { refIndex, members: [{ index, offsetSeconds, confidence, gainDb }] }
     * orphan: index number
     */
    function findGroups(results, settings, onProgress) {
        onProgress = onProgress || function () {};
        if (!results || results.length === 0) return { groups: [], orphans: [] };
        if (results.length === 1) return { groups: [], orphans: [0] };

        var sampleRate = settings.sampleRate || 16000;
        var maxOffset = settings.maxOffset || 10;
        var threshold = settings.matchThreshold || 0.45;
        var n = results.length;
        var corrMatrix = [];
        for (var i = 0; i < n; i++) {
            corrMatrix[i] = [];
        }

        var totalPairs = n * (n - 1) / 2;
        var done = 0;

        for (var i = 0; i < n; i++) {
            for (var j = i + 1; j < n; j++) {
                var corr = AudioUtils.crossCorrelate(results[i].samples, results[j].samples, sampleRate, maxOffset);
                done++;
                onProgress((done / totalPairs) * 100, "مقایسه " + (i + 1) + " و " + (j + 1));

                var conf = corr.peakValue;
                if (conf < 0) conf = 0;
                // offset of j relative to i
                corrMatrix[i][j] = { confidence: conf, offset: corr.offsetSeconds };
                // offset of i relative to j is approximately the inverse
                corrMatrix[j][i] = { confidence: conf, offset: -corr.offsetSeconds };
            }
        }

        // Build adjacency list from confident edges.
        var adj = [];
        for (var i = 0; i < n; i++) adj[i] = [];
        for (var i = 0; i < n; i++) {
            for (var j = i + 1; j < n; j++) {
                if (corrMatrix[i][j].confidence >= threshold) {
                    adj[i].push(j);
                    adj[j].push(i);
                }
            }
        }

        // Find connected components.
        var visited = [];
        for (var i = 0; i < n; i++) visited[i] = false;
        var components = [];

        for (var i = 0; i < n; i++) {
            if (visited[i]) continue;
            var comp = [];
            var stack = [i];
            visited[i] = true;
            while (stack.length > 0) {
                var v = stack.pop();
                comp.push(v);
                var nb = adj[v];
                for (var k = 0; k < nb.length; k++) {
                    var u = nb[k];
                    if (!visited[u]) {
                        visited[u] = true;
                        stack.push(u);
                    }
                }
            }
            components.push(comp);
        }

        var groups = [];
        var orphans = [];

        for (var ci = 0; ci < components.length; ci++) {
            var comp = components[ci];
            if (comp.length === 1) {
                orphans.push(comp[0]);
                continue;
            }

            // Pick the clip with the strongest total correlation to others as reference.
            var bestRef = comp[0];
            var bestScore = -Infinity;
            for (var k = 0; k < comp.length; k++) {
                var v = comp[k];
                var score = 0;
                for (var m = 0; m < comp.length; m++) {
                    if (v === comp[m]) continue;
                    score += corrMatrix[v][comp[m]].confidence;
                }
                if (score > bestScore) {
                    bestScore = score;
                    bestRef = v;
                }
            }

            // Resolve each member's offset relative to the chosen reference.
            var members = [];
            for (var k = 0; k < comp.length; k++) {
                var idx = comp[k];
                if (idx === bestRef) {
                    members.push({
                        index: idx,
                        offsetSeconds: 0,
                        confidence: 1,
                        gainDb: results[idx].gainDb || 0
                    });
                } else {
                    var edge = corrMatrix[bestRef][idx];
                    var conf = edge.confidence;
                    var offset = edge.offset;
                    // If the direct edge is weak but they are connected indirectly, use a path sum.
                    if (conf < threshold) {
                        var pathOffset = getPathOffset(corrMatrix, adj, bestRef, idx, threshold);
                        if (pathOffset !== null) {
                            offset = pathOffset;
                        }
                    }
                    members.push({
                        index: idx,
                        offsetSeconds: offset,
                        confidence: conf,
                        gainDb: results[idx].gainDb || 0
                    });
                }
            }

            groups.push({
                refIndex: bestRef,
                members: members
            });
        }

        return { groups: groups, orphans: orphans };
    }

    /**
     * Find an offset path from start to end through the confidence graph and sum
     * the signed offsets. Returns null if no path exists.
     */
    function getPathOffset(corrMatrix, adj, start, end, threshold) {
        var n = adj.length;
        var visited = [];
        var parent = [];
        for (var i = 0; i < n; i++) {
            visited[i] = false;
            parent[i] = -1;
        }
        var queue = [start];
        visited[start] = true;
        while (queue.length > 0) {
            var v = queue.shift();
            var nb = adj[v];
            for (var k = 0; k < nb.length; k++) {
                var u = nb[k];
                if (!visited[u]) {
                    visited[u] = true;
                    parent[u] = v;
                    if (u === end) {
                        // Reconstruct path and sum offsets.
                        var total = 0;
                        var cur = end;
                        while (cur !== start) {
                            var p = parent[cur];
                            total += corrMatrix[p][cur].offset;
                            cur = p;
                        }
                        return total;
                    }
                    queue.push(u);
                }
            }
        }
        return null;
    }

    /**
     * Build a plan to send to ExtendScript host.
     * groupsObj: result of findGroups()
     * settings: { normalizeAudio }
     *
     * Returns { operations: [...], groups: [...] } where operations include
     * { type:'move', id, trackIndex, clipIndex, newStartSeconds, newTrackIndex, gainDb, isAudio, mediaPath }
     */
    function buildPlan(results, groupsObj, settings) {
        var operations = [];
        var nextTrack = 0;
        var maxGroupEnd = 0;

        // Process sync groups: each group is stacked vertically (separate tracks).
        for (var g = 0; g < groupsObj.groups.length; g++) {
            var group = groupsObj.groups[g];
            var ref = results[group.refIndex];
            var refStart = ref.startSeconds || 0;

            // Compute aligned starts relative to the reference.
            var aligned = [];
            for (var m = 0; m < group.members.length; m++) {
                var mem = group.members[m];
                var start = refStart - mem.offsetSeconds;
                aligned.push({
                    index: mem.index,
                    start: start,
                    member: mem
                });
            }

            // Shift the whole group right if any aligned start would be negative.
            var minStart = Infinity;
            for (var m = 0; m < aligned.length; m++) {
                if (aligned[m].start < minStart) minStart = aligned[m].start;
            }
            var shift = 0;
            if (minStart < 0) shift = -minStart;

            var groupEnd = -Infinity;
            for (var m = 0; m < aligned.length; m++) {
                var idx = aligned[m].index;
                var newStart = aligned[m].start + shift;
                if (newStart < 0) newStart = 0;
                aligned[m].newStart = newStart;
                var dur = results[idx].durationSeconds || 0;
                var end = newStart + dur;
                if (end > groupEnd) groupEnd = end;

                operations.push({
                    type: 'move',
                    id: results[idx].id,
                    trackIndex: results[idx].trackIndex,
                    clipIndex: results[idx].clipIndex,
                    isAudio: results[idx].isAudio,
                    mediaPath: results[idx].mediaPath,
                    newStartSeconds: newStart,
                    newTrackIndex: nextTrack,
                    gainDb: settings.normalizeAudio ? (aligned[m].member.gainDb || 0) : 0
                });

                nextTrack++;
            }

            if (groupEnd > maxGroupEnd) maxGroupEnd = groupEnd;
        }

        // Process leftovers: sequence them end-to-end on one track after all groups.
        var leftoverTrack = nextTrack;
        var cursor = maxGroupEnd;

        var orphanItems = [];
        for (var o = 0; o < groupsObj.orphans.length; o++) {
            var idx = groupsObj.orphans[o];
            orphanItems.push({
                index: idx,
                start: results[idx].startSeconds || 0
            });
        }
        orphanItems.sort(function (a, b) { return a.start - b.start; });

        for (var o = 0; o < orphanItems.length; o++) {
            var idx = orphanItems[o].index;
            var dur = results[idx].durationSeconds || 0;

            operations.push({
                type: 'move',
                id: results[idx].id,
                trackIndex: results[idx].trackIndex,
                clipIndex: results[idx].clipIndex,
                isAudio: results[idx].isAudio,
                mediaPath: results[idx].mediaPath,
                newStartSeconds: cursor,
                newTrackIndex: leftoverTrack,
                gainDb: settings.normalizeAudio ? (results[idx].gainDb || 0) : 0
            });

            cursor += dur;
        }

        return { operations: operations, groups: groupsObj.groups };
    }

    SyncEngine.analyzeClips = analyzeClips;
    SyncEngine.findGroups = findGroups;
    SyncEngine.buildPlan = buildPlan;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SyncEngine;
    }
})();
