/** Multi-label direct volume ray casting in DICOM patient coordinates. */
import type { LesionSegmentation, VolumePreview, Voxel } from "../api/types";
export interface VolumeLesion { id: string; findingId: string; color: [number,number,number]; segmentation: LesionSegmentation }
export interface VolumeSettings { yaw:number; pitch:number; zoom:number; context:number; active:string; moving:boolean; target:Voxel|null }
const vertex = `#version 300 es
in vec2 aPos;out vec2 uv;
void main(){uv=aPos;gl_Position=vec4(aPos*2.0-1.0,0,1);}`;
function fragment(slots:number) {
  const declarations=Array.from({length:slots},(_,i)=>`uniform sampler3D uLabel${i};`).join('\n');
  const samples=Array.from({length:slots},(_,i)=>`
    if(uCount>${i}){
      vec3 local=(lps-uOrigin[${i}])/uSpacing[${i}];
      if(all(greaterThanEqual(local,vec3(-0.5)))&&all(lessThan(local,uSize[${i}]-0.5))){
        vec2 label=texelFetch(uLabel${i},ivec3(floor(local+0.5)),0).rg;
        if(label.r>0.5){
          bool selected=uSelected[${i}];
          // Heat is confined to the mask: denser tissue burns brighter and more opaque.
          float heat=label.g;
          float alpha=1.-exp(-stepSize*(selected?70.+90.*heat:18.+14.*heat));
          vec3 hue=mix(uColor[${i}]*0.85,mix(uColor[${i}],vec3(1.,0.96,0.8),heat*heat),selected?1.:0.3);
          if(!selected)hue=mix(vec3(dot(hue,vec3(0.2126,0.7152,0.0722))),hue,0.55)*0.55;
          if(uPick){color=vec4(float(${i+1})/255.,0,0,1);return;}
          // Selected geometry takes precedence where annotations overlap.
          if(selected||!hit){rgb=hue;opacity=alpha;glow=selected?uColor[${i}]*(0.35+1.2*heat)*stepSize*1.6:vec3(0.);hit=true;}
        }
      }
    }`).join('\n');
  return `#version 300 es
precision highp float;precision highp sampler3D;
in vec2 uv;out vec4 color;
uniform sampler3D uCt;
${declarations}
uniform vec3 uShape,uHalf,uCtOrigin,uCtSpacing,uTarget;
uniform vec3 uOrigin[${slots}],uSpacing[${slots}],uSize[${slots}],uColor[${slots}];
uniform bool uSelected[${slots}];
uniform float uYaw,uPitch,uZoom,uAspect,uContext;
uniform int uSteps,uCount;uniform bool uPick;
void main(){
 vec3 target=(uTarget+0.5)/uShape*2.*uHalf-uHalf;
 vec3 eye=target+vec3(sin(uYaw)*cos(uPitch),-cos(uYaw)*cos(uPitch),sin(uPitch))*3.;
 vec3 dir=normalize(target-eye),right=normalize(cross(dir,vec3(0,0,1))),up=cross(right,dir);
 vec2 p=(uv*2.-1.)*vec2(uAspect,1.)/uZoom;
 vec3 origin=eye+right*p.x+up*p.y;
 vec3 safeDir=mix(vec3(1e-7),dir,greaterThan(abs(dir),vec3(1e-7)));
 vec3 a=(-uHalf-origin)/safeDir,b=(uHalf-origin)/safeDir;
 vec3 n=min(a,b),f=max(a,b);
 float enter=max(0.,max(n.x,max(n.y,n.z))),leave=min(f.x,min(f.y,f.z));
 vec3 bg=vec3(0.018,0.028,0.040)+vec3(0.016)*uv.y;
 if(leave<=enter){color=uPick?vec4(0,0,0,1):vec4(bg,1);return;}
 float stepSize=(leave-enter)/float(uSteps);
 vec4 accum=vec4(0);
 for(int i=0;i<1024;i++){
   if(i>=uSteps||(!uPick&&accum.a>0.992))break;
   vec3 pos=origin+dir*(enter+(float(i)+0.5)*stepSize);
   vec3 native=(pos+uHalf)/(2.*uHalf)*uShape-0.5;
   vec3 lps=uCtOrigin+native*uCtSpacing;
   vec3 ctSize=vec3(textureSize(uCt,0));
   vec3 ctUv=(native/max(uShape-1.,vec3(1.))*(ctSize-1.)+0.5)/ctSize;
   float hu=texture(uCt,ctUv).r;
   float tissue=smoothstep(-700.,-180.,hu),bone=smoothstep(200.,1000.,hu);
   float opacity=(tissue*0.65+bone*1.6)*uContext*stepSize;
   vec3 rgb=mix(vec3(0.24,0.36,0.43),vec3(0.75,0.80,0.79),bone);
   bool hit=false;vec3 glow=vec3(0.);
   ${samples}
   float alpha=clamp(opacity,0.,1.);
   // Emission is added through the translucent chest so the lesion shines from inside.
   accum.rgb+=(1.-accum.a)*(alpha*rgb+glow);accum.a+=(1.-accum.a)*alpha;
 }
 color=uPick?vec4(0,0,0,1):vec4(accum.rgb+(1.-accum.a)*bg,1.);
}`;
}
export class VolumeRenderer {
  private gl:WebGL2RenderingContext;
  private program:WebGLProgram;
  private vao:WebGLVertexArrayObject;
  private buffer:WebGLBuffer;
  private textures:WebGLTexture[]=[];
  private uniforms=new Map<string,WebGLUniformLocation|null>();
  private preview:VolumePreview|null=null;
  private shape:Voxel=[1,1,1];private half:Voxel=[1,1,1];
  private ids:string[]=[];
  private labels:VolumeLesion[]=[];
  private linear:boolean;
  readonly capacity:number;
  constructor(private canvas:HTMLCanvasElement){
    const gl=canvas.getContext('webgl2',{alpha:false,antialias:false,preserveDrawingBuffer:false});
    if(!gl)throw new Error('3D görünüm için WebGL2 gerekli');
    this.gl=gl;this.linear=Boolean(gl.getExtension('OES_texture_float_linear'));
    this.capacity=Math.min(24,gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS)-1);
    const shaders=[vertex,fragment(this.capacity)].map((text,i)=>{
      const shader=gl.createShader(i===0?gl.VERTEX_SHADER:gl.FRAGMENT_SHADER)!;
      gl.shaderSource(shader,text);gl.compileShader(shader);
      if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(shader)??'Shader error');
      return shader;
    });
    this.program=gl.createProgram()!;shaders.forEach((s)=>gl.attachShader(this.program,s));gl.linkProgram(this.program);shaders.forEach((s)=>gl.deleteShader(s));
    if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(this.program)??'Link error');
    this.vao=gl.createVertexArray()!;this.buffer=gl.createBuffer()!;gl.bindVertexArray(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([0,0,1,0,0,1,1,1]),gl.STATIC_DRAW);
    const attr=gl.getAttribLocation(this.program,'aPos');gl.enableVertexAttribArray(attr);gl.vertexAttribPointer(attr,2,gl.FLOAT,false,0,0);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT,1);
  }
  private u(name:string){if(!this.uniforms.has(name))this.uniforms.set(name,this.gl.getUniformLocation(this.program,name));return this.uniforms.get(name)!;}
  private texture(slot:number,shape:Voxel,pixels:Float32Array|Uint8Array,label=false){
    const gl=this.gl;
    if(Math.max(...shape)>gl.getParameter(gl.MAX_3D_TEXTURE_SIZE))throw new Error('Native maske GPU doku sınırını aşıyor');
    if(this.textures[slot])gl.deleteTexture(this.textures[slot]!);
    const tex=gl.createTexture()!;this.textures[slot]=tex;gl.activeTexture(gl.TEXTURE0+slot);gl.bindTexture(gl.TEXTURE_3D,tex);
    const filter=label||!this.linear?gl.NEAREST:gl.LINEAR;
    gl.texParameteri(gl.TEXTURE_3D,gl.TEXTURE_MIN_FILTER,filter);gl.texParameteri(gl.TEXTURE_3D,gl.TEXTURE_MAG_FILTER,filter);
    for(const key of [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T,gl.TEXTURE_WRAP_R])gl.texParameteri(gl.TEXTURE_3D,key,gl.CLAMP_TO_EDGE);
    gl.texImage3D(gl.TEXTURE_3D,0,label?gl.RG8:gl.R32F,shape[2],shape[1],shape[0],0,label?gl.RG:gl.RED,label?gl.UNSIGNED_BYTE:gl.FLOAT,pixels);
  }
  setContext(preview:VolumePreview,pixels:Float32Array){
    this.preview=preview;this.shape=[...preview.native_shape_zyx].reverse() as Voxel;
    const physical=this.shape.map((n,i)=>n*preview.spacing_zyx[2-i]!);const longest=Math.max(...physical);
    this.half=physical.map((s)=>s/longest) as Voxel;
    this.texture(0,preview.shape_zyx,pixels);
    for(let i=0;i<this.capacity;i++)this.texture(i+1,[1,1,1],new Uint8Array(2),true);
    this.ids=[];
  }
  setLesions(labels:VolumeLesion[]){
    const valid=labels.filter((l)=>l.segmentation.rgba3d&&l.segmentation.voxels>0);
    if(valid.length>this.capacity)throw new Error(`${valid.length} maske, bu GPU'nun ${this.capacity} eşzamanlı maske kapasitesini aşıyor`);
    for(let i=0;i<valid.length;i++){
      const item=valid[i]!,seg=item.segmentation;
      if(this.preview?.frame_uid&&seg.frame_uid&&this.preview.frame_uid!==seg.frame_uid)throw new Error('Farklı hasta koordinat çerçeveleri birleştirilemez');
      const key=`${item.id}:${seg.id}`;
      if(this.ids[i]!==key){this.texture(i+1,seg.mask_shape_zyx,seg.rgba3d!,true);this.ids[i]=key;}
    }
    this.labels=valid;
  }
  draw(settings:VolumeSettings,pick=false){
    if(!this.preview)return;const gl=this.gl;
    const scale=Math.min(devicePixelRatio,settings.moving?0.6:1.0);
    const w=Math.max(1,Math.round(this.canvas.clientWidth*scale)),h=Math.max(1,Math.round(this.canvas.clientHeight*scale));
    if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
    gl.viewport(0,0,w,h);gl.useProgram(this.program);gl.bindVertexArray(this.vao);
    this.textures.forEach((texture,i)=>{gl.activeTexture(gl.TEXTURE0+i);gl.bindTexture(gl.TEXTURE_3D,texture);});
    gl.uniform1i(this.u('uCt'),0);
    gl.uniform3fv(this.u('uShape'),this.shape);gl.uniform3fv(this.u('uHalf'),this.half);
    gl.uniform3fv(this.u('uCtOrigin'),this.preview.origin_lps);gl.uniform3fv(this.u('uCtSpacing'),[...this.preview.spacing_zyx].reverse());
    const target=settings.target?settings.target.map((p,i)=>(p-this.preview!.origin_lps[i]!)/this.preview!.spacing_zyx[2-i]!):this.shape.map((n)=>(n-1)/2);
    gl.uniform3fv(this.u('uTarget'),target);
    for(let i=0;i<this.capacity;i++){
      gl.uniform1i(this.u(`uLabel${i}`),i+1);
      const label=this.labels[i];if(!label)continue;
      const seg=label.segmentation;
      gl.uniform3fv(this.u(`uOrigin[${i}]`),seg.mask_origin_lps);
      gl.uniform3fv(this.u(`uSpacing[${i}]`),[...seg.spacing_zyx].reverse());
      gl.uniform3fv(this.u(`uSize[${i}]`),[...seg.mask_shape_zyx].reverse());
      gl.uniform3fv(this.u(`uColor[${i}]`),label.color);
      gl.uniform1i(this.u(`uSelected[${i}]`),label.findingId===settings.active?1:0);
    }
    gl.uniform1i(this.u('uCount'),this.labels.length);gl.uniform1i(this.u('uPick'),pick?1:0);
    gl.uniform1f(this.u('uYaw'),settings.yaw);gl.uniform1f(this.u('uPitch'),settings.pitch);gl.uniform1f(this.u('uZoom'),settings.zoom);
    gl.uniform1f(this.u('uAspect'),w/h);gl.uniform1f(this.u('uContext'),settings.context);
    gl.uniform1i(this.u('uSteps'),settings.moving?160:640);
    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
  }
  pick(x:number,y:number,settings:VolumeSettings):string|null{
    this.draw({...settings,moving:false},true);
    const gl=this.gl;const pixel=new Uint8Array(4);
    const px=Math.floor(x/this.canvas.clientWidth*this.canvas.width),py=this.canvas.height-1-Math.floor(y/this.canvas.clientHeight*this.canvas.height);
    gl.readPixels(px,py,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);this.draw(settings);
    return this.labels[pixel[0]!-1]?.findingId??null;
  }
  dispose(){const gl=this.gl;this.textures.forEach((t)=>gl.deleteTexture(t));gl.deleteBuffer(this.buffer);gl.deleteVertexArray(this.vao);gl.deleteProgram(this.program);}
}
