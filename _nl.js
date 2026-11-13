const { spawn } = require('child_process');
const fs = require('fs');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9360;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const proc = spawn(CHROME, ['--headless=new','--remote-debugging-port='+PORT,'--window-size=1440,960','--no-first-run','--no-default-browser-check','--user-data-dir=D:/tmp-probe-nl','--hide-scrollbars','about:blank'], {stdio:'ignore'});
  let list = null;
  for (let i=0;i<60;i++){ try{ list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if(list&&list.length) break; }catch(e){} await sleep(300); }
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await new Promise(r => ws.onopen = r);
  const send = (m, p={}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({id:i, method:m, params:p})); });
  const ev = async x => { const r = await send('Runtime.evaluate', {expression:x, returnByValue:true, awaitPromise:true}); return r.result && r.result.result ? r.result.result.value : null; };
  const errors = [];
  await send('Page.enable'); await send('Runtime.enable');
  ws.addEventListener('message', e => {
    const m = JSON.parse(e.data);
    if (m.method === 'Runtime.exceptionThrown') errors.push('EXC ' + (m.params.exceptionDetails.text||''));
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('ERR ' + m.params.args.map(a=>a.value||a.description||'').join(' ').slice(0,120));
  });
  await send('Emulation.setDeviceMetricsOverride', {width:1440, height:960, deviceScaleFactor:1, mobile:false});
  await send('Page.navigate', {url:'https://startling-bonbon-a2d0f6.netlify.app/?view=feed'});
  await sleep(6000);
  const info = await ev(`(function(){
    var cards = document.querySelectorAll('article.card');
    var chips = [].slice.call(document.querySelectorAll('.filter.rail .chip, .chip'));
    var rail  = document.querySelectorAll('.filter.rail .chip');
    return {
      title: document.title,
      dateText: (document.querySelector('.date-line, .hero-date, #dateLine') || {}).textContent || '',
      cards: cards.length,
      chips: chips.length,
      railChips: rail.length,
      railSample: [].slice.call(rail).slice(0,3).map(function(c){return c.textContent.replace(/\\s+/g,'')}),
      summary: (document.getElementById('fSummary')||{}).textContent || '',
      nav: [].slice.call(document.querySelectorAll('.tab')).map(function(t){return t.textContent.trim()+'('+t.getAttribute('aria-current')+')'})
    };
  })()`);
  console.log(JSON.stringify(info, null, 1));
  console.log('控制台错误:', errors.length ? errors.slice(0,5) : '无');
  await ev('window.scrollTo(0,600)');
  await sleep(800);
  const shot = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
  if (shot.result && shot.result.data) { fs.writeFileSync('_shots/netlify.png', Buffer.from(shot.result.data,'base64')); console.log('已截图'); }
  ws.close(); proc.kill(); process.exit(0);
})();
