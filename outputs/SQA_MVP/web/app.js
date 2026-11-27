"use strict";

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
const textValue = value => esc(value === "" || value === null || value === undefined ? "—" : value);
const names = {home:"项目总览",metrics:"项目与指标",pr:"PR 规范审计",bugs:"Bug 审计",tasks:"整改与验证",
  imports:"数据导入",admin:"账号权限管理",owner:"负责人",reviewer:"协同审核",member:"成员"};
const libraries = {"bug-reports":"Bug 报告",culture:"业务案例期刊",crashes:"现网崩溃专项",
  "pr-contributions":"PR 检视贡献看板","pr-density":"PR 缺陷密度"};
// Route IDs keep existing data and links stable while the navigation follows the platform structure.
const navigation = [
  {label:"总览",children:[
    {id:"home",label:"项目总览"},
    {label:"项目质量看板",children:[
      {id:"feature-board",label:"Feature 看板",pending:"接入 TP 需求与 Feature 阶段状态，核对项目、版本及完成证据。"},
      {id:"bug-board",label:"Bug 看板"},
      {id:"quality-board",label:"质量看板（阶段指标进展）"}
    ]}
  ]},
  {id:"metrics",label:"项目与指标"},
  {label:"质量管理专项",children:[
    {id:"bugs",label:"Bug 审计"},
    {id:"pr",label:"PR 规范审计"},
    {id:"pr-contributions",label:"PR 检视贡献看板"},
    {id:"pr-density",label:"PR 缺陷密度"},
    {id:"crashes",label:"现网崩溃专项"}
  ]},
  {label:"质量报告",children:[
    {id:"report-assessment",label:"项目质量评估报告",pending:"汇集项目阶段指标、评审结论和签核证据，建立正式报告索引。"},
    {id:"report-risk",label:"项目风险报告",pending:"建立项目风险分级、责任人、措施和验证结论的报告视图。"},
    {id:"report-feature",label:"项目 Feature 报告",pending:"接入 Feature 基线、交付状态及验证证据后生成报告。"},
    {id:"bug-reports",label:"Bug 报告"},
    {id:"report-daily",label:"Daily 报告"},
    {id:"report-regression",label:"回归通过率报告"},
    {id:"report-weekly",label:"质量周报"}
  ]},
  {label:"质量文化建设",children:[
    {id:"culture-exhibition",label:"质量展览馆",pending:"从已复核、已脱敏案例中选择展陈内容。"},
    {id:"culture-cases",label:"质量案例库"},
    {id:"culture",label:"业务案例期刊"},
    {id:"culture-ideas",label:"质量理念期刊",pending:"确认理念期刊材料、编辑责任人及审核发布流程。"},
    {id:"culture-standards",label:"质量规范"},
    {label:"业务知识社区",children:[
      {id:"community-driver",label:"驱动组",pending:"确认驱动组知识条目和维护责任人后接入。"},
      {id:"community-framework",label:"框架组",pending:"确认框架组知识条目和维护责任人后接入。"},
      {id:"community-web",label:"WEB 组",pending:"确认 WEB 组知识条目和维护责任人后接入。"},
      {id:"community-ui",label:"UI 组",pending:"确认 UI 组知识条目和维护责任人后接入。"},
      {id:"community-sqa",label:"SQA 组",pending:"确认 SQA 组知识条目和维护责任人后接入。"}
    ]}
  ]},
  {label:"质量改进",children:[
    {id:"retrospectives",label:"质量回溯管理",pending:"在整改与独立验证基础上补充 RCA、CAPA 效果验证及复发跟踪。"},
    {id:"tasks",label:"整改与验证"}
  ]},
  {label:"后台支持",children:[
    {id:"sources",label:"数据源平台清单"},
    {id:"team",label:"SQA 团队人员清单"},
    {id:"admin",label:"账号权限管理"},
    {id:"metric-catalog",label:"指标集汇总清单"},
    {id:"imports",label:"数据导入"},
    {id:"governance",label:"口径与证据复核"},
    {id:"controlled-documents",label:"受控文件清单"}
  ]}
];
function leaves(nodes) {
  return nodes.flatMap(node => node.children ? leaves(node.children) : [node]);
}
const routes = Object.fromEntries(leaves(navigation).map(node => [node.id,node]));
const ownerRoutes = new Set(["crashes","bug-reports","report-weekly","culture-cases","culture",
  "sources","admin","metric-catalog","imports","pr-contributions","pr-density"]);
const reviewRoutes = new Set(["pilot-metrics","bugs","pr"]);
function canOpen(id) {
  if (!session.user || (!routes[id] && id !== "pilot-metrics")) return false;
  if (!session.governance && ["governance","controlled-documents"].includes(id)) return false;
  if (!session.governance && id === "metrics") return ["owner","reviewer"].includes(session.user.role);
  if (ownerRoutes.has(id)) return own();
  if (reviewRoutes.has(id)) return ["owner","reviewer"].includes(session.user.role);
  return true;
}
function navigationHtml(nodes, depth = 0) {
  return nodes.map(node => {
    if (node.children) {
      const children = navigationHtml(node.children,depth + 1);
      if (!children) return "";
      return `<details class="${depth ? "nav-branch" : "nav-group"}"${node.label === "总览" ? " open" : ""}>
        <summary>${esc(node.label)}</summary><div class="nav-children">${children}</div></details>`;
    }
    return canOpen(node.id) ? `<button type="button" data-page="${node.id}"${depth ? "" : ' class="nav-root"'}>${esc(node.label)}</button>` : "";
  }).join("");
}
const states = {open:["待整改","yellow"],submitted:["待验证","blue"],verified:["验证通过","green"],
  closed:["已关闭","green"],staged:["待发布","blue"],published:["已发布","green"],failed:["失败","red"],
  withdrawn:["已撤回","yellow"]};
const lampLabels = {green:["绿","green"],yellow:["黄","yellow"],red:["红","red"],"":["待补证","yellow"]};
const actions = {create:"创建",submit:"提交证据",verify:"验证通过",reject:"退回整改",
  assign_verifier:"改派验证人",close:"关闭"};
const fileFields = {metrics:{backup:"看板全量备份 JSON"}, pr:{summary:"Release Note 汇总 CSV",
  items:"Release Note PR 明细 CSV",matches:"PR 匹配结果 CSV",audit:"逐规则审计 CSV"},bugs:{snapshot:"TP 缺陷原始 CSV"}};
const kindNames = {metrics:"指标配置与快照",pr:"Release Note 与 PR 审计",bugs:"RID Bug 快照"};
let session = {};
let page = "home";
let prVersion;
let sourceProject = "Race3S";
let sourceStage = "";
let libraryTimer;
let trendObserver;
let toastTimer;
let renderCount = 0;

function tag(label, color = "") {
  return `<span class="tag ${color}">${esc(label)}</span>`;
}
function stateTag(state) {
  return tag(...(states[state] || [state]));
}
function lamp(value) {
  return tag(...(lampLabels[value] || lampLabels[""]));
}
function button(label, action, quiet = false) {
  return `<button type="button" class="small ${quiet ? "quiet" : ""}" data-action="${esc(action)}">${esc(label)}</button>`;
}
function heading(title, subtitle) {
  return `<header class="hero"><h1>${esc(title)}</h1><p class="muted">${esc(subtitle)}</p></header>`;
}
function card(label, value, hint) {
  return `<section class="card"><div>${esc(label)}</div><strong>${textValue(value)}</strong><small>${esc(hint)}</small></section>`;
}
function table(headers, rows) {
  if (!rows.length) return '<p class="muted">没有符合当前条件的记录。</p>';
  return `<div class="table-wrap"><table><thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(row => `<tr>${row.map(value => `<td>${value}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function empty(kind) {
  return `<section class="panel"><p>${esc(kind)}尚无已发布批次。</p>
    ${session.user.role === "owner" ? button("打开数据导入", "open-imports") : ""}</section>`;
}
function listOptions(values, selected = "") {
  return values.map(([value, label]) => `<option value="${esc(value)}"${String(value) === String(selected) ? " selected" : ""}>${esc(label)}</option>`).join("");
}
function field(label, name, value = "", opts = {}) {
  const {type = "text", required = false, max = 2000} = opts;
  const attributes = `name="${esc(name)}" ${required ? "required" : ""} maxlength="${max}"`;
  return type === "textarea"
    ? `<label>${esc(label)}<textarea ${attributes}>${esc(value)}</textarea></label>`
    : `<label>${esc(label)}<input ${attributes} type="${esc(type)}" value="${esc(value)}"></label>`;
}
function select(label, name, options, selected = "") {
  return `<label>${esc(label)}<select name="${esc(name)}" aria-label="${esc(label)}">${listOptions(options, selected)}</select></label>`;
}
function own() { return session.user?.role === "owner"; }

async function api(path, data) {
  const request = data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json",
    "X-SQA-Request":"pilot","X-SQA-CSRF":session.csrf || ""},body:JSON.stringify(data)};
  const response = await fetch("/api/" + path, request);
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !["login","setup"].includes(path)) {
      session = await api("session");
      renderAuth();
    }
    throw new Error(body.error || "操作失败");
  }
  return body;
}
function notify(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3800);
}
function onForm(form, handler) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const error = form.querySelector(".error");
    error.hidden = true;
    submit.disabled = true;
    try { await handler(new FormData(form)); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { submit.disabled = false; }
  });
}
function dialog(title, fields, label, handler) {
  $("dialog-content").innerHTML = `<h2>${esc(title)}</h2><form class="form-grid">${fields}
    <div class="error" role="alert" hidden></div><div class="actions"><button type="submit">${esc(label)}</button>
    <button type="button" class="quiet" data-close>取消</button></div></form>`;
  $("dialog-content").querySelector("[data-close]").onclick = () => $("dialog").close();
  onForm($("dialog-content").querySelector("form"), async data => {
    await handler(data);
    $("dialog").close();
    await show(page);
    notify("已保存");
  });
  $("dialog").showModal();
}
function renderAuth() {
  trendObserver?.disconnect();
  $("shell").hidden = true;
  $("auth").hidden = false;
  $("auth").innerHTML = `<section class="panel"><span class="brand-mark" aria-hidden="true">Q</span>
    <h1>质量管理平台</h1><p class="muted">Race 3S · RID · 2.57.64</p>
    <h2>${session.setup ? "创建本机负责人账号" : "登录质量工作台"}</h2>
    <form class="form-grid"><label>账号<input name="name" autocomplete="username" maxlength="64" required
    ${session.setup ? 'value="曾建芬"' : ""}></label>
    <label>密码<input name="password" type="password" autocomplete="${session.setup ? "new-password" : "current-password"}"
    minlength="${session.setup ? 11 : 1}" maxlength="128" required></label>
    ${session.setup ? '<p class="meta">密码至少 11 个字符，由负责人本机设置。</p>' : ""}
    <div class="error" role="alert" hidden></div><button type="submit">${session.setup ? "创建并登录" : "登录"}</button></form></section>`;
  onForm($("auth").querySelector("form"), async data => {
    const result = await api(session.setup ? "setup" : "login", Object.fromEntries(data));
    session = {...session, ...result, setup:false};
    bootShell();
  });
}
function bootShell() {
  $("auth").hidden = true;
  $("auth").textContent = "";
  $("shell").hidden = false;
  $("identity").textContent = session.user.name + " · " + names[session.user.role];
  $("nav").innerHTML = navigationHtml(navigation);
  $("nav").onclick = event => {
    const target = event.target.closest("[data-page]");
    if (target) show(target.dataset.page);
  };
  show("home");
}

async function home(title = "项目总览") {
  const [data, board] = await Promise.all([api("dashboard"),
    api("release-board").catch(() => null)]);
  const b = data.bugs;
  const chartFields = b ? [["待解决",b.pending],["待验证",b.testing],["待复现",b.reproduce],
    ["Verified + Fixed",b.verified_fixed],["其他有效状态",b.effective-b.pending-b.testing-b.reproduce-b.verified_fixed]] : [];
  const taskOpen = Object.entries(data.tasks).filter(([key]) => key !== "closed").reduce((n,[,v]) => n + v,0);
  const portfolio = board?.source ? `<section class="panel"><h2>项目阶段质量总览</h2>
    <p class="meta">${esc(board.source.name)} · 源文件更新 ${esc(board.source.updated.slice(0,10))}
    · ${board.projects.length} 个项目 · 指标库 ${board.source.library_count} 条来源记录</p>
    ${board.error ? `<p class="notice caution">${esc(board.error)}，当前显示上次有效数据。</p>` : ""}
    ${table(["项目","ES","EP","FEP","RID","OTA","源表绿 / 黄 / 红","有源证据",""],board.projects.map(p => [
      esc(p.name),...["ES","EP","FEP","RID","OTA"].map(stage=>p.counts[stage] ?? 0),
      ["green","yellow","red"].map(color=>p.lamps[color] ?? 0).join(" / "),p.with_evidence,
      button("查看项目","source-project:"+p.name,true)]))}
    <p class="meta">灯色为工程源表的历史记录。阶段实绩和签核按各版本证据核对。</p></section>
    <h2>Race 3S · 2.57.64 试点快照</h2>` : "";
  const html = heading(title, "查看工程看板、阶段评审数据及试点记录。") + stageTrendPanel(board?.stage_reviews) + portfolio +
    `<div class="notice caution">${tag("待补证","yellow")} <strong>2.57.64 版本实绩与 RID 正式签核</strong>。历史快照和阶段 Bug 数据用于对账。</div>
    <div class="cards">${card("已独立复核指标",data.evidence_review?.verified ?? 0,"按版本、阶段和截止时间核对")}
    ${card("RID 指标配置",data.metric_count, data.snapshot_count === null ? "待导入" : data.snapshot_count + " 份历史快照")}
    ${card("版本 PR 条目",data.pr_count, data.pr_rules === null ? "待导入" : data.pr_rules + " 条规则明细")}
    ${card("RID 待解决缺陷",b?.pending,b ? "Blocking " + b.blocking + " · Critical " + b.critical : "待导入")}
    ${card("未关闭整改",taskOpen,session.user.role === "member" ? "我的任务" : "试点任务")}</div>
    <div class="grid"><section class="panel"><h2>RID 缺陷状态</h2>
    ${b ? `<p class="meta">Race 3 / 3S RID 阶段 · 截至 ${esc(data.bug_cutoff)} · ${b.effective} 条有效缺陷</p>
      ${chartFields.map(([name,value]) => `<div class="chart-row"><span>${esc(name)}</span>
      <meter min="0" max="${Math.max(b.effective,1)}" value="${value}" aria-label="${esc(name)}"></meter><strong>${value}</strong></div>`).join("")}
      <p class="meta">管理关闭率 ${b.closure === null ? "—" : b.closure.toFixed(2) + "%"}。阶段统计覆盖两项目，构建号待补证。</p>`
      : '<p class="muted">发布阶段缺陷快照后显示数据。</p>'}</section>
    <section class="panel"><h2>数据来源</h2>${table(["来源","当前批次"],Object.entries(data.batches).map(([k,v]) =>
      [esc(kindNames[k]),v === null ? tag("待导入") : tag("#" + v,"blue")]))}
      ${own() ? button("打开数据导入","open-imports",true) : ""}</section></div>`;
  return {html,mount() {
    mountStageTrends(board?.stage_reviews);
    $("content").onclick = event => {
      const action = event.target.closest("[data-action]")?.dataset.action || "";
      if (action.startsWith("source-project:")) {
        sourceProject = action.slice(15);
        sourceStage = "";
        show("metrics");
      } else if (action === "open-imports") show("imports");
    };
  }};
}

const severitySeries = [
  {label:"B · Blocking",key:"b",color:"#a33245",shape:"circle"},
  {label:"C · Critical",key:"c",color:"#9a6500",shape:"diamond"},
  {label:"M · Major",key:"m",color:"#125da8",shape:"square"},
  {label:"N · Normal",key:"n",color:"#6e43a3",shape:"triangle"}
];
function trendRows(data) {
  return [...(data?.checkpoints || [])].sort((a,b)=>a.date.localeCompare(b.date));
}
function stageDeltaPanel(current, previous) {
  if (!previous) return `<section class="stage-delta" aria-label="与上一阶段对比"><h4>与上一阶段对比</h4>
    <p class="meta">${esc(current.label)} 是首个已录入评审阶段，无上一阶段可比较。</p></section>`;
  const valid = value => typeof value === "number" && Number.isFinite(value);
  const value = (number, precision, unit) => valid(number) ? number.toFixed(precision)+unit : "待补证";
  const change = (now, before, precision, unit) => {
    if (!valid(now) || !valid(before)) return "待补证";
    const delta = Number((now-before).toFixed(precision));
    return (delta > 0 ? "+" : "")+delta.toFixed(precision)+unit+(delta === 0 ? "（持平）" : "");
  };
  const fields = [
    ["DI",previous.di,current.di,1,"",""],
    ["研发关闭率",previous.closure,current.closure,2,"%"," 个百分点"],
    ...["B","C","M","N"].map((label,i)=>[label,previous.severity?.[i],current.severity?.[i],0,"",""])
  ];
  return `<section class="stage-delta" aria-label="与上一阶段对比"><h4>与上一阶段对比 · ${esc(current.label)} 对比 ${esc(previous.label)}</h4>
    <p class="meta">本阶段 ${esc(current.date)} · 上一阶段 ${esc(previous.date)}。变化 = 本阶段 − 上一阶段；B/C/M/N 单位为条。</p>
    ${table(["指标",previous.label+"（上阶段）",current.label+"（本阶段）","变化"],fields.map(([label,before,now,precision,unit,deltaUnit])=>[
      esc(label),esc(value(before,precision,unit)),esc(value(now,precision,unit)),
      `<strong class="delta-value">${esc(change(now,before,precision,deltaUnit))}</strong>`]))}
    <p class="meta delta-source">上阶段评审来源 · ${esc(previous.reference || "待补充")}</p></section>`;
}
function stageTrendPanel(data) {
  const rows = trendRows(data);
  if (!rows.length) return "";
  return `<section class="panel stage-trends" id="stage-trends" aria-labelledby="trend-heading">
    <h2 id="trend-heading">Race 3S 阶段质量趋势</h2>
    <p class="muted">按评审日期排列，点击阶段或图上数据点查看评审数据与来源。</p>
    <div class="stage-picker" role="group" aria-label="选择阶段评审">${rows.map((r,i)=>
      `<button type="button" class="quiet" data-review-index="${i}" aria-pressed="false" aria-controls="stage-review-detail">
      <strong>${esc(r.label)}</strong><span>${esc(r.date)}</span></button>`).join("")}</div>
    <div class="trend-grid">
      <section class="trend-card"><h3>DI</h3><p class="meta">${esc(data.formula)}</p><div class="trend-chart" data-chart="di"></div></section>
      <section class="trend-card"><h3>研发关闭率</h3><p class="meta">阶段评审记录 · 单位 %</p><div class="trend-chart" data-chart="closure"></div></section>
      <section class="trend-card trend-severity"><h3>B / C / M / N 缺陷数</h3>
        <p class="trend-legend">${severitySeries.map((s,i)=>`<span class="series-${s.key}">${["●","◆","■","▲"][i]} ${s.label}</span>`).join("")}</p>
        <div class="trend-chart" data-chart="severity"></div></section>
    </div>
    <p class="meta">横轴为评审节点等间距排列，连线用于观察阶段变化。研发关闭率沿用评审口径，真实验证关闭需另核验证据。</p>
    <section id="stage-review-detail" class="stage-review-detail" aria-live="polite" aria-label="选中阶段评审详情"></section>
    <details><summary>查看全部阶段评审记录与 v85 对照</summary>${stageReviewPanel(data, "")}</details>
  </section>`;
}
function trendChart(rows, kind, width) {
  const w = Math.max(280,Math.round(width)), h = 264, left = 48, right = w-42, top = 28, bottom = 202;
  const x = i => rows.length === 1 ? (left+right)/2 : left+(right-left)*i/(rows.length-1);
  const series = kind === "severity" ? severitySeries.map((s,i)=>({...s,values:rows.map(r=>r.severity?.[i])})) :
    [{label:kind === "di" ? "DI" : "研发关闭率",key:kind,color:kind === "di" ? "#125da8" : "#6e43a3",
      shape:"circle",values:rows.map(r=>r[kind])}];
  const valid = v => typeof v === "number" && Number.isFinite(v) && v >= 0;
  const max = Math.max(1,...series.flatMap(s=>s.values.filter(valid)));
  const power = 10 ** Math.floor(Math.log10(max/4));
  const step = [1,1.5,2,2.5,3,4,5,7.5,10].find(n=>n*power >= max/4)*power;
  const ceiling = kind === "closure" ? 100 : step*4;
  const y = value => bottom-(bottom-top)*value/ceiling;
  const format = value => valid(value) ? (kind === "closure" ? value.toFixed(2)+"%" : kind === "di" ? value.toFixed(1) : String(value)) : "待补证";
  const marker = (shape,px,py,color) => {
    const attrs = `fill="${color}" stroke="white" stroke-width="2"`;
    if (shape === "diamond") return `<path d="M ${px} ${py-7} l 7 7 -7 7 -7 -7 Z" ${attrs}/>`;
    if (shape === "square") return `<rect x="${px-6}" y="${py-6}" width="12" height="12" ${attrs}/>`;
    if (shape === "triangle") return `<path d="M ${px} ${py-7} l 7 13 -14 0 Z" ${attrs}/>`;
    return `<circle cx="${px}" cy="${py}" r="6" ${attrs}/>`;
  };
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="group" aria-labelledby="chart-title-${kind}">
    <title id="chart-title-${kind}">${kind === "severity" ? "B/C/M/N 缺陷数" : series[0].label}阶段趋势，数据点可点击</title>
    ${rows.map((r,i)=>`<rect class="trend-band" data-band-index="${i}" x="${x(i)-24}" y="12" width="48" height="${bottom-4}" rx="8"/>`).join("")}
    ${Array.from({length:5},(_,i)=>{const v=ceiling*i/4;return `<line class="trend-gridline" x1="${left}" x2="${right}" y1="${y(v)}" y2="${y(v)}"/>
      <text class="trend-axis" x="${left-10}" y="${y(v)+5}" text-anchor="end">${Number(v.toFixed(2))}${kind === "closure" ? "%" : ""}</text>`;}).join("")}
    ${series.map(s=>{
      let path = "", previous = false;
      s.values.forEach((v,i)=>{if (valid(v)) {path += `${previous ? "L" : "M"}${x(i)} ${y(v)} `;previous=true;} else previous=false;});
      return `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2.5"/>
        ${s.values.map((v,i)=>valid(v) ? `<g class="trend-point" role="button" tabindex="0" data-review-index="${i}" data-series="${s.key}"
          aria-pressed="false" aria-controls="stage-review-detail" aria-label="${esc(rows[i].label)} ${esc(s.label)} ${format(v)}，查看评审数据与来源">
          <title>${esc(rows[i].label)} · ${esc(rows[i].date)} · ${esc(s.label)} ${format(v)}</title>
          <circle class="trend-hit" cx="${x(i)}" cy="${y(v)}" r="16"/>
          ${marker(s.shape,x(i),y(v),s.color)}
          ${kind !== "severity" ? `<text class="trend-value" x="${x(i)}" y="${y(v)-16}" text-anchor="middle">${format(v)}</text>` : ""}</g>` : "").join("")}`;
    }).join("")}
    ${rows.map((r,i)=>`<text class="trend-axis trend-stage-label" x="${x(i)}" y="228" text-anchor="middle">${esc(r.label)}</text>
      <text class="trend-axis" x="${x(i)}" y="249" text-anchor="middle">${esc(r.date.slice(5))}</text>`).join("")}
    </svg>`;
}
function mountStageTrends(data) {
  const area = $("stage-trends"), rows = trendRows(data);
  if (!area || !rows.length) return;
  let selected = rows.length-1;
  function highlight() {
    area.querySelectorAll("[data-review-index]").forEach(node=>node.setAttribute("aria-pressed",String(Number(node.dataset.reviewIndex)===selected)));
    area.querySelectorAll("[data-band-index]").forEach(node=>node.classList.toggle("is-selected",Number(node.dataset.bandIndex)===selected));
  }
  function selectReview(index, reveal = false) {
    if (!rows[index]) return;
    selected = index;
    const row = rows[selected];
    const comparison = data.comparison?.rows?.find(r=>r.stage===row.stage);
    $("stage-review-detail").innerHTML = `<div class="display-line"><h3>${esc(row.label)} 阶段评审 · ${esc(row.date)}</h3>
      ${button("查看该阶段项目指标","review-metrics",true)}</div>
      <dl class="review-values"><div><dt>DI</dt><dd>${textValue(row.di)}</dd></div>
      <div><dt>研发关闭率</dt><dd>${row.closure == null ? "待补证" : Number(row.closure).toFixed(2)+"%"}</dd></div>
      <div><dt>B / C / M / N</dt><dd>${esc(row.severity?.join(" / ") || "待补证")}</dd></div></dl>
      ${stageDeltaPanel(row,rows[selected-1])}
      <p><strong>评审来源</strong>　${esc(row.reference || "待补充")}</p>
      <p class="meta">录入依据 · ${esc(data.source)}。当前保留评审文档名称，原始飞书链接待补充。</p>
      ${comparison ? `<details><summary>查看 ${esc(data.comparison.snapshot_date)} v85 同阶段快照对照</summary>
      <p class="meta">${esc(data.comparison.note)}</p>
      ${table(["Release","有效缺陷","待解决","待解决 B / C","待验证","待复现","管理关闭率"],[[esc(comparison.label),comparison.effective,comparison.pending,
        esc(comparison.bc.join(" / ")),comparison.testing,comparison.reproduce,Number(comparison.closure).toFixed(2)+"%"]])}
      <p class="meta">该快照按发布阶段统计，趋势图采用各次评审记录。</p></details>` : ""}`;
    highlight();
    if (reveal) $("stage-review-detail").scrollIntoView({block:"nearest"});
  }
  area.onclick = event => {
    const target = event.target.closest("[data-review-index]");
    if (target) selectReview(Number(target.dataset.reviewIndex),true);
    if (event.target.closest('[data-action="review-metrics"]')) {
      sourceProject = "Race3S";
      sourceStage = rows[selected].stage;
      show("metrics");
    }
  };
  area.onkeydown = event => {
    const target = event.target.closest(".trend-point");
    if (target && ["Enter"," "].includes(event.key)) {
      event.preventDefault();
      selectReview(Number(target.dataset.reviewIndex),true);
    }
  };
  const draw = element => {
    const width = Math.max(280,Math.round(element.clientWidth));
    if (element.dataset.width === String(width)) return;
    element.dataset.width = width;
    element.innerHTML = trendChart(rows,element.dataset.chart,width);
    highlight();
  };
  trendObserver = new ResizeObserver(entries=>entries.forEach(entry=>draw(entry.target)));
  area.querySelectorAll("[data-chart]").forEach(element=>{draw(element);trendObserver.observe(element);});
  selectReview(selected);
}

function stageReviewPanel(data, stage) {
  if (!data?.checkpoints?.length) return "";
  const percent = value => Number(value).toFixed(2) + "%";
  const reviews = data.checkpoints.filter(row => !stage || row.stage === stage);
  const comparison = data.comparison.rows.filter(row => !stage || row.stage === stage);
  if (!reviews.length && !comparison.length) return "";
  return `<section class="panel"><h2>Race 3S 阶段评审记录</h2>
    <p class="meta">${esc(data.source)} · ${esc(data.formula)}</p>
    ${table(["阶段","评审日期","DI","B / C / M / N","研发关闭率","来源"],reviews.map(r=>[
      tag(r.label,"blue"),esc(r.date),esc(Number(r.di).toFixed(1)),esc(r.severity.join(" / ")),esc(percent(r.closure)),esc(r.reference)]))}
    <details${stage ? " open" : ""}><summary>OTA1 发布时点对照 · v85 快照</summary>
    <p class="meta">${esc(data.comparison.note)}</p>
    ${table(["Release","有效缺陷","待解决","待解决 B / C","待验证","待复现","管理关闭率"],comparison.map(r=>[
      esc(r.label),r.effective,r.pending,esc(r.bc.join(" / ")),r.testing,r.reproduce,esc(percent(r.closure))]))}</details>
    </section>`;
}

async function showMetrics() {
  const board = await api("release-board?detail=1").catch(() => null);
  if (!board?.source) return showPilotMetrics();
  const projects = board.projects;
  if (!projects.length) return heading("项目与指标","当前账号的负责项目尚无工程指标，请由负责人核对项目分工和源表配置。");
  if (!projects.some(p => p.name === sourceProject)) sourceProject = projects[0].name;
  const html = heading("项目与指标", "使用现行工程看板的指标、源表实绩和证据。") +
    `<section class="panel"><div class="filter">
     ${select("项目","source-project",projects.map(p=>[p.name,p.name]),sourceProject)}
     ${select("阶段","source-stage",[["","全部阶段"],...["ES","EP","FEP","RID","OTA"].map(s=>[s,s])],sourceStage)}
     ${field("搜索指标","source-query")}${button("刷新工程数据","release-refresh",true)}
     ${["owner","reviewer"].includes(session.user.role) ? button("2.57.64 试点填报","pilot-metrics",true) : ""}
     ${session.governance ? button("口径与证据复核","open-page:governance",true) : ""}</div>
     <p class="meta">${esc(board.source.name)} · 源文件更新 ${esc(board.source.updated.slice(0,10))}
     · 工程实绩按逐项证据日期展示。</p>
     ${board.error ? `<p class="notice caution">${esc(board.error)}，当前显示上次有效数据。</p>` : ""}
     <div id="source-profile"></div></section><div id="source-checkpoints"></div>
     <section class="panel"><h2>工程指标与证据</h2><div id="source-metric-body"></div></section>`;
  return {html,mount() {
    const projectField = document.querySelector('[name="source-project"]');
    const stageField = document.querySelector('[name="source-stage"]');
    const query = document.querySelector('[name="source-query"]');
    function draw() {
      sourceProject = projectField.value;
      sourceStage = stageField.value;
      const project = projects.find(row=>row.name===sourceProject);
      const items = project.rows.filter(row=>(!sourceStage || row.stage===sourceStage) &&
        row.name.toLowerCase().includes(query.value.toLowerCase().trim()));
      const stages = Object.entries(project.milestones).filter(([label])=>!sourceStage || label===sourceStage);
      $("source-profile").innerHTML = `<details><summary>项目基础信息与源表风险</summary>
        <p class="evidence">${esc(project.profile.overview || "")}</p>
        <p class="evidence">${esc(project.profile.risk || "")}</p><p class="meta">${esc(project.profile.source || "")}</p></details>
        <details><summary>阶段里程碑</summary>${table(["阶段","开始","结束","里程碑","源表节点"],stages.map(([label,m])=>[
          esc(label),textValue(m.start),textValue(m.end),textValue(m.date),
          esc([m.note,...(m.sourceNodes || []).map(n=>n.text)].filter(Boolean).join(" · "))]))}</details>`;
      $("source-checkpoints").innerHTML = sourceProject === "Race3S" ? stageReviewPanel(board.stage_reviews,sourceStage) : "";
      $("source-metric-body").innerHTML = table(["阶段 / 指标","来源目标","历史实绩 / 状态","证据"],items.map(row=>[
        `${tag(row.stage)} <strong>${esc(row.name)}</strong><p class="meta">${esc(row.definition)}</p>`,
        `${textValue(row.target)}<p class="meta">${esc(row.target_note)}</p>`,
        `${textValue(row.actual)}<p>${tag(row.review_status || "待复核","yellow")}</p>
        ${row.snapshot ? `<p class="meta">${esc(row.snapshot)}</p>` : ""}`,
        `<details><summary>查看来源</summary><p>${esc(row.source)}</p><p>${esc(row.evidence)}</p><p>${esc(row.action)}</p></details>`]));
    }
    projectField.onchange = stageField.onchange = query.oninput = draw;
    $("content").onclick = async event => {
      if (event.target.closest('[data-action="open-page:governance"]')) show("governance");
      if (event.target.closest('[data-action="pilot-metrics"]')) show("pilot-metrics");
      if (event.target.closest('[data-action="release-refresh"]')) {
        try {
          await api("library.refresh",{});
          if (page === "metrics") await show("metrics");
        } catch (failure) { notify(failure.message); }
      }
    };
    draw();
  }};
}
async function showPilotMetrics() {
  const {batch,payload,reviews} = await api("metrics");
  const head = heading("2.57.64 试点填报","维护 RID 试点配置、版本实绩和可核查的证据。") +
    (own() ? `<p>${button("返回工程看板","release-metrics",true)}</p>` : "");
  if (!payload) return head + empty("看板");
  const options = [["live","2.57.64 实绩"],...payload.snapshots.map(s => [s.label,s.label + " 历史快照"])];
  const html = `<section class="panel"><div class="filter">${select("查看范围","metric-scope",options)}
    ${field("搜索指标","metric-query")}${session.governance ? button("口径与证据复核","metric-governance",true) : ""}</div><div id="metric-body"></div></section>`;
  return {html:head + html, mount() {
    $("content").onclick = event => {
      if (event.target.closest('[data-action="release-metrics"]')) show("metrics");
      if (event.target.closest('[data-action="metric-governance"]')) show("governance");
    };
    const scope = document.querySelector('[name="metric-scope"]');
    const query = document.querySelector('[name="metric-query"]');
    function draw() {
      const historic = payload.snapshots.find(s => s.label === scope.value);
      const rows = (historic?.rows || payload.items).filter(r => r.name.toLowerCase().includes(query.value.toLowerCase()));
      $("metric-body").innerHTML = (historic
        ? `<p class="notice caution">${esc(historic.source_version)} ${esc(historic.note)}。灯色仅表示历史来源。</p>`
        : '<p class="notice">当前视图标注 2.57.64。判灯需要目标批准凭据、实绩和证据。</p>') +
        table(["指标 / 定义","目标","实绩 / 证据","结果",""],rows.map(row => {
          const current = historic ? row : reviews[row.id] || {};
          return [`<strong>${esc(row.name)}</strong><p class="meta">${esc(row.definition || "")}</p>`,
            `${textValue(current.target || row.target)}<p class="meta">${esc(current.approval || row.approval || "")}</p>`,
            `${textValue(current.actual)}<p class="meta">${esc(current.evidence || "")}</p>`,lamp(current.lamp),
            own() && !historic ? `<div class="actions">${button("填报","edit:"+row.id,true)}${button("创建整改","task:"+row.id,true)}</div>` : ""];
        }));
      $("metric-body").onclick = event => {
        const marker = event.target.closest("[data-action]")?.dataset.action;
        if (!marker) return;
        const [operation,id] = marker.split(":");
        const row = payload.items.find(item => item.id === id);
        if (!row) return;
        if (operation === "task") createTask("metrics",batch,id,row.name);
        if (operation === "edit") {
          const current = reviews[id] || {};
          dialog(row.name,field("目标","target",current.target || row.target) +
            field("目标批准凭据","approval",current.approval) + field("版本实绩","actual",current.actual) +
            field("实绩证据或链接","evidence",current.evidence,{type:"textarea"}) +
            select("结果","lamp",Object.entries(lampLabels).filter(([key])=>key!=="green").map(([key,[label]]) => [key,label]),current.lamp),
          "保存",form => api("metric.save",{id,...Object.fromEntries(form)}));
        }
      };
    }
    scope.onchange = draw;
    query.oninput = draw;
    draw();
  }};
}

function libraryFileLinks(key, files) {
  return files.map(file => {
    const url = "/api/library-file/" + key + "/" + file.id;
    const image = ["PNG","JPG","JPEG"].includes(file.format);
    const preview = image ? button("预览","library-image:"+file.id,true) :
      `<a href="${url}?view=1" target="_blank" rel="noopener noreferrer">打开预览</a>`;
    return `<div class="asset-file"><span>${tag(file.format)} ${esc(file.name)}</span><span class="actions">
      ${file.preview ? preview : ""}<a href="${url}">下载</a></span></div>`;
  }).join("");
}
async function auditReportPanel() {
  if (!own()) return "";
  try {
    const data = await api("library/pr-audit-reports");
    return `<section class="panel"><h2>PR 审计原报告</h2>${data.error ? `<p class="notice caution">${esc(data.error)}</p>` : ""}
      ${data.groups.map(row => `<article><h3>${esc(row.title)}</h3><p class="meta">${esc(row.date)} · ${esc(row.basis)}</p>
        <p>${esc(row.note)}</p>${libraryFileLinks("pr-audit-reports",row.files)}</article>`).join("")}</section>`;
  } catch {
    return '<p class="notice caution">审计原报告已配置，后台重启后载入文件目录。</p>';
  }
}

async function showPR() {
  if (!prVersion) return showPROverview();
  const {batch,payload,reviews,versions,version} = await api("pr?version=" + encodeURIComponent(prVersion));
  const head = heading("PR 规范审计","核对 Release Note 条目、匹配记录和历史审计证据。") + await auditReportPanel();
  const selector = `<section class="panel"><div class="filter">${select("发布版本","pr-version",
    [["","全部版本"],...Array.from(new Set([version,...versions])).map(v=>[v,v])],version)}</div></section>`;
  function selectVersion() {
    document.querySelector('[name="pr-version"]').onchange = event => { prVersion = event.target.value; show("pr"); };
  }
  if (!payload) return {html:head+selector+empty("PR"),mount:selectVersion};
  const html = head + selector + `<div class="cards">${card("Release Note 条目",payload.entries.length,version)}
    ${card("匹配成功",payload.entries.filter(e => e.match.match_status === "MATCHED").length,"按发布条目")}
    ${card("历史规则明细",payload.audit_count,"保留原始结论")}
    ${card("已提交复核",reviews.length,"按规则")}</div>
    <section class="panel"><h2>版本 PR 明细</h2>${payload.entries.map((entry,index) => {
      return `<article class="task"><div class="display-line"><h3>${esc(entry.pr_id ? "PR " + entry.pr_id : "发布条目")}</h3>
      <div class="actions">${tag(entry.item["分类"])}${tag(entry.match.match_status,entry.pr_id ? "green" : "yellow")}
      ${own() ? button("创建整改","pr-task:"+index,true) : ""}</div></div>
      <p>${esc(entry.item["PR条目原文"])}</p><p class="meta">Release Note 原文行 ${esc(entry.item["原文行号"])}</p>
      <details><summary>查看 ${entry.rules.length} 条逐规则明细</summary>
      ${table(["条款","历史结论","证据","试点复核",""],entry.rules.map((rule,ruleIndex) => {
        const review = reviews.find(r => r.entry === entry.key && r.rule === rule.rule_id);
        return [`<strong>${esc(rule.rule_id)}</strong> ${esc(rule.rule_name)}`,tag(rule.result),
          `<p>${textValue(rule.evidence)}</p><p class="meta">${esc(rule.issue)}</p>`,
          review ? `${tag(review.result,"blue")}<p>${esc(review.evidence)}</p>` : tag("待复核"),
          button("复核","pr-review:"+index+":"+ruleIndex,true)];
      }))}</details></article>`;
    }).join("")}</section>`;
  return {html,mount() {
    selectVersion();
    $("content").onclick = event => {
      const marker = event.target.closest("[data-action]")?.dataset.action;
      if (!marker?.startsWith("pr-")) return;
      const [action,i,j] = marker.split(":");
      const entry = payload.entries[i];
      if (action === "pr-task") createTask("pr",batch,entry.key,"审计 PR " + (entry.pr_id || entry.key));
      if (action === "pr-review") {
        const rule = entry.rules[j];
        reviewDialog("pr",batch,entry.key,rule.rule_id,"PR " + entry.pr_id + " · " + rule.rule_name);
      }
    };
  }};
}
async function showPROverview() {
  const {rows,totals} = await api("pr-overview");
  const head = heading("PR 规范审计","查看发布版本、PR 匹配覆盖与原始审计证据。") + await auditReportPanel();
  if (!rows.length) return head + empty("PR");
  const range = rows.at(-1).version + "–" + rows[0].version;
  const html = head + `<div class="cards">
    ${card("发布版本",totals.versions,range)}
    ${card("Release Note PR 条目",totals.count,`匹配 ${totals.matched} · 歧义 ${totals.ambiguous} · 未匹配 ${totals.unmatched}`)}
    ${card("跨版本唯一 PR",totals.unique_prs,"按已匹配 PR 编号去重")}
    ${card("逐规则审计明细",totals.audit,"保留原结论口径")}</div>
    <section class="panel"><h2>全部发布版本</h2><div class="filter">
    ${field("搜索版本","pr-query")}${select("范围","pr-coverage",[["all","全部版本"],["gap","有歧义或未匹配"]])}
    </div><div id="pr-overview-table"></div></section>`;
  return {html,mount() {
    const query = document.querySelector('[name="pr-query"]');
    const scope = document.querySelector('[name="pr-coverage"]');
    function draw() {
      const visible = rows.filter(row => row.version.includes(query.value.trim()) &&
        (scope.value === "all" || row.ambiguous || row.unmatched));
      $("pr-overview-table").innerHTML = table(
        ["版本","发布日期","PR 条目","匹配","有歧义","未匹配","规则明细",""],
        visible.map(row => [`<strong>${esc(row.version)}</strong>`,esc(row.date),row.count,row.matched,
          row.ambiguous ? tag(row.ambiguous,"yellow") : "0", row.unmatched ? tag(row.unmatched,"yellow") : "0",
          row.audit,button("查看 " + row.version,"version:"+row.version,true)]));
    }
    query.oninput = draw;
    scope.onchange = draw;
    $("pr-overview-table").onclick = event => {
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action?.startsWith("version:")) { prVersion = action.slice(8); show("pr"); }
    };
    draw();
  }};
}
function reviewDialog(kind,batch,key,rule,title) {
  dialog("复核 · " + title,select("复核状态","result",[["待补证","待补证"],["继续跟踪","继续跟踪"],["已核对","已核对"]]) +
    field("证据或复核意见","evidence","",{required:true,type:"textarea"}),"提交复核",
    form => api(kind + ".review",{batch,key,rule,...Object.fromEntries(form)}));
}

async function showBugs() {
  const [{batch,payload,reviews},runs] = await Promise.all([api("bugs"),api("bug-audit/runs")]);
  const head = heading("Bug 审计","以 TP 原始快照复算 Race 3 / 3S RID 阶段缺陷。");
  const html = head + `<div class="bug-audit-tabs" role="tablist" aria-label="Bug 审计视图">
      <button type="button" class="quiet" role="tab" aria-selected="true" data-bug-audit-view="snapshot">当前快照</button>
      <button type="button" class="quiet" role="tab" aria-selected="false" data-bug-audit-view="runs">每日审计运行</button>
    </div><div id="bug-audit-content"></div>`;
  return {html,mount() {
    const content = $("bug-audit-content");
    function snapshotHtml() {
      if (!payload) return empty("缺陷");
      const b = payload.stats;
      return `<p class="notice caution">截至 ${esc(payload.cutoff)} · 原始阶段标签 ${esc(payload.scope)} · 2.57.64 构建关联待补证。</p>
        <div class="cards">${card("有效缺陷",b.effective,b.rid_rows + " 条 RID 原始记录")}
        ${card("待解决",b.pending,"Blocking " + b.blocking + " · Critical " + b.critical)}
        ${card("待验证",b.testing,"待复现 "+b.reproduce)}
        ${card("管理关闭率",b.closure === null ? "—" : b.closure.toFixed(2) + "%","Verified + Fixed "+b.verified_fixed)}</div>
        <section class="panel"><div class="filter">${select("缺陷状态","bug-state",[["all","全部有效缺陷"],["pending","研发待解决"],
        ["In Testing","待验证"],["Reproduce","待复现"]],"pending")}${field("查找 Bug ID 或标题","bug-query")}</div>
        <div id="bug-body"></div></section>`;
    }
    function attachSnapshot() {
      const status = document.querySelector('[name="bug-state"]');
      const query = document.querySelector('[name="bug-query"]');
      if (!status || !query) return;
      function draw() {
        const waiting = ["New","In Progress","In Review"];
        const key = query.value.toLowerCase();
        const rows = payload.rows.filter(row => {
          const state = row["Entity State"];
          const visible = status.value === "all" || status.value === state || (status.value === "pending" && waiting.includes(state));
          return visible && (row.ID + " " + row.Name).toLowerCase().includes(key);
        });
        $("bug-body").innerHTML = `<p class="meta">显示 ${rows.length} 条</p>` +
          table(["Bug","严重度","状态 / 团队","复核",""],rows.map(row => {
            const review = reviews.find(r => r.bug_id === row.ID);
            return [`<strong>#${esc(row.ID)}</strong><p>${esc(row.Name)}</p>`,
              tag(row.Severity,["Blocking","Critical"].includes(row.Severity) ? "red" : ""),
              `${tag(row["Entity State"])}<p class="meta">${esc(row.Teams)}</p>`,
              review ? `${tag(review.result,"blue")}<p>${esc(review.evidence)}</p>` : tag("待复核"),
              `<div class="actions">${button("复核","bug-review:"+row.ID,true)}
              ${own() ? button("创建整改","bug-task:"+row.ID,true) : ""}</div>`];
          }));
      }
      status.onchange = draw;
      query.oninput = draw;
      $("bug-body").onclick = event => {
        const marker = event.target.closest("[data-action]")?.dataset.action;
        if (!marker) return;
        const [action,id] = marker.split(":");
        if (action === "bug-review") reviewDialog("bugs",batch,id,"","Bug #" + id);
        if (action === "bug-task") createTask("bugs",batch,id,"跟踪 Bug #" + id);
      };
      draw();
    }
    function statusTag(value) {
      const labels = {running:"进行中",complete:"已完成",partial:"部分完成",blocked:"受阻",failed:"失败",
        pending:"待处理",verified:"已核验",not_required:"不适用",unknown:"结果未知",conflict:"冲突",
        written:"已写入待回读",sent:"已发送待回读",skipped:"未执行",mismatch:"附件不匹配",
        not_requested:"未请求",failed:"失败"};
      const colors = {complete:"green",verified:"green",partial:"yellow",blocked:"red",failed:"red",conflict:"red",unknown:"yellow"};
      return tag(labels[value] || value, colors[value] || "");
    }
    function runsHtml() {
      if (!runs.length) return `<section class="panel bug-audit-runs"><h2>每日审计运行</h2>${empty("每日审计运行")}</section>`;
      return `<section class="panel bug-audit-runs"><div class="display-line"><h2>每日审计运行</h2><span class="meta">${runs.length} 次运行</span></div>
        <div class="table-wrap"><table><thead><tr><th>运行窗口 / 执行方式</th><th>范围</th><th>采集 / 审计</th><th>TP 回填</th><th>飞书通知</th><th>状态</th><th></th></tr></thead><tbody>
        ${runs.map(run => `<tr><td>${esc(run.window.start)}<br>至 ${esc(run.window.end)}${executionHtml(run)}</td>
          <td>${esc(JSON.stringify(run.scope))}</td><td>${statusTag(run.collection_status)} / ${statusTag(run.audit_status)}</td>
          <td>${statusTag(run.writeback_status)}</td><td>${statusTag(run.notification_status)}</td><td>${statusTag(run.status)}</td>
          <td>${button("查看","bug-run:" + run.id,true)}</td></tr>`).join("")}</tbody></table></div>
        <div id="bug-audit-run-detail"></div></section>`;
    }
    function executionHtml(run) {
      const execution = run.execution || {producer:"legacy",mode:"legacy"};
      const modes = {legacy:"外部执行器台账",demo:"合成演示",dry_run:"在线只读分析",apply:"授权执行"};
      const stages = {collect:"采集",audit:"审计",writeback:"写回与回读",notify:"通知与附件核验",planned:"计划已生成",partial:"待恢复 / 对账",complete:"所选模式已完成"};
      const note = ["demo","dry_run"].includes(execution.mode) ? " · 未执行真实回填和通知" : "";
      return `<p class="meta">${esc(modes[execution.mode] || execution.mode)} · ${esc(execution.profile || execution.producer)}${esc(note)}${execution.stage ? `<br>阶段：${esc(stages[execution.stage] || execution.stage)}` : ""}</p>`;
    }
    async function loadRun(id) {
      const detail = await api("bug-audit/runs/" + encodeURIComponent(id));
      const target = $("bug-audit-run-detail");
      const receipts = detail.events.filter(event => event.stage === "notification");
      target.innerHTML = `<section class="bug-run-detail"><div class="display-line"><h3>运行 #${esc(detail.id)} · ${statusTag(detail.status)}</h3>
          <span class="meta">规则 ${esc(detail.rule_version)} · ${esc(detail.changed)}</span></div>
          ${executionHtml(detail)}
          <p class="meta">采集 ${statusTag(detail.collection_status)} · 审计 ${statusTag(detail.audit_status)} · TP ${statusTag(detail.writeback_status)} · 飞书 ${statusTag(detail.notification_status)}</p>
          ${receipts.length ? `<p class="meta">通知回执：${receipts.map(event => `${statusTag(event.details.status)} · ${esc(event.details.target || "目标未记录")} · ${esc(event.details.message_id || "消息待回读")} · 附件 ${statusTag(event.details.attachment_status || "pending")}`).join("；")}</p>` : ""}
          ${detail.items.length ? detail.items.map(item => `<article class="bug-audit-item"><h4>Bug #${esc(item.bug_id)} ${esc(item.snapshot.title || "")}</h4>
            <p class="meta">${esc(item.inclusion_reason)} · 证据 ${esc(JSON.stringify(item.evidence))}</p>
            <div class="table-wrap"><table><thead><tr><th>History</th><th>字段</th><th>旧值 → 新值</th><th>审计字段</th><th>原值 / 拟值</th><th>状态 / 回读</th></tr></thead><tbody>
            ${item.history.map(history => `<tr><td>${esc(history.id)}<br>${esc(history.changed_at)}</td><td>${esc(history.field)}</td>
              <td>${esc(history.old_value)} → ${esc(history.new_value)}</td><td colspan="3"></td></tr>`).join("")}
            ${item.fields.map(field => `<tr><td colspan="3"></td><td>${esc(field.name)}${field.ready ? " · ready" : " · pending"}${field.conflict ? " · 冲突" : ""}</td>
              <td>${esc(field.original_value)} → ${esc(field.proposed_value)}${field.reason ? `<p class="meta">${esc(field.reason)}</p>` : ""}</td>
              <td>${statusTag(field.status)}${field.readback_value === null ? "" : `<p class="meta">回读：${esc(field.readback_value)}</p>`}</td></tr>`).join("")}
            </tbody></table></div></article>`).join("") : empty("Bug 候选")}</section>`;
    }
    async function setView(view) {
      content.parentElement.querySelectorAll("[data-bug-audit-view]").forEach(button => {
        button.setAttribute("aria-selected", String(button.dataset.bugAuditView === view));
      });
      content.innerHTML = view === "runs" ? runsHtml() : snapshotHtml();
      if (view === "snapshot") attachSnapshot();
    }
    content.innerHTML = snapshotHtml();
    attachSnapshot();
    content.parentElement.addEventListener("click", event => {
      const view = event.target.closest("[data-bug-audit-view]")?.dataset.bugAuditView;
      if (view) return setView(view);
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action?.startsWith("bug-run:")) loadRun(action.split(":")[1]).catch(error => notify(error.message));
    });
  }};
}

async function createTask(kind,batch,source_key,title) {
  try {
    const people = (await api("people")).map(p => [p.id,p.name]);
    dialog("分派整改",field("任务标题","title",title,{required:true,max:200}) +
      field("整改要求与关闭标准","detail","",{required:true,type:"textarea"}) +
      `<div class="field-row">${select("整改负责人","assignee",people)}
      ${select("独立验证人","verifier",people,people[1]?.[0])}</div>` +
      field("截止日期","due","",{required:true,type:"date"}),"创建",form => api("task.create",{
        kind,batch,source_key,...Object.fromEntries(form),assignee:Number(form.get("assignee")),verifier:Number(form.get("verifier"))}));
  } catch (failure) { notify(failure.message); }
}
async function showTasks() {
  const [tasks,people] = await Promise.all([api("tasks"),api("people")]);
  const uid = session.user.id;
  const peopleOptions = people.map(p => [p.id,p.name]);
  const html = heading("整改与验证","记录分派、证据提交、独立验证和负责人关闭。") +
    `<section class="panel"><h2>${session.user.role === "member" ? "我的任务" : "试点任务"}</h2>
    ${tasks.length ? tasks.map(task => {
      const canVerify = uid === task.verifier && !task.contributors.includes(uid) && uid !== task.assignee;
      return `<article class="task"><div class="display-line"><h3>#${task.id} ${esc(task.title)}</h3>${stateTag(task.state)}</div>
      <p>${esc(task.detail)}</p><p class="meta">整改 ${esc(task.assignee_name)} · 验证 ${esc(task.verifier_name)} · 到期 ${esc(task.due)}
      · ${esc(kindNames[task.kind])} #${task.batch} / ${esc(task.source_key)}
      ${task.source_status === "withdrawn" ? " · 来源批次已撤回" : ""}</p>
      <div class="actions">${uid === task.assignee && task.state === "open" ? button(actions.submit,"submit:"+task.id) : ""}
      ${canVerify && task.state === "submitted" ? button(actions.verify,"verify:"+task.id)+button(actions.reject,"reject:"+task.id,true) : ""}
      ${own() && ["open","submitted"].includes(task.state) ? button(actions.assign_verifier,"assign_verifier:"+task.id,true) : ""}
      ${own() && task.state === "verified" ? button(actions.close,"close:"+task.id) : ""}</div>
      <details><summary>操作与证据记录</summary><div class="timeline">${task.history.map(item => {
        const action = item.action.split(".")[1];
        return `<div><p><strong>${esc(item.name)} · ${esc(actions[action])}</strong> <span class="meta">${esc(item.created)}</span></p>
        ${item.detail.note ? `<p>${esc(item.detail.note)}</p>` : ""}
        ${item.detail.contributors?.length ? `<p class="meta">共同提交人 ${esc(item.detail.contributors.map(id => people.find(p => p.id === id)?.name || "#" + id).join("、"))}</p>` : ""}</div>`;
      }).join("")}</div></details></article>`;
    }).join("") : '<p class="muted">暂无任务。负责人可从指标、PR 或 Bug 记录创建整改。</p>'}</section>`;
  return {html,mount() {
    $("content").onclick = event => {
      const marker = event.target.closest("[data-action]")?.dataset.action;
      if (!marker) return;
      const [action,id] = marker.split(":");
      const task = tasks.find(t => t.id === Number(id));
      if (!task || !actions[action]) return;
      let fields = field(action === "submit" ? "整改证据或链接" : "验证结论或处理依据","note","",{required:true,type:"textarea"});
      if (action === "submit") fields += '<fieldset><legend>共同提交人（可选）</legend>' +
        people.filter(p => p.id !== uid).map(p => `<label class="actions"><input type="checkbox" name="contributors" value="${p.id}"> ${esc(p.name)}</label>`).join("") + "</fieldset>";
      if (action === "assign_verifier") fields += select("新验证人","verifier",
        peopleOptions.filter(([id]) => id !== task.assignee && !task.contributors.includes(id)));
      dialog(actions[action],fields,"提交",form => api("task.action",{id:task.id,action,note:form.get("note"),
        verifier:Number(form.get("verifier")),contributors:form.getAll("contributors").map(Number)}));
    };
  }};
}

function fileBase64(file) {
  return new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name:file.name,base64:String(reader.result).split(",")[1]});
    reader.onerror = () => reject(new Error("无法读取 " + file.name));
    reader.readAsDataURL(file);
  });
}
async function showImports() {
  const [data,sync] = await Promise.all([api("batches"),api("sync")]);
  const syncNames = {published:"已发布",review:"待复核",error:"异常",withdrawn:"已撤回"};
  const syncHtml = sync.enabled ? `<section class="panel"><div class="display-line"><h2>自动更新</h2>
    ${button("立即检查","sync-run",true)}</div><p class="meta">上次检查 ${textValue(sync.checked)}</p>
    ${sync.error ? `<p class="error">${esc(sync.error)}</p>` : ""}
    ${table(["来源","状态","结果"],sync.jobs.map(job => [esc(kindNames[job.kind]) +
      (job.version ? " · " + esc(job.version) : ""), tag(syncNames[job.state] || job.state,
      ({published:"green",review:"yellow",error:"red"})[job.state]), esc(job.message)
      + (job.batch ? " · 批次 #" + esc(job.batch) : "")]))}</section>` : "";
  const html = heading("数据导入","先检查来源文件与对账数量，再将批次发布到试点看板。") +
    syncHtml + `<section class="panel"><h2>新增来源批次</h2><form id="import-form" class="form-grid">
    ${select("导入类型","kind",Object.entries(kindNames))}<div id="file-fields" class="form-grid"></div>
    <div class="error" role="alert" hidden></div><div class="actions"><button type="submit">校验并暂存</button>
    ${session.samples ? button("暂存已核对历史样本","stage-samples",true) : ""}</div></form></section>
    <section class="panel"><h2>批次记录</h2>
    ${table(["批次","来源","状态 / 对账","原始文件","操作"],data.map(row => [tag("#"+row.id,"blue"),
      `${esc(kindNames[row.kind])}<p class="meta">${esc(row.cutoff || row.created)}</p>`,
      `${stateTag(row.status)}<p>${row.summary ? esc(Object.entries(row.summary).map(([k,v]) => k+" "+v).join(" · ")) : esc(row.error || "")}</p>`,
      Object.entries(row.manifest).map(([slot,item]) => `<p>${row.summary
        ? `<a href="/api/source/${row.id}/${esc(slot)}">${esc(item.name)}</a>` : esc(item.name)}
        <span class="meta" title="${esc(item.sha256)}">${esc(item.sha256.slice(0,12))}</span></p>`).join(""),
      row.status === "staged" ? button("发布","publish:"+row.id) :
      row.status === "published" ? button("撤回","withdraw:"+row.id,true) : "—"]))}</section>`;
  return {html,mount() {
    const form = $("import-form");
    const kind = form.elements.kind;
    function files() {
      $("file-fields").innerHTML = Object.entries(fileFields[kind.value]).map(([key,label]) =>
        `<label>${esc(label)}<input type="file" name="${esc(key)}" accept="${kind.value === "metrics" ? ".json" : ".csv"}" required></label>`).join("") +
        (kind.value === "bugs" ? field("快照截止日期（北京时间）","cutoff","",{required:true,type:"date"}) :
          kind.value === "pr" ? field("发布版本","cutoff",session.version,{required:true,max:32}) : "");
    }
    kind.onchange = files;
    files();
    onForm(form,async data => {
      const selected = data.get("kind");
      const content = await Promise.all(Object.keys(fileFields[selected]).map(async slot => {
        const file = data.get(slot);
        if (!(file instanceof File) || !file.size || file.size > 8*1024*1024) throw new Error("请选择不超过 8 MiB 的完整文件");
        return [slot,await fileBase64(file)];
      }));
      const result = await api("import",{kind:selected,cutoff:data.get("cutoff") || "",files:Object.fromEntries(content)});
      await show("imports");
      notify(result.duplicate ? "已存在相同批次 #" + result.id : "已暂存批次 #" + result.id);
    });
    $("content").onclick = async event => {
      const marker = event.target.closest("[data-action]")?.dataset.action;
      if (!marker) return;
      const [action,id] = marker.split(":");
      if (action === "sync-run") {
        try {
          await api("sync.run",{});
          notify("更新检查已提交");
          setTimeout(() => { if (page === "imports" && !$("dialog").open) show("imports"); },1200);
        } catch (failure) { notify(failure.message); }
      }
      if (action === "stage-samples") {
        const trigger = event.target.closest("button");
        trigger.disabled = true;
        try {
          await api("samples.stage",{});
          await show("imports");
          notify("历史样本已校验。请核对批次后发布。");
        } catch (failure) { notify(failure.message); }
        finally { trigger.disabled = false; }
      }
      if (action === "publish") dialog("发布批次 #" + id,
        '<p>批次将参与试点看板和审计。请核对来源和数量。</p>',"发布",() => api("publish",{id:Number(id)}));
      if (action === "withdraw") dialog("撤回批次 #" + id,field("撤回原因","reason","",{required:true,type:"textarea"}),"撤回",
        f => api("withdraw",{id:Number(id),reason:f.get("reason")}));
    };
  }};
}

async function showAdmin() {
  const [accounts,audit] = await Promise.all([api("accounts"),api("audit")]);
  const html = heading("账号权限管理","管理本机验收账号，查看操作留痕。") +
    `<section class="panel"><div class="display-line"><h2>独立账号</h2>${button("新增账号","new-account")}</div>
    ${table(["账号","权限","状态",""],accounts.map(account => [esc(account.name),esc(names[account.role]),
      account.enabled ? tag("启用","green") : tag("停用","yellow"),
      account.role === "owner" ? "" : button(account.enabled ? "停用" : "启用","account:"+account.id,true)]))}</section>
    <section class="panel"><h2>操作审计</h2>${table(["时间","操作者","操作","对象"],audit.map(row => [
      esc(row.created),esc(row.name),esc(row.action),esc(row.subject)]))}</section>`;
  return {html,mount() {
    $("content").onclick = event => {
      const marker = event.target.closest("[data-action]")?.dataset.action;
      if (!marker) return;
      if (marker === "new-account") {
        dialog("新增本机账号",field("姓名或账号","name","",{required:true,max:64}) +
          field("初始密码（至少 11 字符）","password","",{required:true,type:"password",max:128}) +
          select("权限","role",[["member","成员"],["reviewer","协同审核"]]),"创建",f => api("account.create",Object.fromEntries(f)));
      } else if (marker.startsWith("account:")) {
        const account = accounts.find(a => a.id === Number(marker.split(":")[1]));
        const verb = account.enabled ? "停用" : "启用";
        dialog(verb+"账号",`<p>${esc(account.name)} · ${esc(names[account.role])}</p>`,verb,
          () => api("account.status",{id:account.id,enabled:!account.enabled}));
      }
    };
  }};
}

const libraryProjects = {
  "bug-reports":"https://chatgpt.com/s/cx_6b05167c64ec81918003666d0fa859c0",
  culture:"https://chatgpt.com/s/cx_6b0516b381a48191a25284f54f2f5b7a"
};
function issueGroups(groups) {
  const periods = new Map();
  for (const row of groups) {
    const match = row.title.match(/(?:质量案例第|案例期刊第?)([0-9０-９]+)期/);
    const issue = row.issue ?? (match ? Number(match[1].replace(/[０-９]/g,ch=>String.fromCharCode(ch.charCodeAt(0)-65248))) : null);
    const id = issue === null ? row.title : String(issue);
    const current = periods.get(id);
    if (!current) periods.set(id,{...row,issue,title:issue === null ? row.title : "质量案例第"+String(issue).padStart(2,"0")+"期",files:[...row.files]});
    else {
      current.files.push(...row.files);
      current.date = current.date > row.date ? current.date : row.date;
      current.updated = current.updated > row.updated ? current.updated : row.updated;
    }
  }
  return [...periods.values()].sort((a,b)=>(b.issue ?? 0)-(a.issue ?? 0) || b.date.localeCompare(a.date));
}
function issueCards(groups) {
  return groups.map(row=>{
    const editions = new Map();
    for (const file of row.files) {
      const stem = file.name.replace(/\.[^.]+$/,"");
      if (!editions.has(stem)) editions.set(stem,[]);
      editions.get(stem).push(file);
    }
    return `<article class="panel issue-card" data-issue="${esc(row.issue ?? "other")}">
      <h2>${esc(row.title)}</h2><p class="meta">最近文件更新 ${esc(row.updated?.slice(0,10) || row.date)} · ${editions.size} 组材料 · ${row.files.length} 个文件</p>
      <p class="meta">保留来源中的主题与修订稿，定稿及发布状态按原工程复核。</p>
      ${[...editions].map(([title,files])=>`<details class="issue-edition"><summary>${esc(title)}</summary>${libraryFileLinks("culture",files)}</details>`).join("")}</article>`;
  }).join("") || '<p class="muted">当前筛选条件下暂无期刊。</p>';
}

async function showLibrary(key, title = libraries[key], route = key, initialQuery = "") {
  let data;
  try { data = await api("library/" + key); }
  catch (failure) {
    if (key.startsWith("pr-")) return heading(title,"本机报告来源已配置。") +
      '<p class="notice caution">后台重启后载入报告文件目录。</p>';
    throw failure;
  }
  let crash = key === "crashes" ? await api("crash-site") : null;
  let crashRevision = "";
  const subtitle = route === "culture-cases" ? "复用已接入的案例材料目录，按日期查看来源文件。审核和脱敏状态按原文核对。" :
    route === "report-weekly" ? "从已接入的本机报告中按“周报”名称筛选，可按日期查询并调整搜索条件。" : {
    "bug-reports":"按日期查看历史质量周报、缺陷专项和遗留问题报告。",
    "culture":"按期号浏览案例材料，每期保留主题、修订稿与原始文件。",
    "pr-contributions":"查看 Race3S 检视贡献与 NG3 平台合并 PR 历史报告。",
    "pr-density":"查看原报告统计范围、代理密度口径及证据来源。",
    "crashes":"查看源站崩溃趋势、类型明细与本机历史资料。"
  }[key];
  const crashPanel = crash?.enabled ? `<section class="panel crash-site-panel"><div class="display-line">
    <h2>内网崩溃数据看板</h2><div class="actions">${button("检查发布更新","crash-check",true)}
    ${button("重载源站看板","crash-reload",true)}<a href="${esc(crash.source_url)}" target="_blank" rel="noopener noreferrer">打开源站</a></div></div>
    <p id="crash-site-status" class="meta" role="status"></p>
    <p id="crash-site-publication" class="meta"></p>
    <p class="meta">默认查看 Race 3S 的 2.57.62 / 2.57.64。每 5 分钟检查发布；新数据载入后保留你在当前浏览器选择的日期、筛选条件和统计粒度。
    日期保持原范围，可手动选择“最近 30 天”等选项更新范围。</p>
    <div id="crash-site-frame-host"></div></section><h2>本机历史崩溃资料</h2>` : "";
  const projectLink = libraryProjects[key] ? `<p class="meta"><a href="${esc(libraryProjects[key])}" target="_blank" rel="noopener noreferrer">打开来源工程</a></p>` : "";
  const periodSelector = key === "culture" ? select("期号","library-issue",[["all","全部期刊"],...issueGroups(data.groups).filter(row=>row.issue !== null).map(row=>[row.issue,row.title])]) : "";
  const html = heading(title,subtitle) + projectLink + crashPanel + `<section class="panel">
    <div class="filter">${periodSelector}${field("开始日期","library-start","",{type:"date"})}
    ${field("结束日期","library-end","",{type:"date"})}${field("搜索资料","library-query",initialQuery)}
    ${button("刷新目录","library-refresh",true)}</div>
    <p id="library-meta" class="meta" role="status"></p><div id="library-items"></div></section>`;
  return {html,mount() {
    function drawCrash(reload = false) {
      if (!crash?.enabled || !$("crash-site-status")) return;
      const publication = crash.publication;
      $("crash-site-status").textContent = (crash.error || "源站发布清单校验通过") + " · 最近检查 " + (crash.checked || "待检查");
      $("crash-site-status").className = crash.error ? "notice caution" : "meta";
      $("crash-site-publication").textContent = publication ?
        `${crash.error ? "上次通过校验的发布" : "当前发布"} · ${publication.start_date} 至 ${publication.end_date} UTC · Android / iOS · 发布于 ${publication.published_at_utc} · ${publication.snapshot_id}` :
        "尚无通过校验的发布信息。可检查连接后重试，或查看下方本机历史资料。";
      if (publication && !crash.error && (reload || crashRevision !== publication.revision)) {
        $("crash-site-frame")?.contentWindow?.saveSqaCrashPreferences?.();
        const frame = document.createElement("iframe");
        frame.id = "crash-site-frame";
        frame.title = "内网崩溃趋势与类型明细";
        frame.className = "crash-site-frame";
        frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
        frame.referrerPolicy = "no-referrer";
        frame.src = "/api/crash-view/index.html?viewer=" + encodeURIComponent(session.user.id);
        $("crash-site-frame-host").replaceChildren(frame);
        crashRevision = publication.revision;
      }
    }
    async function refreshCrash(force, reload = false) {
      if (!crash?.enabled) return;
      try {
        const result = await api(force ? "crash-site.refresh" : "crash-site", force ? {} : undefined);
        if (page === route && $("crash-site-status")) { crash = result; drawCrash(reload); }
      } catch (failure) {
        if (page === route && $("crash-site-status")) {
          $("crash-site-status").textContent = "发布检查失败 · " + failure.message;
          $("crash-site-status").className = "notice caution";
        }
      }
    }
    const fields = ["start","end","query"].map(name=>document.querySelector('[name="library-'+name+'"]'));
    function draw() {
      const [start,end,query] = fields.map(f=>f.value.trim().toLowerCase());
      const period = document.querySelector('[name="library-issue"]')?.value || "all";
      const groups = key === "culture" ? issueGroups(data.groups) : data.groups;
      const visible = groups.filter(row=>(period === "all" || String(row.issue) === period) &&
        (!start || row.date>=start) && (!end || row.date<=end) &&
        (!query || row.title.toLowerCase().includes(query) || row.files.some(f=>f.name.toLowerCase().includes(query))));
      $("library-meta").textContent = (data.error ? data.error + " · " : "") +
        "显示 " + visible.length + " 项 · 最近检查 " + data.checked.replace("T"," ").slice(0,19) + " · 每 5 分钟检查来源";
      if (start && end && start > end) {
        $("library-items").innerHTML = '<p class="error">开始日期应早于或等于结束日期。</p>';
        return;
      }
      $("library-items").innerHTML = key === "culture" ? issueCards(visible) : table(["日期","资料","文件"],visible.map(row=>[
        `<strong class="nowrap">${esc(row.date)}</strong><p class="meta">${esc(row.basis)}</p>`,
        `<strong>${esc(row.title)}</strong>${row.note ? `<p>${esc(row.note)}</p>` : ""}<p class="meta">文件更新 ${esc(row.updated?.slice(0,10))}</p>`,
        libraryFileLinks(key,row.files)]));
    }
    async function refresh(force) {
      try {
        if (force) await api("library.refresh",{});
        const result = await api("library/" + key);
        if (page === route && $("library-items")) {
          data = result;
          const issueSelect = document.querySelector('[name="library-issue"]');
          if (issueSelect) {
            const chosen = issueSelect.value;
            const available = issueGroups(data.groups).filter(row=>row.issue !== null);
            issueSelect.innerHTML = '<option value="all">全部期刊</option>' + available.map(row=>
              `<option value="${row.issue}">${esc(row.title)}</option>`).join("");
            if ([...issueSelect.options].some(option=>option.value === chosen)) issueSelect.value = chosen;
          }
          draw();
        }
      } catch (failure) {
        if (force) notify(failure.message);
      }
    }
    fields.forEach(f=>f.oninput=draw);
    const issueSelect = document.querySelector('[name="library-issue"]');
    if (issueSelect) issueSelect.onchange = draw;
    $("content").onclick = event => {
      const action = event.target.closest("[data-action]")?.dataset.action || "";
      if (action === "crash-check") refreshCrash(true);
      if (action === "crash-reload") refreshCrash(true,true);
      if (action === "library-refresh") refresh(true);
      if (action.startsWith("library-image:")) {
        const id = action.slice(14);
        const file = data.groups.flatMap(group=>group.files).find(f=>f.id===id);
        if (!file) return;
        $("dialog-content").innerHTML = `<h2>${esc(file.name)}</h2>
          <img class="asset-preview" alt="${esc(file.name)}" src="/api/library-file/${key}/${id}?view=1">
          <p class="actions"><a href="/api/library-file/${key}/${id}">下载</a><button type="button" class="quiet" id="preview-close">关闭</button></p>`;
        $("preview-close").onclick = () => $("dialog").close();
        $("dialog").showModal();
      }
    };
    draw();
    drawCrash();
    libraryTimer = setInterval(()=>Promise.all([refresh(false),refreshCrash(false)]),60000);
  }};
}

const externalDocuments = {
  "culture-standards": {title:"质量规范",url:"https://suuntoglobal.feishu.cn/file/SFD5bqXrFoOShdxVYB7cxD5Mnth"},
  "report-daily": {title:"Daily 报告",url:"https://suuntoglobal.feishu.cn/file/FLEObmfKOoIRDMxPLPVcGlhvnVc?office_edit=1"},
  "report-regression": {title:"回归通过率报告",url:"https://suuntoglobal.feishu.cn/file/UKrxbAeReoeWqGxgTWVcOjmsngn?office_edit=1"}
};
function showExternalDocument(id) {
  const document = externalDocuments[id];
  return heading(document.title,"用户指定的飞书原文件。") +
    `<section class="panel"><a href="${esc(document.url)}" target="_blank" rel="noopener noreferrer">打开${esc(document.title)}（飞书）</a></section>`;
}

function plannedPage(route) {
  return heading(route.label,"栏目已建立，数据接入待完成。") +
    `<section class="panel">${tag("待建设","yellow")}<p class="planned-description">${esc(route.pending)}</p>
    ${route.id === "retrospectives" ? button("查看整改与验证","open-page:tasks") : ""}
    ${route.id.startsWith("report-") && canOpen("bug-reports") ? button("查看已接入报告","open-page:bug-reports",true) : ""}
    ${route.id.startsWith("culture-") && canOpen("culture") ? button("查看已有案例材料","open-page:culture",true) : ""}</section>`;
}
async function showBugBoard() {
  const data = await api("dashboard");
  const b = data.bugs;
  if (!b) return heading("Bug 看板","已发布的阶段缺陷快照。") + empty("Bug");
  return heading("Bug 看板","Race 3 / 3S RID 阶段 · 截至 " + data.bug_cutoff) +
    `<div class="cards">${card("有效缺陷",b.effective,"阶段快照")}${card("待解决",b.pending,"待解决缺陷")}
    ${card("待验证",b.testing,"等待验证")}${card("管理关闭率",b.closure === null ? "—" : b.closure.toFixed(2) + "%","按源数据状态统计")}</div>
    <section class="panel"><h2>阶段状态与严重程度</h2>${table(["待复现","已验证修复","待解决 Blocking","待解决 Critical"],
      [[b.reproduce,b.verified_fixed,b.blocking,b.critical]])}
    <p class="meta">阶段统计覆盖 Race 3 / 3S，构建号待补证。真实验证关闭需要逐条验证证据。</p>
    ${canOpen("bugs") ? button("查看 Bug 审计","open-page:bugs",true) : ""}</section>`;
}
const teamResponsibilities = {
  "曾建芬": {projects:["Race3s","Race3"],specialties:["质量体系建设专项"]},
  "肖强": {projects:["Chitu","NG3-Q3"],specialties:["Bug专项-消减/售后"]},
  "夏春燕": {projects:["Flex"],specialties:["Feature看护专项"]},
  "谢如菊": {projects:["Ocean2","SHRM2","Run2-NIO"],specialties:[]},
  "薛小玉": {projects:["Core2","Vertical3","Vertical3s"],specialties:[]},
  "杜楠楠": {projects:["SU11"],specialties:["FT smoke专项"]},
  "蒋青霞": {projects:["Dilu","NG3-Q3"],specialties:[]},
  "王程": {projects:["Vertical3","Vertical3s"],specialties:[]},
  "陈天忍": {projects:["Vertical3","Vertical3s"],specialties:["Daily 看护专项"]},
  "段郁敏": {projects:[],specialties:["FT smoke专项","Feature看护专项"]}
};
async function showTeam() {
  const people = own() || session.user.role === "reviewer" ?
    (await api("accounts")).filter(person=>person.enabled) : await api("people");
  const duties = {owner:"配置、导入、发布、账号管理及依据独立验证结论关闭整改",
    reviewer:"协同审核与按任务参与验证",member:"查看试点信息、完成分配任务并提交证据"};
  const rows = people.map(person => {
    const assignment = teamResponsibilities[person.name];
    const projects = assignment?.projects.map(project=>tag(project,"blue")).join(" ") || "—";
    const specialties = assignment?.specialties.map(specialty=>tag(specialty)).join(" ") || "—";
    return [esc(person.name),assignment ? tag("SQA") : "待确认",
      `<div class="actions">${projects}</div>`,`<div class="actions">${specialties}</div>`,
      `${esc(names[person.role] || "按任务指派")}<p class="meta">${esc(duties[person.role] || "在整改任务中查看具体职责与指派")}</p>`];
  });
  return heading("SQA 团队人员清单","团队项目与专项分工，依据 2026-11-24 提供的人员清单。") +
    `<section class="panel">${table(["姓名 / 账号","部门","负责项目","专项职责","平台协作职责"],rows)}
    <p class="meta">姓名即登录账号。整改验证人须独立于该条整改的提交人和共同提交人，具体整改职责以任务指派为准。</p></section>`;
}
async function showSources() {
  const [sync,board,...catalogs] = await Promise.all([api("sync"),api("release-board"),
    ...Object.keys(libraries).map(key=>api("library/"+key))]);
  const rows = [
    ["项目与指标工程",esc(board.source?.name || "待接入"),esc(board.source?.updated || "待确认"),esc(board.error || "已接入")],
    ["PR / Bug 文件同步",esc((sync.jobs || []).length + " 项最近任务"),esc(sync.checked || "待检查"),sync.enabled ? "自动检查已启用" : "待确认"]
  ];
  Object.keys(libraries).forEach((key,index) => {
    const catalog = catalogs[index];
    rows.push([esc(libraries[key]),esc(catalog.groups.length + " 组本机资料"),esc(catalog.checked || "待检查"),esc(catalog.error || "目录已接入")]);
  });
  return heading("数据源平台清单","查看现有接入来源与最近检查状态。") +
    `<section class="panel">${table(["来源","内容","更新时间 / 检查时间","状态"],rows)}
    <p class="meta">TP 需求、Bitbucket PR、TeamCity / CI/CD 的直连能力按实际授权和连接验证逐项确认。</p>
    ${button("查看同步与导入任务","open-page:imports",true)}</section>`;
}
async function showMetricCatalog() {
  const board = await api("release-board?detail=1");
  const items = board.projects.flatMap(project=>project.rows.map(row=>({...row,project:project.name})));
  return heading("指标集汇总清单","按工程来源汇总项目阶段配置，保留项目裁剪项。") +
    `<section class="panel"><p class="meta">${esc(board.source?.name || "待接入")} · ${items.length} 条项目阶段配置</p>
    ${board.error ? `<p class="notice caution">${esc(board.error)}</p>` : ""}
    ${table(["项目","阶段","指标","定义","目标","来源"],items.map(row=>[esc(row.project),esc(row.stage),esc(row.name),esc(row.definition),textValue(row.target),esc(row.source)]))}</section>`;
}
const governanceStates = {
  draft:["草稿 / 待补证","yellow"], submitted:["待独立复核","blue"], verified:["已独立复核","green"],
  rejected:["已退回","red"], stale:["来源已变化 / 待重新复核","yellow"],
  approved:["审批通过 / 待生效","blue"], effective:["有效","green"], obsolete:["已作废","red"]
};
function governanceTag(state) { return tag(...(governanceStates[state] || [state])); }
function governanceAction(kind, row, action, label) {
  dialog(label,field("复核结论或操作依据","note","",{type:"textarea",required:true}),label,
    form=>api(`governance/${kind}.action`,{id:row.id,revision:row.revision,action,note:form.get("note")}));
}
async function showGovernance() {
  const [{records},{items}] = await Promise.all([api("governance/records"),api("governance/catalog")]);
  let visible = records;
  const head = heading("口径与证据复核","按项目、版本、阶段和截止时间维护指标口径，由独立人员复核证据。") +
    `<section class="panel"><div class="filter">${field("筛选项目 / 版本 / 指标","evidence-query")}
    ${items.length ? button("登记指标证据","evidence-new") : ""}</div>
    <p class="meta">来源更新或登记内容变更后，需要重新提交复核。绿色结论须通过独立复核。原始来源、登记快照及各次操作保留审计记录。</p>
    <div id="evidence-table"></div></section>`;
  return {html:head,mount() {
    const draw = () => {
      const query = document.querySelector('[name="evidence-query"]').value.trim().toLowerCase();
      visible = records.filter(r=>[r.project,r.version,r.stage,r.payload.name].join(" ").toLowerCase().includes(query));
      $("evidence-table").innerHTML = table(["项目 / 版本 / 阶段","指标与口径","目标 / 实绩","证据 / 来源","复核状态","操作"],visible.map(r=>[
        `${esc(r.project)}<p>${esc(r.version)} · ${esc(r.stage)}</p><p class="meta">截至 ${esc(r.payload.cutoff || "待补证")}</p>`,
        `${esc(r.payload.name)}<p>${esc(r.payload.formula)}</p><p class="meta">范围 ${esc(r.payload.scope)}<br>规则 ${esc(r.payload.rule_version)}</p>`,
        `${esc(r.payload.target || "待补证")} / ${esc(r.payload.actual || "待补证")}<p class="meta">目标批准 ${esc(r.payload.approval || "待补证")}</p>`,
        `<details><summary>查看证据</summary><p>${esc(r.payload.evidence)}</p><p>${esc(r.payload.source)}</p>
        <p class="meta">SHA-256 ${esc(r.payload.snapshot_sha256)}</p>${["owner","reviewer"].includes(session.user.role) ? `<a href="/api/governance-snapshot/${esc(r.payload.snapshot_sha256)}">下载登记时来源快照</a>` : ""}</details>`,
        `${governanceTag(r.effective_state)} ${lamp(r.effective_lamp)}<p class="meta">登记 ${esc(r.editor_name)}<br>复核 ${esc(r.reviewer_name || "待复核")}<br>修订 ${r.revision}</p>`,
        `<div class="actions">${own() || r.editor === session.user.id ? button("编辑","evidence-edit:"+r.id,true) : ""}
        ${["draft","rejected"].includes(r.state) && r.effective_state !== "stale" && r.editor === session.user.id ? button("提交复核","evidence-submit:"+r.id,true) : ""}
        ${r.state === "submitted" && r.effective_state !== "stale" && ["owner","reviewer"].includes(session.user.role) && r.editor !== session.user.id && r.submitter !== session.user.id ? button("复核通过","evidence-verify:"+r.id)+button("退回","evidence-reject:"+r.id,true) : ""}</div>`]));
    };
    document.querySelector('[name="evidence-query"]').oninput = draw;
    $("content").onclick = event => {
      const action = event.target.closest("[data-action]")?.dataset.action || "";
      if (action === "evidence-new") return editEvidence(items);
      const [operation,id] = action.split(":");
      const row = records.find(r=>r.id===Number(id));
      if (!row) return;
      if (operation === "evidence-edit") editEvidence(items,row);
      else governanceAction("evidence",row,operation.replace("evidence-",""),operation.endsWith("submit") ? "提交复核" : operation.endsWith("verify") ? "独立复核通过" : "退回补证");
    };
    draw();
  }};
}
function editEvidence(items, record) {
  const source = items.find(r=>r.source_id===record?.source_id) || items[0];
  if (!source) return notify("当前来源已撤回，请核对源表后再登记");
  const values = record?.payload || source;
  const fields = select("来源指标","source_id",items.map(r=>[r.source_id,`${r.project} · ${r.stage} · ${r.name}`]),source.source_id) +
    field("项目版本","version",record?.version || "",{required:true}) +
    field("数据截止日期（北京时间）","cutoff",values.cutoff || "",{type:"date"}) +
    field("计算公式 / 分子分母","formula",values.formula || "",{type:"textarea"}) +
    field("统计范围 / 纳入与排除条件","scope",values.scope || "",{type:"textarea"}) +
    field("目标","target",values.target || "") + field("目标批准凭据","approval",values.approval || "") +
    field("实绩","actual",values.actual || "") + field("证据定位及核对说明","evidence",values.evidence || "",{type:"textarea"}) +
    field("口径规则版本","rule_version",values.rule_version || "") +
    select("申请复核的灯色","lamp",Object.entries(lampLabels).map(([key,[label]])=>[key,label]),values.lamp || "");
  dialog(record ? "更新指标口径及证据" : "登记指标口径及证据",fields,"保存草稿",form=>api("governance/evidence.save",{...Object.fromEntries(form),revision:record?.revision || 0}));
  const form = $("dialog-content").querySelector("form");
  if (record) {
    form.elements.source_id.disabled = true;
    // Disabled controls are excluded by FormData, so retain identity in a hidden input.
    form.insertAdjacentHTML("beforeend",`<input type="hidden" name="source_id" value="${esc(record.source_id)}">`);
    form.elements.version.readOnly = true;
  } else {
    form.elements.source_id.onchange = () => {
      const selected = items.find(r=>r.source_id===form.elements.source_id.value);
      for (const key of ["formula","target","actual","evidence","cutoff"]) form.elements[key].value = selected[key] || "";
    };
  }
}
async function showControlledDocuments() {
  const data = await api("governance/documents");
  const rows = data.documents;
  const head = heading("受控文件清单","登记文件版本、责任人、批准凭据、生效日期和作废状态，保留历次修订快照。") +
    `<section class="panel">${own() ? button("登记受控文件","document-new") : ""}
    <p class="meta">公司标准版本、正式发布批准角色和记录保留期限继续待确认。现有规范候选文件以草稿登记，生效需完成审批和发布。</p>
    ${table(["编号 / 名称","版本 / 责任人","来源与批准凭据","状态 / 生效 / 保留","操作"],rows.map(r=>[
      `${esc(r.code)}<p>${esc(r.payload.title)}</p>`,`${esc(r.payload.version)}<p>${esc(r.payload.owner)}</p>`,
      `<details><summary>查看来源与凭据</summary><p>${esc(r.payload.source_key)}</p><p>${esc(r.payload.approval_evidence || "待确认")}</p>
      ${r.payload.url ? `<a href="${esc(r.payload.url)}" target="_blank" rel="noopener noreferrer">打开公司文件</a>` : ""}
      ${r.payload.sha256 && (r.state === "effective" || ["owner","reviewer"].includes(session.user.role)) ? `<a href="/api/governance-snapshot/${esc(r.payload.sha256)}">下载文件快照</a>` : ""}
      <p class="meta">${esc(r.payload.sha256)}</p></details>`,
      `${governanceTag(r.state)}<p>${esc(r.payload.effective_date || "待确认")}<br>保留 ${esc(r.payload.retention)}</p>`,
      `<div class="actions">${own() ? button("修订","document-edit:"+r.id,true) : ""}
      ${own() && r.state === "draft" ? button("提交审批","document-submit:"+r.id,true) : ""}
      ${r.state === "submitted" && ["owner","reviewer"].includes(session.user.role) && r.editor !== session.user.id && r.submitter !== session.user.id ? button("批准","document-approve:"+r.id)+button("退回","document-reject:"+r.id,true) : ""}
      ${own() && r.state === "approved" ? button("生效发布","document-publish:"+r.id) : ""}
      ${own() && ["approved","effective"].includes(r.state) ? button("作废","document-obsolete:"+r.id,true) : ""}</div>
      <details><summary>修订记录</summary>${data.history.filter(h=>h.document_id===r.id).map(h=>`<p>修订 ${h.revision} · ${esc(h.payload.version)} · ${esc(h.state)} · ${esc(h.changed)}</p>`).join("")}</details>`]))}</section>`;
  return {html:head,mount() {
    $("content").onclick = event => {
      const action = event.target.closest("[data-action]")?.dataset.action || "";
      if (action === "document-new") return editDocument(data);
      const [operation,id] = action.split(":");
      const row = rows.find(r=>r.id===Number(id));
      if (!row) return;
      if (operation === "document-edit") editDocument(data,row);
      else governanceAction("document",row,operation.replace("document-",""),{submit:"提交审批",approve:"批准文件",reject:"退回修订",publish:"生效发布",obsolete:"作废文件"}[operation.replace("document-","")]);
    };
  }};
}
function editDocument(data, row) {
  const values = row?.payload || {};
  dialog("受控文件登记",field("文件编号","code",row?.code || "",{required:true}) +
    field("文件名称","title",values.title || "",{required:true}) + field("文件版本","version",values.version || "待确认",{required:true}) +
    field("文件责任人","owner",values.owner || "待确认",{required:true}) +
    select("本地候选文件","source_key",[["","使用公司文件链接"],...data.candidates.map(r=>[r.key,r.name])],values.source_key || "") +
    field("公司文件链接","url",values.url || "") + field("批准凭据定位","approval_evidence",values.approval_evidence || "",{type:"textarea"}) +
    field("拟生效日期","effective_date",values.effective_date || "",{type:"date"}) +
    field("记录保留期限","retention",values.retention || "待确认") + field("修订说明","note","",{type:"textarea",required:true}),
    "保存草稿",form=>api("governance/document.save",{...Object.fromEntries(form),revision:row?.revision || 0}));
  if (row) $("dialog-content").querySelector('[name="code"]').readOnly = true;
}
async function showTeamGovernance() {
  if (!session.governance) return showTeam();
  const {people} = await api("governance/team");
  const rights = {owner:"全部项目管理、导入发布、账号与分工维护、依据独立验证关闭整改",reviewer:"跨项目证据复核、受控文件协同审核、按指定任务独立验证",member:"分工项目指标查看与证据登记、已生效规范查看、分派整改及独立验证"};
  return {html:heading("SQA 团队人员清单","项目与专项分工依据已确认人员清单登记，调整由负责人维护并保留审计。") +
    `<section class="panel">${table(["姓名 / 状态","负责项目","专项职责","岗位权限",""],people.map(r=>[
      `${esc(r.name)} ${tag(r.enabled ? "启用" : "停用")}`,r.projects.map(p=>tag(p,"blue")).join(" ") || "待分配项目",
      r.specialties.map(p=>tag(p)).join(" ") || "—",`${esc(names[r.role])}<p class="meta">${esc(rights[r.role])}</p>`,
      own() ? button("维护分工","team-edit:"+r.id,true) : ""]))}
    <p class="meta">项目授权按明确名称校验。Race3 与归属待确认的 Race3 来源对应，Chitu 与 Chitu NIO 对应。专项职责保留岗位分工，跨项目访问由负责人明确授予。</p></section>`,mount() {
      $("content").onclick = event => {
        const action = event.target.closest("[data-action]")?.dataset.action || "";
        const row = people.find(r=>action==="team-edit:"+r.id);
        if (!row) return;
        dialog("维护 "+row.name+" 的岗位分工",field("负责项目，每行一个","projects",row.projects.join("\n"),{type:"textarea"}) +
          field("专项职责，每行一个","specialties",row.specialties.join("\n"),{type:"textarea"}) + field("调整依据","note","",{type:"textarea",required:true}),"保存分工",
          form=>api("governance/team.save",{id:row.id,revision:row.revision || 0,note:form.get("note"),
            projects:form.get("projects").split("\n").map(v=>v.trim()).filter(Boolean),specialties:form.get("specialties").split("\n").map(v=>v.trim()).filter(Boolean)}));
      };
    }};
}

const screens = {
  ...Object.fromEntries(Object.values(routes).filter(route=>route.pending).map(route=>[route.id,()=>plannedPage(route)])),
  ...Object.fromEntries(Object.keys(externalDocuments).map(id=>[id,()=>showExternalDocument(id)])),
  home, "quality-board":()=>home("质量看板（阶段指标进展）"), "bug-board":showBugBoard,
  metrics:showMetrics, "pilot-metrics":showPilotMetrics, pr:showPR, bugs:showBugs,
  tasks:showTasks, imports:showImports, admin:showAdmin, team:showTeamGovernance, sources:showSources,
  governance:showGovernance, "controlled-documents":showControlledDocuments,
  "metric-catalog":showMetricCatalog,
  ...Object.fromEntries(Object.keys(libraries).map(key=>[key,()=>showLibrary(key)])),
  "culture-cases":()=>showLibrary("culture","质量案例库","culture-cases"),
  "report-weekly":()=>showLibrary("bug-reports","质量周报","report-weekly","周报")
};
async function show(name) {
  if (!canOpen(name)) { notify("当前账号无此栏目访问权限"); return; }
  const version = ++renderCount;
  trendObserver?.disconnect();
  clearInterval(libraryTimer);
  page = name;
  $("content").onclick = event => {
    const action = event.target.closest("[data-action]")?.dataset.action || "";
    if (action === "open-imports") show("imports");
    if (action.startsWith("open-page:")) show(action.slice(10));
  };
  $("content").innerHTML = '<p class="muted">正在读取…</p>';
  $("nav").querySelectorAll("[data-page]").forEach(item => {
    if (item.dataset.page === (name === "pilot-metrics" ? "metrics" : name)) {
      item.setAttribute("aria-current","page");
      let ancestor = item.parentElement;
      while (ancestor && ancestor !== $("nav")) {
        if (ancestor.tagName === "DETAILS") ancestor.open = true;
        ancestor = ancestor.parentElement;
      }
    }
    else item.removeAttribute("aria-current");
  });
  try {
    const result = await screens[name]();
    if (version !== renderCount) return;
    $("content").innerHTML = typeof result === "string" ? result : result.html;
    if (result.mount) result.mount();
  } catch (failure) {
    if (version === renderCount) $("content").innerHTML = `<p class="error">${esc(failure.message)}</p>`;
  }
}

$("logout").onclick = async () => {
  try {
    await api("logout",{});
    clearInterval(libraryTimer);
    ++renderCount;
    session = await api("session");
    renderAuth();
  } catch (failure) { notify(failure.message); }
};
api("session").then(result => {
  session = result;
  if (session.user) bootShell(); else renderAuth();
}).catch(failure => {
  $("auth").hidden = false;
  $("auth").innerHTML = `<p class="error">${esc(failure.message)}</p>`;
});
