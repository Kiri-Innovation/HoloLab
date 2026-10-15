/*
 * krauss2_lnn_dyn.js — portage JS du Lagrangien appris (cas Krauss 2-segments, d=4,
 * forçage de pression pneumatique).
 *
 * Évalue la MÊME dynamique que `models.py::LNN.accel` du dépôt LaGS, à partir des poids
 * exportés dans `krauss2_lnn.js` (window.KRAUSS2_LNN, produit par
 * demo_Lags/export_krauss2_lnn.py depuis lnn_2seg_lr1e-3_c1_s1_500ep.pt) :
 *
 *     M̃(q) q̈ = ∂T/∂q − ∇V(q) − (∂(M̂q̇)/∂q)q̇ − C(q)q̇ + F_ext + F_P(q,P)
 *
 * avec M̃ = m·M̂ (m = exp(log_m)), V INVEXE (Bregman d'un ICNN composé à un difféo
 * i-ResNet Φ), M̂(q) et C(q) SPD via des facteurs de Cholesky produits par des MLP ELU,
 * et F_P = ∂(Pᵀν_φ)/∂q le forçage des 4 chambres de pression, ν_φ = −(ICNN ∘ Φ_ν)
 * CONCAVE (`models.InvexVolume`, mode 'invex').
 *
 * Deux différences avec `sac_lnn_dyn.js`, dont c'est le pendant :
 *   - **d est générique** (le sac déroulait des 2×2 à la main ; ici tout est en d
 *     quelconque, matrices à plat en row-major) ;
 *   - **la pression** est un chemin de calcul en plus, absent du sac.
 *
 * PyTorch obtient ∂T/∂q, ∂(M̂q̇)/∂q et ∂ν/∂q par autograd ; ici tout est calculé en MODE
 * DIRECT (on propage la valeur ET sa jacobienne d×d le long du réseau). C'est exact,
 * sans graphe, et coûte ~d fois un forward — négligeable à 60 fps.
 *
 * ⚠️ Unité de temps : le LNN a été entraîné avec dt = 1 FRAME, à 59.94 fps (DT =
 * 1001/60000 s). `step()` avance donc d'UNE frame, pas d'une seconde.
 *
 * ⚠️ Unité de pression : le LNN attend p[Pa]/101325 (« atmosphères »), quelle que soit
 * la source. Les curseurs de la démo sont en kPa ⟹ passer par kpaToP(). NE PAS utiliser
 * config.PRESSURE_NORM, qui vaut 1.0 sur la source NPZ (Krauss a déjà normalisé) et
 * enverrait 25 000 au lieu de 0.247.
 *
 * ⚠️ Espaces : le LNN vit dans l'espace BLANCHI u = (z − mean)·W. Les gaussiennes du
 * décodeur vivent dans l'espace latent BRUT z. Utiliser toZ()/toU()/forceToU() pour
 * passer de l'un à l'autre — jamais mélanger les deux.
 *
 * Fidélité vérifiée contre PyTorch par demo_Lags/check_krauss2_lnn.py (jeu de référence
 * dans krauss2_lnn.json, retiré du .js servi au navigateur).
 */
(function (root) {
  'use strict';

  const softplus = x => x > 30 ? x : Math.log1p(Math.exp(x));
  const sigmoid = x => 1 / (1 + Math.exp(-x));

  /** y = W·x + b, W (n×m) en tableau de lignes. */
  function matVec(W, x, b) {
    const n = W.length, out = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const row = W[i];
      let s = b ? b[i] : 0;
      for (let j = 0; j < row.length; j++) s += row[j] * x[j];
      out[i] = s;
    }
    return out;
  }

  /** J_out = W·J, J étant (m×d) aplatie en row-major (longueur d·m). */
  function matJac(W, J, d) {
    const n = W.length, out = new Float64Array(d * n);
    for (let i = 0; i < n; i++) {
      const row = W[i];
      for (let j = 0; j < row.length; j++) {
        const w = row[j];
        if (w === 0) continue;
        for (let k = 0; k < d; k++) out[i * d + k] += w * J[j * d + k];
      }
    }
    return out;
  }

  /** Identité d×d à plat. */
  function eye(d) {
    const I = new Float64Array(d * d);
    for (let i = 0; i < d; i++) I[i * d + i] = 1;
    return I;
  }

  /** Résout A·x = b, A (d×d) à plat. Élimination de Gauss à pivot partiel.
   *  A = m·M̂ + ridge·I est SPD (Cholesky + plancher ε), donc jamais singulière ; le
   *  pivot partiel est là pour le conditionnement, pas pour un cas dégénéré. */
  function solveN(A, b, d) {
    const M = Float64Array.from(A), x = Float64Array.from(b);
    for (let c = 0; c < d; c++) {
      let piv = c, best = Math.abs(M[c * d + c]);
      for (let r = c + 1; r < d; r++) {
        const v = Math.abs(M[r * d + c]);
        if (v > best) { best = v; piv = r; }
      }
      if (piv !== c) {
        for (let k = 0; k < d; k++) {
          const t = M[c * d + k]; M[c * d + k] = M[piv * d + k]; M[piv * d + k] = t;
        }
        const t = x[c]; x[c] = x[piv]; x[piv] = t;
      }
      const p = M[c * d + c];
      for (let r = c + 1; r < d; r++) {
        const f = M[r * d + c] / p;
        if (f === 0) continue;
        for (let k = c; k < d; k++) M[r * d + k] -= f * M[c * d + k];
        x[r] -= f * x[c];
      }
    }
    for (let r = d - 1; r >= 0; r--) {
      let s = x[r];
      for (let k = r + 1; k < d; k++) s -= M[r * d + k] * x[k];
      x[r] = s / M[r * d + r];
    }
    return x;
  }

  // ── Φ : difféomorphisme i-ResNet, valeur + jacobienne ─────────────────────
  // Φ = (I + α_K r_K) ∘ … ∘ (I + α_1 r_1), r = Linear∘tanh∘Linear (spectralement
  // normalisés à l'export ⟹ ici de simples matrices denses).
  function diffeo(blocks, q, d) {
    let x = Float64Array.from(q);
    const Jx = eye(d);                                  // ∂x/∂q, row-major (d×d)
    for (const B of blocks) {
      const t = matVec(B.W1, x, B.b1);                  // (H,)
      const Jt = matJac(B.W1, Jx, d);                   // (H×d)
      const H = t.length;
      const s = new Float64Array(H), Js = new Float64Array(d * H);
      for (let i = 0; i < H; i++) {
        const th = Math.tanh(t[i]);
        const dd = 1 - th * th;                         // tanh'
        s[i] = th;
        for (let k = 0; k < d; k++) Js[i * d + k] = dd * Jt[i * d + k];
      }
      const y = matVec(B.W2, s, B.b2);                  // (d,)
      const Jy = matJac(B.W2, Js, d);                   // (d×d)
      const a = B.alpha;
      for (let i = 0; i < d; i++) x[i] += a * y[i];
      for (let k = 0; k < d * d; k++) Jx[k] += a * Jy[k];
    }
    return { x, J: Jx };
  }

  // ── ICNN : valeur(s) + jacobienne de la dernière couche cachée ─────────────
  // h_{k+1} = softplus(W_z^k x + b_k + W_h^k h_k), W_h ≥ 0 (softplus appliqué à
  // l'export). On propage J = ∂h/∂x en mode direct (cf. models.py::_icnn_grad) et on
  // laisse l'appelant contracter avec le poids de sortie voulu — c'est ce qui permet de
  // servir à la fois le potentiel scalaire (w_out (H,)) et ν_φ à n_c têtes (w_out
  // (n_c,H)) sans repasser dans le réseau une fois par tête.
  function icnnHidden(layers, x, d) {
    let h = null, J = null;
    const I = eye(d);
    for (const L of layers) {
      const out = matVec(L.Wz, x, L.b);                 // passthrough libre en x
      const dout = matJac(L.Wz, I, d);
      if (L.Wh && h) {                                  // récurrence W_h ≥ 0
        const rec = matVec(L.Wh, h, null), dr = matJac(L.Wh, J, d);
        for (let i = 0; i < out.length; i++) {
          out[i] += rec[i];
          for (let k = 0; k < d; k++) dout[i * d + k] += dr[i * d + k];
        }
      }
      const n = out.length;
      const hn = new Float64Array(n), Jn = new Float64Array(d * n);
      for (let i = 0; i < n; i++) {
        const sg = sigmoid(out[i]);                     // softplus'
        hn[i] = softplus(out[i]);
        for (let k = 0; k < d; k++) Jn[i * d + k] = sg * dout[i * d + k];
      }
      h = hn; J = Jn;
    }
    return { h, J };
  }

  /** Contraction d'une sortie ICNN par un vecteur de poids w (H,) → valeur + gradient. */
  function contract(h, J, w, d) {
    let g = 0;
    const grad = new Float64Array(d);
    for (let i = 0; i < h.length; i++) {
      const wi = w[i];
      if (wi === 0) continue;
      g += wi * h[i];
      for (let k = 0; k < d; k++) grad[k] += wi * J[i * d + k];
    }
    return { g, grad };
  }

  // ── Potentiel invexe V et son gradient ────────────────────────────────────
  // V(q) = g(Φ(q)) − g(u_r) − ∇g(u_r)ᵀ(Φ(q) − u_r) + ½ε‖Φ(q) − u_r‖²   (Bregman)
  // ∇V(q) = JΦ(q)ᵀ · [ ∇g(Φ(q)) − ∇g(u_r) + ε(Φ(q) − u_r) ]
  function potential(E, q, d) {
    const P = diffeo(E.diffeo, q, d);
    const H = icnnHidden(E.icnn, P.x, d);
    const G = contract(H.h, H.J, E.w_out, d);
    const eps = E.eps_strong;
    const gu = new Float64Array(d);
    let V = G.g - E.g_rest;
    for (let i = 0; i < d; i++) {
      const du = P.x[i] - E.u_rest[i];
      gu[i] = G.grad[i] - E.grad_rest[i] + eps * du;
      V += -E.grad_rest[i] * du + 0.5 * eps * du * du;
    }
    // chaîne par la TRANSPOSÉE de la jacobienne du difféo
    const grad = new Float64Array(d);
    for (let k = 0; k < d; k++) {
      let s = 0;
      for (let i = 0; i < d; i++) s += P.J[i * d + k] * gu[i];
      grad[k] = s;
    }
    return { V, grad };
  }

  // ── Forçage de pression F_P = ∂(Pᵀν_φ)/∂q ─────────────────────────────────
  // ν_φ = −C(Φ_ν(q)), C l'ICNN à n_c têtes ⟹ Pᵀν_φ = −Pᵀ C, et le gradient se contracte
  // en une seule passe avec le vecteur de poids effectif w_eff = Pᵀ W_out (H,).
  // Le SIGNE est essentiel : ν CONCAVE ⟹ V_eff = V − Pᵀν_φ coercif (équilibre chargé
  // stable). Une ν convexe rendrait l'équilibre instable (cf. models.py::InvexVolume).
  function pressureForce(P, q, p) {
    const d = P.d, nu = P.nu, W = nu.w_out;             // W : (n_c, H)
    const Hh = W[0].length;
    const wEff = new Float64Array(Hh);
    let any = false;
    for (let c = 0; c < p.length; c++) {
      const pc = p[c];
      if (!pc) continue;
      any = true;
      const row = W[c];
      for (let i = 0; i < Hh; i++) wEff[i] += pc * row[i];
    }
    const out = new Float64Array(d);
    if (!any) return out;                               // dépressurisé : F_P = 0 exact
    const Ph = diffeo(nu.diffeo, q, d);
    const Hn = icnnHidden(nu.icnn, Ph.x, d);
    const G = contract(Hn.h, Hn.J, wEff, d);            // ∇_u (Pᵀ C)
    for (let k = 0; k < d; k++) {
      let s = 0;
      for (let i = 0; i < d; i++) s += Ph.J[i * d + k] * G.grad[i];
      out[k] = -s;                                      // ν = −C
    }
    return out;
  }

  // ── Facteur de Cholesky SPD : S(q) = L(q)L(q)ᵀ + εI, plus ∂S/∂q ───────────
  // Sert deux fois : masse M̂(q) (ε=0.1) et dissipation C(q) (ε=c₀, ici 1.0).
  function cholSPD(spec, q, d, wantGrad) {
    const net = spec.net;
    let h = Float64Array.from(q), Jh = eye(d);
    for (let li = 0; li < net.length; li++) {
      const L = net[li];
      const z = matVec(L.W, h, L.b);
      const Jz = matJac(L.W, Jh, d);
      if (li === net.length - 1) { h = z; Jh = Jz; break; }   // dernière couche : linéaire nue
      const n = z.length;
      const hn = new Float64Array(n), Jn = new Float64Array(d * n);
      for (let i = 0; i < n; i++) {
        const v = z[i];
        const e = v > 0 ? v : Math.exp(v) - 1;                // ELU
        const dd = v > 0 ? 1 : e + 1;                         // ELU'
        hn[i] = e;
        for (let k = 0; k < d; k++) Jn[i * d + k] = dd * Jz[i * d + k];
      }
      h = hn; Jh = Jn;
    }
    // vecteur triangulaire inférieur → L (diagonale passée au softplus)
    const Lm = new Float64Array(d * d);
    const dLm = wantGrad ? Array.from({ length: d }, () => new Float64Array(d * d)) : null;
    for (let t = 0; t < spec.tril_i.length; t++) {
      const i = spec.tril_i[t], j = spec.tril_j[t], raw = h[t], isDiag = spec.diag[t];
      Lm[i * d + j] = isDiag ? softplus(raw) : raw;
      if (wantGrad) {
        const dd = isDiag ? sigmoid(raw) : 1;
        for (let k = 0; k < d; k++) dLm[k][i * d + j] = dd * Jh[t * d + k];
      }
    }
    // S = L·Lᵀ + εI
    const S = new Float64Array(d * d);
    for (let i = 0; i < d; i++) {
      for (let j = 0; j <= i; j++) {
        let s = 0;
        for (let k = 0; k <= j; k++) s += Lm[i * d + k] * Lm[j * d + k];
        S[i * d + j] = s; S[j * d + i] = s;
      }
      S[i * d + i] += spec.eps;
    }
    if (!wantGrad) return { S, dS: null };
    // ∂S/∂q_k = (∂L/∂q_k)Lᵀ + L(∂L/∂q_k)ᵀ
    const dS = [];
    for (let k = 0; k < d; k++) {
      const dL = dLm[k], D = new Float64Array(d * d);
      for (let i = 0; i < d; i++) {
        for (let j = 0; j <= i; j++) {
          let s = 0;
          for (let t = 0; t < d; t++) s += dL[i * d + t] * Lm[j * d + t] + Lm[i * d + t] * dL[j * d + t];
          D[i * d + j] = s; D[j * d + i] = s;
        }
      }
      dS.push(D);
    }
    return { S, dS };
  }

  /** y = S·x, S (d×d) à plat. */
  function mul(S, x, d) {
    const out = new Float64Array(d);
    for (let i = 0; i < d; i++) {
      let s = 0;
      for (let j = 0; j < d; j++) s += S[i * d + j] * x[j];
      out[i] = s;
    }
    return out;
  }

  /**
   * Accélération q̈ dans l'espace BLANCHI u, en unités « par frame² ».
   * @param {object} P   poids exportés (window.KRAUSS2_LNN)
   * @param {number[]} q état latent blanchi (d)
   * @param {number[]} v vitesse latente blanchie (d), par frame
   * @param {object} [o] options :
   *   - p  : pressions normalisées p[Pa]/101325 (n_c) — cf. kpaToP()
   *   - F  : force généralisée externe en espace u (d) — prise interactive
   *   - ds : facteur d'échelle sur la dissipation apprise (1 = apprise telle quelle).
   *          Équivalent du knob `LNN_DISS_SCALE` côté Python ; C(q) reste SPD pour tout
   *          ds > 0, donc la dissipation reste physiquement admissible.
   *   - c0 : plancher d'amortissement ADDITIF, en Rayleigh proportionnel à la masse :
   *          C_eff = ds·C(q) + c0·M̃(q). C'est le mode de dissipation par défaut du
   *          pipeline Python (α·M̃), donc SPD par construction. c0 = 0 ⟹ dynamique
   *          strictement celle du checkpoint.
   *   - ms : facteur d'échelle sur la masse (1 = apprise telle quelle).
   */
  function accel(P, q, v, o) {
    const d = P.d, opt = o || {};
    const m = Math.exp(P.metric.log_m) * (opt.ms === undefined ? 1 : opt.ms);
    const Mh = cholSPD(P.metric, q, d, true);          // M̂(q) et ∂M̂/∂q
    const M = Mh.S, dM = Mh.dS;

    const dT = new Float64Array(d);                    // ∂T/∂q_k = ½ q̇ᵀ(∂M̂/∂q_k)q̇
    const pq = new Float64Array(d);                    // Coriolis (∂(M̂q̇)/∂q)q̇
    for (let k = 0; k < d; k++) {
      const Dv = mul(dM[k], v, d);
      let s = 0;
      for (let i = 0; i < d; i++) s += v[i] * Dv[i];
      dT[k] = 0.5 * s;
      for (let i = 0; i < d; i++) pq[i] += Dv[i] * v[k];
    }

    const gV = potential(P.energy, q, d).grad;
    const C = cholSPD(P.rayleigh, q, d, false).S;      // C(q), valeur seule
    const sd = opt.ds === undefined ? 1 : opt.ds;
    const cf = opt.c0 ? opt.c0 * m : 0;                // plancher c0·M̃ = c0·m·M̂
    const Cv = mul(C, v, d), Mv = mul(M, v, d);
    const FP = opt.p ? pressureForce(P, q, opt.p) : null;

    const force = new Float64Array(d);
    for (let i = 0; i < d; i++) {
      force[i] = m * dT[i] - gV[i] - m * pq[i] - (sd * Cv[i] + cf * Mv[i])
        + (opt.F ? opt.F[i] : 0) + (FP ? FP[i] : 0);
    }
    const A = new Float64Array(d * d);
    for (let i = 0; i < d * d; i++) A[i] = m * M[i];
    for (let i = 0; i < d; i++) A[i * d + i] += P.ridge;
    return solveN(A, force, d);
  }

  /** Un pas de RK4 d'UNE frame (l'unité de temps du LNN). Options constantes sur le pas. */
  function step(P, q, v, o, dt) {
    const d = P.d, h = dt === undefined ? P.dt_frames : dt;
    const add = (a, b, s) => {
      const r = new Float64Array(d);
      for (let i = 0; i < d; i++) r[i] = a[i] + s * b[i];
      return r;
    };
    const k1v = accel(P, q, v, o);
    const q2 = add(q, v, h / 2), v2 = add(v, k1v, h / 2);
    const k2v = accel(P, q2, v2, o);
    const q3 = add(q, v2, h / 2), v3 = add(v, k2v, h / 2);
    const k3v = accel(P, q3, v3, o);
    const q4 = add(q, v3, h), v4 = add(v, k3v, h);
    const k4v = accel(P, q4, v4, o);
    const qn = new Float64Array(d), vn = new Float64Array(d);
    for (let i = 0; i < d; i++) {
      qn[i] = q[i] + (h / 6) * (v[i] + 2 * v2[i] + 2 * v3[i] + v4[i]);
      vn[i] = v[i] + (h / 6) * (k1v[i] + 2 * k2v[i] + 2 * k3v[i] + k4v[i]);
    }
    return { q: qn, v: vn };
  }

  // ── conversions espace blanchi u ↔ espace latent brut z ───────────────────
  // u = (z − mean)·W    (vecteurs LIGNE, cf. LatentWhiten)
  function toU(P, z) {
    const d = P.d, W = P.whiten.W, mn = P.whiten.mean, out = new Float64Array(d);
    for (let i = 0; i < d; i++) {
      let s = 0;
      for (let j = 0; j < d; j++) s += (z[j] - mn[j]) * W[j][i];
      out[i] = s;
    }
    return out;
  }
  // z = mean + u·W⁻¹
  function toZ(P, u) {
    const d = P.d, Wi = P.whiten.W_inv, mn = P.whiten.mean, out = new Float64Array(d);
    for (let i = 0; i < d; i++) {
      let s = mn[i];
      for (let j = 0; j < d; j++) s += u[j] * Wi[j][i];
      out[i] = s;
    }
    return out;
  }
  // Une force est un COVECTEUR : F_u = W⁻¹ · F_z  (car ∂z_j/∂u_i = W⁻¹_ij).
  function forceToU(P, Fz) {
    const d = P.d, Wi = P.whiten.W_inv, out = new Float64Array(d);
    for (let i = 0; i < d; i++) {
      let s = 0;
      for (let j = 0; j < d; j++) s += Wi[i][j] * Fz[j];
      out[i] = s;
    }
    return out;
  }

  /** Curseurs en kPa → unité du LNN (p[Pa]/101325). */
  const kpaToP = (P, kpa) => kpa.map(k => k * 1000 / P.p_atm_pa);

  root.Krauss2LNN = {
    accel, step, potential, pressureForce, cholSPD,
    toU, toZ, forceToU, kpaToP,
  };
})(typeof window !== 'undefined' ? window : globalThis);

