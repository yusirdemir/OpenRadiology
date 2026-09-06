self.onmessage=(event: MessageEvent<string>)=>{
  const raw=Uint8Array.from(atob(event.data),(c)=>c.charCodeAt(0));
  const view=new DataView(raw.buffer);const pixels=new Float32Array(raw.length/2);
  for(let i=0;i<pixels.length;i++)pixels[i]=view.getInt16(i*2,true);
  self.postMessage(pixels,{transfer:[pixels.buffer]});
};
export {};
