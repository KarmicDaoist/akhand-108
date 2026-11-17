import { spawn } from "node:child_process";
import { chromium } from "playwright";
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const feed=spawn(process.execPath,["tools/feed.mjs"],{stdio:["pipe","ignore","inherit"]});
const b=await chromium.launch({headless:true});
try{
  await sleep(2500);
  // demo
  let c=await b.newContext({viewport:{width:390,height:844},deviceScaleFactor:2}); let p=await c.newPage(); const errs=[];
  p.on("pageerror",e=>errs.push(String(e))); p.on("console",m=>m.type()==="error"&&errs.push(m.text()));
  await p.goto("http://127.0.0.1:8787/?demo=1"); await sleep(7000);
  console.log("demo badge", await p.evaluate(()=>document.getElementById("demoBadge").getAttribute("display")), await p.textContent("#status"), await p.evaluate(()=>window.__akhand.view()));
  await p.screenshot({path:"qa/demo-390x844.png"}); await c.close();
  // reduced motion
  c=await b.newContext({viewport:{width:390,height:844},reducedMotion:"reduce"}); p=await c.newPage();
  p.on("pageerror",e=>errs.push(String(e)));
  await p.goto("http://127.0.0.1:8787/"); await sleep(6000);
  console.log("reduced: view", await p.evaluate(()=>window.__akhand.view()), "animName", await p.evaluate(()=>getComputedStyle(document.querySelector(".spin")).animationName)); await c.close();
  // polling fallback
  c=await b.newContext({viewport:{width:390,height:844}}); p=await c.newPage();
  p.on("pageerror",e=>errs.push(String(e)));
  await p.addInitScript(()=>{ window.WebSocket=function(){ throw new Error("blocked"); }; });
  await p.goto("http://127.0.0.1:8787/"); await sleep(9000);
  console.log("polling: view", await p.evaluate(()=>window.__akhand.view()), "polling", await p.evaluate(()=>window.__akhand.S.polling), await p.textContent("#nShloka"));
  await c.close();
  console.log("errors", errs);
} finally { await b.close(); feed.kill(); }
