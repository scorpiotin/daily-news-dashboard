const { spawn } = require('child_process');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9362;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const SIZES = [[1440,960],[1440,760],[1280,800]];
(async () => {
  const proc = spawn(CHROME, ['--headless=new','--remote-debugging-port='+PORT,'--window-size=1440,960','--no-first-run','--no-default-browser-check','--user-data-dir=D:/tmp-probe-ov','--hide-scrollbars','about:blank'], {stdio:'ignore'});
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
  for (const [w,h] of SIZES) {
    await send('Emulation.setDeviceMetricsOverride', {width:w, height:h, deviceScaleFactor:1, mobile:false});
    await send('Page.navigate', {url:'https://startling-bonbon-a2d0f6.netlify.app/?view=feed'});
    await sleep(5200);
    await ev(`document.documentElement.style.scrollBehavior='auto';window.scrollTo(0,document.documentElement.scrollHeight);window.dispatchEvent(new Event('scroll'))`);
    await sleep(1000);
    const r = await ev(`(function(){
      var out = {vw:innerWidth, vh:innerHeight};
      // 返回顶部按钮
      var tops = [].slice.call(document.querySelectorAll('button,a')).filter(function(b){
        var t=(b.textContent||'').trim(); var a=(b.getAttribute('aria-label')||'')+(b.title||'');
        return /顶部|top/i.test(a+t) || b.classList.contains('to-top') || b.id==='toTop';
      });
      out.toTop = tops.map(function(b){var r=b.getBoundingClientRect();return {id:b.id||b.className, l:Math.round(r.left), t:Math.round(r.top), r:Math.round(r.right), b:Math.round(r.bottom), v:getComputedStyle(b).visibility};});
      // netlify 徽标
      var nl = [].slice.call(document.querySelectorAll('a,iframe,div')).filter(function(e){
        return /powered by netlify/i.test(e.textContent||'') || /netlify/i.test(e.id||'') || /netlify/i.test(e.className||'');
      }).filter(function(e){var r=e.getBoundingClientRect(); return r.width>0 && r.width<220 && r.bottom>innerHeight-120;});
      out.netlify = nl.map(function(e){var r=e.getBoundingClientRect();var cs=getComputedStyle(e);return {tag:e.tagName, id:e.id||'', cls:(e.className||'').toString().slice(0,40), l:Math.round(r.left), t:Math.round(r.top), r:Math.round(r.right), b:Math.round(r.bottom), pos:cs.position, z:cs.zIndex};});
      // 右列最后一块
      var chips=[].slice.call(document.querySelectorAll('.filter.rail .chip'));
      if(chips.length){var c=chips[chips.length-1].getBoundingClientRect(); out.lastChip={t:Math.round(c.top), b:Math.round(c.bottom), l:Math.round(c.left), r:Math.round(c.right)};}
      out.chips = chips.length;
      return out;
    })()`);
    console.log(JSON.stringify(r));
  }
  ws.close(); proc.kill(); process.exit(0);
})();
