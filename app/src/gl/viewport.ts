/**
 * Screen geometry for a calibrated image.
 *
 * Everything is derived from millimetres, never from pixel counts. A coronal
 * reformat of a 1 mm x 0.76 mm study is anisotropic; laying it out by pixel
 * count would stretch the patient. So the image is placed by its physical size
 * -- `cols * mmPerPx[1]` wide, `rows * mmPerPx[0]` tall -- and the same
 * transform is inverted to turn a mouse position back into a native row and
 * column. Coordinates that reach the evidence ledger come from here.
 */
export interface ImageGeometry {
  rows: number;
  cols: number;
  /** [vertical, horizontal] millimetres per pixel. */
  mmPerPx: [number, number];
}

export interface CanvasSize {
  width: number;
  height: number;
}

export interface ViewState {
  /** 1 = fit to the canvas. */
  zoom: number;
  /** Pan in CSS pixels, from the centred position. */
  panX: number;
  panY: number;
}

export const IDENTITY_VIEW: ViewState = { zoom: 1, panX: 0, panY: 0 };

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function physicalSize(image: ImageGeometry): { width: number; height: number } {
  return { width: image.cols * image.mmPerPx[1], height: image.rows * image.mmPerPx[0] };
}

/** Screen pixels per millimetre at which the whole image just fits. */
export function fitScale(image: ImageGeometry, canvas: CanvasSize): number {
  const mm = physicalSize(image);
  if (mm.width <= 0 || mm.height <= 0 || canvas.width <= 0 || canvas.height <= 0) return 1;
  return Math.min(canvas.width / mm.width, canvas.height / mm.height);
}

/** Where the image lands on the canvas, in CSS pixels. */
export function displayRect(image: ImageGeometry, canvas: CanvasSize, view: ViewState): Rect {
  const mm = physicalSize(image);
  const scale = fitScale(image, canvas) * view.zoom;
  const width = mm.width * scale;
  const height = mm.height * scale;
  return {
    x: (canvas.width - width) / 2 + view.panX,
    y: (canvas.height - height) / 2 + view.panY,
    width,
    height,
  };
}

/**
 * Screen pixels per image pixel, on the axis where the image is smallest.
 *
 * The attestation policy uses this: below one screen pixel per image pixel the
 * image is being downsampled and a small finding may not have survived to the
 * screen, so displaying it attests nothing.
 */
export function screenPxPerImagePx(image: ImageGeometry, canvas: CanvasSize, view: ViewState): number {
  const rect = displayRect(image, canvas, view);
  if (image.rows <= 0 || image.cols <= 0) return 0;
  return Math.min(rect.width / image.cols, rect.height / image.rows);
}

/** Canvas position (CSS pixels, relative to the canvas) to native row and column. */
export function canvasToImage(
  image: ImageGeometry,
  canvas: CanvasSize,
  view: ViewState,
  x: number,
  y: number,
): { row: number; col: number; inside: boolean } {
  const rect = displayRect(image, canvas, view);
  const col = rect.width > 0 ? ((x - rect.x) / rect.width) * image.cols : 0;
  const row = rect.height > 0 ? ((y - rect.y) / rect.height) * image.rows : 0;
  return { row, col, inside: col >= 0 && col < image.cols && row >= 0 && row < image.rows };
}

/** Native row and column to canvas position (CSS pixels). */
export function imageToCanvas(
  image: ImageGeometry,
  canvas: CanvasSize,
  view: ViewState,
  row: number,
  col: number,
): { x: number; y: number } {
  const rect = displayRect(image, canvas, view);
  return {
    x: rect.x + (col / Math.max(image.cols, 1)) * rect.width,
    y: rect.y + (row / Math.max(image.rows, 1)) * rect.height,
  };
}

/** Zoom about a fixed canvas point, so the pixel under the cursor stays put. */
export function zoomAbout(
  image: ImageGeometry,
  canvas: CanvasSize,
  view: ViewState,
  factor: number,
  anchorX: number,
  anchorY: number,
  limits: { min: number; max: number } = { min: 0.25, max: 40 },
): ViewState {
  const zoom = Math.min(limits.max, Math.max(limits.min, view.zoom * factor));
  if (zoom === view.zoom) return view;
  const before = canvasToImage(image, canvas, view, anchorX, anchorY);
  const zoomed: ViewState = { ...view, zoom };
  const after = canvasToImage(image, canvas, zoomed, anchorX, anchorY);
  const rect = displayRect(image, canvas, zoomed);
  return {
    zoom,
    panX: view.panX + ((after.col - before.col) / Math.max(image.cols, 1)) * rect.width,
    panY: view.panY + ((after.row - before.row) / Math.max(image.rows, 1)) * rect.height,
  };
}

/** Millimetres between two native pixel positions, using this image's calibration. */
export function distanceMm(image: ImageGeometry, a: { row: number; col: number }, b: { row: number; col: number }): number {
  const dy = (b.row - a.row) * image.mmPerPx[0];
  const dx = (b.col - a.col) * image.mmPerPx[1];
  return Math.hypot(dx, dy);
}
