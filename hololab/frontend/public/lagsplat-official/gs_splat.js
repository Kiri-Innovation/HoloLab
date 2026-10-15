/*
 * gs_splat.js — décodeur Gaussian Splatting (2+d)GS évalué EN DIRECT dans le navigateur,
 * générique en la dimension latente d.
 *
 * Utilisé par lagsplat.html pour l'onglet « sac » (d=2, 15 000 gaussiennes) et l'onglet
 * « SCR souple » (d=4, 15 000 gaussiennes). C'était à l'origine krauss2_splat.js, écrit
 * pour d=4 seulement ; le sac lisait alors sa reconstruction dans un ATLAS de 19×19
 * tuiles pré-décodées (sac_frames.png, 11 Mo). Rendre en direct est à la fois plus léger
 * (1,1 Mo de blocs de Schur) et EXACT — la coupe est calculée à l'état courant au lieu
 * d'être interpolée entre les quatre tuiles voisines. En d=4 l'atlas était de toute
 * façon hors de portée : il aurait fallu 19⁴ = 130 321 tuiles.
 *
 * Conditionner la gaussienne jointe (x, y, q) ∈ ℝ^{2+d} sur l'état q donne une
 * gaussienne 2D en forme close, valable en toute dimension d :
 *
 *     μ(q)   = μ_xy + Σ_xz Σ_zz⁻¹ (q − μ_z)          (AFFINE en q)
 *     Σ_cond = Σ_xx − Σ_xz Σ_zz⁻¹ Σ_zxᵀ              (INDÉPENDANTE de q)
 *     w_z(q) = exp(−½ (q−μ_z)ᵀ Σ_zz⁻¹ (q−μ_z))       (poids d'opacité)
 *
 * Tout cela tient dans le VERTEX SHADER, `q` étant un uniform : le CPU n'a rien à
 * recalculer par frame, la carte fait les 15000 gaussiennes d'un trait. Le shader est
 * ENGENDRÉ à partir de d et de la disposition binaire (`meta.layout`) : aucun offset
 * n'est écrit en dur, ni ici ni dans les scripts d'extraction/vérification.
 *
 * ── Fidélité au décodeur entraîné ────────────────────────────────────────────────
 * Le décodeur a été entraîné sous `gsplat.rasterization` : reproduire « une gaussienne
 * 2D » ne suffit pas, il faut sa recette exacte. Elle a été fixée par MESURE contre des
 * rendus gsplat de référence (demo_Lags/check_krauss2_splat.py, 8 états latents par cas) :
 *
 *   - covariance en pixels : gsplat projette une gaussienne 3D « pancake »
 *     block_diag(Σ_cond + 1e-5·I, ε_z) par une caméra fictive de focales (W, H) placée à
 *     Z = 1, d'où un terme radial ε_z·(W·x)(H·y) en plus de W²Σ_cond ; puis ajoute son
 *     flou d'anticrénelage EPS2D = 0.3 px² sur la diagonale ;
 *   - opacité : clamp(α · w_z, 0, 0.99) ;
 *   - compositing : front-to-back « over », dans l'ordre d'INDEX. Toutes les gaussiennes
 *     sont posées à Z = 1.0 EXACTEMENT (models_2pt.py) ⟹ le tri par profondeur de gsplat
 *     est une égalité parfaite et l'ordre effectif est celui du tableau.
 *
 *   Mesures (PSNR contre gsplat, cas d=4) : recette ci-dessus 46.5 dB · sans EPS2D
 *   38.2 dB · sans le terme pancake 43.7 dB · en ORDRE INVERSÉ 15.2 dB. Les 31 dB
 *   d'écart entre ordre direct et ordre inverse sont ce qui établit l'hypothèse d'ordre.
 *
 * Le compositing « over » front-to-back s'obtient sans aucun tri, par le blending
 * `(ONE_MINUS_DST_ALPHA, ONE)` : le canal alpha de destination accumule 1 − T, donc
 * (1 − dst.a) EST la transmittance restante. OpenGL garantit l'ordre des primitives
 * dans le blending, et les instances sont traitées dans l'ordre — rien à trier.
 *
 * ⚠️ ESPACE : `render(z)` attend l'état latent BRUT z (celui de μ_z), PAS le u blanchi
 * du LNN. Passer par <Cas>LNN.toZ().
 *
 * API :
 *   const S = GSSplat.create(canvas, buffer, meta);   // buffer = ArrayBuffer du .bin
 *   S.render(z);        // z : Float64Array/Array (d)
 *   S.resize();
 *   S.select(z, x, y)       // saisie : paquet de gaussiennes figé (ancrage matériel)
 *   S.pointOf(z, sel)       // position COURANTE du point matériel saisi
 *   S.presence(z, sel)      // ∈[0,1] : ce qu'il reste de la matière saisie à cet état
 *   S.jacobianAt(z, x, y)   // Jᵀ moyen au point image (x,y) ∈ [0,1]², sans ancrage
 */
(function (root) {
  'use strict';

  // Constantes de la recette gsplat (cf. en-tête ; ne pas toucher sans reprendre
  // check_krauss2_splat.py, elles sont mesurées, pas choisies).
  const EPS2D = 0.3;      // flou d'anticrénelage de gsplat, en px²
  const EPS_Z = 1e-5;     // épaisseur de la gaussienne « pancake » selon Z
  const S_FLOOR = 1e-5;   // plancher SPD ajouté à Σ_cond avant décomposition
  const CUTOFF = 3.0;     // rayon de coupe, en σ

  // ── disposition binaire ────────────────────────────────────────────────────
  /** Table des offsets (en float32) d'une gaussienne. `meta.layout` fait foi ; à défaut
   *  on la reconstruit pour d — c'est la même que celle des scripts d'extraction. */
  function layoutOf(meta) {
    const d = meta.d;
    if (meta.layout) return meta.layout;
    const lay = {}; let off = 0;
    for (const [name, n] of [['mu_xy', 2], ['mz', d], ['sxz_szzi', 2 * d],
                             ['szzi', d * (d + 1) / 2], ['scond_inv', 3],
                             ['alpha', 1], ['color', 3]]) {
      lay[name] = [off, off + n]; off += n;
    }
    return lay;
  }

  /** Découpe n float consécutifs en attributs de vertex de 4 composantes au plus
   *  (une seule contrainte GLSL) et rend de quoi les déclarer ET les lire :
   *  `chunks` pour vertexAttribPointer, `comp(i)` pour le i-ième float dans le shader. */
  function group(name, base, n, loc0) {
    const chunks = [];
    for (let o = 0; o < n; o += 4) {
      chunks.push({ loc: loc0 + chunks.length, size: Math.min(4, n - o),
                    off: base + o, name: name + chunks.length });
    }
    const decl = chunks.map(c => `  layout(location=${c.loc}) in ` +
      (c.size === 1 ? 'float' : 'vec' + c.size) + ` a_${c.name};`).join('\n');
    const comp = i => chunks[i >> 2].size === 1
      ? `a_${chunks[i >> 2].name}`
      : `a_${chunks[i >> 2].name}.${'xyzw'[i & 3]}`;
    return { chunks, decl, comp };
  }

  /** Shaders engendrés pour la dimension d et la disposition `lay`. Le fragment shader
   *  ne dépend pas de d ; seul le vertex shader déroule les sommes en d. */
  function shaders(d, lay) {
    let loc = 2;
    const mu = group('mu', lay.mu_xy[0], 2, loc); loc += mu.chunks.length;
    const mz = group('mz', lay.mz[0], d, loc); loc += mz.chunks.length;
    const sxz = group('sxz', lay.sxz_szzi[0], 2 * d, loc); loc += sxz.chunks.length;
    const szzi = group('szzi', lay.szzi[0], d * (d + 1) / 2, loc); loc += szzi.chunks.length;
    const sci = group('sci', lay.scond_inv[0], 3, loc); loc += sci.chunks.length;
    const alpha = group('alpha', lay.alpha[0], 1, loc); loc += alpha.chunks.length;
    const color = group('color', lay.color[0], 3, loc); loc += color.chunks.length;
    const groups = [mu, mz, sxz, szzi, sci, alpha, color];

    // dz = q − μ_z, puis la forme quadratique dzᵀ Σ_zz⁻¹ dz (triangle SUPÉRIEUR : les
    // termes hors diagonale comptent double).
    const dzL = [];
    for (let i = 0; i < d; i++) dzL.push(`    float dz${i} = uQ[${i}] - ${mz.comp(i)};`);
    const quad = [];
    let t = 0;
    for (let i = 0; i < d; i++) {
      for (let j = i; j < d; j++, t++) {
        quad.push(`${i === j ? '' : '2.0*'}${szzi.comp(t)}*dz${i}*dz${j}`);
      }
    }
    // μ(q) = μ_xy + (Σ_xz Σ_zz⁻¹) dz — les d premiers floats sont la ligne x, les d
    // suivants la ligne y (row-major).
    const shift = r => Array.from({ length: d },
      (_, i) => `${sxz.comp(r * d + i)}*dz${i}`).join(' + ');

    const VERT = `#version 300 es
  precision highp float;
  layout(location=0) in vec2 aCorner;      // coin du quad, ∈ {−1,+1}²
${groups.map(g => g.decl).join('\n')}

  uniform float uQ[${d}];  // état latent BRUT z
  uniform vec2 uSize;      // (W, H) en pixels de rendu

  out vec3 vColor;
  out float vOpacity;
  out vec3 vConic;        // Σ_2d⁻¹ : (a, b, c)
  out vec2 vDelta;        // écart au centre, en pixels

  void main() {
${dzL.join('\n')}

    // w_z = exp(−½ dzᵀ Σ_zz⁻¹ dz)
    float qf = ${quad.join('\n             + ')};
    float wz = exp(-0.5 * min(qf, 20.0));
    float op = clamp(${alpha.comp(0)} * wz, 0.0, 0.99);

    vec2 mu = vec2(${mu.comp(0)}, ${mu.comp(1)}) + vec2(${shift(0)}, ${shift(1)});

    // Σ_cond depuis son inverse (2×2 en forme close) + plancher SPD de gsplat
    float ia = ${sci.comp(0)}, ib = ${sci.comp(1)}, ic = ${sci.comp(2)};
    float idet = ia*ic - ib*ib;
    float c00 =  ic/idet + ${S_FLOOR.toExponential()};
    float c01 = -ib/idet;
    float c11 =  ia/idet + ${S_FLOOR.toExponential()};

    // Projection par la caméra fictive (focales (W,H), Z = 1) + flou d'anticrénelage
    float X = (mu.x - 0.5) * uSize.x;
    float Y = (mu.y - 0.5) * uSize.y;
    float s00 = uSize.x*uSize.x*c00 + ${EPS_Z.toExponential()}*X*X + ${EPS2D.toFixed(4)};
    float s01 = uSize.x*uSize.y*c01 + ${EPS_Z.toExponential()}*X*Y;
    float s11 = uSize.y*uSize.y*c11 + ${EPS_Z.toExponential()}*Y*Y + ${EPS2D.toFixed(4)};

    float det = s00*s11 - s01*s01;
    // Gaussienne évanouie ou covariance dégénérée : quad nul, rien n'est rasterisé.
    if (op < 0.0039 || det <= 0.0) {
      gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
      vOpacity = 0.0; vColor = vec3(0.0); vConic = vec3(1.0, 0.0, 1.0); vDelta = vec2(0.0);
      return;
    }
    vConic = vec3(s11/det, -s01/det, s00/det);

    // Demi-étendue du quad : ${CUTOFF}σ le long du grand axe (valeur propre max)
    float mid = 0.5*(s00 + s11);
    float rad = sqrt(max(mid*mid - det, 0.0));
    float r = ${CUTOFF.toFixed(1)} * sqrt(max(mid + rad, 1e-12));

    vDelta = aCorner * r;
    vColor = vec3(${color.comp(0)}, ${color.comp(1)}, ${color.comp(2)});
    vOpacity = op;

    // Centre en pixels : px = μ_x·W − 0.5 (le centre du pixel i est i + 0.5)
    vec2 px = vec2(mu.x*uSize.x - 0.5, mu.y*uSize.y - 0.5) + vDelta;
    // → NDC. L'axe y de l'image descend, celui de NDC monte.
    gl_Position = vec4(2.0*(px.x + 0.5)/uSize.x - 1.0,
                       1.0 - 2.0*(px.y + 0.5)/uSize.y, 0.0, 1.0);
  }`;

    const FRAG = `#version 300 es
  precision highp float;
  in vec3 vColor;
  in float vOpacity;
  in vec3 vConic;
  in vec2 vDelta;
  out vec4 fragColor;
  void main() {
    float q = vConic.x*vDelta.x*vDelta.x
            + 2.0*vConic.y*vDelta.x*vDelta.y
            + vConic.z*vDelta.y*vDelta.y;
    if (q > ${(2 * CUTOFF * CUTOFF).toFixed(1)}) discard;
    float a = vOpacity * exp(-0.5*q);
    if (a < 0.0039) discard;
    fragColor = vec4(vColor * a, a);      // prémultiplié : cf. le blending « under »
  }`;

    return { VERT, FRAG, groups };
  }

  function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error('shader : ' + gl.getShaderInfoLog(s));
    }
    return s;
  }

  // ── prise : sélection MATÉRIELLE et pullback d'une force image ──────────────
  // J_k = ∂μ_k/∂z = Σ_xz Σ_zz⁻¹ est CONSTANT par gaussienne (le décodeur est affine en
  // z). Saisir revient donc à choisir UNE FOIS un paquet de gaussiennes et son jacobien
  // moyen ; ce qui bouge ensuite, c'est leur position à l'écran.
  //
  // ⚠️ La sélection est FIGÉE à la prise, et les poids avec elle. Resélectionner à chaque
  // frame les gaussiennes voisines d'un pixel FIXE ferait glisser la prise d'une matière
  // à l'autre pendant que l'objet se déplace sous le curseur — on n'attraperait plus le
  // bout du bras mais « ce qui passe à cet endroit de l'écran ». Avec l'ancrage matériel,
  // `pointOf` rend la position COURANTE du morceau saisi : la flèche part de la matière,
  // et l'écart au curseur (donc la force) se contracte à mesure que l'objet arrive.
  // Les deux variantes sont câblées dans lagsplat.html : ancrage matériel pour le sac,
  // point d'écran fixe pour le bras d=4.
  //
  // Fonctions PURES (hors de la closure WebGL) pour être testables sans contexte GL —
  // cf. demo_Lags/check_krauss2_splat.py.

  // Réglages du moyennage, réglables depuis l'UI. Les défauts sont ceux que reproduit
  // check_krauss2_splat.py : ne pas les changer sans rejouer ce test.
  const SEL_DEFAULTS = {
    radius: 0.05,   // rayon de sélection, en fraction de l'image (coupure franche)
    sharp: 3.0,     // décroissance spatiale : σ = radius / sharp. ↑ = prise plus locale
    wzMin: 1e-3,    // présence latente minimale pour participer
    jmin: 0.0,      // mobilité minimale, en fraction du max local de ‖J‖ (0 = filtre off)
  };

  /** Offsets (en float) des champs utiles aux fonctions CPU, pour une gaussienne. */
  function offs(meta) {
    const lay = layoutOf(meta);
    return { mu: lay.mu_xy[0], mz: lay.mz[0], sxz: lay.sxz_szzi[0],
             szzi: lay.szzi[0], a: lay.alpha[0] };
  }

  /** Gaussiennes saisies au point image (x,y) à l'état z, avec leurs poids figés.
   *  `opts` : {radius, sharp, wzMin, jmin} (un nombre est accepté comme radius, pour
   *  compatibilité). Retourne {idx, w, wsum, kern, kernsum, J} ou null.
   *
   *  `kern` = opacité × noyau spatial, c'est-à-dire la part du poids qui NE DÉPEND PAS de
   *  l'état : c'est le dénominateur de `presence()`. Le poids complet est w = kern·w_z(z),
   *  et c'est lui qui pondère J̄ et le point matériel.
   *
   *  Le filtre de MOBILITÉ (`jmin`) mérite un mot : ‖J‖ = ‖∂μ/∂z‖ mesure de combien une
   *  gaussienne bouge quand l'état bouge. Le fond de la scène (le portique, la toile
   *  noire) est immobile, donc J ≈ 0 — et il est MAJORITAIRE en nombre. Sans ce filtre, un
   *  clic sur le bras noie la sélection dans du décor, et le point d'application dérive
   *  vers le barycentre de l'image au lieu de suivre la matière qui bouge. Le seuil est
   *  relatif au max de la sélection, donc valable partout sur l'objet. */
  function select(data, meta, z, x, y, opts) {
    const d = meta.d, K = meta.K, st = meta.stride, F = offs(meta);
    const o_ = (typeof opts === 'number') ? { radius: opts } : (opts || {});
    const radius = o_.radius > 0 ? o_.radius : SEL_DEFAULTS.radius;
    const sharp = o_.sharp > 0 ? o_.sharp : SEL_DEFAULTS.sharp;
    const wzMin = o_.wzMin >= 0 ? o_.wzMin : SEL_DEFAULTS.wzMin;
    const jmin = o_.jmin >= 0 ? o_.jmin : SEL_DEFAULTS.jmin;
    const r2 = radius * radius, sig2 = (radius / sharp) * (radius / sharp);

    // 1er passage : candidats (présence latente, puis rayon image), avec leur mobilité.
    const cand = [], jn = [];
    let jmax = 0;
    for (let k = 0; k < K; k++) {
      const o = k * st;
      const dz = [];
      for (let i = 0; i < d; i++) dz.push(z[i] - data[o + F.mz + i]);
      let qf = 0, t = F.szzi;
      for (let i = 0; i < d; i++) {
        for (let j = i; j < d; j++) { qf += (i === j ? 1 : 2) * data[o + t] * dz[i] * dz[j]; t++; }
      }
      const wz = Math.exp(-0.5 * Math.min(qf, 20));
      if (wz < wzMin) continue;                 // gaussienne absente à cet état
      let mx = data[o + F.mu], my = data[o + F.mu + 1];
      for (let i = 0; i < d; i++) { mx += dz[i] * data[o + F.sxz + i]; my += dz[i] * data[o + F.sxz + d + i]; }
      const dd = (mx - x) * (mx - x) + (my - y) * (my - y);
      if (dd > r2) continue;
      let jj = 0;
      for (let i = 0; i < 2 * d; i++) { const v = data[o + F.sxz + i]; jj += v * v; }
      jj = Math.sqrt(jj);
      if (jj > jmax) jmax = jj;
      cand.push([k, wz, dd]); jn.push(jj);
    }
    if (!cand.length) return null;

    // 2e passage : filtre de mobilité (relatif), puis poids et moyennes.
    const thr = jmin * jmax;
    const idx = [], ws = [], kern = [];
    const J = new Float64Array(2 * d);
    let wsum = 0, kernsum = 0;
    for (let n = 0; n < cand.length; n++) {
      if (jmin > 0 && jn[n] <= thr) continue;
      const k = cand[n][0], wz = cand[n][1], dd = cand[n][2], o = k * st;
      // noyau de sélection : opacité × décroissance spatiale (figées) × présence w_z
      const kn = data[o + F.a] * Math.exp(-0.5 * dd / sig2);
      const w = kn * wz;
      idx.push(k); ws.push(w); kern.push(kn); wsum += w; kernsum += kn;
      for (let i = 0; i < d; i++) { J[i] += w * data[o + F.sxz + i]; J[d + i] += w * data[o + F.sxz + d + i]; }
    }
    if (wsum <= 0) return null;                 // le filtre a tout mangé : rien à saisir
    for (let i = 0; i < 2 * d; i++) J[i] /= wsum;
    return { idx: Int32Array.from(idx), w: Float64Array.from(ws), wsum,
             kern: Float64Array.from(kern), kernsum, J };
  }

  /** Présence de la matière saisie à l'état z : moyenne de w_z sur la sélection, pondérée
   *  par la part figée du noyau. Vaut 1 si toutes les gaussiennes saisies sont pleinement
   *  là, tend vers 0 quand elles s'effacent.
   *
   *  ⚠️ Sans ce facteur, l'amplitude de la force NE DÉPEND PAS de la présence : J̄ est une
   *  moyenne NORMALISÉE (division par Σw), donc le Σw se simplifie et saisir un filament
   *  quasi transparent tire aussi fort que saisir de la matière franche. Et comme les
   *  poids sont figés à la prise, l'extinction ultérieure des gaussiennes saisies serait
   *  invisible pour la force. `sel.wsum/sel.kernsum` est sa valeur à l'instant de la
   *  prise, ce qui permet à l'appelant d'en faire un rapport. */
  function presence(data, meta, z, sel) {
    const d = meta.d, st = meta.stride, F = offs(meta);
    let acc = 0;
    for (let n = 0; n < sel.idx.length; n++) {
      const o = sel.idx[n] * st;
      let qf = 0, t = F.szzi;
      const dz = [];
      for (let i = 0; i < d; i++) dz.push(z[i] - data[o + F.mz + i]);
      for (let i = 0; i < d; i++) {
        for (let j = i; j < d; j++) { qf += (i === j ? 1 : 2) * data[o + t] * dz[i] * dz[j]; t++; }
      }
      acc += sel.kern[n] * Math.exp(-0.5 * Math.min(qf, 20));
    }
    return acc / sel.kernsum;
  }

  /** Position image du point matériel `sel` à l'état z : moyenne des μ_k(z) aux poids
   *  FIGÉS de la sélection. C'est elle qui suit les gaussiennes. */
  function pointOf(data, meta, z, sel) {
    const d = meta.d, st = meta.stride, F = offs(meta);
    let px = 0, py = 0;
    for (let n = 0; n < sel.idx.length; n++) {
      const o = sel.idx[n] * st, w = sel.w[n];
      let mx = data[o + F.mu], my = data[o + F.mu + 1];
      for (let i = 0; i < d; i++) {
        const dzi = z[i] - data[o + F.mz + i];
        mx += dzi * data[o + F.sxz + i]; my += dzi * data[o + F.sxz + d + i];
      }
      px += w * mx; py += w * my;
    }
    return [px / sel.wsum, py / sel.wsum];
  }

  /** Jacobien moyen au point image (x,y) — sélection immédiate, sans ancrage. */
  function jacobianAt(data, meta, z, x, y, opts) {
    const s = select(data, meta, z, x, y, opts);
    return s ? s.J : null;
  }

  /** @param opts.maxSide  Plafond, en pixels, du COTE du viewport carre.
   *
   *  Le decodeur a ete ajuste a `meta.img_size` (256 px) : au-dela, on n'obtient
   *  aucun detail de plus, on expose la structure des gaussiennes elles-memes.
   *  L'anticrenelage `eps2d` vaut 0.3 px^2 quelle que soit la resolution, donc
   *  rendre a 768 px donne aux gaussiennes 3x moins de flou RELATIF que celui
   *  avec lequel elles ont ete ajustees — d'ou un rendu « trop net », qui a l'air
   *  d'un defaut de rasterisation alors que c'est une sur-resolution.
   *  Plafonner le backing store et laisser le CSS agrandir le canevas rend
   *  exactement la reconstruction entrainee, affichee plus grand.
   *  Absent ou 0 : aucun plafond (comportement d'origine). */
  // Recopie de la cible d'accumulation vers le canevas. Aucun melange : la texture
  // porte deja des couleurs PREMULTIPLIEES, comme le canevas (premultipliedAlpha).
  const BLIT_VERT = `#version 300 es
  precision highp float;
  layout(location=0) in vec2 aCorner;
  out vec2 vUV;
  void main(){ vUV = aCorner*0.5 + 0.5; gl_Position = vec4(aCorner, 0.0, 1.0); }`;
  const BLIT_FRAG = `#version 300 es
  precision highp float;
  uniform sampler2D uTex;
  in vec2 vUV;
  out vec4 frag;
  void main(){ frag = texture(uTex, vUV); }`;

  function create(canvas, buffer, meta, opts) {
    const MAX_SIDE = (opts && opts.maxSide) || 0;
    /* Rapport L/H du viewport. 1 (defaut) = le carre historique.
       Le decodeur rend dans [0,1]^2 : ce rapport dit comment ce carre
       d'IMAGE s'etale a l'ecran. Il vaut 1 quand la source est carree (sac,
       SCR souple) et 16/9 pour le rocking chair, dont les images
       d'entrainement sont un 1280x720 ecrase dans un 256x256 : les afficher
       carrees conserve l'ecrasement. */
    const ASPECT = (opts && opts.aspect > 0) ? opts.aspect : 1;
    const gl = canvas.getContext('webgl2', {
      alpha: true, antialias: false, premultipliedAlpha: true,
    });
    if (!gl) throw new Error('WebGL2 indisponible');

    const d = meta.d, lay = layoutOf(meta);
    const src = shaders(d, lay);
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, src.VERT));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, src.FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error('link : ' + gl.getProgramInfoLog(prog));
    }
    gl.useProgram(prog);
    const uQ = gl.getUniformLocation(prog, 'uQ[0]') || gl.getUniformLocation(prog, 'uQ');
    const uSize = gl.getUniformLocation(prog, 'uSize');

    const data = new Float32Array(buffer);
    const K = meta.K;
    const qbuf = new Float32Array(d);

    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);

    // quad unité, partagé par toutes les instances
    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

    // Les `stride` float32 par gaussienne sont lus TELS QUELS depuis le .bin : la
    // disposition du fichier (cf. `layout` du meta) est déjà celle des attributs, aucun
    // ré-empaquetage CPU.
    const gbuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, gbuf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    for (const g of src.groups) {
      for (const c of g.chunks) {
        gl.enableVertexAttribArray(c.loc);
        gl.vertexAttribPointer(c.loc, c.size, gl.FLOAT, false, meta.stride * 4, c.off * 4);
        gl.vertexAttribDivisor(c.loc, 1);
      }
    }
    gl.bindVertexArray(null);

    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    // « under » : dst.rgb += (1−dst.a)·src.rgb ; dst.a += (1−dst.a)·src.a.
    // (1−dst.a) est la transmittance restante ⟹ compositing front-to-back exact, sans tri.
    gl.blendFuncSeparate(gl.ONE_MINUS_DST_ALPHA, gl.ONE,
                         gl.ONE_MINUS_DST_ALPHA, gl.ONE);

    /* ── Cible d'accumulation en flottant 16 bits ────────────────────────────
     * Le framebuffer par defaut d'un canevas est RGBA8 : en compositing
     * front-to-back, `dst.rgb` ET `dst.a` sont donc RE-ARRONDIS a 8 bits apres
     * CHAQUE gaussienne composee. Sur 15 000 gaussiennes dont beaucoup ne
     * portent qu'une opacite infime — une scene photographique entiere, fond et
     * tapis compris —, les contributions de poids faible sont arrondies a zero
     * et la transmittance 1 - dst.a devient grossiere. Resultat : une image plus
     * dure, aux aplats plus francs, qui se lit comme un defaut de rasterisation.
     *
     * Mesure (rocking chair d=1, 256 px, contre une accumulation float64) :
     *   RGBA8   27.6 dB      <- ce que faisait ce module
     *   RGBA16F 70.5 dB      <- invisible
     * et le portage complet mesure dans le navigateur passe de 25.4 dB a la
     * conformite (cf. demo/_rock_check.html).
     *
     * On accumule donc dans une texture RGBA16F, recopiee ensuite sur le
     * canevas. Sans l'extension de rendu flottant (WebGL2 ancien), on retombe
     * sur le chemin direct : degrade, pas casse. */
    const canFloat = gl.getExtension('EXT_color_buffer_half_float')
                  || gl.getExtension('EXT_color_buffer_float');
    let fbo = null, tex = null, texW = 0, texH = 0, blit = null, uTex = null;
    if (canFloat) {
      blit = gl.createProgram();
      gl.attachShader(blit, compile(gl, gl.VERTEX_SHADER, BLIT_VERT));
      gl.attachShader(blit, compile(gl, gl.FRAGMENT_SHADER, BLIT_FRAG));
      gl.linkProgram(blit);
      if (!gl.getProgramParameter(blit, gl.LINK_STATUS)) { blit = null; }
      else {
        uTex = gl.getUniformLocation(blit, 'uTex');
        fbo = gl.createFramebuffer();
        tex = gl.createTexture();
      }
    }

    /** (Re)dimensionne la cible d'accumulation. Renvoie false si elle est
     *  inutilisable — on rend alors directement sur le canevas. */
    function ensureTarget(w, h) {
      if (!fbo) return false;
      if (w === texW && h === texH) return true;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      if (!ok) { fbo = null; return false; }
      texW = w; texH = h;
      return true;
    }

    function resize() {
      let w = Math.max(1, Math.round(canvas.clientWidth * (window.devicePixelRatio || 1)));
      let h = Math.max(1, Math.round(canvas.clientHeight * (window.devicePixelRatio || 1)));
      // Plafond homothetique : c'est le COTE du viewport carre (min(w,h)) qui compte,
      // et le rapport w/h doit survivre sinon la bande noire se decalerait.
      if (MAX_SIDE > 0) {
        const m = Math.min(w, h);
        if (m > MAX_SIDE) {
          const k = MAX_SIDE / m;
          w = Math.max(1, Math.round(w * k));
          h = Math.max(1, Math.round(h * k));
        }
      }
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    }

    /** Viewport CARRÉ centré : le décodeur rend en coordonnées normalisées [0,1]², donc
     *  une image carrée. Étirer ce carré sur un panneau large déformerait l'objet. Le
     *  reste du canevas est laissé au noir — la couleur de fond sur laquelle le décodeur
     *  a été entraîné, donc la bande n'introduit aucune discontinuité visible. */
    function box() {
      // Plus grand rectangle de rapport ASPECT tenant dans le canevas, centre.
      let w = canvas.width, h = Math.round(w / ASPECT);
      if (h > canvas.height) { h = canvas.height; w = Math.round(h * ASPECT); }
      return { w, h, side: Math.min(w, h),
               x: Math.round((canvas.width - w) / 2),
               y: Math.round((canvas.height - h) / 2) };
    }

    /** Point du canevas en fraction [0,1]² → point de l'IMAGE en [0,1]² (défait la
     *  bande noire). Renvoie null hors de l'image, SAUF si `outside` : attraper la bande
     *  ne saisit rien, mais une fois la prise faite le curseur a le droit d'en sortir. */
    function toImage(cx, cy, outside) {
      const b = box();
      const x = (cx * canvas.width - b.x) / b.w;
      const y = (cy * canvas.height - b.y) / b.h;
      if (!outside && (x < 0 || x > 1 || y < 0 || y > 1)) return null;
      return [x, y];
    }

    /** Inverse de `toImage` : point de l'IMAGE → fraction du canevas (pour dessiner). */
    function fromImage(ix, iy) {
      const b = box();
      return [(b.x + ix * b.w) / canvas.width, (b.y + iy * b.h) / canvas.height];
    }

    function render(z) {
      resize();
      const b = box();
      // Alpha = 0 au départ : c'est dst.a qui porte 1 − T pendant l'accumulation. Le
      // canevas est posé sur un fond noir en CSS (celui du décodeur entraîné).
      const off = ensureTarget(b.w, b.h);   // cible 16F, exactement le viewport

      gl.bindFramebuffer(gl.FRAMEBUFFER, off ? fbo : null);
      gl.clearColor(0, 0, 0, 0);
      if (off) {
        gl.viewport(0, 0, b.w, b.h);
        gl.clear(gl.COLOR_BUFFER_BIT);
      } else {
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.viewport(b.x, b.y, b.w, b.h);
      }
      gl.enable(gl.BLEND);
      gl.useProgram(prog);
      for (let i = 0; i < d; i++) qbuf[i] = z[i];
      gl.uniform1fv(uQ, qbuf);
      gl.uniform2f(uSize, b.w, b.h);
      gl.bindVertexArray(vao);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, K);
      gl.bindVertexArray(null);
      if (!off) return;

      // Recopie sur le canevas, dans le carré centré. Melange DESACTIVE : la
      // texture porte le resultat fini, en couleurs premultipliees.
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.disable(gl.BLEND);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.viewport(b.x, b.y, b.w, b.h);
      gl.useProgram(blit);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform1i(uTex, 0);
      gl.bindVertexArray(vao);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.bindVertexArray(null);
    }

    return { gl, render, resize, toImage, fromImage, box, K, data,
             select: (z, x, y, opts) => select(data, meta, z, x, y, opts),
             pointOf: (z, sel) => pointOf(data, meta, z, sel),
             presence: (z, sel) => presence(data, meta, z, sel),
             jacobianAt: (z, x, y, opts) => jacobianAt(data, meta, z, x, y, opts) };
  }

  root.GSSplat = { create, select, pointOf, presence, jacobianAt, shaders, layoutOf,
                   SEL_DEFAULTS, EPS2D, EPS_Z, CUTOFF };
})(typeof window !== 'undefined' ? window : globalThis);

