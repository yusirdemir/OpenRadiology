import { prepareMasks } from "./lesionMasks";
self.onmessage = (event) => {
  try {
    const result = prepareMasks(event.data);
    const buffers = [result.rgba3d!.buffer, result.hu3d!.buffer, result.mask3d!.buffer, ...Object.values(result.planes).flatMap((planes) => planes.map((s) => s.mask!.buffer))];
    self.postMessage({ result }, { transfer: buffers });
  } catch (error) { self.postMessage({ error: String(error) }); }
};
