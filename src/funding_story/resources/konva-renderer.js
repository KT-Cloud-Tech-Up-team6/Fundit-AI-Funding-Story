/* Shared headless/browser renderer. Ported from templates/design/konva-renderer.ts.
 * Never shrink fonts, truncate copy, or move unrelated template nodes. */
function textAttrs(n) {
  const pad = n.sideBorders?.paddingX ?? 0;
  return {id:n.id,x:n.x+pad,y:n.y,width:n.width-2*pad,height:n.height,text:n.text,
    fontFamily:n.fontFamily,fontSize:n.fontSize,fontStyle:[n.italic?'italic':'',n.fontWeight].filter(Boolean).join(' '),
    lineHeight:n.lineHeight,letterSpacing:n.letterSpacing,align:n.align,verticalAlign:n.verticalAlign??'top',
    fill:n.fill,wrap:n.wrap??'word',ellipsis:false};
}
function layoutBlock(block) {
  const nodes=structuredClone(block.nodes), issues=[];
  for(const flow of block.textFlows??[]) {
    let x=flow.x;
    for(const id of flow.nodes) {
      const n=nodes.find(n=>n.id===id);
      if(!n) throw new Error('Missing flow node: '+id);
      const t=new Konva.Text({...textAttrs(n),width:undefined,height:undefined,wrap:'none'});
      n.x=x;n.y=flow.y;n.width=t.width();n.wrap='none';
      x+=n.width+flow.gap;t.destroy();
    }
    if(x-flow.gap>flow.x+flow.width+.5) issues.push({id:flow.nodes.join('+'),reason:'inline width',actual:x-flow.gap-flow.x,limit:flow.width});
  }
  const metrics=[];
  for(const n of nodes.filter(n=>n.kind==='text')) {
    const a=textAttrs(n),t=new Konva.Text({...a,height:undefined});
    const natural=new Konva.Text({...a,width:undefined,height:undefined,wrap:'none'});
    const lines=Math.round(t.height()/(n.fontSize*n.lineHeight));
    const rule=n.copyFit??{};
    if(t.height()>n.height+.5) issues.push({id:n.id,reason:'height',actual:t.height(),limit:n.height});
    if(n.wrap==='none' && natural.width()>a.width+.5) issues.push({id:n.id,reason:'width',actual:natural.width(),limit:a.width});
    if(rule.lines && lines!==rule.lines) issues.push({id:n.id,reason:'lines',actual:lines,limit:rule.lines});
    if(rule.maxLines && lines>rule.maxLines) issues.push({id:n.id,reason:'maxLines',actual:lines,limit:rule.maxLines});
    metrics.push({id:n.id,x:n.x,y:n.y,width:n.width,fontSize:n.fontSize,lines,text:n.text,height:t.height()});
    t.destroy();natural.destroy();
  }
  return {nodes,metrics,issues};
}
window.measureScene=scene=>scene.blocks.map(b=>({id:b.id,...layoutBlock(b)}));
window.renderBlock=async (block,sources)=>{
  const layout=layoutBlock(block);
  const stage=new Konva.Stage({container:'stage',width:block.width,height:block.height});
  const layer=new Konva.Layer();stage.add(layer);
  try {
    for(const n of layout.nodes) {
      const common={id:n.id,x:n.x,y:n.y,width:n.width,height:n.height};
      let shape;
      if(n.kind==='text') {
        shape=new Konva.Text(textAttrs(n));
        if(n.sideBorders) {
          const b=n.sideBorders;
          for(const x of [n.x,n.x+n.width]) layer.add(new Konva.Line({points:[x,n.y+b.insetY,x,n.y+n.height-b.insetY],stroke:b.color,strokeWidth:b.width}));
        }
      } else if(n.kind==='rect') {
        shape=new Konva.Rect({...common,fill:n.fadeToTransparent?undefined:n.fill,cornerRadius:n.radius,stroke:n.stroke,strokeWidth:n.strokeWidth??0,...(n.fadeToTransparent?{fillLinearGradientStartPoint:{x:0,y:0},fillLinearGradientEndPoint:{x:0,y:n.height},fillLinearGradientColorStops:[0,n.fill,1,'rgba(0,0,0,0)']}:{})});
      } else if(n.kind==='line') {
        shape=new Konva.Line({...common,points:n.points,fill:n.closed?n.fill:undefined,stroke:n.fill,strokeWidth:n.strokeWidth,closed:n.closed});
      } else if(n.pending || !n.assetId) {
        shape=new Konva.Rect({...common,fill:'#F2F2F4',cornerRadius:n.radius??0});
      } else {
        if(!sources[n.assetId]) throw new Error('Missing image: '+n.assetId);
        const img=await new Promise((resolve,reject)=>{const i=new Image();i.onload=()=>resolve(i);i.onerror=()=>reject(new Error('Image load: '+n.assetId));i.src=sources[n.assetId];});
        const crop=n.sourceCrop?{x:n.sourceCrop.x*img.width,y:n.sourceCrop.y*img.height,width:n.sourceCrop.width*img.width,height:n.sourceCrop.height*img.height}:undefined;
        const sw=crop?.width??img.width,sh=crop?.height??img.height;
        const scale=(n.fit==='contain'?Math.min(n.width/sw,n.height/sh):Math.max(n.width/sw,n.height/sh))*(n.zoom??1);
        const w=sw*scale,h=sh*scale,r=Math.min(n.radius??0,n.width/2,n.height/2);
        shape=new Konva.Group({...common,clipFunc(ctx){ctx.beginPath();ctx.roundRect(0,0,n.width,n.height,r);ctx.closePath();}});
        shape.add(new Konva.Image({image:img,x:(n.width-w)*n.focalX,y:(n.height-h)*n.focalY,width:w,height:h,...(crop?{crop}:{})}));
      }
      layer.add(shape);
    }
    layer.draw();
    return {png:stage.toDataURL({pixelRatio:1}),layout:{metrics:layout.metrics,issues:layout.issues}};
  } finally {stage.destroy();}
};
