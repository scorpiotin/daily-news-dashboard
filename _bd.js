const { spawn } = require('child_process');
const fs = require('fs');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9364;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const proc = spawn(CHROME, ['--headless=new','--remote-debugging-port='+PORT,'--window-size=1440,960','--no-first-run','--no-default-browser-check','--user-data-dir=D:/tmp-probe-bd','--hide-scrollbars','about:blank'], {stdio:'ignore'});
  let list = null;
  for (let i=0;i<60;i++){ try{ list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if(list&&list.length) break; }catch(e){} await sleep(300); }
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await new Promise(r => ws.onopen = r);
  const send = (m, p={}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({id:i, method:m, params:p})); });
  const ev = async x => { const r = await send('Runtime.evaluate', {expression:x, returnByValue:true, awaitPromise:true}); return r.result && r.result.result ? r.result.result.value : null; };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {width:1440, height:960, deviceScaleFactor:1, mobile:false});
  await send('Page.navigate', {url:'https://startling-bonbon-a2d0f6.netlify.app/?view=feed'});
  await sleep(6000);
  await ev(`window.scrollTo(0,500)`); await sleep(600);

  console.log('body 末尾标签:', await ev(`[].slice.call(document.body.children).slice(-6).map(function(e){return e.tagName+'#'+(e.id||'')+'.'+(e.className||'')}).join(' | ')`));
  console.log('自定义元素:', await ev(`[].slice.call(document.querySelectorAll('*')).filter(function(e){return e.tagName.indexOf('-')>0}).map(function(e){return e.tagName}).join(',')`));
  console.log('右下角固定元素:', await ev(`(function(){
    return [].slice.call(document.querySelectorAll('*')).filter(function(e){
      var cs=getComputedStyle(e); if(cs.position!=='fixed')return false;
      var r=e.getBoundingClientRect(); return r.width>0 && r.bottom>innerHeight-140 && r.left>innerWidth-400;
    }).map(function(e){var r=e.getBoundingClientRect();return e.tagName+'#'+(e.id||'')+'.'+((e.className||'').toString().slice(0,26))+' ['+Math.round(r.left)+','+Math.round(r.top)+','+Math.round(r.right)+','+Math.round(r.bottom)+'] z='+getComputedStyle(e).zIndex;});
  })()`));
  // 指针命中测试：toTop 中心点上方是谁
  console.log('toTop 中心命中:', await ev(`(function(){
    var b=document.getElementById('toTop'); if(!b)return 'no toTop';
    var r=b.getBoundingClientRect();
    var pts=[[r.left+r.width/2,r.top+r.height/2],[r.left+4,r.bottom-4],[r.right-4,r.bottom-4]];
    return pts.map(function(p){var el=document.elementFromPoint(p[0],p[1]);return el?(el.tagName+'#'+(el.id||'')+'.'+((el.className||'').toString().slice(0,20))):'null'}).join(' || ');
  })()`));
  await ev('window.scrollTo(0,document.documentElement.scrollHeight)'); await sleep(800);
  const shot = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:false, clip:{x:1180, y:820, width:260, height:140, scale:3}});
  if (shot.result && shot.result.data) { fs.writeFileSync('_shots/corner.png', Buffer.from(shot.result.data,'base64')); console.log('角部截图已保存'); }
  ws.close(); proc.kill(); process.exit(0);
})();
