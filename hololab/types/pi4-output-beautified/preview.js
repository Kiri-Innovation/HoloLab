const byId = (id) => document.getElementById(id);
const canvas = byId("point-cloud");
const status = byId("viewer-status");
const timeline = byId("timeline");
const play = byId("play");
const follow = byId("follow-view");
const reset = byId("reset-view");
const layers = ["cloud", "frame", "tracks", "cameras"].map((name) => byId("show-" + name));
const pointSizeMultiplier = 1.5;

const add = (a, b) => a.map((value, axis) => value + b[axis]);
const scale = (a, factor) => a.map((value) => value * factor);
const subtract = (a, b) => add(a, scale(b, -1));
const dot = (a, b) => a.reduce((sum, value, axis) => sum + value * b[axis], 0);
const cross = ([x, y, z], [u, v, w]) => [y * w - z * v, z * u - x * w, x * v - y * u];
const unit = (a) => scale(a, 1 / (Math.hypot(...a) || 1));

function sibling(base, name) {
  if (typeof name !== "string" || !/^[\w.-]+$/.test(name) || name === "." || name === "..") {
    throw new Error("Invalid scene member");
  }
  const url = new URL(base, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith("/proxy/")) {
    throw new Error("Invalid artifact URL");
  }
  url.pathname = url.pathname.replace(/\/$/, "") + "/" + encodeURIComponent(name);
  return url;
}

async function request(url, signal) {
  const response = await fetch(url, { signal, redirect: "error" });
  if (!response.ok) throw new Error("Scene request failed (" + response.status + ")");
  return response;
}

async function loadScene(base, signal) {
  const manifest = await (await request(sibling(base, "scene.json"), signal)).json();
  const { frames, data } = manifest;
  if (manifest.format !== "pi4_web_scene_v1" || data?.compression !== "gzip"
      || !Number.isSafeInteger(frames) || frames < 1) {
    throw new Error("Unsupported scene manifest");
  }
  const response = await request(sibling(base, data.file), signal);
  const bytes = await new Response(response.body.pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
  if (bytes.byteLength !== data.bytes) throw new Error("Invalid scene binary length");
  function array(name, dtype, Type, shape) {
    const spec = data.arrays?.[name];
    const count = shape.reduce((total, length) => total * length, 1);
    if (!spec || spec.dtype !== dtype || String(spec.shape) !== String(shape)
        || !Number.isSafeInteger(spec.offset) || spec.offset < 0
        || spec.bytes !== count * Type.BYTES_PER_ELEMENT
        || spec.offset + spec.bytes > bytes.byteLength) {
      throw new Error("Invalid scene array " + name);
    }
    // Slice also supports valid packed exports with unaligned offsets.
    return new Type(bytes.slice(spec.offset, spec.offset + spec.bytes));
  }
  const offsets = array("frame_offsets", "<u4", Uint32Array, [frames + 1]);
  if (offsets[0] !== 0 || offsets.some((value, index) => index > 0 && value < offsets[index - 1])) {
    throw new Error("Invalid frame offsets");
  }
  const timestamps = array("timestamps", "<f8", Float64Array, [frames]);
  if (timestamps[0] !== 0 || !(manifest.duration > timestamps[frames - 1])
      || timestamps.some((value, index) => !Number.isFinite(value) || (index > 0 && value <= timestamps[index - 1]))) {
    throw new Error("Invalid scene timeline");
  }
  const framePoints = offsets[frames];
  const queries = manifest.queries ?? 0;
  return {
    manifest,
    offsets,
    timestamps,
    xyz: array("xyz", "<f4", Float32Array, [manifest.points, 3]),
    rgb: array("rgb", "|u1", Uint8Array, [manifest.points, 3]),
    frameXYZ: array("frame_xyz", "<f4", Float32Array, [framePoints, 3]),
    frameRGB: array("frame_rgb", "|u1", Uint8Array, [framePoints, 3]),
    centers: array("camera_centers", "<f4", Float32Array, [frames, 3]),
    frustums: array("camera_frustums", "<f4", Float32Array, [frames, 5, 3]),
    segments: array("segments", "<u4", Uint32Array, [frames]),
    tracks: queries ? array("track_xyz", "<f4", Float32Array, [frames, queries, 3]) : [],
    valid: queries ? array("track_valid", "|u1", Uint8Array, [frames, queries]) : [],
  };
}

function makeRenderer() {
  const gl = canvas.getContext("webgl", { antialias: true });
  if (!gl) throw new Error("WebGL unavailable");
  function shader(type, source) {
    const compiled = gl.createShader(type);
    gl.shaderSource(compiled, source);
    gl.compileShader(compiled);
    if (!gl.getShaderParameter(compiled, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(compiled));
    return compiled;
  }
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, `
    attribute vec3 position, color;
    uniform vec3 eye, right, up, forward;
    uniform float aspect, size, pixelRatio;
    varying vec3 rgb;
    void main() {
      vec3 delta = position - eye;
      float depth = dot(delta, forward);
      gl_Position = vec4(2.54 * dot(delta, right) / aspect,
                         2.54 * dot(delta, up), 1.00002 * depth - 0.004, depth);
      gl_PointSize = clamp(size * pixelRatio * 2.3 / max(depth, 0.05),
                          pixelRatio, 12.0 * pixelRatio);
      rgb = color;
    }
  `));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, `
    precision mediump float;
    uniform bool points;
    varying vec3 rgb;
    void main() {
      if (points && distance(gl_PointCoord, vec2(0.5)) > 0.5) discard;
      gl_FragColor = vec4(rgb, 1.0);
    }
  `));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.clearColor(0.055, 0.082, 0.118, 1);
  const uniform = Object.fromEntries(
    ["eye", "right", "up", "forward", "aspect", "size", "pixelRatio", "points"]
      .map((name) => [name, gl.getUniformLocation(program, name)]),
  );
  const position = gl.getAttribLocation(program, "position");
  const color = gl.getAttribLocation(program, "color");
  const lineBuffer = gl.createBuffer();
  const sceneBuffers = [];
  function buffer(data) {
    const result = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, result);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    sceneBuffers.push(result);
    return result;
  }
  function attribute(location, source, type, normalized) {
    gl.bindBuffer(gl.ARRAY_BUFFER, source);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 3, type, normalized, 0, 0);
  }
  return {
    upload(scene) {
      for (const existing of sceneBuffers.splice(0)) gl.deleteBuffer(existing);
      scene.cloudBuffers = [buffer(scene.xyz), buffer(scene.rgb)];
      scene.frameBuffers = [buffer(scene.frameXYZ), buffer(scene.frameRGB)];
    },
    begin({ eye, right, up, forward }) {
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
      const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      gl.viewport(0, 0, width, height);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.uniform3fv(uniform.eye, eye);
      gl.uniform3fv(uniform.right, right);
      gl.uniform3fv(uniform.up, up);
      gl.uniform3fv(uniform.forward, forward);
      gl.uniform1f(uniform.aspect, width / height);
      gl.uniform1f(uniform.pixelRatio, ratio);
    },
    points(buffers, start, count, size) {
      gl.uniform1i(uniform.points, 1);
      gl.uniform1f(uniform.size, size);
      attribute(position, buffers[0], gl.FLOAT, false);
      attribute(color, buffers[1], gl.UNSIGNED_BYTE, true);
      gl.drawArrays(gl.POINTS, start, count);
    },
    lines(vertices, rgb) {
      gl.uniform1i(uniform.points, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, lineBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.DYNAMIC_DRAW);
      attribute(position, lineBuffer, gl.FLOAT, false);
      gl.disableVertexAttribArray(color);
      gl.vertexAttrib3fv(color, rgb);
      gl.drawArrays(gl.LINES, 0, vertices.length / 3);
    },
  };
}

let renderer, scene, baseUrl, controller;
let frame = 0;
let playing = false;
let animation = 0;
let target, distance, yaw, pitch, orbitBasis;

function resetOrbit() {
  const bounds = scene.manifest.viewBounds ?? scene.manifest.bounds;
  target = scale(add(bounds.min, bounds.max), 0.5);
  distance = Math.max(0.1, Math.hypot(...subtract(bounds.max, bounds.min)) * 1.5);
  const { forward, right, up } = capturedCamera(0);
  orbitBasis = { outward: scale(forward, -1), right, up };
  yaw = 0.17;
  pitch = 0.15;
  follow.setAttribute("aria-pressed", "false");
}

function orbitCamera() {
  const offset = add(
    scale(orbitBasis.outward, Math.cos(yaw) * Math.cos(pitch)),
    add(scale(orbitBasis.right, Math.sin(yaw) * Math.cos(pitch)), scale(orbitBasis.up, Math.sin(pitch))),
  );
  const forward = scale(offset, -1);
  const right = unit(cross(forward, orbitBasis.up));
  return { eye: add(target, scale(offset, distance)), right, up: cross(right, forward), forward };
}

function cameraPoint(frameIndex, corner) {
  const start = frameIndex * 15 + corner * 3;
  return Array.from(scene.frustums.subarray(start, start + 3));
}

function capturedCamera(index) {
  const eye = cameraPoint(index, 0);
  const corners = [1, 2, 3, 4].map((corner) => cameraPoint(index, corner));
  const forward = unit(subtract(scale(corners.reduce(add), 0.25), eye));
  const horizontal = subtract(corners[1], corners[0]);
  const up = unit(cross(horizontal, forward));
  const right = cross(forward, up);
  return { eye, right, up, forward };
}

function trackLines() {
  const vertices = [];
  const queries = scene.manifest.queries ?? 0;
  for (let index = Math.max(1, frame - 11); index <= frame; index++) {
    if (scene.segments[index] !== scene.segments[frame] || scene.segments[index - 1] !== scene.segments[frame]) continue;
    for (let query = 0; query < queries; query++) {
      const end = index * queries + query;
      const start = end - queries;
      if (scene.valid[start] && scene.valid[end]) {
        vertices.push(...scene.tracks.subarray(start * 3, start * 3 + 3),
                      ...scene.tracks.subarray(end * 3, end * 3 + 3));
      }
    }
  }
  return vertices;
}

function cameraLines() {
  const vertices = [];
  for (let index = 1; index < scene.manifest.frames; index++) {
    if (scene.segments[index] === scene.segments[index - 1]) {
      vertices.push(...scene.centers.subarray((index - 1) * 3, (index + 1) * 3));
    }
  }
  const edges = [[0, 1], [0, 2], [0, 3], [0, 4], [1, 2], [2, 3], [3, 4], [4, 1]];
  for (const [start, end] of edges) vertices.push(...cameraPoint(frame, start), ...cameraPoint(frame, end));
  return vertices;
}

function render() {
  if (!scene) return;
  renderer.begin(follow.getAttribute("aria-pressed") === "true" ? capturedCamera(frame) : orbitCamera());
  const size = pointSizeMultiplier * (scene.manifest.pointScale ?? 1);
  if (layers[0].checked) renderer.points(scene.cloudBuffers, 0, scene.manifest.points, size);
  if (layers[1].checked) {
    const start = scene.offsets[frame];
    renderer.points(scene.frameBuffers, start, scene.offsets[frame + 1] - start, size * 1.3);
  }
  if (layers[2].checked) renderer.lines(trackLines(), [0.98, 0.55, 0.25]);
  if (layers[3].checked) renderer.lines(cameraLines(), [0.42, 0.82, 1]);
}

function setFrame(value) {
  frame = Math.max(0, Math.min(scene.manifest.frames - 1, value));
  timeline.value = frame + 1;
  byId("frame-label").value = (frame + 1) + " / " + scene.manifest.frames;
  render();
}

function pause() {
  playing = false;
  cancelAnimationFrame(animation);
  play.textContent = "▶";
  play.setAttribute("aria-label", "Play");
}

function togglePlayback() {
  if (!scene) return;
  if (playing) {
    pause();
    return;
  }
  playing = true;
  play.textContent = "❚❚";
  play.setAttribute("aria-label", "Pause");
  const start = performance.now();
  const first = scene.timestamps[frame];
  function tick(now) {
    const time = (first + (now - start) / 1000) % scene.manifest.duration;
    let low = 0;
    let high = scene.timestamps.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (scene.timestamps[middle] <= time) low = middle + 1;
      else high = middle;
    }
    const next = low - 1;
    if (next !== frame) setFrame(next);
    animation = requestAnimationFrame(tick);
  }
  animation = requestAnimationFrame(tick);
}

play.addEventListener("click", togglePlayback);
timeline.addEventListener("input", () => { pause(); setFrame(Number(timeline.value) - 1); });
for (const layer of layers) layer.addEventListener("change", render);
reset.addEventListener("click", () => { resetOrbit(); render(); });
follow.addEventListener("click", () => {
  follow.setAttribute("aria-pressed", String(follow.getAttribute("aria-pressed") !== "true"));
  render();
});
new ResizeObserver(render).observe(canvas);

canvas.addEventListener("pointerdown", (event) => {
  if (scene && event.button === 0) canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!scene || !canvas.hasPointerCapture(event.pointerId)) return;
  follow.setAttribute("aria-pressed", "false");
  if (event.shiftKey) {
    const { right, up } = orbitCamera();
    const step = distance / Math.max(1, canvas.clientHeight);
    target = add(target, add(scale(right, -event.movementX * step), scale(up, event.movementY * step)));
  } else {
    yaw -= event.movementX * 0.01;
    pitch = Math.max(-1.54, Math.min(1.54, pitch + event.movementY * 0.01));
  }
  render();
});
canvas.addEventListener("wheel", (event) => {
  if (!scene) return;
  event.preventDefault();
  follow.setAttribute("aria-pressed", "false");
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1);
  distance = Math.max(0.015, Math.min(250, distance * Math.exp(delta * 0.001)));
  render();
}, { passive: false });
canvas.addEventListener("keydown", (event) => {
  if (!scene) return;
  if (event.code === "Space") togglePlayback();
  else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    pause();
    setFrame(frame + (event.key === "ArrowLeft" ? -1 : 1));
  } else return;
  event.preventDefault();
});

window.addEventListener("message", async (event) => {
  const message = event.data;
  if (event.source !== parent || event.origin !== location.origin
      || message?.type !== "hololab-viewer-state" || message.viewer_id !== "type-viewer") return;
  const url = message.history_url ?? message.frames?.at(-1)?.proxy_url;
  if (typeof url !== "string" || url === baseUrl) return;
  baseUrl = url;
  controller?.abort();
  controller = new AbortController();
  const { signal } = controller;
  pause();
  scene = null;
  const controls = [play, timeline, reset, follow];
  for (const control of controls) control.disabled = true;
  status.hidden = false;
  status.textContent = "Opening scene…";
  try {
    const loaded = await loadScene(url, signal);
    if (signal.aborted) return;
    renderer ??= makeRenderer();
    renderer.upload(loaded);
    scene = loaded;
    timeline.max = scene.manifest.frames;
    resetOrbit();
    setFrame(0);
    for (const control of controls) control.disabled = false;
    status.hidden = true;
  } catch (error) {
    if (!signal.aborted) {
      scene = null;
      status.textContent = error.message;
    }
  }
});
