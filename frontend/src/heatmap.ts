import type { HeatmapBuffer } from "./heatmap-buffer.js";

const VERT_SRC = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

const FRAG_SRC = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D uHeatmap;
uniform float uWritePosition;
uniform float uMaxValue;

vec3 colormap(float t) {
  // blue → cyan → green → yellow → red
  vec3 a = vec3(0.0, 0.0, 0.5);
  vec3 b = vec3(0.0, 1.0, 1.0);
  vec3 c = vec3(0.0, 1.0, 0.0);
  vec3 d = vec3(1.0, 1.0, 0.0);
  vec3 e = vec3(1.0, 0.0, 0.0);
  if (t < 0.25) return mix(a, b, t * 4.0);
  if (t < 0.5)  return mix(b, c, (t - 0.25) * 4.0);
  if (t < 0.75) return mix(c, d, (t - 0.5)  * 4.0);
  return mix(d, e, (t - 0.75) * 4.0);
}

void main() {
  // Ring-buffer unwrap: shift UV x by write position so oldest data is on the left
  float col = fract(vUv.x + uWritePosition);
  // Flip y so higher prices are at top
  float row = 1.0 - vUv.y;
  float val = texture(uHeatmap, vec2(col, row)).r;
  float maxV = max(uMaxValue, 0.001);
  float logVal = log(1.0 + val) / log(1.0 + maxV);
  if (logVal < 0.01) {
    fragColor = vec4(0.05, 0.05, 0.05, 1.0);
  } else {
    fragColor = vec4(colormap(logVal), 1.0);
  }
}`;

function compileShader(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, src);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error("Shader compile error: " + gl.getShaderInfoLog(shader));
  }
  return shader;
}

export class HeatmapRenderer {
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private texture: WebGLTexture;
  private uWritePosition: WebGLUniformLocation;
  private uMaxValue: WebGLUniformLocation;
  private buffer: HeatmapBuffer;
  private maxValue = 1;

  constructor(canvas: HTMLCanvasElement, buffer: HeatmapBuffer) {
    this.buffer = buffer;
    const gl = canvas.getContext("webgl2");
    if (!gl) throw new Error("WebGL 2 not supported");
    this.gl = gl;

    // Compile program
    const vert = compileShader(gl, gl.VERTEX_SHADER, VERT_SRC);
    const frag = compileShader(gl, gl.FRAGMENT_SHADER, FRAG_SRC);
    const prog = gl.createProgram()!;
    gl.attachShader(prog, vert);
    gl.attachShader(prog, frag);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error("Program link error: " + gl.getProgramInfoLog(prog));
    }
    this.program = prog;

    // Fullscreen quad
    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);
    const vbo = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    // R32F texture
    this.texture = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(
      gl.TEXTURE_2D, 0, gl.R32F,
      buffer.width, buffer.height, 0,
      gl.RED, gl.FLOAT, buffer.data
    );

    this.uWritePosition = gl.getUniformLocation(prog, "uWritePosition")!;
    this.uMaxValue = gl.getUniformLocation(prog, "uMaxValue")!;

    this.resize(canvas);
    const ro = new ResizeObserver(() => this.resize(canvas));
    ro.observe(canvas.parentElement!);
  }

  resize(canvas: HTMLCanvasElement) {
    const dpr = window.devicePixelRatio || 1;
    const parent = canvas.parentElement!;
    const w = parent.clientWidth || parent.offsetWidth;
    const h = parent.clientHeight || parent.offsetHeight;
    if (w === 0 || h === 0) return;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    this.gl.viewport(0, 0, canvas.width, canvas.height);
    this.draw();
  }

  /** Upload one column that was just written and redraw. */
  updateColumn(col: number) {
    const gl = this.gl;
    const { height, data } = this.buffer;

    // Update max value from the new column
    for (let row = 0; row < height; row++) {
      const v = data[col * height + row];
      if (v > this.maxValue) this.maxValue = v;
    }
    // Slowly decay max so color scale adapts
    this.maxValue *= 0.9995;

    // Upload the single column
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    const colData = data.subarray(col * height, col * height + height);
    gl.texSubImage2D(
      gl.TEXTURE_2D, 0,
      col, 0, 1, height,
      gl.RED, gl.FLOAT, colData
    );

    this.draw();
  }

  /** Upload entire texture and redraw (used after loadHistorical). */
  uploadAll() {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texImage2D(
      gl.TEXTURE_2D, 0, gl.R32F,
      this.buffer.width, this.buffer.height, 0,
      gl.RED, gl.FLOAT, this.buffer.data
    );
    let maxVal = 1;
    for (const v of this.buffer.data) if (v > maxVal) maxVal = v;
    this.maxValue = maxVal;
    this.draw();
  }

  draw() {
    const gl = this.gl;
    gl.useProgram(this.program);
    gl.uniform1f(this.uWritePosition, this.buffer.writePositionNorm);
    gl.uniform1f(this.uMaxValue, this.maxValue);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}
