/**
 * Audio Sync Pro - ExtendScript host bridge
 * Runs inside Premiere Pro. Handles timeline selection, clip movement, and audio gain.
 */
var host = host || {};

(function () {
    function stringify(obj) {
        if (typeof JSON !== "undefined" && JSON.stringify) {
            return JSON.stringify(obj);
        }
        var s = [];
        for (var k in obj) {
            if (obj.hasOwnProperty(k)) {
                var v = obj[k];
                var key = '"' + k + '"';
                var val;
                if (typeof v === "string") val = '"' + v.replace(/"/g, '\\"') + '"';
                else if (typeof v === "boolean" || typeof v === "number") val = String(v);
                else if (v && typeof v === "object" && Array.isArray && Array.isArray(v)) {
                    val = "[" + v.map(stringify).join(",") + "]";
                } else if (v && typeof v === "object") val = stringify(v);
                else val = "null";
                s.push(key + ":" + val);
            }
        }
        return "{" + s.join(",") + "}";
    }

    function parse(str) {
        if (typeof JSON !== "undefined" && JSON.parse) {
            return JSON.parse(str);
        }
        return eval("(" + str + ")");
    }

    function result(obj) {
        return stringify({ success: true, data: obj });
    }

    function error(msg) {
        return stringify({ success: false, error: String(msg) });
    }

    function findClipByIndex(seq, trackIndex, clipIndex, isAudio) {
        var tracks = isAudio ? seq.audioTracks : seq.videoTracks;
        if (trackIndex < 0 || trackIndex >= tracks.numTracks) return null;
        var track = tracks[trackIndex];
        if (clipIndex < 0 || clipIndex >= track.clips.numItems) return null;
        return track.clips[clipIndex];
    }

    function getClipPath(clip) {
        try {
            if (clip.projectItem) {
                return clip.projectItem.getMediaPath ? clip.projectItem.getMediaPath() : "";
            }
        } catch (e) {}
        return "";
    }

    function isAudioClip(clip) {
        try {
            if (clip.mediaType === "Audio") return true;
            if (clip.type === 2) return true; // 2 = audio in some versions
        } catch (e) {}
        try {
            if (clip.projectItem && clip.projectItem.mediaType) {
                return clip.projectItem.mediaType === "Audio";
            }
        } catch (e) {}
        return false;
    }

    function getClipStartSeconds(clip) {
        try {
            return parseFloat(clip.start.seconds);
        } catch (e) {}
        return 0;
    }

    function getClipDurationSeconds(clip) {
        try {
            var start = parseFloat(clip.start.seconds);
            var end = parseFloat(clip.end.seconds);
            if (!isNaN(end - start)) return end - start;
        } catch (e) {}
        try {
            var inPoint = parseFloat(clip.inPoint.seconds);
            var outPoint = parseFloat(clip.outPoint.seconds);
            if (!isNaN(outPoint - inPoint)) return outPoint - inPoint;
        } catch (e) {}
        return 0;
    }

    function getTrackEndSeconds(track) {
        try {
            if (track.clips && track.clips.numItems > 0) {
                var last = track.clips[track.clips.numItems - 1];
                return parseFloat(last.end.seconds);
            }
        } catch (e) {}
        return 0.0;
    }

    /**
     * Collect selected TrackItems by walking every video/audio track and
     * checking isSelected(). Linked video+audio instances (e.g. from an MP4)
     * are collapsed into a single primary item, preferring the video track item.
     */
    function getSelectedTrackItems(seq) {
        var raw = [];
        var ti, ci, track, c;

        // Video tracks
        for (ti = 0; ti < seq.videoTracks.numTracks; ti++) {
            try {
                track = seq.videoTracks[ti];
                for (ci = 0; ci < track.clips.numItems; ci++) {
                    c = track.clips[ci];
                    try {
                        if (c.isSelected && c.isSelected()) {
                            raw.push({ clip: c, trackIndex: ti, clipIndex: ci, isAudio: false });
                        }
                    } catch (e2) {}
                }
            } catch (e) {}
        }

        // Audio tracks
        for (ti = 0; ti < seq.audioTracks.numTracks; ti++) {
            try {
                track = seq.audioTracks[ti];
                for (ci = 0; ci < track.clips.numItems; ci++) {
                    c = track.clips[ci];
                    try {
                        if (c.isSelected && c.isSelected()) {
                            raw.push({ clip: c, trackIndex: ti, clipIndex: ci, isAudio: true });
                        }
                    } catch (e2) {}
                }
            } catch (e) {}
        }

        // Fallback if isSelected is not available: try seq.getSelection().
        if (raw.length === 0 && seq.getSelection) {
            try {
                var sel = seq.getSelection();
                if (sel) {
                    var count = 0;
                    if (typeof sel.length === "number") count = sel.length;
                    else if (typeof sel.numItems === "number") count = sel.numItems;
                    for (var i = 0; i < count; i++) {
                        var clip = sel[i];
                        if (!clip) continue;
                        var info = findTrackAndClipIndex(seq, clip);
                        if (info) {
                            raw.push({ clip: clip, trackIndex: info.trackIndex, clipIndex: info.clipIndex, isAudio: info.isAudio });
                        }
                    }
                }
            } catch (e) {}
        }

        // Deduplicate linked video/audio pairs by source+in/out+start. Keep video as primary.
        var seen = {};
        var seenIdx = {};
        var items = [];
        for (var i = 0; i < raw.length; i++) {
            var item = raw[i];
            if (!item || !item.clip) continue;
            var sig = getClipSignature(item.clip);
            var key = sig.name + "|" + sig.start.toFixed(3) + "|" + sig.inPoint.toFixed(3) + "|" + sig.outPoint.toFixed(3);
            if (seen[key]) {
                if (!item.isAudio && seen[key].isAudio) {
                    seen[key] = item;
                    items[seenIdx[key]] = item;
                }
            } else {
                seen[key] = item;
                seenIdx[key] = items.length;
                items.push(item);
            }
        }

        return items;
    }

    function getClipSignature(clip) {
        var name = "";
        var start = 0.0, end = 0.0, inPoint = 0.0, outPoint = 0.0, nodeId = "";
        try { name = String(clip.name); } catch (e) {}
        try { start = parseFloat(clip.start.seconds); } catch (e) {}
        try { end = parseFloat(clip.end.seconds); } catch (e) {}
        try { inPoint = parseFloat(clip.inPoint.seconds); } catch (e) {}
        try { outPoint = parseFloat(clip.outPoint.seconds); } catch (e) {}
        try { nodeId = String(clip.nodeId); } catch (e) {}
        return { name: name, start: start, end: end, inPoint: inPoint, outPoint: outPoint, nodeId: nodeId };
    }

    function isSameClip(candidate, sig) {
        try {
            var cnode = "";
            try { cnode = String(candidate.nodeId); } catch (e) {}
            if (sig.nodeId && cnode && cnode === sig.nodeId) return true;

            var cname = "";
            try { cname = String(candidate.name); } catch (e) {}
            if (cname !== sig.name) return false;

            var cstart = 0.0, cend = 0.0;
            try { cstart = parseFloat(candidate.start.seconds); } catch (e) {}
            try { cend = parseFloat(candidate.end.seconds); } catch (e) {}
            if (Math.abs(cstart - sig.start) > 0.001) return false;
            if (Math.abs(cend - sig.end) > 0.001) return false;
            return true;
        } catch (e) {}
        return false;
    }

    function findTrackAndClipIndex(seq, clip) {
        var sig = getClipSignature(clip);
        var ti, ci, track, c;
        for (ti = 0; ti < seq.videoTracks.numTracks; ti++) {
            track = seq.videoTracks[ti];
            for (ci = 0; ci < track.clips.numItems; ci++) {
                c = track.clips[ci];
                if (isSameClip(c, sig)) return { trackIndex: ti, clipIndex: ci, isAudio: false };
            }
        }
        for (ti = 0; ti < seq.audioTracks.numTracks; ti++) {
            track = seq.audioTracks[ti];
            for (ci = 0; ci < track.clips.numItems; ci++) {
                c = track.clips[ci];
                if (isSameClip(c, sig)) return { trackIndex: ti, clipIndex: ci, isAudio: true };
            }
        }
        return null;
    }

    /**
     * Get selected clips info from the active sequence.
     * Also stores the actual clip references so applyPlan can use them directly,
     * avoiding index-shift or name-lookup problems.
     */
    host.getSelectedClips = function () {
        var seq = app.project.activeSequence;
        if (!seq) return error("No active sequence");

        var selected = getSelectedTrackItems(seq);
        if (selected.length === 0) {
            return error("No clips selected on timeline");
        }

        var clips = [];
        for (var i = 0; i < selected.length; i++) {
            var item = selected[i];
            if (!item || !item.clip) continue;

            var clip = item.clip;
            var trackIndex = item.trackIndex;
            var clipIndex = item.clipIndex;
            var isAudio = item.isAudio;

            var name = "";
            try { name = clip.name; } catch (e) {}

            clips.push({
                id: i,
                name: name,
                mediaPath: getClipPath(clip),
                startSeconds: getClipStartSeconds(clip),
                durationSeconds: getClipDurationSeconds(clip),
                trackIndex: trackIndex,
                clipIndex: clipIndex,
                isAudio: isAudio
            });
        }

        if (clips.length === 0) return error("No valid clips in selection");

        // Persist selected clip references for applyPlan.
        try { host.__clipStore = selected; } catch (e) {}

        return result({ clips: clips });
    };

    /**
     * Ensure the sequence has enough tracks for the planned operations.
     * Uses QE DOM if available, which is officially unsupported but widely used.
     */
    function ensureTracksAvailable(seq, operations) {
        try {
            var maxVideoTrack = -1, maxAudioTrack = -1;
            for (var i = 0; i < operations.length; i++) {
                var op = operations[i];
                if (op.type !== "move" || typeof op.newTrackIndex !== "number") continue;
                var isAudio = op.isAudio === true;
                if (isAudio) {
                    if (op.newTrackIndex > maxAudioTrack) maxAudioTrack = op.newTrackIndex;
                } else {
                    if (op.newTrackIndex > maxVideoTrack) maxVideoTrack = op.newTrackIndex;
                }
            }
            if (maxVideoTrack < 0 && maxAudioTrack < 0) return true;

            var numVideo = seq.videoTracks.numTracks;
            var numAudio = seq.audioTracks.numTracks;
            var needVideo = Math.max(0, maxVideoTrack - numVideo + 1);
            var needAudio = Math.max(0, maxAudioTrack - numAudio + 1);
            if (needVideo === 0 && needAudio === 0) return true;

            app.enableQE();
            var qeSeq = null;
            if (qe && qe.project) {
                try { qeSeq = qe.project.getActiveSequence(); } catch (e) {}
                if (!qeSeq) try { qeSeq = qe.project.getActiveSequence(0); } catch (e) {}
            }
            if (!qeSeq) return false;

            // Try the common QE addTracks signature:
            // addTracks(numVideo, videoAfterIndex, numAudio, audioAfterIndex, ...)
            try {
                qeSeq.addTracks(needVideo, numVideo, needAudio, numAudio);
            } catch (e) {
                // If that fails, try the documented 8-argument form.
                try {
                    qeSeq.addTracks(needVideo, numVideo, 0, 0, needAudio, 0, 0, numAudio);
                } catch (e2) {
                    return false;
                }
            }

            // Verify tracks were added.
            if (seq.videoTracks.numTracks >= maxVideoTrack + 1 && seq.audioTracks.numTracks >= maxAudioTrack + 1) {
                return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    }

    /**
     * Apply a plan of move and gain operations.
     * plan JSON array of operations.
     */
    host.applyPlan = function (planJson) {
        var seq = app.project.activeSequence;
        if (!seq) return error("No active sequence");

        var plan;
        try {
            plan = parse(planJson);
        } catch (e) {
            return error("Invalid plan JSON: " + e.toString());
        }
        if (!plan || !plan.operations || plan.operations.length === 0) {
            return error("Plan is empty");
        }

        if (!ensureTracksAvailable(seq, plan.operations)) {
            return error("Sequence does not have enough tracks for the sync result. Add more video/audio tracks and try again.");
        }

        var moved = 0;
        var gained = 0;
        var errors = [];

        // Process moves from highest original clip index down to avoid index shifts during removal/insertion.
        plan.operations.sort(function (a, b) {
            if (a.type === 'move' && b.type === 'move') {
                if (a.trackIndex !== b.trackIndex) return a.trackIndex - b.trackIndex;
                return b.clipIndex - a.clipIndex;
            }
            if (a.type === 'move') return -1;
            if (b.type === 'move') return 1;
            return 0;
        });

        for (var i = 0; i < plan.operations.length; i++) {
            var op = plan.operations[i];
            if (op.type === "move") {
                var moveRes = moveClip(seq, op);
                if (moveRes.success) moved++;
                else errors.push(moveRes.error);
            } else if (op.type === "gain") {
                var gainRes = setClipGain(seq, op);
                if (gainRes.success) gained++;
                else errors.push(gainRes.error);
            }
        }

        return result({
            moved: moved,
            gained: gained,
            errors: errors
        });
    };

    function getStoredClip(op) {
        try {
            if (host.__clipStore && host.__clipStore[op.id]) {
                return host.__clipStore[op.id];
            }
        } catch (e) {}
        return null;
    }

    function moveClip(seq, op) {
        try {
            var item = getStoredClip(op);
            var clip = null, isAudio = false, origTrackIndex = -1, origClipIndex = -1;
            if (item && item.clip) {
                clip = item.clip;
                isAudio = item.isAudio;
                origTrackIndex = item.trackIndex;
                origClipIndex = item.clipIndex;
            } else {
                clip = findClipByIndex(seq, op.trackIndex, op.clipIndex, op.isAudio);
                if (!clip) return { success: false, error: "Clip not found at track " + op.trackIndex + " index " + op.clipIndex };
                isAudio = op.isAudio;
                origTrackIndex = op.trackIndex;
                origClipIndex = op.clipIndex;
            }

            var targetVTrack = (op.newTrackIndex !== undefined) ? op.newTrackIndex : origTrackIndex;
            var targetATrack = targetVTrack;
            var projectItem = null;
            try { projectItem = clip.projectItem; } catch (e) {}
            if (!projectItem) return { success: false, error: "Clip has no project item; cannot move" };

            if (targetVTrack < 0 || targetVTrack >= seq.videoTracks.numTracks) {
                return { success: false, error: "Target video track index out of range: " + targetVTrack };
            }
            if (targetATrack < 0 || targetATrack >= seq.audioTracks.numTracks) {
                return { success: false, error: "Target audio track index out of range: " + targetATrack };
            }

            var currentStart = getClipStartSeconds(clip);
            if (origTrackIndex === targetVTrack && Math.abs(currentStart - op.newStartSeconds) < 0.0001 && !op.gainDb) {
                return { success: true };
            }

            // Preserve the source in/out points so the re-inserted segment keeps its trim.
            var origIn = null, origOut = null, clipIn = null, clipOut = null;
            try {
                clipIn = clip.inPoint;
                clipOut = clip.outPoint;
                origIn = projectItem.getInPoint(4);
                origOut = projectItem.getOutPoint(4);
                if (clipIn && clipIn.ticks) projectItem.setInPoint(clipIn.ticks, 4);
                if (clipOut && clipOut.ticks) projectItem.setOutPoint(clipOut.ticks, 4);
            } catch (e) {}

            // Find original audio before any modifications.
            var origAudio = null;
            try { if (!isAudio) origAudio = findLinkedAudioClip(seq, op); } catch (e) {}

            var vTrack = seq.videoTracks[targetVTrack];
            var aTrack = seq.audioTracks[targetATrack];

            // Use a "safe insert" at the end of the target tracks, then move to the
            // final time. This avoids the ripple/overwrite bugs that occur when
            // inserting directly onto a track that already contains clips.
            var safeV = getTrackEndSeconds(vTrack);
            var safeA = getTrackEndSeconds(aTrack);
            var safeTime = Math.max(safeV, safeA);
            var safeTimeObj = new Time();
            safeTimeObj.seconds = safeTime;

            var newVClip = null;
            var newAClip = null;
            var usedSafeInsert = false;

            try {
                seq.insertClip(projectItem, safeTimeObj, targetVTrack, targetATrack);
                usedSafeInsert = true;
                if (vTrack.clips && vTrack.clips.numItems > 0) newVClip = vTrack.clips[vTrack.clips.numItems - 1];
                if (aTrack.clips && aTrack.clips.numItems > 0) newAClip = aTrack.clips[aTrack.clips.numItems - 1];
            } catch (e1) {
                // Fallback: direct overwrite at target time.
                try {
                    var targetTimeObj = new Time();
                    targetTimeObj.seconds = op.newStartSeconds;
                    seq.overwriteClip(projectItem, targetTimeObj, targetVTrack, targetATrack);
                    newVClip = findClipByProjectAndStart(vTrack, projectItem, op.newStartSeconds);
                    if (!isAudio) newAClip = findClipByProjectAndStart(aTrack, projectItem, op.newStartSeconds);
                } catch (e2) {
                    return { success: false, error: "Could not insert/overwrite clip on target tracks: " + e2.toString() };
                }
            }

            // Remove originals (TrackItem.remove only removes one track item).
            try { clip.remove(false, false); } catch (e) {}
            if (origAudio) {
                try { origAudio.remove(false, false); } catch (e) {}
            }

            // If we used safe-insert, shift the new clips to their final time.
            if (usedSafeInsert) {
                var delta = op.newStartSeconds - safeTime;
                if (Math.abs(delta) > 0.0001) {
                    var deltaTime = new Time();
                    deltaTime.seconds = delta;
                    if (newVClip) {
                        try { newVClip.move(deltaTime); } catch (e) {}
                    }
                    if (newAClip) {
                        // Avoid double-moving if the video move already dragged linked audio.
                        var expectedA = safeTime + delta;
                        var currentA = getClipStartSeconds(newAClip);
                        if (Math.abs(currentA - expectedA) > 0.001) {
                            try { newAClip.move(deltaTime); } catch (e) {}
                        }
                    }
                }
            }

            // Restore projectItem in/out points.
            try {
                if (origIn !== null || origOut !== null) {
                    if (origIn !== null) projectItem.setInPoint(origIn, 4);
                    if (origOut !== null) projectItem.setOutPoint(origOut, 4);
                }
            } catch (e) {}

            // Apply gain to the new audio track item.
            if (op.gainDb) {
                var gainClip = newAClip || (isAudio ? clip : null);
                if (!gainClip && !isAudio) gainClip = findClipByProjectAndStart(aTrack, projectItem, op.newStartSeconds);
                if (gainClip) setClipGainOnClip(gainClip, op.gainDb);
            }

            return { success: true };
        } catch (e) {
            return { success: false, error: "moveClip exception: " + e.toString() };
        }
    }

    function findClipByProjectAndStart(track, projectItem, startSeconds) {
        try {
            var best = null;
            var bestDelta = Infinity;
            for (var i = 0; i < track.clips.numItems; i++) {
                var c = track.clips[i];
                if (c.projectItem === projectItem) {
                    var delta = Math.abs(getClipStartSeconds(c) - startSeconds);
                    if (delta < 0.1 && delta < bestDelta) {
                        bestDelta = delta;
                        best = c;
                    }
                }
            }
            return best;
        } catch (e) {}
        return null;
    }

    function setClipGain(seq, op) {
        try {
            var item = getStoredClip(op);
            var isAudio = false;
            var clip = null;
            if (item && item.clip) {
                clip = item.clip;
                isAudio = item.isAudio;
            } else {
                isAudio = op.isAudio === true;
                clip = findClipByIndex(seq, op.trackIndex, op.clipIndex, isAudio);
                if (!clip && !isAudio) {
                    clip = findLinkedAudioClip(seq, op);
                }
            }
            if (!clip) return { success: false, error: "Could not find clip for gain adjustment" };

            var db = parseFloat(op.gainDb);
            if (isNaN(db)) return { success: false, error: "Invalid gain dB: " + op.gainDb };

            if (setClipGainOnClip(clip, db)) {
                return { success: true };
            }
            return { success: false, error: "Clip has no volume property" };
        } catch (e) {
            return { success: false, error: "setClipGain exception: " + e.toString() };
        }
    }

    function setClipGainOnClip(clip, gainDb) {
        if (!clip) return false;
        var dec = dbToDec(parseFloat(gainDb));
        if (clip.components && clip.components.numItems) {
            for (var ci = 0; ci < clip.components.numItems; ci++) {
                var comp = clip.components[ci];
                if (comp.properties && comp.properties.numItems) {
                    for (var pi = 0; pi < comp.properties.numItems; pi++) {
                        var prop = comp.properties[pi];
                        var displayName = "";
                        try { displayName = prop.displayName || prop.name || ""; } catch (e) {}
                        if (String(displayName).toLowerCase().indexOf("volume") !== -1) {
                            if (prop.setValue) {
                                prop.setValue(dec, true);
                                return true;
                            }
                        }
                    }
                }
            }
        }
        return false;
    }

    function findLinkedAudioClip(seq, op) {
        // Heuristic: match by name and start time across audio tracks.
        try {
            var sourceClip = findClipByIndex(seq, op.trackIndex, op.clipIndex, false);
            if (!sourceClip) return null;
            var sourceName = String(sourceClip.name || "").toLowerCase();
            var sourceStart = getClipStartSeconds(sourceClip);
            for (var ti = 0; ti < seq.audioTracks.numTracks; ti++) {
                var track = seq.audioTracks[ti];
                for (var ci = 0; ci < track.clips.numItems; ci++) {
                    var c = track.clips[ci];
                    if (String(c.name || "").toLowerCase() === sourceName && Math.abs(getClipStartSeconds(c) - sourceStart) < 0.001) {
                        return c;
                    }
                }
            }
        } catch (e) {}
        return null;
    }

    function dbToDec(x) {
        return Math.pow(10, (x - 15) / 20);
    }

    function decToDb(x) {
        return 20 * Math.log(x) * Math.LOG10E + 15;
    }

    host.dbToDec = dbToDec;
    host.decToDb = decToDb;
})();
