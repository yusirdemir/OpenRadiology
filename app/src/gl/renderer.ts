/**
 * WebGL2 renderer for calibrated slices.
 *
 * The whole fluidity argument lives here. A slice arrives once as raw engine
 * values -- Hounsfield units, SUV, MR signal -- and is uploaded to a single
 * channel float texture. Window width and level are shader uniforms, so
 * dragging them is one uniform write per frame: no decode, no request, no
 * copy. Scrolling reuses textures out of a small pool, so moving through a
 * study does not churn GPU memory.
 *
 * Textures are `R32F` even for int16 payloads. Integer textures cannot be
 * linearly filtered and would need a second sampler type and a second program;
 * one code path that can filter is worth a 0.3 ms conversion per slice.
 */
import type { SliceImage } from "../api/types";
import { displayRect, screenPxPerImagePx, type CanvasSize, type ImageGeometry, type ViewState } from "./viewport";

const VERTEX_SHADER = `#version 300 es
in vec2 aPos;
uniform vec4 uRect;      // x, y, width, height in clip space
out vec2 vUv;
void main() {
  vUv = aPos;
  gl_Position = vec4(uRect.xy + aPos * uRect.zw, 0.0, 1.0);
}`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;
precision highp sampler2D;

in vec2 vUv;
out vec4 fragColor;

uniform sampler2D uImage;
uniform float uCenter;
uniform float uWidth;
uniform bool  uInvert;

uniform sampler2D uOverlay;
uniform bool  uHasOverlay;
uniform vec4  uOverlayXf;      // uv = vUv * xy + zw, so a fused series keeps its own grid
uniform float uOverlayCenter;
uniform float uOverlayWidth;
uniform float uOverlayAlpha;
uniform float uOverlayFloor;   // below this fraction the overlay is transparent

float windowed(float value, float center, float width) {
  return clamp((value - (center - width * 0.5)) / max(width, 1e-6), 0.0, 1.0);
}

// Hot-metal ramp for PET. Monotone in luminance so a brighter pixel always
// means a higher value, which a rainbow ramp does not guarantee.
vec3 hotMetal(float t) {
  return clamp(vec3(t * 2.4 - 0.1, t * 2.0 - 0.75, t * 3.6 - 2.6), 0.0, 1.0);
}

void main() {
  float value = texture(uImage, vUv).r;
  float grey = windowed(value, uCenter, uWidth);
  if (uInvert) grey = 1.0 - grey;
  vec3 rgb = vec3(grey);

  if (uHasOverlay) {
    vec2 uv = vUv * uOverlayXf.xy + uOverlayXf.zw;
    if (uv.x >= 0.0 && uv.x <= 1.0 && uv.y >= 0.0 && uv.y <= 1.0) {
      float hot = windowed(texture(uOverlay, uv).r, uOverlayCenter, uOverlayWidth);
      if (hot > uOverlayFloor) {
        float t = (hot - uOverlayFloor) / max(1.0 - uOverlayFloor, 1e-6);
        rgb = mix(rgb, hotMetal(hot), uOverlayAlpha * t);
      }
    }
  }
  fragColor = vec4(rgb, 1.0);
}`;

export interface WindowSetting {
  center: number;
  width: number;
  invert?: boolean;
}

export interface OverlaySetting {
  image: SliceImage;
  center: number;
  width: number;
  alpha: number;
  floor: number;
  /** uv = vUv * [sx, sy] + [ox, oy]; identity means the grids already match. */
  transform: [number, number, number, number];
}

interface PooledTexture {
  texture: WebGLTexture;
  rows: number;
  cols: number;
  lastUsed: number;
}

const POOL_LIMIT = 24;

/** Screen pixels per image pixel past which a sample is individually visible. */
export const PIXEL_INSPECT_SCALE = 4;

export class SliceRenderer {
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private vao: WebGLVertexArrayObject;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private pool = new Map<string, PooledTexture>();
  private clock = 0;
  private linearFiltering: boolean;
  private scratch = new Float32Array(0);
  private disposed = false;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      desynchronized: true,
      powerPreference: "high-performance",
      preserveDrawingBuffer: false,
    });
    if (!gl) throw new Error("WebGL2 is required to display medical images in this application.");
    this.gl = gl;
    this.linearFiltering = gl.getExtension("OES_texture_float_linear") !== null;
    this.program = this.link(VERTEX_SHADER, FRAGMENT_SHADER);
    for (const name of [
      "uRect", "uImage", "uCenter", "uWidth", "uInvert",
      "uOverlay", "uHasOverlay", "uOverlayXf", "uOverlayCenter", "uOverlayWidth", "uOverlayAlpha", "uOverlayFloor",
    ]) {
      this.uniforms[name] = gl.getUniformLocation(this.program, name);
    }

    const vao = gl.createVertexArray();
    if (!vao) throw new Error("Could not allocate a vertex array");
    this.vao = vao;
    gl.bindVertexArray(vao);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(this.program, "aPos");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);

    gl.clearColor(0, 0, 0, 1);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.BLEND);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  }

  // -- setup ------------------------------------------------------------
  private compile(kind: number, source: string): WebGLShader {
    const gl = this.gl;
    const shader = gl.createShader(kind);
    if (!shader) throw new Error("Could not allocate a shader");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(`Shader compilation failed: ${log}`);
    }
    return shader;
  }

  private link(vertex: string, fragment: string): WebGLProgram {
    const gl = this.gl;
    const program = gl.createProgram();
    if (!program) throw new Error("Could not allocate a program");
    const vs = this.compile(gl.VERTEX_SHADER, vertex);
    const fs = this.compile(gl.FRAGMENT_SHADER, fragment);
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const log = gl.getProgramInfoLog(program);
      gl.deleteProgram(program);
      throw new Error(`Program link failed: ${log}`);
    }
    return program;
  }

  /** Match the drawing buffer to the CSS size and device pixel ratio. */
  resize(cssWidth: number, cssHeight: number, dpr = window.devicePixelRatio || 1): boolean {
    const width = Math.max(1, Math.round(cssWidth * dpr));
    const height = Math.max(1, Math.round(cssHeight * dpr));
    if (this.canvas.width === width && this.canvas.height === height) return false;
    this.canvas.width = width;
    this.canvas.height = height;
    return true;
  }

  // -- textures ---------------------------------------------------------
  private asFloat32(pixels: Int16Array | Float32Array): Float32Array {
    if (pixels instanceof Float32Array) return pixels;
    if (this.scratch.length < pixels.length) this.scratch = new Float32Array(pixels.length);
    const out = this.scratch;
    for (let i = 0; i < pixels.length; i += 1) out[i] = pixels[i] as number;
    return out.subarray(0, pixels.length);
  }

  private upload(key: string, image: SliceImage): PooledTexture {
    const gl = this.gl;
    const existing = this.pool.get(key);
    if (existing && existing.rows === image.rows && existing.cols === image.cols) {
      existing.lastUsed = ++this.clock;
      return existing;
    }
    this.evict();
    const texture = existing?.texture ?? gl.createTexture();
    if (!texture) throw new Error("Could not allocate a texture");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(
      gl.TEXTURE_2D, 0, gl.R32F, image.cols, image.rows, 0, gl.RED, gl.FLOAT,
      this.asFloat32(image.pixels),
    );
    const entry: PooledTexture = { texture, rows: image.rows, cols: image.cols, lastUsed: ++this.clock };
    this.pool.set(key, entry);
    return entry;
  }

  private evict(): void {
    if (this.pool.size < POOL_LIMIT) return;
    let oldestKey: string | null = null;
    let oldest = Infinity;
    for (const [key, entry] of this.pool) {
      if (entry.lastUsed < oldest) {
        oldest = entry.lastUsed;
        oldestKey = key;
      }
    }
    if (oldestKey === null) return;
    const victim = this.pool.get(oldestKey);
    if (victim) this.gl.deleteTexture(victim.texture);
    this.pool.delete(oldestKey);
  }

  /** Textures are keyed by what uniquely identifies the pixels, not by slot. */
  static keyFor(image: SliceImage): string {
    return `${image.seriesUid}|${image.plane}|${image.index}|${image.mipMm}`;
  }

  /** Drop cached textures for a series that is no longer on screen. */
  releaseSeries(seriesUid: string): void {
    for (const [key, entry] of [...this.pool]) {
      if (key.startsWith(`${seriesUid}|`)) {
        this.gl.deleteTexture(entry.texture);
        this.pool.delete(key);
      }
    }
  }

  // -- drawing ----------------------------------------------------------
  draw(image: SliceImage, view: ViewState, window: WindowSetting, overlay?: OverlaySetting): void {
    if (this.disposed) return;
    const gl = this.gl;
    const canvas: CanvasSize = { width: this.canvas.clientWidth, height: this.canvas.clientHeight };
    const geometry: ImageGeometry = { rows: image.rows, cols: image.cols, mmPerPx: image.mmPerPx };

    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (canvas.width <= 0 || canvas.height <= 0) return;

    const rect = displayRect(geometry, canvas, view);
    const scale = screenPxPerImagePx(geometry, canvas, view);
    /*
     * Filtering policy.
     *
     * Nearest-neighbour is right only when the reader is deliberately
     * inspecting individual samples: at that magnification a smoothed pixel
     * would invite reading detail the scanner did not record. Everywhere else
     * it is actively harmful. A thin-slice low-dose CT carries real noise
     * around 70 HU standard deviation in soft tissue, and replicating each
     * sample into a 1.65 x 1.65 block of screen pixels turns that noise into a
     * coarse speckle that buries low-contrast findings. So: nearest only past
     * PIXEL_INSPECT_SCALE, linear below it.
     */
    const inspecting = scale >= PIXEL_INSPECT_SCALE;
    const filter = inspecting || !this.linearFiltering ? gl.NEAREST : gl.LINEAR;

    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);

    const main = this.upload(SliceRenderer.keyFor(image), image);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, main.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.uniform1i(this.uniforms.uImage ?? null, 0);

    if (overlay) {
      const fused = this.upload(`overlay:${SliceRenderer.keyFor(overlay.image)}`, overlay.image);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, fused.texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, this.linearFiltering ? gl.LINEAR : gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, this.linearFiltering ? gl.LINEAR : gl.NEAREST);
      gl.uniform1i(this.uniforms.uOverlay ?? null, 1);
      gl.uniform1i(this.uniforms.uHasOverlay ?? null, 1);
      gl.uniform4fv(this.uniforms.uOverlayXf ?? null, overlay.transform);
      gl.uniform1f(this.uniforms.uOverlayCenter ?? null, overlay.center);
      gl.uniform1f(this.uniforms.uOverlayWidth ?? null, overlay.width);
      gl.uniform1f(this.uniforms.uOverlayAlpha ?? null, overlay.alpha);
      gl.uniform1f(this.uniforms.uOverlayFloor ?? null, overlay.floor);
    } else {
      gl.uniform1i(this.uniforms.uHasOverlay ?? null, 0);
    }

    gl.uniform4f(
      this.uniforms.uRect ?? null,
      (rect.x / canvas.width) * 2 - 1,
      1 - (rect.y / canvas.height) * 2,
      (rect.width / canvas.width) * 2,
      -(rect.height / canvas.height) * 2,
    );
    gl.uniform1f(this.uniforms.uCenter ?? null, window.center);
    gl.uniform1f(this.uniforms.uWidth ?? null, window.width);
    gl.uniform1i(this.uniforms.uInvert ?? null, window.invert ? 1 : 0);

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindVertexArray(null);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    const gl = this.gl;
    for (const entry of this.pool.values()) gl.deleteTexture(entry.texture);
    this.pool.clear();
    gl.deleteProgram(this.program);
    gl.deleteVertexArray(this.vao);
  }
}
