/**
 * Audio Sync Pro - Pure Node.js audio utilities
 * No external npm dependencies; only uses built-in 'child_process' and 'fs'.
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

var fs = requireSafe('fs');
var path = requireSafe('path');
var childProcess = requireSafe('child_process');

var AudioUtils = AudioUtils || {};

(function () {
    /**
     * Iterative radix-2 FFT (Cooley-Tukey) in-place.
     * re/im are parallel arrays of equal length (must be power of 2).
     * invert: false for forward FFT, true for inverse.
     */
    function fft(re, im, invert) {
        var n = re.length;
        if (n !== im.length) throw new Error("fft: re/im length mismatch");
        if (n < 2) return;

        for (var i = 1, j = 0; i < n; i++) {
            var bit = n >> 1;
            for (; j & bit; bit >>= 1) j ^= bit;
            j ^= bit;
            if (i < j) {
                var tmp = re[i]; re[i] = re[j]; re[j] = tmp;
                tmp = im[i]; im[i] = im[j]; im[j] = tmp;
            }
        }

        for (var len = 2; len <= n; len <<= 1) {
            var ang = 2 * Math.PI / len * (invert ? -1 : 1);
            var wlenRe = Math.cos(ang);
            var wlenIm = Math.sin(ang);
            for (var i = 0; i < n; i += len) {
                var wRe = 1, wIm = 0;
                for (var j = 0; j < len / 2; j++) {
                    var uRe = re[i + j], uIm = im[i + j];
                    var vRe = re[i + j + len / 2] * wRe - im[i + j + len / 2] * wIm;
                    var vIm = re[i + j + len / 2] * wIm + im[i + j + len / 2] * wRe;
                    re[i + j] = uRe + vRe;
                    im[i + j] = uIm + vIm;
                    re[i + j + len / 2] = uRe - vRe;
                    im[i + j + len / 2] = uIm - vIm;
                    var nextWRe = wRe * wlenRe - wIm * wlenIm;
                    wIm = wRe * wlenIm + wIm * wlenRe;
                    wRe = nextWRe;
                }
            }
        }

        if (invert) {
            for (var i = 0; i < n; i++) {
                re[i] /= n;
                im[i] /= n;
            }
        }
    }

    function nextPowerOfTwo(n) {
        var p = 1;
        while (p < n) p <<= 1;
        return p;
    }

    /**
     * Normalized cross-correlation using FFT.
     * Returns object: { peakLagSamples, peakValue, offsetSeconds, sampleRate }
     * peakLagSamples > 0 means the target is delayed relative to the reference.
     * maxLagSeconds limits the search window around zero lag.
     */
    function crossCorrelate(refSamples, targetSamples, sampleRate, maxLagSeconds) {
        if (!refSamples || !targetSamples || refSamples.length === 0 || targetSamples.length === 0) {
            throw new Error("Empty audio buffers");
        }

        sampleRate = sampleRate || 16000;
        var refLen = refSamples.length;
        var tgtLen = targetSamples.length;

        // Do not truncate; FFT size depends on both lengths.
        var ref = normalizeSignal(refSamples.slice());
        var tgt = normalizeSignal(targetSamples.slice());

        // Maximum meaningful absolute lag is bounded by the shorter clip.
        var maxLagSamples = Math.floor((maxLagSeconds || 5) * sampleRate);
        var hardMax = Math.min(refLen, tgtLen) - 1;
        if (maxLagSamples > hardMax) maxLagSamples = hardMax;
        if (maxLagSamples < 1) maxLagSamples = 1;

        var n = nextPowerOfTwo(refLen + tgtLen - 1);

        var reA = new Array(n).fill(0);
        var imA = new Array(n).fill(0);
        var reB = new Array(n).fill(0);
        var imB = new Array(n).fill(0);

        for (var i = 0; i < refLen; i++) reA[i] = ref[i];
        for (var i = 0; i < tgtLen; i++) reB[i] = tgt[i];

        fft(reA, imA, false);
        fft(reB, imB, false);

        // Cross-correlation R[m] = sum_n ref[n] * target[n + m]
        // FFT(ref) * conj(FFT(target)) would give the reverse; use conj(FFT(ref)) * FFT(target).
        var reC = new Array(n);
        var imC = new Array(n);
        for (var i = 0; i < n; i++) {
            // (ar - j ai) * (br + j bi) = (ar*br + ai*bi) + j(ar*bi - ai*br)
            reC[i] = reA[i] * reB[i] + imA[i] * imB[i];
            imC[i] = reA[i] * imB[i] - imA[i] * reB[i];
        }

        fft(reC, imC, true);

        // With the formula above, R[m] lands at output index m (circular).
        // Positive lags (target delayed) are at low indices 0..maxLagSamples.
        // Negative lags (target ahead) are at high indices n-maxLagSamples..n-1.
        var bestI = 0;
        var bestVal = -Infinity;
        for (var i = 0; i <= maxLagSamples; i++) {
            var v = reC[i];
            if (v > bestVal) {
                bestVal = v;
                bestI = i;
            }
        }
        for (var i = n - maxLagSamples; i < n; i++) {
            var v = reC[i];
            if (v > bestVal) {
                bestVal = v;
                bestI = i;
            }
        }

        var k = (bestI <= maxLagSamples) ? bestI : bestI - n;

        // Quadratic interpolation for sub-sample accuracy (neighbors wrap circularly).
        var i0 = (bestI - 1 + n) % n;
        var i2 = (bestI + 1) % n;
        var y0 = reC[i0];
        var y1 = reC[bestI];
        var y2 = reC[i2];
        var denom = y0 - 2 * y1 + y2;
        if (Math.abs(denom) > 1e-12) {
            var p = 0.5 * (y0 - y2) / denom;
            if (!isNaN(p) && isFinite(p) && Math.abs(p) < 1.0) {
                k += p;
            }
        }

        // Compute a true Pearson correlation on the overlapping window at the best lag
        // so peakValue is a normalized confidence in [-1, 1].
        var bestPearson = pearsonCorrelationAtLag(refSamples, targetSamples, bestI <= maxLagSamples ? bestI : bestI - n);
        if (bestPearson < 0) bestPearson = 0;

        return {
            peakLagSamples: k,
            peakValue: bestPearson,
            offsetSeconds: k / sampleRate,
            sampleRate: sampleRate
        };
    }

    function pearsonCorrelationAtLag(ref, target, lag) {
        var startRef = Math.max(0, -lag);
        var startTgt = Math.max(0, lag);
        var end = Math.min(ref.length, target.length - lag);
        if (end <= startRef) return 0;
        var count = end - startRef;
        if (count <= 1) return 0;

        var sumRef = 0, sumTgt = 0;
        for (var i = 0; i < count; i++) {
            sumRef += ref[startRef + i];
            sumTgt += target[startTgt + i];
        }
        var meanRef = sumRef / count;
        var meanTgt = sumTgt / count;

        var num = 0, denRef = 0, denTgt = 0;
        for (var i = 0; i < count; i++) {
            var a = ref[startRef + i] - meanRef;
            var b = target[startTgt + i] - meanTgt;
            num += a * b;
            denRef += a * a;
            denTgt += b * b;
        }
        if (denRef === 0 || denTgt === 0) return 0;
        return num / Math.sqrt(denRef * denTgt);
    }

    function normalizeSignal(samples) {
        var sum = 0, sumSq = 0;
        for (var i = 0; i < samples.length; i++) {
            sum += samples[i];
            sumSq += samples[i] * samples[i];
        }
        var mean = sum / samples.length;
        var variance = sumSq / samples.length - mean * mean;
        var std = Math.sqrt(Math.max(variance, 1e-12));
        var out = new Array(samples.length);
        for (var i = 0; i < samples.length; i++) {
            out[i] = (samples[i] - mean) / std;
        }
        return out;
    }

    /**
     * Spawn ffmpeg and return stdout, stderr, and exit code.
     */
    function spawnFfmpeg(ffmpegPath, args) {
        return new Promise(function (resolve, reject) {
            var proc;
            try {
                proc = childProcess.spawn(ffmpegPath, args, {
                    stdio: ['ignore', 'pipe', 'pipe']
                });
            } catch (e) {
                reject(new Error("ffmpeg spawn failed: " + e.message));
                return;
            }

            var chunks = [];
            var errChunks = [];

            proc.stdout.on('data', function (chunk) { chunks.push(chunk); });
            proc.stderr.on('data', function (chunk) { errChunks.push(chunk); });

            proc.on('error', function (err) {
                reject(new Error("ffmpeg spawn failed: " + err.message));
            });

            proc.on('close', function (code) {
                resolve({
                    stdout: Buffer.concat(chunks),
                    stderr: Buffer.concat(errChunks).toString('utf8'),
                    code: code
                });
            });
        });
    }

    /**
     * Extract mono PCM (float32) from media file via ffmpeg.
     * Returns Promise resolving to Float32Array samples.
     */
    function extractAudio(ffmpegPath, mediaPath, sampleRate, sampleSeconds) {
        var args = [
            '-hide_banner',
            '-loglevel', 'error',
            '-i', mediaPath,
            '-ar', String(sampleRate || 16000),
            '-ac', '1',
            '-t', String(sampleSeconds || 30),
            '-f', 'f32le',
            '-acodec', 'pcm_f32le',
            'pipe:1'
        ];
        return spawnFfmpeg(ffmpegPath, args).then(function (res) {
            if (res.code !== 0) {
                throw new Error("ffmpeg extract failed (code " + res.code + "): " + res.stderr.slice(0, 400));
            }
            if (res.stdout.length === 0) {
                throw new Error("ffmpeg returned empty audio for " + mediaPath);
            }
            var buf = res.stdout;
            var count = Math.floor(buf.length / 4);
            if (count === 0) {
                throw new Error("ffmpeg returned no complete samples for " + mediaPath);
            }
            var aligned = Buffer.alloc(count * 4);
            buf.copy(aligned);
            var samples = new Float32Array(aligned.buffer, aligned.byteOffset, count);
            return Array.prototype.slice.call(samples);
        });
    }

    /**
     * Detect peak volume using ffmpeg volumedetect.
     * Returns Promise resolving to { maxVolume, meanVolume } in dB.
     */
    function detectVolume(ffmpegPath, mediaPath, sampleSeconds) {
        var args = [
            '-hide_banner',
            '-loglevel', 'info',
            '-i', mediaPath
        ];
        if (sampleSeconds) args.push('-t', String(sampleSeconds));
        args.push('-af', 'volumedetect', '-f', 'null', '-');

        return spawnFfmpeg(ffmpegPath, args).then(function (res) {
            if (res.code !== 0 && res.code !== null) {
                throw new Error("ffmpeg volumedetect failed (code " + res.code + "): " + res.stderr.slice(0, 400));
            }
            var out = parseVolumeDetectOutput(res.stderr);
            if (out.maxVolume === -Infinity) {
                throw new Error("Could not parse volumedetect output for " + mediaPath);
            }
            return out;
        });
    }

    /**
     * Parse ffmpeg volumedetect stderr output.
     */
    function parseVolumeDetectOutput(stderrStr) {
        var maxMatch = stderrStr.match(/max_volume:\s*([-+]?\d+\.?\d*)\s*dB/);
        var meanMatch = stderrStr.match(/mean_volume:\s*([-+]?\d+\.?\d*)\s*dB/);
        return {
            maxVolume: maxMatch ? parseFloat(maxMatch[1]) : -Infinity,
            meanVolume: meanMatch ? parseFloat(meanMatch[1]) : -Infinity
        };
    }

    /**
     * Compute gain dB needed to bring peak to target dBFS.
     */
    function gainForNormalization(maxVolumeDb, targetDb) {
        if (maxVolumeDb === -Infinity || isNaN(maxVolumeDb)) return 0;
        return targetDb - maxVolumeDb;
    }

    AudioUtils.fft = fft;
    AudioUtils.crossCorrelate = crossCorrelate;
    AudioUtils.extractAudio = extractAudio;
    AudioUtils.detectVolume = detectVolume;
    AudioUtils.parseVolumeDetectOutput = parseVolumeDetectOutput;
    AudioUtils.gainForNormalization = gainForNormalization;
    AudioUtils.spawnFfmpeg = spawnFfmpeg;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = AudioUtils;
    }
})();
