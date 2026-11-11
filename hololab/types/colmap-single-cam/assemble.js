// Preview adapter only: preserve world-to-camera geometry, invent no time or points.
// IDs/parameter counts match ColmapUtil/src/codec/schema/colmap.js.
const models = [
  ['SIMPLE_PINHOLE', 3], ['PINHOLE', 4], ['SIMPLE_RADIAL', 4], ['RADIAL', 5],
  ['OPENCV', 8], ['OPENCV_FISHEYE', 8], ['FULL_OPENCV', 12], ['FOV', 5],
  ['SIMPLE_RADIAL_FISHEYE', 4], ['RADIAL_FISHEYE', 5], ['THIN_PRISM_FISHEYE', 12],
];
const fields = ['model', 'width', 'height', 'params', 'qvec', 'tvec'];

function require(condition, message) {
  if (!condition) throw new Error(message);
}

function validate(data, name) {
  const fail = message => `${name}: ${message}`;
  require(data && typeof data === 'object' && !Array.isArray(data)
    && Object.keys(data).length === fields.length && fields.every(key => Object.hasOwn(data, key)),
  fail(`expected exactly ${fields.join(', ')}`));
  const model = models.findIndex(([name]) => name === data.model);
  require(model >= 0, fail('unsupported COLMAP camera model'));
  for (const key of ['width', 'height']) {
    require(Number.isSafeInteger(data[key]) && data[key] > 0, fail(`${key} must be a positive safe integer`));
  }
  for (const [key, size] of [['params', models[model][1]], ['qvec', 4], ['tvec', 3]]) {
    require(Array.isArray(data[key]) && data[key].length === size
      && data[key].every(value => typeof value === 'number' && Number.isFinite(value)),
    fail(`${key} must contain ${size} finite numbers`));
  }
  require(data.params[0] > 0 && (![1, 4, 5, 6, 7, 10].includes(model) || data.params[1] > 0),
    fail('focal lengths must be positive'));
  require(Math.abs(Math.hypot(...data.qvec) - 1) < 1e-6,
    fail('qvec must be a unit world-to-camera quaternion (w,x,y,z)'));
  return model;
}

// A small fixed-size COLMAP writer; all values are little endian.
function writer(size) {
  const buffer = new ArrayBuffer(size), view = new DataView(buffer);
  let offset = 0;
  return {
    u32(value) { view.setUint32(offset, value, true); offset += 4; },
    u64(value) { view.setBigUint64(offset, BigInt(value), true); offset += 8; },
    f64(value) { view.setFloat64(offset, value, true); offset += 8; },
    bytes(value) { new Uint8Array(buffer, offset, value.length).set(value); offset += value.length; },
    buffer,
  };
}

export function assemble(entries) {
  require(entries.length > 0 && entries.length < 2 ** 31, 'Expected a nonempty camera array');
  const rows = entries.map(({name, camera}) => {
    require(typeof name === 'string' && name.length > 0 && !/[\x00\r\n]/.test(name), 'Invalid camera member name');
    return {name, camera, model: validate(camera, name), encoded: new TextEncoder().encode(name)};
  });
  const cameras = writer(8 + rows.reduce((size, row) => size + 24 + 8 * row.camera.params.length, 0));
  const images = writer(8 + rows.reduce((size, row) => size + 73 + row.encoded.length, 0));
  cameras.u64(rows.length); images.u64(rows.length);
  const centers = [];
  rows.forEach(({camera: c, model, encoded}, index) => {
    const id = index + 1;
    cameras.u32(id); cameras.u32(model); cameras.u64(c.width); cameras.u64(c.height);
    c.params.forEach(cameras.f64);
    images.u32(id); c.qvec.forEach(images.f64); c.tvec.forEach(images.f64);
    images.u32(id); images.bytes(encoded); images.bytes(new Uint8Array(1)); images.u64(0);
    // Camera centers are -R^T t; qvec/tvec in images.bin remain unmodified.
    const [w, x, y, z] = c.qvec, [tx, ty, tz] = c.tvec;
    centers.push([
      -((1 - 2*y*y - 2*z*z)*tx + 2*(x*y + w*z)*ty + 2*(x*z - w*y)*tz),
      -(2*(x*y - w*z)*tx + (1 - 2*x*x - 2*z*z)*ty + 2*(y*z + w*x)*tz),
      -(2*(x*z + w*y)*tx + 2*(y*z - w*x)*ty + (1 - 2*x*x - 2*y*y)*tz),
    ]);
  });
  const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  for (const center of centers) for (let axis = 0; axis < 3; axis++) {
    low[axis] = Math.min(low[axis], center[axis]); high[axis] = Math.max(high[axis], center[axis]);
  }
  const center = low.map((value, axis) => (value + high[axis]) / 2);
  const radius = centers.reduce((radius, position) => Math.max(radius,
    Math.hypot(...position.map((value, axis) => value - center[axis]))), 0);
  return {
    files: [
      {name: 'cameras.bin', blob: new Blob([cameras.buffer])},
      {name: 'images.bin', blob: new Blob([images.buffer])},
      {name: 'points3D.bin', blob: new Blob([new ArrayBuffer(8)])},
    ],
    count: rows.length,
    view: {v: 2, mode: 'orbit', yaw: 2.365, pitch: .485, fov: 60,
      orbitTarget: center, orbitRadius: Math.max(.2, radius) * 3},
  };
}
