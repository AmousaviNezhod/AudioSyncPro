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

    /**
     * Collect the currently selected TrackItems, first by seq.getSelection(),
     * then (if that fails) by iterating every track and calling isSelected().
     */
    function getSelectedTrackItems(seq) {
        var items = [];
        if (seq.getSelection) {
            try {
                var sel = seq.getSelection();
                if (sel) {
                    var count = 0;
                    if (typeof sel.length === "number") count = sel.length;
                    else if (typeof sel.numItems === "number") count = sel.numItems;
                    for (var i = 0; i < count; i++) {
                        if (sel[i]) items.push(sel[i]);
                    }
                }
            } catch (e) {}
        }
        if (items.length === 0) {
            var ti, ci, track, c;
            for (ti = 0; ti < seq.videoTracks.numTracks; ti++) {
                try {
                    track = seq.videoTracks[ti];
                    for (ci = 0; ci < track.clips.numItems; ci++) {
                        c = track.clips[ci];
                        try { if (c.isSelected()) items.push(c); } catch (e2) {}
                    }
                } catch (e) {}
            }
            for (ti = 0; ti < seq.audioTracks.numTracks; ti++) {
                try {
                    track = seq.audioTracks[ti];
                    for (ci = 0; ci < track.clips.numItems; ci++) {
                        c = track.clips[ci];
                        try { if (c.isSelected()) items.push(c); } catch (e2) {}
                    }
                } catch (e) {}
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
            var clip = selected[i];
            if (!clip) continue;

            var info = findTrackAndClipIndex(seq, clip);
            var isAudio = false;
            try {
                if (clip.mediaType === "Audio" || clip.type === 2) isAudio = true;
            } catch (e) {}
            var trackIndex = info ? info.trackIndex : -1;
            var clipIndex = info ? info.clipIndex : -1;
            if (info && info.isAudio) isAudio = true;

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
        return result({ clips: clips });
    };

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

    function moveClip(seq, op) {
        try {
            var clip = findClipByIndex(seq, op.trackIndex, op.clipIndex, op.isAudio);
            if (!clip) return { success: false, error: "Clip not found at track " + op.trackIndex + " index " + op.clipIndex };

            var tracks = op.isAudio ? seq.audioTracks : seq.videoTracks;
            var targetTrackIndex = (op.newTrackIndex !== undefined) ? op.newTrackIndex : op.trackIndex;
            var projectItem = null;
            try { projectItem = clip.projectItem; } catch (e) {}

            // Distribute to a different track by removing the original and overwriting on the target track.
            if (targetTrackIndex !== op.trackIndex) {
                if (targetTrackIndex < 0 || targetTrackIndex >= tracks.numTracks) {
                    return { success: false, error: "Target track index out of range: " + targetTrackIndex };
                }
                if (!projectItem) return { success: false, error: "Clip has no project item; cannot change track" };

                var targetTrack = tracks[targetTrackIndex];

                // Preserve the source in/out points so overwriteClip inserts the same trimmed segment.
                var origIn = null, origOut = null, clipIn = null, clipOut = null;
                try {
                    clipIn = clip.inPoint;
                    clipOut = clip.outPoint;
                    origIn = projectItem.getInPoint(4);
                    origOut = projectItem.getOutPoint(4);
                    if (clipIn && clipIn.ticks) projectItem.setInPoint(clipIn.ticks, 4);
                    if (clipOut && clipOut.ticks) projectItem.setOutPoint(clipOut.ticks, 4);
                } catch (e) {}

                var insertTime = new Time();
                insertTime.seconds = op.newStartSeconds;
                var inserted = false;
                try {
                    targetTrack.overwriteClip(projectItem, insertTime);
                    inserted = true;
                } catch (e) {
                    try {
                        insertTime.ticks = String(Math.round(op.newStartSeconds * 254016000000));
                        targetTrack.overwriteClip(projectItem, insertTime);
                        inserted = true;
                    } catch (e2) {
                        return { success: false, error: "Could not insert clip to track " + targetTrackIndex + ": " + e2.toString() };
                    }
                }

                if (origIn !== null || origOut !== null) {
                    try {
                        if (origIn !== null) projectItem.setInPoint(origIn, 4);
                        if (origOut !== null) projectItem.setOutPoint(origOut, 4);
                    } catch (e) {}
                }

                if (inserted) {
                    var newClip = findClipByProjectAndStart(targetTrack, projectItem, op.newStartSeconds);
                    if (op.gainDb && newClip) {
                        setClipGainOnClip(newClip, op.gainDb);
                    }

                    try {
                        clip.remove(false, false);
                    } catch (e) {
                        return { success: false, error: "Inserted on new track but could not remove original: " + e.toString() };
                    }
                }
                return { success: true };
            }

            // Same-track time shift: clip.move expects a relative offset in seconds.
            var currentStart = getClipStartSeconds(clip);
            var delta = op.newStartSeconds - currentStart;
            if (Math.abs(delta) > 0.0001) {
                var moveTime = new Time();
                moveTime.seconds = delta;
                try {
                    clip.move(moveTime);
                } catch (e) {
                    return { success: false, error: "clip.move failed: " + e.toString() };
                }
            }

            if (op.gainDb) {
                var gainClip = op.isAudio ? clip : (findLinkedAudioClip(seq, op) || clip);
                setClipGainOnClip(gainClip, op.gainDb);
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
            var isAudio = op.isAudio === true;
            var clip = findClipByIndex(seq, op.trackIndex, op.clipIndex, isAudio);
            if (!clip && !isAudio) {
                clip = findLinkedAudioClip(seq, op);
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
