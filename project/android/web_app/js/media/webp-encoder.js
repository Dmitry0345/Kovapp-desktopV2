(function (root) {
  const Kov = root.Kov = root.Kov || {};

  // ── Таблицы VP8 (взяты из libwebp © 2010 Google Inc., BSD 3-Clause — THIRD-PARTY-NOTICES.txt) ──
  const DC_TABLE = new Uint8Array([4,5,6,7,8,9,10,10,11,12,13,14,15,16,17,17,18,19,20,20,21,21,22,22,23,23,24,25,25,26,27,28,29,30,31,32,33,34,35,36,37,37,38,39,40,41,42,43,44,45,46,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,76,77,78,79,80,81,82,83,84,85,86,87,88,89,91,93,95,96,98,100,101,102,104,106,108,110,112,114,116,118,122,124,126,128,130,132,134,136,138,140,143,145,148,151,154,157]);
  const AC_TABLE = new Uint16Array([4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,60,62,64,66,68,70,72,74,76,78,80,82,84,86,88,90,92,94,96,98,100,102,104,106,108,110,112,114,116,119,122,125,128,131,134,137,140,143,146,149,152,155,158,161,164,167,170,173,177,181,185,189,193,197,201,205,209,213,217,221,225,229,234,239,245,249,254,259,264,269,274,279,284]);
  const COEF_PROBS = new Uint8Array([128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,253,136,254,255,228,219,128,128,128,128,128,189,129,242,255,227,213,255,219,128,128,128,106,126,227,252,214,209,255,255,128,128,128,1,98,248,255,236,226,255,255,128,128,128,181,133,238,254,221,234,255,154,128,128,128,78,134,202,247,198,180,255,219,128,128,128,1,185,249,255,243,255,128,128,128,128,128,184,150,247,255,236,224,128,128,128,128,128,77,110,216,255,236,230,128,128,128,128,128,1,101,251,255,241,255,128,128,128,128,128,170,139,241,252,236,209,255,255,128,128,128,37,116,196,243,228,255,255,255,128,128,128,1,204,254,255,245,255,128,128,128,128,128,207,160,250,255,238,128,128,128,128,128,128,102,103,231,255,211,171,128,128,128,128,128,1,152,252,255,240,255,128,128,128,128,128,177,135,243,255,234,225,128,128,128,128,128,80,129,211,255,194,224,128,128,128,128,128,1,1,255,128,128,128,128,128,128,128,128,246,1,255,128,128,128,128,128,128,128,128,255,128,128,128,128,128,128,128,128,128,128,198,35,237,223,193,187,162,160,145,155,62,131,45,198,221,172,176,220,157,252,221,1,68,47,146,208,149,167,221,162,255,223,128,1,149,241,255,221,224,255,255,128,128,128,184,141,234,253,222,220,255,199,128,128,128,81,99,181,242,176,190,249,202,255,255,128,1,129,232,253,214,197,242,196,255,255,128,99,121,210,250,201,198,255,202,128,128,128,23,91,163,242,170,187,247,210,255,255,128,1,200,246,255,234,255,128,128,128,128,128,109,178,241,255,231,245,255,255,128,128,128,44,130,201,253,205,192,255,255,128,128,128,1,132,239,251,219,209,255,165,128,128,128,94,136,225,251,218,190,255,255,128,128,128,22,100,174,245,186,161,255,199,128,128,128,1,182,249,255,232,235,128,128,128,128,128,124,143,241,255,227,234,128,128,128,128,128,35,77,181,251,193,211,255,205,128,128,128,1,157,247,255,236,231,255,255,128,128,128,121,141,235,255,225,227,255,255,128,128,128,45,99,188,251,195,217,255,224,128,128,128,1,1,251,255,213,255,128,128,128,128,128,203,1,248,255,255,128,128,128,128,128,128,137,1,177,255,224,255,128,128,128,128,128,253,9,248,251,207,208,255,192,128,128,128,175,13,224,243,193,185,249,198,255,255,128,73,17,171,221,161,179,236,167,255,234,128,1,95,247,253,212,183,255,255,128,128,128,239,90,244,250,211,209,255,255,128,128,128,155,77,195,248,188,195,255,255,128,128,128,1,24,239,251,218,219,255,205,128,128,128,201,51,219,255,196,186,128,128,128,128,128,69,46,190,239,201,218,255,228,128,128,128,1,191,251,255,255,128,128,128,128,128,128,223,165,249,255,213,255,128,128,128,128,128,141,124,248,255,255,128,128,128,128,128,128,1,16,248,255,255,128,128,128,128,128,128,190,36,230,255,236,255,128,128,128,128,128,149,1,255,128,128,128,128,128,128,128,128,1,226,255,128,128,128,128,128,128,128,128,247,192,255,128,128,128,128,128,128,128,128,240,128,255,128,128,128,128,128,128,128,128,1,134,252,255,255,128,128,128,128,128,128,213,62,250,255,255,128,128,128,128,128,128,55,93,255,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,128,202,24,213,235,186,191,220,160,240,175,255,126,38,182,232,169,184,228,174,255,187,128,61,46,138,219,151,178,240,170,255,216,128,1,112,230,250,199,191,247,159,255,255,128,166,109,228,252,211,215,255,174,128,128,128,39,77,162,232,172,180,245,178,255,255,128,1,52,220,246,198,199,249,220,255,255,128,124,74,191,243,183,193,250,221,255,255,128,24,71,130,219,154,170,243,182,255,255,128,1,182,225,249,219,240,255,224,128,128,128,149,150,226,252,216,205,255,171,128,128,128,28,108,170,242,183,194,254,223,255,255,128,1,81,230,252,204,203,255,192,128,128,128,123,102,209,247,188,196,255,233,128,128,128,20,95,153,243,164,173,255,203,128,128,128,1,222,248,255,216,213,128,128,128,128,128,168,175,246,252,235,205,255,255,128,128,128,47,116,215,255,211,212,255,255,128,128,128,1,121,236,253,212,214,255,255,128,128,128,141,84,213,252,201,202,255,219,128,128,128,42,80,160,240,162,185,255,205,128,128,128,1,1,255,128,128,128,128,128,128,128,128,244,1,255,128,128,128,128,128,128,128,128,238,1,255,128,128,128,128,128,128,128,128]);
  const COEF_UPDATE = new Uint8Array([255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,176,246,255,255,255,255,255,255,255,255,255,223,241,252,255,255,255,255,255,255,255,255,249,253,253,255,255,255,255,255,255,255,255,255,244,252,255,255,255,255,255,255,255,255,234,254,254,255,255,255,255,255,255,255,255,253,255,255,255,255,255,255,255,255,255,255,255,246,254,255,255,255,255,255,255,255,255,239,253,254,255,255,255,255,255,255,255,255,254,255,254,255,255,255,255,255,255,255,255,255,248,254,255,255,255,255,255,255,255,255,251,255,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,253,254,255,255,255,255,255,255,255,255,251,254,254,255,255,255,255,255,255,255,255,254,255,254,255,255,255,255,255,255,255,255,255,254,253,255,254,255,255,255,255,255,255,250,255,254,255,254,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,217,255,255,255,255,255,255,255,255,255,255,225,252,241,253,255,255,254,255,255,255,255,234,250,241,250,253,255,253,254,255,255,255,255,254,255,255,255,255,255,255,255,255,255,223,254,254,255,255,255,255,255,255,255,255,238,253,254,254,255,255,255,255,255,255,255,255,248,254,255,255,255,255,255,255,255,255,249,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,253,255,255,255,255,255,255,255,255,255,247,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,253,254,255,255,255,255,255,255,255,255,252,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,254,254,255,255,255,255,255,255,255,255,253,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,254,253,255,255,255,255,255,255,255,255,250,255,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,186,251,250,255,255,255,255,255,255,255,255,234,251,244,254,255,255,255,255,255,255,255,251,251,243,253,254,255,254,255,255,255,255,255,253,254,255,255,255,255,255,255,255,255,236,253,254,255,255,255,255,255,255,255,255,251,253,253,254,254,255,255,255,255,255,255,255,254,254,255,255,255,255,255,255,255,255,254,254,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,254,254,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,248,255,255,255,255,255,255,255,255,255,255,250,254,252,254,255,255,255,255,255,255,255,248,254,249,253,255,255,255,255,255,255,255,255,253,253,255,255,255,255,255,255,255,255,246,253,253,255,255,255,255,255,255,255,255,252,254,251,254,254,255,255,255,255,255,255,255,254,252,255,255,255,255,255,255,255,255,248,254,253,255,255,255,255,255,255,255,255,253,255,254,254,255,255,255,255,255,255,255,255,251,254,255,255,255,255,255,255,255,255,245,251,254,255,255,255,255,255,255,255,255,253,253,254,255,255,255,255,255,255,255,255,255,251,253,255,255,255,255,255,255,255,255,252,253,254,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,252,255,255,255,255,255,255,255,255,255,249,255,254,255,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,253,255,255,255,255,255,255,255,255,250,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,254,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]);
  const BMODE_PROBS = new Uint8Array([231,120,48,89,115,113,120,152,112,152,179,64,126,170,118,46,70,95,175,69,143,80,85,82,72,155,103,56,58,10,171,218,189,17,13,152,114,26,17,163,44,195,21,10,173,121,24,80,195,26,62,44,64,85,144,71,10,38,171,213,144,34,26,170,46,55,19,136,160,33,206,71,63,20,8,114,114,208,12,9,226,81,40,11,96,182,84,29,16,36,134,183,89,137,98,101,106,165,148,72,187,100,130,157,111,32,75,80,66,102,167,99,74,62,40,234,128,41,53,9,178,241,141,26,8,107,74,43,26,146,73,166,49,23,157,65,38,105,160,51,52,31,115,128,104,79,12,27,217,255,87,17,7,87,68,71,44,114,51,15,186,23,47,41,14,110,182,183,21,17,194,66,45,25,102,197,189,23,18,22,88,88,147,150,42,46,45,196,205,43,97,183,117,85,38,35,179,61,39,53,200,87,26,21,43,232,171,56,34,51,104,114,102,29,93,77,39,28,85,171,58,165,90,98,64,34,22,116,206,23,34,43,166,73,107,54,32,26,51,1,81,43,31,68,25,106,22,64,171,36,225,114,34,19,21,102,132,188,16,76,124,62,18,78,95,85,57,50,48,51,193,101,35,159,215,111,89,46,111,60,148,31,172,219,228,21,18,111,112,113,77,85,179,255,38,120,114,40,42,1,196,245,209,10,25,109,88,43,29,140,166,213,37,43,154,61,63,30,155,67,45,68,1,209,100,80,8,43,154,1,51,26,71,142,78,78,16,255,128,34,197,171,41,40,5,102,211,183,4,1,221,51,50,17,168,209,192,23,25,82,138,31,36,171,27,166,38,44,229,67,87,58,169,82,115,26,59,179,63,59,90,180,59,166,93,73,154,40,40,21,116,143,209,34,39,175,47,15,16,183,34,223,49,45,183,46,17,33,183,6,98,15,32,183,57,46,22,24,128,1,54,17,37,65,32,73,115,28,128,23,128,205,40,3,9,115,51,192,18,6,223,87,37,9,115,59,77,64,21,47,104,55,44,218,9,54,53,130,226,64,90,70,205,40,41,23,26,57,54,57,112,184,5,41,38,166,213,30,34,26,133,152,116,10,32,134,39,19,53,221,26,114,32,73,255,31,9,65,234,2,15,1,118,73,75,32,12,51,192,255,160,43,51,88,31,35,67,102,85,55,186,85,56,21,23,111,59,205,45,37,192,55,38,70,124,73,102,1,34,98,125,98,42,88,104,85,117,175,82,95,84,53,89,128,100,113,101,45,75,79,123,47,51,128,81,171,1,57,17,5,71,102,57,53,41,49,38,33,13,121,57,73,26,1,85,41,10,67,138,77,110,90,47,114,115,21,2,10,102,255,166,23,6,101,29,16,10,85,128,101,196,26,57,18,10,102,102,213,34,20,43,117,20,15,36,163,128,68,1,26,102,61,71,37,34,53,31,243,192,69,60,71,38,73,119,28,222,37,68,45,128,34,1,47,11,245,171,62,17,19,70,146,85,55,62,70,37,43,37,154,100,163,85,160,1,63,9,92,136,28,64,32,201,85,75,15,9,9,64,255,184,119,16,86,6,28,5,64,255,25,248,1,56,8,17,132,137,255,55,116,128,58,15,20,82,135,57,26,121,40,164,50,31,137,154,133,25,35,218,51,103,44,131,131,123,31,6,158,86,40,64,135,148,224,45,183,128,22,26,17,131,240,154,14,1,209,45,16,21,91,64,222,7,1,197,56,21,39,155,60,138,23,102,213,83,12,13,54,192,255,68,47,28,85,26,85,85,128,128,32,146,171,18,11,7,63,144,171,4,4,246,35,27,10,146,174,171,12,26,128,190,80,35,99,180,80,126,54,45,85,126,47,87,176,51,41,20,32,101,75,128,139,118,146,116,128,85,56,41,15,176,236,85,37,9,62,71,30,17,119,118,255,17,18,138,101,38,60,138,55,70,43,26,142,146,36,19,30,171,255,97,27,20,138,45,61,62,219,1,81,188,64,32,41,20,117,151,142,20,21,163,112,19,12,61,195,128,48,4,24]);
  const ZIGZAG = [0, 1, 4, 8, 5, 2, 3, 6, 9, 12, 13, 10, 7, 11, 14, 15];
  const BANDS = [0, 1, 2, 3, 6, 4, 5, 6, 6, 6, 6, 6, 6, 6, 6, 7, 0];
  const CAT3 = [173, 148, 140], CAT4 = [176, 155, 140, 135], CAT5 = [180, 157, 141, 134, 130];
  const CAT6 = [254, 254, 243, 230, 196, 177, 153, 140, 133, 130, 129];
  const B_DC = 0, B_TM = 1, B_VE = 2, B_HE = 3, B_RD = 4, B_VR = 5, B_LD = 6, B_VL = 7, B_HD = 8, B_HU = 9;
  const I16_TO_B = [B_DC, B_VE, B_HE, B_TM];
  const BMODE_PATH = [[[0, 0]], [[0, 1], [1, 0]], [[0, 1], [1, 1], [2, 0]],
    [[0, 1], [1, 1], [2, 1], [3, 0], [4, 0]], [[0, 1], [1, 1], [2, 1], [3, 0], [4, 1], [5, 0]],
    [[0, 1], [1, 1], [2, 1], [3, 0], [4, 1], [5, 1]], [[0, 1], [1, 1], [2, 1], [3, 1], [6, 0]],
    [[0, 1], [1, 1], [2, 1], [3, 1], [6, 1], [7, 0]], [[0, 1], [1, 1], [2, 1], [3, 1], [6, 1], [7, 1], [8, 0]],
    [[0, 1], [1, 1], [2, 1], [3, 1], [6, 1], [7, 1], [8, 1]]];
  const Y16_PATH = [[[145, 1], [156, 0], [163, 0]], [[145, 1], [156, 0], [163, 1]], [[145, 1], [156, 1], [128, 0]], [[145, 1], [156, 1], [128, 1]]];
  const UV_PATH = [[[142, 0]], [[142, 1], [114, 0]], [[142, 1], [114, 1], [183, 0]], [[142, 1], [114, 1], [183, 1]]];
  const BITCOST = new Float64Array(256);
  for (let p = 1; p < 256; p++) BITCOST[p] = -Math.log2(p / 256);
  const cost = (bit, p) => bit ? BITCOST[256 - p] || 8 : BITCOST[p];

  // ── Двоичный арифметический кодер VP8 ──
  class BoolWriter {
    constructor(size) { this.buf = new Uint8Array(size || 1 << 16); this.pos = 0; this.range = 255; this.low = 0; this.count = 24; }
    emit(b) {
      if (this.pos >= this.buf.length) { const n = new Uint8Array(this.buf.length * 2); n.set(this.buf); this.buf = n; }
      this.buf[this.pos++] = b;
    }
    carry() { let i = this.pos - 1; while (i >= 0 && this.buf[i] === 255) { this.buf[i] = 0; i--; } if (i >= 0) this.buf[i]++; }
    put(bit, prob) {
      const split = 1 + (((this.range - 1) * prob) >> 8);
      if (bit) { this.low += split; this.range -= split; } else this.range = split;
      while (this.range < 128) {
        this.range <<= 1;
        this.low *= 2;
        if (--this.count === 0) {
          let byte = Math.floor(this.low / 16777216);
          if (byte > 255) { this.carry(); byte -= 256; }
          this.emit(byte);
          this.low %= 16777216;
          this.count = 8;
        }
      }
    }
    literal(v, n) { for (let i = n - 1; i >= 0; i--) this.put((v >> i) & 1, 128); }
    finish() {
      this.low *= Math.pow(2, this.count);
      let byte = Math.floor(this.low / 16777216);
      if (byte > 255) { this.carry(); byte -= 256; }
      this.emit(byte);
      const rest = this.low % 16777216;
      this.emit((rest >> 16) & 255); this.emit((rest >> 8) & 255); this.emit(rest & 255);
      return this.buf.subarray(0, this.pos);
    }
  }

  // ── Преобразования ──
  const clip = v => v < 0 ? 0 : v > 255 ? 255 : v;
  function fdct(src, so, ss, pred, po, ps, out) {
    const t = fdct.tmp || (fdct.tmp = new Int32Array(16));
    for (let i = 0; i < 4; i++) {
      const s = so + i * ss, p = po + i * ps;
      const d0 = src[s] - pred[p], d1 = src[s + 1] - pred[p + 1], d2 = src[s + 2] - pred[p + 2], d3 = src[s + 3] - pred[p + 3];
      const a0 = d0 + d3, a1 = d1 + d2, a2 = d1 - d2, a3 = d0 - d3;
      t[i * 4] = (a0 + a1) * 8;
      t[1 + i * 4] = (a2 * 2217 + a3 * 5352 + 1812) >> 9;
      t[2 + i * 4] = (a0 - a1) * 8;
      t[3 + i * 4] = (a3 * 2217 - a2 * 5352 + 937) >> 9;
    }
    for (let i = 0; i < 4; i++) {
      const a0 = t[i] + t[12 + i], a1 = t[4 + i] + t[8 + i], a2 = t[4 + i] - t[8 + i], a3 = t[i] - t[12 + i];
      out[i] = (a0 + a1 + 7) >> 4;
      out[4 + i] = ((a2 * 2217 + a3 * 5352 + 12000) >> 16) + (a3 !== 0 ? 1 : 0);
      out[8 + i] = (a0 - a1 + 7) >> 4;
      out[12 + i] = (a3 * 2217 - a2 * 5352 + 51000) >> 16;
    }
  }
  const MUL1 = a => ((a * 20091) >> 16) + a;
  const MUL2 = a => (a * 35468) >> 16;
  function idctAdd(inp, dst, d, ds) {
    const c = idctAdd.tmp || (idctAdd.tmp = new Int32Array(16));
    for (let i = 0; i < 4; i++) {
      const a = inp[i] + inp[8 + i], b = inp[i] - inp[8 + i];
      const cc = MUL2(inp[4 + i]) - MUL1(inp[12 + i]), dd = MUL1(inp[4 + i]) + MUL2(inp[12 + i]);
      c[i * 4] = a + dd; c[i * 4 + 1] = b + cc; c[i * 4 + 2] = b - cc; c[i * 4 + 3] = a - dd;
    }
    for (let i = 0; i < 4; i++) {
      const dc = c[i] + 4;
      const a = dc + c[8 + i], b = dc - c[8 + i];
      const cc = MUL2(c[4 + i]) - MUL1(c[12 + i]), dd = MUL1(c[4 + i]) + MUL2(c[12 + i]);
      const o = d + i * ds;
      dst[o] = clip(dst[o] + ((a + dd) >> 3));
      dst[o + 1] = clip(dst[o + 1] + ((b + cc) >> 3));
      dst[o + 2] = clip(dst[o + 2] + ((b - cc) >> 3));
      dst[o + 3] = clip(dst[o + 3] + ((a - dd) >> 3));
    }
  }
  function fwht(dcs, out) {
    const t = new Int32Array(16);
    for (let i = 0; i < 4; i++) {
      const a0 = dcs[i * 4] + dcs[i * 4 + 2], a1 = dcs[i * 4 + 1] + dcs[i * 4 + 3];
      const a2 = dcs[i * 4 + 1] - dcs[i * 4 + 3], a3 = dcs[i * 4] - dcs[i * 4 + 2];
      t[i * 4] = a0 + a1; t[i * 4 + 1] = a3 + a2; t[i * 4 + 2] = a3 - a2; t[i * 4 + 3] = a0 - a1;
    }
    for (let i = 0; i < 4; i++) {
      const a0 = t[i] + t[8 + i], a1 = t[4 + i] + t[12 + i], a2 = t[4 + i] - t[12 + i], a3 = t[i] - t[8 + i];
      out[i] = (a0 + a1) >> 1; out[4 + i] = (a3 + a2) >> 1; out[8 + i] = (a3 - a2) >> 1; out[12 + i] = (a0 - a1) >> 1;
    }
  }
  function iwht(inp, out) {
    const t = new Int32Array(16);
    for (let i = 0; i < 4; i++) {
      const a0 = inp[i] + inp[12 + i], a1 = inp[4 + i] + inp[8 + i], a2 = inp[4 + i] - inp[8 + i], a3 = inp[i] - inp[12 + i];
      t[i] = a0 + a1; t[8 + i] = a0 - a1; t[4 + i] = a3 + a2; t[12 + i] = a3 - a2;
    }
    for (let i = 0; i < 4; i++) {
      const dc = t[i * 4] + 3;
      const a0 = dc + t[3 + i * 4], a1 = t[1 + i * 4] + t[2 + i * 4], a2 = t[1 + i * 4] - t[2 + i * 4], a3 = dc - t[3 + i * 4];
      out[i * 4] = (a0 + a1) >> 3; out[i * 4 + 1] = (a3 + a2) >> 3; out[i * 4 + 2] = (a0 - a1) >> 3; out[i * 4 + 3] = (a3 - a2) >> 3;
    }
  }
  function satd(src, so, ss, pred, po, ps) {
    let sum = 0;
    const d = satd.d || (satd.d = new Int32Array(16));
    for (let i = 0; i < 4; i++) {
      const s = so + i * ss, p = po + i * ps;
      const d0 = src[s] - pred[p], d1 = src[s + 1] - pred[p + 1], d2 = src[s + 2] - pred[p + 2], d3 = src[s + 3] - pred[p + 3];
      const a0 = d0 + d2, a1 = d1 + d3, a2 = d1 - d3, a3 = d0 - d2;
      d[i * 4] = a0 + a1; d[i * 4 + 1] = a3 + a2; d[i * 4 + 2] = a3 - a2; d[i * 4 + 3] = a0 - a1;
    }
    for (let i = 0; i < 4; i++) {
      const a0 = d[i] + d[8 + i], a1 = d[4 + i] + d[12 + i], a2 = d[4 + i] - d[12 + i], a3 = d[i] - d[8 + i];
      sum += Math.abs(a0 + a1) + Math.abs(a3 + a2) + Math.abs(a3 - a2) + Math.abs(a0 - a1);
    }
    return sum >> 1;
  }

  // ── Предсказания: рабочий буфер как в декодере ──
  const WS = 21;
  function predict16(W, mode, mbx, mby, out) {
    for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
      let v;
      if (mode === 0) v = 0;
      else if (mode === 1) v = W[x + 1];
      else if (mode === 2) v = W[(y + 1) * WS];
      else v = clip(W[x + 1] + W[(y + 1) * WS] - W[0]);
      out[y * 16 + x] = v;
    }
    if (mode === 0) {
      let dc;
      if (mbx > 0 && mby > 0) { let s = 16; for (let j = 0; j < 16; j++) s += W[j + 1] + W[(j + 1) * WS]; dc = s >> 5; }
      else if (mby > 0) { let s = 8; for (let j = 0; j < 16; j++) s += W[j + 1]; dc = s >> 4; }
      else if (mbx > 0) { let s = 8; for (let j = 0; j < 16; j++) s += W[(j + 1) * WS]; dc = s >> 4; }
      else dc = 128;
      out.fill(dc);
    }
  }
  function predict8(C, mode, mbx, mby, out) {
    const S = 9;
    if (mode === 0) {
      let dc;
      if (mbx > 0 && mby > 0) { let s = 8; for (let j = 0; j < 8; j++) s += C[j + 1] + C[(j + 1) * S]; dc = s >> 4; }
      else if (mby > 0) { let s = 4; for (let j = 0; j < 8; j++) s += C[j + 1]; dc = s >> 3; }
      else if (mbx > 0) { let s = 4; for (let j = 0; j < 8; j++) s += C[(j + 1) * S]; dc = s >> 3; }
      else dc = 128;
      out.fill(dc, 0, 64);
      return;
    }
    for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
      out[y * 8 + x] = mode === 1 ? C[x + 1] : mode === 2 ? C[(y + 1) * S] : clip(C[x + 1] + C[(y + 1) * S] - C[0]);
    }
  }
  function predict4(W, o, mode, out) {
    const T = o - WS;
    const A = W[T], B = W[T + 1], Cc = W[T + 2], D = W[T + 3], E = W[T + 4], F = W[T + 5], G = W[T + 6], H = W[T + 7];
    const X = W[T - 1], I = W[o - 1], J = W[o - 1 + WS], K = W[o - 1 + 2 * WS], L = W[o - 1 + 3 * WS];
    const a3 = (a, b, c) => (a + 2 * b + c + 2) >> 2, a2 = (a, b) => (a + b + 1) >> 1;
    const set = (x, y, v) => { out[y * 4 + x] = v; };
    switch (mode) {
      case B_DC: { const dc = (A + B + Cc + D + I + J + K + L + 4) >> 3; out.fill(dc, 0, 16); break; }
      case B_TM: { const top = [A, B, Cc, D], left = [I, J, K, L]; for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) set(x, y, clip(top[x] + left[y] - X)); break; }
      case B_VE: { const v = [a3(X, A, B), a3(A, B, Cc), a3(B, Cc, D), a3(Cc, D, E)]; for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) set(x, y, v[x]); break; }
      case B_HE: { const v = [a3(X, I, J), a3(I, J, K), a3(J, K, L), a3(K, L, L)]; for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) set(x, y, v[y]); break; }
      case B_RD:
        set(0, 3, a3(J, K, L)); set(1, 3, a3(I, J, K)); set(0, 2, a3(I, J, K));
        set(2, 3, a3(X, I, J)); set(1, 2, a3(X, I, J)); set(0, 1, a3(X, I, J));
        set(3, 3, a3(A, X, I)); set(2, 2, a3(A, X, I)); set(1, 1, a3(A, X, I)); set(0, 0, a3(A, X, I));
        set(3, 2, a3(B, A, X)); set(2, 1, a3(B, A, X)); set(1, 0, a3(B, A, X));
        set(3, 1, a3(Cc, B, A)); set(2, 0, a3(Cc, B, A)); set(3, 0, a3(D, Cc, B)); break;
      case B_LD:
        set(0, 0, a3(A, B, Cc)); set(1, 0, a3(B, Cc, D)); set(0, 1, a3(B, Cc, D));
        set(2, 0, a3(Cc, D, E)); set(1, 1, a3(Cc, D, E)); set(0, 2, a3(Cc, D, E));
        set(3, 0, a3(D, E, F)); set(2, 1, a3(D, E, F)); set(1, 2, a3(D, E, F)); set(0, 3, a3(D, E, F));
        set(3, 1, a3(E, F, G)); set(2, 2, a3(E, F, G)); set(1, 3, a3(E, F, G));
        set(3, 2, a3(F, G, H)); set(2, 3, a3(F, G, H)); set(3, 3, a3(G, H, H)); break;
      case B_VR:
        set(0, 0, a2(X, A)); set(1, 2, a2(X, A)); set(1, 0, a2(A, B)); set(2, 2, a2(A, B));
        set(2, 0, a2(B, Cc)); set(3, 2, a2(B, Cc)); set(3, 0, a2(Cc, D));
        set(0, 3, a3(K, J, I)); set(0, 2, a3(J, I, X)); set(0, 1, a3(I, X, A)); set(1, 3, a3(I, X, A));
        set(1, 1, a3(X, A, B)); set(2, 3, a3(X, A, B)); set(2, 1, a3(A, B, Cc)); set(3, 3, a3(A, B, Cc)); set(3, 1, a3(B, Cc, D)); break;
      case B_VL:
        set(0, 0, a2(A, B)); set(1, 0, a2(B, Cc)); set(0, 2, a2(B, Cc)); set(2, 0, a2(Cc, D)); set(1, 2, a2(Cc, D));
        set(3, 0, a2(D, E)); set(2, 2, a2(D, E));
        set(0, 1, a3(A, B, Cc)); set(1, 1, a3(B, Cc, D)); set(0, 3, a3(B, Cc, D)); set(2, 1, a3(Cc, D, E)); set(1, 3, a3(Cc, D, E));
        set(3, 1, a3(D, E, F)); set(2, 3, a3(D, E, F)); set(3, 2, a3(E, F, G)); set(3, 3, a3(F, G, H)); break;
      case B_HD:
        set(0, 0, a2(I, X)); set(2, 1, a2(I, X)); set(0, 1, a2(J, I)); set(2, 2, a2(J, I));
        set(0, 2, a2(K, J)); set(2, 3, a2(K, J)); set(0, 3, a2(L, K));
        set(3, 0, a3(A, B, Cc)); set(2, 0, a3(X, A, B)); set(1, 0, a3(I, X, A)); set(3, 1, a3(I, X, A));
        set(1, 1, a3(J, I, X)); set(3, 2, a3(J, I, X)); set(1, 2, a3(K, J, I)); set(3, 3, a3(K, J, I)); set(1, 3, a3(L, K, J)); break;
      case B_HU:
        set(0, 0, a2(I, J)); set(2, 0, a2(J, K)); set(0, 1, a2(J, K)); set(2, 1, a2(K, L)); set(0, 2, a2(K, L));
        set(1, 0, a3(I, J, K)); set(3, 0, a3(J, K, L)); set(1, 1, a3(J, K, L)); set(3, 1, a3(K, L, L)); set(1, 2, a3(K, L, L));
        set(3, 2, L); set(2, 2, L); set(0, 3, L); set(1, 3, L); set(2, 3, L); set(3, 3, L); break;
    }
  }

  // ── Квантование ──
  function quantBlock(coef, first, qdc, qac, bdc, bac, levels, deq) {
    let last = -1;
    for (let n = first; n < 16; n++) {
      const j = ZIGZAG[n], q = n > 0 ? qac : qdc, b = n > 0 ? bac : bdc;
      const c = coef[j];
      let lv = Math.floor(Math.abs(c) / q + b);
      if (lv > 2047) lv = 2047;
      if (c < 0) lv = -lv;
      levels[n] = lv;
      deq[j] = lv * q;
      if (lv) last = n;
    }
    return last;
  }
  function levelBits(levels, first, last) {
    let bits = 1;
    for (let n = first; n <= last; n++) { const a = Math.abs(levels[n]); bits += a === 0 ? 1 : a === 1 ? 3 : a < 5 ? 5 : a < 11 ? 7 : 9 + Math.log2(a); }
    return bits;
  }

  // ── Кодирование одного кадра VP8 ──
  function encodeFrame(Yp, Up, Vp, width, height, opts) {
    const mbw = (width + 15) >> 4, mbh = (height + 15) >> 4;
    const ys = mbw * 16, cs = mbw * 8;
    const q = Math.max(0, Math.min(127, opts.q | 0));
    const y1dc = DC_TABLE[q], y1ac = AC_TABLE[q];
    const y2dc = DC_TABLE[q] * 2, y2ac = Math.max(8, (AC_TABLE[q] * 101581) >> 16);
    const uvdc = DC_TABLE[Math.min(q, 117)], uvac = AC_TABLE[q];
    const lambda = (opts.lambda || 0.6) * y1ac;
    const RY = new Uint8Array(Yp.length), RU = new Uint8Array(Up.length), RV = new Uint8Array(Vp.length);
    const nMB = mbw * mbh;
    const mbI4 = new Uint8Array(nMB), mbY16 = new Uint8Array(nMB), mbUV = new Uint8Array(nMB), mbSkip = new Uint8Array(nMB);
    const mbB = new Uint8Array(nMB * 16);
    const levelsAll = new Int16Array(nMB * 25 * 16);
    const lastAll = new Int8Array(nMB * 25);
    const topModes = new Uint8Array(mbw * 4);
    const leftModes = new Uint8Array(4);
    const W = new Uint8Array(17 * WS), W2 = new Uint8Array(17 * WS);
    const CU = new Uint8Array(81), CV = new Uint8Array(81);
    const pred16 = new Uint8Array(256), best16 = new Uint8Array(256), p4 = new Uint8Array(16), b4 = new Uint8Array(16);
    const pu = new Uint8Array(64), pv = new Uint8Array(64);
    const coef = new Int32Array(16), deq = new Int32Array(16), lv = new Int16Array(16);
    const src = new Uint8Array(17 * WS), su = new Uint8Array(81), sv = new Uint8Array(81);
    const coefs16 = new Int32Array(256), dcs = new Int32Array(16), y2 = new Int32Array(16), y2deq = new Int32Array(16), dcRec = new Int32Array(16);
    const i4levels = new Int16Array(256), i4last = new Int8Array(16), i4modes = new Uint8Array(16);

    function loadBorders(mbx, mby) {
      W.fill(0);
      for (let x = 0; x < 20; x++) {
        let v;
        if (mby === 0) v = 127;
        else if (x < 16) v = RY[(mby * 16 - 1) * ys + mbx * 16 + x];
        else if (mbx === mbw - 1) v = RY[(mby * 16 - 1) * ys + mbx * 16 + 15];
        else v = RY[(mby * 16 - 1) * ys + mbx * 16 + x];
        W[1 + x] = v;
      }
      W[0] = mby === 0 ? 127 : mbx === 0 ? 129 : RY[(mby * 16 - 1) * ys + mbx * 16 - 1];
      for (let y = 0; y < 16; y++) W[(y + 1) * WS] = mbx === 0 ? 129 : RY[(mby * 16 + y) * ys + mbx * 16 - 1];
      for (const [R, C] of [[RU, CU], [RV, CV]]) {
        for (let x = 0; x < 8; x++) C[1 + x] = mby === 0 ? 127 : R[(mby * 8 - 1) * cs + mbx * 8 + x];
        C[0] = mby === 0 ? 127 : mbx === 0 ? 129 : R[(mby * 8 - 1) * cs + mbx * 8 - 1];
        for (let y = 0; y < 8; y++) C[(y + 1) * 9] = mbx === 0 ? 129 : R[(mby * 8 + y) * cs + mbx * 8 - 1];
      }
      for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) src[(y + 1) * WS + x + 1] = Yp[(mby * 16 + y) * ys + mbx * 16 + x];
      for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) { su[(y + 1) * 9 + x + 1] = Up[(mby * 8 + y) * cs + mbx * 8 + x]; sv[(y + 1) * 9 + x + 1] = Vp[(mby * 8 + y) * cs + mbx * 8 + x]; }
    }

    for (let mby = 0; mby < mbh; mby++) {
      leftModes.fill(B_DC);
      for (let mbx = 0; mbx < mbw; mbx++) {
        const mb = mby * mbw + mbx;
        loadBorders(mbx, mby);
        let best16Mode = 0, best16Cost = Infinity;
        for (let m = 0; m < 4; m++) {
          predict16(W, m, mbx, mby, pred16);
          let s = 0;
          for (let b = 0; b < 16; b++) { const r = b >> 2, c = b & 3; s += satd(src, (r * 4 + 1) * WS + c * 4 + 1, WS, pred16, r * 64 + c * 4, 16); }
          let bits = 0; for (const [p, bit] of Y16_PATH[m]) bits += cost(bit, p);
          const j = s + lambda * bits;
          if (j < best16Cost) { best16Cost = j; best16Mode = m; }
        }
        W2.set(W);
        let i4Cost = lambda * cost(0, 145);
        for (let b = 0; b < 16 && i4Cost < best16Cost; b++) {
          const r = b >> 2, c = b & 3, o = (r * 4 + 1) * WS + c * 4 + 1;
          if (c === 3 && r > 0) for (let k = 0; k < 4; k++) W2[(r * 4) * WS + 17 + k] = W2[17 + k];
          const A = r === 0 ? topModes[mbx * 4 + c] : i4modes[b - 4];
          const L = c === 0 ? leftModes[r] : i4modes[b - 1];
          let bm = 0, bj = Infinity;
          for (let m = 0; m < 10; m++) {
            predict4(W2, o, m, p4);
            let bits = 0; const pr = (A * 10 + L) * 9;
            for (const [node, bit] of BMODE_PATH[m]) bits += cost(bit, BMODE_PROBS[pr + node]);
            const j = satd(src, o, WS, p4, 0, 4) + lambda * bits;
            if (j < bj) { bj = j; bm = m; b4.set(p4); }
          }
          i4modes[b] = bm; i4Cost += bj;
          fdct(src, o, WS, b4, 0, 4, coef);
          deq.fill(0);
          i4last[b] = quantBlock(coef, 0, y1dc, y1ac, 0.45, 0.38, lv, deq);
          i4levels.set(lv, b * 16);
          for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) W2[o + y * WS + x] = b4[y * 4 + x];
          idctAdd(deq, W2, o, WS);
        }
        const useI4 = i4Cost < best16Cost;
        const L0 = mb * 25;
        if (useI4) {
          mbI4[mb] = 1;
          for (let b = 0; b < 16; b++) { mbB[mb * 16 + b] = i4modes[b]; levelsAll.set(i4levels.subarray(b * 16, b * 16 + 16), (L0 + b) * 16); lastAll[L0 + b] = i4last[b]; }
          for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) RY[(mby * 16 + y) * ys + mbx * 16 + x] = W2[(y + 1) * WS + x + 1];
          for (let c = 0; c < 4; c++) topModes[mbx * 4 + c] = i4modes[12 + c];
          for (let r = 0; r < 4; r++) leftModes[r] = i4modes[r * 4 + 3];
        } else {
          mbI4[mb] = 0; mbY16[mb] = best16Mode;
          predict16(W, best16Mode, mbx, mby, pred16);
          for (let b = 0; b < 16; b++) {
            const r = b >> 2, c = b & 3;
            fdct(src, (r * 4 + 1) * WS + c * 4 + 1, WS, pred16, r * 64 + c * 4, 16, coef);
            coefs16.set(coef, b * 16); dcs[b] = coef[0];
          }
          fwht(dcs, y2);
          y2deq.fill(0);
          lastAll[L0 + 24] = quantBlock(y2, 0, y2dc, y2ac, 0.45, 0.42, lv, y2deq);
          levelsAll.set(lv, (L0 + 24) * 16);
          iwht(y2deq, dcRec);
          for (let b = 0; b < 16; b++) {
            const r = b >> 2, c = b & 3;
            deq.fill(0);
            lastAll[L0 + b] = quantBlock(coefs16.subarray(b * 16, b * 16 + 16), 1, y1dc, y1ac, 0.45, 0.38, lv, deq);
            levelsAll.set(lv, (L0 + b) * 16);
            levelsAll[(L0 + b) * 16] = 0;
            deq[0] = dcRec[b];
            const base = (mby * 16 + r * 4) * ys + mbx * 16 + c * 4;
            for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) RY[base + y * ys + x] = pred16[(r * 4 + y) * 16 + c * 4 + x];
            idctAdd(deq, RY, base, ys);
          }
          const bm = I16_TO_B[best16Mode];
          for (let c = 0; c < 4; c++) topModes[mbx * 4 + c] = bm;
          leftModes.fill(bm);
        }
        let bestUV = 0, bestUVCost = Infinity;
        for (let m = 0; m < 4; m++) {
          predict8(CU, m, mbx, mby, pu); predict8(CV, m, mbx, mby, pv);
          let s = 0;
          for (let b = 0; b < 4; b++) { const r = b >> 1, c = b & 1, o = (r * 4 + 1) * 9 + c * 4 + 1; s += satd(su, o, 9, pu, r * 32 + c * 4, 8) + satd(sv, o, 9, pv, r * 32 + c * 4, 8); }
          let bits = 0; for (const [p, bit] of UV_PATH[m]) bits += cost(bit, p);
          if (s + lambda * bits < bestUVCost) { bestUVCost = s + lambda * bits; bestUV = m; }
        }
        mbUV[mb] = bestUV;
        predict8(CU, bestUV, mbx, mby, pu); predict8(CV, bestUV, mbx, mby, pv);
        for (let ch = 0; ch < 2; ch++) {
          const S = ch ? sv : su, P = ch ? pv : pu, R = ch ? RV : RU;
          for (let b = 0; b < 4; b++) {
            const r = b >> 1, c = b & 1;
            fdct(S, (r * 4 + 1) * 9 + c * 4 + 1, 9, P, r * 32 + c * 4, 8, coef);
            deq.fill(0);
            const k = L0 + 16 + ch * 4 + b;
            lastAll[k] = quantBlock(coef, 0, uvdc, uvac, 0.45, 0.4, lv, deq);
            levelsAll.set(lv, k * 16);
            const base = (mby * 8 + r * 4) * cs + mbx * 8 + c * 4;
            for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) R[base + y * cs + x] = P[(r * 4 + y) * 8 + c * 4 + x];
            idctAdd(deq, R, base, cs);
          }
        }
        let any = false;
        for (let k = 0; k < 24 && !any; k++) if (lastAll[L0 + k] >= (k < 16 && !mbI4[mb] ? 1 : 0)) any = true;
        if (!mbI4[mb] && lastAll[L0 + 24] >= 0) any = true;
        mbSkip[mb] = any ? 0 : 1;
        if (opts.onRow && mbx === mbw - 1) opts.onRow(mby + 1, mbh);
      }
    }

    // ── Токены: подсчёт и запись ──
    function tokens(put) {
      const topNz = new Uint8Array(mbw * 9), leftNz = new Uint8Array(9);
      for (let mby = 0; mby < mbh; mby++) {
        leftNz.fill(0);
        for (let mbx = 0; mbx < mbw; mbx++) {
          const mb = mby * mbw + mbx, L0 = mb * 25, T = topNz.subarray(mbx * 9, mbx * 9 + 9);
          if (mbSkip[mb]) {
            T.fill(0, 0, 8); leftNz.fill(0, 0, 8);
            if (!mbI4[mb]) { T[8] = 0; leftNz[8] = 0; }
            continue;
          }
          let first = 0, type = 3;
          if (!mbI4[mb]) {
            const last = lastAll[L0 + 24];
            block(put, 1, 0, T[8] + leftNz[8], levelsAll, (L0 + 24) * 16, last);
            T[8] = leftNz[8] = last >= 0 ? 1 : 0;
            first = 1; type = 0;
          }
          for (let b = 0; b < 16; b++) {
            const r = b >> 2, c = b & 3, last = lastAll[L0 + b];
            block(put, type, first, T[c] + leftNz[r], levelsAll, (L0 + b) * 16, last);
            T[c] = leftNz[r] = last >= first ? 1 : 0;
          }
          for (let ch = 0; ch < 2; ch++) for (let b = 0; b < 4; b++) {
            const r = b >> 1, c = b & 1, k = L0 + 16 + ch * 4 + b, last = lastAll[k];
            block(put, 2, 0, T[4 + ch * 2 + c] + leftNz[4 + ch * 2 + r], levelsAll, k * 16, last);
            T[4 + ch * 2 + c] = leftNz[4 + ch * 2 + r] = last >= 0 ? 1 : 0;
          }
        }
      }
    }
    function block(put, t, first, ctx, levels, off, last) {
      const P = (band, c) => ((t * 8 + band) * 3 + c) * 11;
      let n = first, p = P(BANDS[n], ctx);
      if (last < first) { put(0, p); return; }
      put(1, p);
      while (n < 16) {
        const v0 = levels[off + n++];
        const v = Math.abs(v0);
        if (!v) { put(0, p + 1); p = P(BANDS[n], 0); continue; }
        put(1, p + 1);
        if (v === 1) { put(0, p + 2); }
        else {
          put(1, p + 2);
          if (v <= 4) { put(0, p + 3); if (v === 2) put(0, p + 4); else { put(1, p + 4); put(v === 4 ? 1 : 0, p + 5); } }
          else if (v <= 10) {
            put(1, p + 3); put(0, p + 6);
            if (v <= 6) { put(0, p + 7); put(v === 6 ? 1 : 0, -159); }
            else { put(1, p + 7); put(v >= 9 ? 1 : 0, -165); put((v & 1) ? 0 : 1, -145); }
          } else {
            put(1, p + 3); put(1, p + 6);
            let tab, e;
            if (v < 19) { put(0, p + 8); put(0, p + 9); tab = CAT3; e = v - 11; }
            else if (v < 35) { put(0, p + 8); put(1, p + 9); tab = CAT4; e = v - 19; }
            else if (v < 67) { put(1, p + 8); put(0, p + 10); tab = CAT5; e = v - 35; }
            else { put(1, p + 8); put(1, p + 10); tab = CAT6; e = v - 67; }
            for (let i = 0; i < tab.length; i++) put((e >> (tab.length - 1 - i)) & 1, -tab[i]);
          }
        }
        p = P(BANDS[n], v === 1 ? 1 : 2);
        put(v0 < 0 ? 1 : 0, -128);
        if (n === 16) return;
        if (n > last) { put(0, p); return; }
        put(1, p);
      }
    }
    const counts = new Uint32Array(1056 * 2);
    tokens((bit, idx) => { if (idx >= 0) counts[idx * 2 + bit]++; });
    const probs = new Uint8Array(COEF_PROBS), update = new Uint8Array(1056);
    for (let i = 0; i < 1056; i++) {
      const n0 = counts[i * 2], n1 = counts[i * 2 + 1];
      if (!n0 && !n1) continue;
      const np = Math.max(1, Math.min(255, Math.round(255 * n0 / (n0 + n1))));
      const old = COEF_PROBS[i];
      const saving = n0 * (cost(0, old) - cost(0, np)) + n1 * (cost(1, old) - cost(1, np)) - 8 - (cost(1, COEF_UPDATE[i]) - cost(0, COEF_UPDATE[i]));
      if (saving > 0 && np !== old) { probs[i] = np; update[i] = 1; }
    }
    let skipped = 0; for (let i = 0; i < nMB; i++) skipped += mbSkip[i];
    const useSkip = skipped > 0;
    const skipProb = Math.max(1, Math.min(254, Math.round(255 * (nMB - skipped) / nMB)));
    if (!useSkip) mbSkip.fill(0);

    const hdr = new BoolWriter(1 << 16);
    hdr.literal(0, 1); hdr.literal(0, 1);
    hdr.literal(0, 1);
    hdr.literal(0, 1); hdr.literal(opts.filter == null ? Math.min(63, Math.round(0.4 * y1ac)) : opts.filter, 6); hdr.literal(0, 3);
    hdr.literal(0, 1);
    hdr.literal(0, 2);
    hdr.literal(q, 7); for (let i = 0; i < 5; i++) hdr.literal(0, 1);
    hdr.literal(0, 1);
    for (let i = 0; i < 1056; i++) { hdr.put(update[i], COEF_UPDATE[i]); if (update[i]) hdr.literal(probs[i], 8); }
    hdr.literal(useSkip ? 1 : 0, 1); if (useSkip) hdr.literal(skipProb, 8);
    const tm = new Uint8Array(mbw * 4);
    for (let mby = 0; mby < mbh; mby++) {
      let lm = [0, 0, 0, 0];
      for (let mbx = 0; mbx < mbw; mbx++) {
        const mb = mby * mbw + mbx;
        if (useSkip) hdr.put(mbSkip[mb], skipProb);
        if (mbI4[mb]) {
          hdr.put(0, 145);
          const modes = mbB.subarray(mb * 16, mb * 16 + 16);
          for (let b = 0; b < 16; b++) {
            const r = b >> 2, c = b & 3;
            const A = r === 0 ? tm[mbx * 4 + c] : modes[b - 4], L = c === 0 ? lm[r] : modes[b - 1];
            const pr = (A * 10 + L) * 9;
            for (const [node, bit] of BMODE_PATH[modes[b]]) hdr.put(bit, BMODE_PROBS[pr + node]);
          }
          for (let c = 0; c < 4; c++) tm[mbx * 4 + c] = modes[12 + c];
          lm = [modes[3], modes[7], modes[11], modes[15]];
        } else {
          for (const [p, bit] of Y16_PATH[mbY16[mb]]) hdr.put(bit, p);
          const bm = I16_TO_B[mbY16[mb]];
          for (let c = 0; c < 4; c++) tm[mbx * 4 + c] = bm;
          lm = [bm, bm, bm, bm];
        }
        for (const [p, bit] of UV_PATH[mbUV[mb]]) hdr.put(bit, p);
      }
    }
    const first = hdr.finish();
    const tok = new BoolWriter(1 << 20);
    tokens((bit, idx) => tok.put(bit, idx >= 0 ? probs[idx] : -idx));
    const part2 = tok.finish();
    const out = new Uint8Array(10 + first.length + part2.length);
    const tag = (first.length << 5) | (1 << 4);
    out[0] = tag & 255; out[1] = (tag >> 8) & 255; out[2] = (tag >> 16) & 255;
    out[3] = 0x9d; out[4] = 0x01; out[5] = 0x2a;
    out[6] = width & 255; out[7] = (width >> 8) & 0x3f; out[8] = height & 255; out[9] = (height >> 8) & 0x3f;
    out.set(first, 10); out.set(part2, 10 + first.length);
    return { data: out, recon: opts.keepRecon ? { Y: RY, U: RU, V: RV, ys, cs } : null };
  }

  // ── Прозрачность: сжатие без потерь в формате VP8L ──
  class BitWriterL {
    constructor() { this.buf = new Uint8Array(1 << 16); this.pos = 0; this.acc = 0; this.n = 0; }
    put(v, n) {
      while (n > 0) {
        const take = Math.min(n, 24 - this.n);
        this.acc |= (v & ((1 << take) - 1)) << this.n;
        this.n += take; v >>>= take; n -= take;
        while (this.n >= 8) {
          if (this.pos >= this.buf.length) { const b = new Uint8Array(this.buf.length * 2); b.set(this.buf); this.buf = b; }
          this.buf[this.pos++] = this.acc & 255; this.acc >>>= 8; this.n -= 8;
        }
      }
    }
    finish() { if (this.n > 0) this.put(0, 8 - this.n); return this.buf.subarray(0, this.pos); }
  }
  function huffLengths(freq, limit) {
    const n = freq.length;
    let f = Array.from(freq);
    for (;;) {
      const nodes = [];
      for (let i = 0; i < n; i++) if (f[i] > 0) nodes.push({ w: f[i], s: i });
      const len = new Uint8Array(n);
      if (nodes.length === 0) return len;
      if (nodes.length === 1) { len[nodes[0].s] = 1; return len; }
      let heap = nodes.map(x => ({ w: x.w, leaves: [x.s] }));
      heap.sort((a, b) => a.w - b.w);
      while (heap.length > 1) {
        const a = heap.shift(), b = heap.shift();
        for (const s of a.leaves) len[s]++;
        for (const s of b.leaves) len[s]++;
        const m = { w: a.w + b.w, leaves: a.leaves.concat(b.leaves) };
        let i = 0; while (i < heap.length && heap[i].w <= m.w) i++;
        heap.splice(i, 0, m);
      }
      let max = 0; for (let i = 0; i < n; i++) if (len[i] > max) max = len[i];
      if (max <= limit) return len;
      f = f.map(x => x > 0 ? Math.max(1, x >> 1) : 0);
    }
  }
  function canonical(len) {
    const n = len.length, codes = new Uint32Array(n);
    const count = new Uint32Array(16);
    for (let i = 0; i < n; i++) count[len[i]]++;
    count[0] = 0;
    const next = new Uint32Array(16);
    let code = 0;
    for (let b = 1; b < 16; b++) { code = (code + count[b - 1]) << 1; next[b] = code; }
    for (let i = 0; i < n; i++) if (len[i]) {
      let c = next[len[i]]++, r = 0;
      for (let k = 0; k < len[i]; k++) { r = (r << 1) | (c & 1); c >>= 1; }
      codes[i] = r;
    }
    let used = 0; for (let i = 0; i < n; i++) if (len[i]) used++;
    return { len, codes, single: used <= 1 };
  }
  const CLC_ORDER = [17, 18, 0, 1, 2, 3, 4, 5, 16, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
  function writeCode(bw, freq) {
    const used = []; for (let i = 0; i < freq.length; i++) if (freq[i]) used.push(i);
    if (used.length <= 2 && used.every(s => s < 256)) {
      const syms = used.length ? used : [0];
      bw.put(1, 1); bw.put(syms.length - 1, 1);
      if (syms[0] < 2) { bw.put(0, 1); bw.put(syms[0], 1); } else { bw.put(1, 1); bw.put(syms[0], 8); }
      if (syms.length === 2) bw.put(syms[1], 8);
      const len = new Uint8Array(freq.length); syms.forEach(s => { len[s] = 1; });
      return canonical(len);
    }
    const len = huffLengths(freq, 15);
    const seq = [];
    for (let i = 0; i < len.length;) {
      const v = len[i]; let run = 1;
      while (i + run < len.length && len[i + run] === v) run++;
      if (v === 0 && run >= 3) {
        let left = run;
        while (left >= 3) { if (left >= 11) { const k = Math.min(138, left); seq.push([18, k - 11, 7]); left -= k; } else { const k = Math.min(10, left); seq.push([17, k - 3, 3]); left -= k; } }
        for (; left > 0; left--) seq.push([0, 0, 0]);
      } else if (v !== 0 && run >= 4) {
        seq.push([v, 0, 0]); let left = run - 1;
        while (left >= 3) { const k = Math.min(6, left); seq.push([16, k - 3, 2]); left -= k; }
        for (; left > 0; left--) seq.push([v, 0, 0]);
      } else for (let k = 0; k < run; k++) seq.push([v, 0, 0]);
      i += run;
    }
    const cf = new Uint32Array(19); seq.forEach(s => cf[s[0]]++);
    const clen = huffLengths(cf, 7);
    const cc = canonical(clen);
    let num = 19; while (num > 4 && clen[CLC_ORDER[num - 1]] === 0) num--;
    bw.put(0, 1); bw.put(num - 4, 4);
    for (let i = 0; i < num; i++) bw.put(clen[CLC_ORDER[i]], 3);
    bw.put(0, 1);
    for (const [s, e, eb] of seq) { if (!cc.single) bw.put(cc.codes[s], cc.len[s]); if (eb) bw.put(e, eb); }
    return canonical(len);
  }
  function prefix(v) {
    const d = v - 1;
    if (d < 4) return [d, 0, 0];
    const hb = 31 - Math.clz32(d), second = (d >> (hb - 1)) & 1, nb = hb - 1;
    return [2 * hb + second, nb, d & ((1 << nb) - 1)];
  }
  function encodeAlpha(alpha, w, h) {
    const n = w * h, toks = [];
    const green = new Uint32Array(280), dist = new Uint32Array(40);
    for (let i = 0; i < n;) {
      let l1 = 0, l2 = 0;
      if (i >= 1) while (i + l1 < n && l1 < 4096 && alpha[i + l1] === alpha[i + l1 - 1]) l1++;
      if (i >= w) while (i + l2 < n && l2 < 4096 && alpha[i + l2] === alpha[i + l2 - w]) l2++;
      const len = Math.max(l1, l2);
      if (len >= 3) {
        const code = l2 >= l1 ? 1 : 2;
        const [lc, lb, le] = prefix(len), [dc, db, de] = prefix(code);
        toks.push([1, lc, lb, le, dc, db, de]); green[256 + lc]++; dist[dc]++; i += len;
      } else { toks.push([0, alpha[i]]); green[alpha[i]]++; i++; }
    }
    const bw = new BitWriterL();
    bw.put(0, 1); bw.put(0, 1); bw.put(0, 1);
    const G = writeCode(bw, green);
    writeCode(bw, new Uint32Array(256)); writeCode(bw, new Uint32Array(256)); writeCode(bw, new Uint32Array(256));
    const D = writeCode(bw, dist);
    for (const t of toks) {
      if (t[0] === 0) { if (!G.single) bw.put(G.codes[t[1]], G.len[t[1]]); }
      else {
        if (!G.single) bw.put(G.codes[256 + t[1]], G.len[256 + t[1]]);
        if (t[2]) bw.put(t[3], t[2]);
        if (!D.single) bw.put(D.codes[t[4]], D.len[t[4]]);
        if (t[5]) bw.put(t[6], t[5]);
      }
    }
    const body = bw.finish();
    const out = new Uint8Array(body.length + 1);
    out[0] = 1; out.set(body, 1);
    return out;
  }

  // ── Цвет и контейнер ──
  function toYUV(rgba, width, height) {
    const mbw = (width + 15) >> 4, mbh = (height + 15) >> 4;
    const ys = mbw * 16, cs = mbw * 8, yh = mbh * 16, ch = mbh * 8;
    const Y = new Uint8Array(ys * yh), U = new Uint8Array(cs * ch), V = new Uint8Array(cs * ch);
    const px = (x, y) => ((y < height ? y : height - 1) * width + (x < width ? x : width - 1)) * 4;
    for (let y = 0; y < yh; y++) for (let x = 0; x < ys; x++) {
      const i = px(x, y);
      Y[y * ys + x] = (16839 * rgba[i] + 33059 * rgba[i + 1] + 6420 * rgba[i + 2] + (1 << 15) + (16 << 16)) >> 16;
    }
    const clipUV = (v) => { v = (v + (1 << 17) + (128 << 18)) >> 18; return v < 0 ? 0 : v > 255 ? 255 : v; };
    for (let y = 0; y < ch; y++) for (let x = 0; x < cs; x++) {
      let r = 0, g = 0, b = 0;
      for (let k = 0; k < 4; k++) { const i = px(2 * x + (k & 1), 2 * y + (k >> 1)); r += rgba[i]; g += rgba[i + 1]; b += rgba[i + 2]; }
      U[y * cs + x] = clipUV(-9719 * r - 19081 * g + 28800 * b);
      V[y * cs + x] = clipUV(28800 * r - 24116 * g - 4684 * b);
    }
    return { Y, U, V };
  }
  function flattenTransparent(rgba, width, height) {
    for (let by = 0; by < height; by += 16) for (let bx = 0; bx < width; bx += 16) {
      let r = 0, g = 0, b = 0, n = 0, clear = 0;
      for (let y = by; y < Math.min(height, by + 16); y++) for (let x = bx; x < Math.min(width, bx + 16); x++) {
        const i = (y * width + x) * 4;
        if (rgba[i + 3] === 0) clear++; else { r += rgba[i]; g += rgba[i + 1]; b += rgba[i + 2]; n++; }
      }
      if (!clear) continue;
      const fr = n ? Math.round(r / n) : 0, fg = n ? Math.round(g / n) : 0, fb = n ? Math.round(b / n) : 0;
      for (let y = by; y < Math.min(height, by + 16); y++) for (let x = bx; x < Math.min(width, bx + 16); x++) {
        const i = (y * width + x) * 4;
        if (rgba[i + 3] === 0) { rgba[i] = fr; rgba[i + 1] = fg; rgba[i + 2] = fb; }
      }
    }
  }
  function chunk(type, data) {
    const pad = data.length & 1;
    const out = new Uint8Array(8 + data.length + pad);
    for (let i = 0; i < 4; i++) out[i] = type.charCodeAt(i);
    out[4] = data.length & 255; out[5] = (data.length >> 8) & 255; out[6] = (data.length >> 16) & 255; out[7] = (data.length >>> 24) & 255;
    out.set(data, 8);
    return out;
  }
  function qualityToQ(quality) {
    const c = quality < 0.75 ? quality * 2 / 3 : 2 * quality - 1;
    return Math.max(0, Math.min(127, Math.round(127 * (1 - c) * 0.35)));
  }
  function encode(rgba, width, height, opts) {
    opts = opts || {};
    if (width > 16383 || height > 16383) throw new Error('WebP: слишком большая картинка');
    let alpha = null;
    for (let i = 3; i < rgba.length; i += 4) if (rgba[i] !== 255) { alpha = true; break; }
    let alphaChunk = null;
    if (alpha) {
      const a = new Uint8Array(width * height);
      for (let i = 0; i < a.length; i++) a[i] = rgba[i * 4 + 3];
      flattenTransparent(rgba, width, height);
      alphaChunk = chunk('ALPH', encodeAlpha(a, width, height));
    }
    const { Y, U, V } = toYUV(rgba, width, height);
    const q = opts.q != null ? opts.q : qualityToQ(opts.quality != null ? opts.quality : 0.8);
    const frame = encodeFrame(Y, U, V, width, height, Object.assign({}, opts, { q }));
    const vp8 = chunk('VP8 ', frame.data);
    let parts;
    if (alphaChunk) {
      const x = new Uint8Array(10);
      x[0] = 0x10;
      const w1 = width - 1, h1 = height - 1;
      x[4] = w1 & 255; x[5] = (w1 >> 8) & 255; x[6] = (w1 >> 16) & 255; x[7] = h1 & 255; x[8] = (h1 >> 8) & 255; x[9] = (h1 >> 16) & 255;
      parts = [chunk('VP8X', x), alphaChunk, vp8];
    } else parts = [vp8];
    const total = 4 + parts.reduce((s, p) => s + p.length, 0);
    const out = new Uint8Array(8 + total);
    out.set([82, 73, 70, 70], 0);
    out[4] = total & 255; out[5] = (total >> 8) & 255; out[6] = (total >> 16) & 255; out[7] = (total >>> 24) & 255;
    out.set([87, 69, 66, 80], 8);
    let o = 12; for (const p of parts) { out.set(p, o); o += p.length; }
    return { bytes: out, recon: frame.recon, q };
  }

  // ── Фоновый поток ──
  const inWorker = typeof WorkerGlobalScope !== 'undefined' && root instanceof WorkerGlobalScope;
  if (inWorker) {
    root.onmessage = e => {
      const { id, rgba, width, height, opts } = e.data;
      try {
        const r = encode(new Uint8Array(rgba), width, height, opts);
        root.postMessage({ id, bytes: r.bytes.buffer, q: r.q }, [r.bytes.buffer]);
      } catch (err) { root.postMessage({ id, error: String(err && err.message || err) }); }
    };
  }
  let worker = null, seq = 0;
  const waiting = new Map();
  function workerFor() {
    if (worker !== null) return worker;
    try {
      const src = (document.currentScript && document.currentScript.src) || Kov.WebPEncoder.scriptUrl;
      worker = new Worker(src);
      worker.onmessage = e => { const w = waiting.get(e.data.id); if (!w) return; waiting.delete(e.data.id); if (e.data.error) w.reject(new Error(e.data.error)); else w.resolve(e.data); };
      worker.onerror = () => { waiting.forEach(w => w.reject(new Error('WebP: фоновый поток недоступен'))); waiting.clear(); worker = false; };
    } catch (e) { worker = false; }
    return worker;
  }
  async function encodeImageData(img, opts) {
    const w = workerFor();
    if (w) {
      try {
        const copy = new Uint8Array(img.data).buffer;
        const r = await new Promise((resolve, reject) => { const id = ++seq; waiting.set(id, { resolve, reject }); w.postMessage({ id, rgba: copy, width: img.width, height: img.height, opts: opts || {} }, [copy]); });
        return new Blob([r.bytes], { type: 'image/webp' });
      } catch (e) { /* считаем здесь же */ }
    }
    return new Blob([encode(new Uint8Array(img.data), img.width, img.height, opts).bytes], { type: 'image/webp' });
  }
  const api = { encode, encodeImageData, qualityToQ, encodeAlpha, scriptUrl: (typeof document !== 'undefined' && document.currentScript) ? document.currentScript.src : 'js/media/webp-encoder.js' };
  Kov.WebPEncoder = api;
})(typeof self !== 'undefined' ? self : globalThis);
