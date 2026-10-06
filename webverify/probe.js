/* LUMEN probe colours in the browser: the same sum of cosines as src/lumen/probe.py.
 *
 * The server sends each sequence's frequencies, phases and scale (generated from the one-time
 * code); this file evaluates them at the moment a frame is drawn. The server later regenerates
 * the colour for every reported draw time from the code itself, so the signed record never
 * trusts what this file computed. The same file runs under Node for the tests
 * (tests/test_web.py compares it with probe.py).
 */
(function (root) {
  "use strict";

  const D65 = [0.95047, 1.0, 1.08883];
  const XYZ_TO_LINEAR_SRGB = [
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
  ];
  const DELTA = 6.0 / 29.0;

  // (a*, b*) of one sequence at probe time t: scale * sum cos(2 pi f t + phase), as probe.py.
  function chromaAt(seq, t) {
    let a = 0, b = 0;
    const f = seq.freqs, pa = seq.phase_a, pb = seq.phase_b;
    for (let i = 0; i < f.length; i++) {
      const arg = 2 * Math.PI * t * f[i];
      a += Math.cos(arg + pa[i]);
      b += Math.cos(arg + pb[i]);
    }
    return [a * seq.scale_a, b * seq.scale_b];
  }

  function labToLinearSrgb(L, a, b) {
    const fy = (L + 16.0) / 116.0;
    const f = [fy + a / 500.0, fy, fy - b / 200.0];
    const xyz = f.map((v, i) => (v > DELTA ? v * v * v : 3 * DELTA * DELTA * (v - 4.0 / 29.0)) * D65[i]);
    return XYZ_TO_LINEAR_SRGB.map(row => row[0] * xyz[0] + row[1] * xyz[1] + row[2] * xyz[2]);
  }

  function linearToSrgb(c) {
    return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(Math.max(c, 0), 1 / 2.4) - 0.055;
  }

  function labToSrgb(L, a, b) {
    return labToLinearSrgb(L, a, b).map(linearToSrgb);
  }

  // Lab of every screen area (left, right for split) at probe time t.
  function labAt(probe, t) {
    return probe.sequences.map(seq => {
      const [a, b] = chromaAt(seq, t);
      return [probe.lightness, a, b];
    });
  }

  // CSS colour strings for every area at probe time t; mid-grey (no chroma) outside the probe.
  function cssAt(probe, t) {
    const inside = t >= 0 && t <= probe.duration_s;
    const labs = inside ? labAt(probe, t) : null;
    return probe.sequences.map((seq, i) => {
      const lab = inside ? labs[i] : [probe.lightness, 0, 0];
      const rgb = labToSrgb(lab[0], lab[1], lab[2]).map(v => Math.round(Math.min(1, Math.max(0, v)) * 255));
      return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
    });
  }

  const api = { chromaAt, labAt, labToSrgb, cssAt };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LumenProbe = api;
})(typeof window !== "undefined" ? window : globalThis);
