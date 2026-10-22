// 生成《顺翼 QMS 开发设计 V2》Word 版
const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  HeadingLevel, AlignmentType, ImageRun, PageBreak, Header, Footer, PageNumber, TableOfContents, LevelFormat,
  VerticalAlign, PositionalTab, PositionalTabAlignment, PositionalTabRelativeTo, PositionalTabLeader,
} = require('docx');
const HEADINGS = [];

const DIR = __dirname;
const data = JSON.parse(fs.readFileSync(path.join(DIR, 'content.json'), 'utf8'));
const OUT = process.argv[2] || path.join(DIR, 'qms-design.docx');

const BLUE = '1263F0', INK = '1F2D3D', MUTED = '6B7A8C', LINE = 'C9D3E1', HEAD_BG = 'E8F1FF', SOFT = 'F5F7FB';
const FONT = { ascii: 'Arial', hAnsi: 'Arial', eastAsia: 'Microsoft YaHei', cs: 'Arial' };
const MONO = { ascii: 'Consolas', hAnsi: 'Consolas', eastAsia: 'Microsoft YaHei', cs: 'Consolas' };
const CONTENT_W = 9638; // A4 宽 11906 − 左右各 1134（2 cm）

// ---------------------------------------------------------------- 文本
function tr(r, base = {}) {
  const o = { text: r.t, font: r.code ? MONO : FONT, size: base.size || 21, color: base.color || INK };
  if (r.b || base.bold) o.bold = true;
  if (r.color) o.color = r.color;
  if (r.code) o.color = r.color || '24508F';
  if (r.small) { o.color = MUTED; o.size = Math.max(16, (base.size || 21) - 3); }
  return new TextRun(o);
}
function para(runs, opt = {}) {
  return new Paragraph({
    children: runs.map(r => tr(r, opt)),
    spacing: { after: opt.after ?? 100, line: opt.line ?? 320 },
    alignment: opt.align,
    ...(opt.extra || {}),
  });
}
const txt = (t, o = {}) => ({ t, ...o });

// ---------------------------------------------------------------- 表格
const border = { style: BorderStyle.SINGLE, size: 4, color: LINE };
const borders = { top: border, bottom: border, left: border, right: border };

function plainLen(runs) { return runs.reduce((n, r) => n + [...r.t].length, 0); }

function colWidths(head, rows, total = CONTENT_W) {
  const n = Math.max(head.length, ...rows.map(r => r.reduce((a, c) => a + (c.colspan || 1), 0)));
  const w = new Array(n).fill(4);
  const all = [head.map(h => ({ runs: h, colspan: 1 })), ...rows];
  for (const r of all) {
    let i = 0;
    for (const c of r) {
      if ((c.colspan || 1) === 1 && i < n) w[i] = Math.max(w[i], Math.min(plainLen(c.runs), 120));
      i += c.colspan || 1;
    }
  }
  const weights = w.map(x => Math.sqrt(x) + 1.5);
  const sum = weights.reduce((a, b) => a + b, 0);
  const out = weights.map(x => Math.max(560, Math.round(total * x / sum)));
  const diff = total - out.reduce((a, b) => a + b, 0);
  out[out.indexOf(Math.max(...out))] += diff;
  return out;
}

function cell(runs, width, opt = {}) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA }, columnSpan: opt.colspan,
    shading: opt.fill ? { fill: opt.fill, type: ShadingType.CLEAR, color: 'auto' } : undefined,
    margins: { top: 60, bottom: 60, left: 90, right: 90 }, verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({ children: runs.map(r => tr(r, { size: opt.size, bold: opt.bold })),
      spacing: { after: 0, line: 280 } })],
  });
}

function table(b) {
  const widths = colWidths(b.head, b.rows);
  const n = widths.length;
  const size = n >= 7 ? 16 : n >= 5 ? 17 : 18;
  const rows = [];
  if (b.head.length) {
    rows.push(new TableRow({ tableHeader: true, children: b.head.map((h, i) => cell(h, widths[i], { fill: HEAD_BG, size, bold: true })) }));
  }
  for (const r of b.rows) {
    let i = 0;
    const cells = [];
    for (const c of r) {
      const span = c.colspan || 1;
      const w = widths.slice(i, i + span).reduce((a, x) => a + x, 0);
      cells.push(cell(c.runs, w, { colspan: span > 1 ? span : undefined, size }));
      i += span;
    }
    while (i < n) { cells.push(cell([], widths[i], { size })); i++; }
    rows.push(new TableRow({ cantSplit: true, children: cells }));
  }
  return [new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths, rows }),
    new Paragraph({ children: [], spacing: { after: 120 } })];
}

function flow(b) {
  const widths = [800, 3000, CONTENT_W - 3800];
  const rows = [new TableRow({ tableHeader: true, children: [
    cell([txt('步骤')], widths[0], { fill: HEAD_BG, bold: true, size: 18 }),
    cell([txt('节点')], widths[1], { fill: HEAD_BG, bold: true, size: 18 }),
    cell([txt('处理内容 / 说明')], widths[2], { fill: HEAD_BG, bold: true, size: 18 })] })];
  let k = 0;
  for (const s of b.steps) {
    const isDec = s.kind === 'dec';
    if (!isDec) k++;
    const name = isDec ? `〈判断〉${s.name}` : (s.kind === 'opt' ? `${s.name}（条件节点）` : s.name);
    rows.push(new TableRow({ cantSplit: true, children: [
      cell([txt(isDec ? '◇' : String(k), { b: true, color: BLUE })], widths[0], { size: 18, fill: s.kind === 'end' ? HEAD_BG : undefined }),
      cell([txt(name, { b: true, color: isDec ? 'C27A00' : INK })], widths[1], { size: 18, fill: s.kind === 'end' ? HEAD_BG : undefined }),
      cell([txt(s.sub || '—', { color: s.sub ? INK : MUTED })], widths[2], { size: 18, fill: s.kind === 'end' ? HEAD_BG : undefined }),
    ] }));
  }
  return [new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths, rows }),
    new Paragraph({ children: [], spacing: { after: 60 } })];
}

function arch(b) {
  const widths = [1700, CONTENT_W - 1700];
  const rows = b.rows.map(r => new TableRow({ cantSplit: true, children: [
    cell([txt(r.layer, { b: true, color: 'FFFFFF' })], widths[0], { fill: BLUE, size: 19 }),
    new TableCell({ borders, width: { size: widths[1], type: WidthType.DXA }, margins: { top: 60, bottom: 60, left: 90, right: 90 },
      children: r.boxes.map(([name, sub]) => new Paragraph({ spacing: { after: 30, line: 280 }, children: [
        tr(txt(name, { b: true }), { size: 19 }), ...(sub ? [tr(txt('：' + sub, { color: MUTED }), { size: 18 })] : [])] })) }),
  ] }));
  return [new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths, rows }),
    new Paragraph({ children: [], spacing: { after: 120 } })];
}

function callout(b) {
  const color = b.warn ? 'D98A00' : BLUE;
  return [new Paragraph({
    children: b.runs.map(r => tr(r, { size: 20 })), spacing: { before: 80, after: 160, line: 320 },
    shading: { fill: b.warn ? 'FFF8EA' : 'F2F7FF', type: ShadingType.CLEAR, color: 'auto' },
    border: { left: { style: BorderStyle.SINGLE, size: 24, color, space: 8 } }, indent: { left: 120, right: 120 },
  })];
}

// ---------------------------------------------------------------- 原型截图
const PROTOS = [
  ['home', '质量门户', 'P3 功能页：OA 门户卡片样式。顶部为待办工作、待检任务、在途异常、在途变更、待签收文件计数与待办卡片；下方为质量日报一句话结论、异常柏拉图与常用功能入口。'],
  ['new', '新建工作', 'P1 流程在通达流程中心「新建工作」中按「质量管理-文档 / 实验室 / 审批」分类展示，与 OA 现有流程入口一致。'],
  ['form', '文件借阅申请表', 'P1 通达流程表单：红色流水号标题、左侧表单 / 附件 / 会签工具条、黄底自动带出字段、审批意见分区、底部「转交下一步 / 保存 / 保存返回 / 取消」。借阅文件用 QMS 文件选择控件，只能选受控正式版。'],
  ['turn', '转交下一步', 'OA 自带的转交对话框：选择下一步骤与主办人、经办人，并发送事务提醒。到「文控」节点办结时，流程插件回调 QMS 开放阅读权限并设定到期收回。'],
  ['insp', '首件检验', 'P3 检验终端（平板优先）：人机料法与调机次数、按控制计划带出的检验项目，录入实测值即按 LSL / USL 自动判定，SC / CC 高亮；图纸悬浮窗可缩放拖动；不合格时提示反应计划并可一键生成异常单。'],
  ['nc', '异常单办理', 'P2 QMS 办理页沿用通达表单样式：不合格品信息自动带入，处置方式含「下工站确认」，会签明细含计划完成日期、产能影响与手写签名；可退回到指定已办节点、追加反馈、发起 ECN。'],
  ['cp', '控制计划编制', 'P2 编制网格：同工序合并显示，规范 / 公差实时拆分为 LSL / USL，新增「适用检验类型」「数据类型」「是否 SPC」三列；提交前按填写说明校验并定位到行。'],
  ['prj', '项目甘特图', 'P3 甘特图：每个节点一行，计划条与实际条并列，按绿 / 黄 / 红预警着色，橙线为今天。'],
];
function protos() {
  const out = [];
  let i = 1;
  for (const [id, name, desc] of PROTOS) {
    const file = path.join(DIR, `proto-${id}.png`);
    const buf = fs.readFileSync(file);
    const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
    let dw = 600, dh = Math.round(h * dw / w);
    if (dh > 700) { dh = 700; dw = Math.round(w * dh / h); }
    HEADINGS.push({ level: 2, text: `5.${i} ${name}` });
    out.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: `5.${i} ${name}`, font: FONT })] }));
    out.push(para([txt(desc)]));
    out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
      children: [new ImageRun({ type: 'png', data: buf, transformation: { width: dw, height: dh },
        altText: { title: name, description: `${name}原型`, name } })] }));
    out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 },
      children: [new TextRun({ text: `图 5-${i}　${name}原型`, font: FONT, size: 18, color: MUTED })] }));
    i++;
  }
  return out;
}

// ---------------------------------------------------------------- 正文
const body = [];
let module3 = false;
for (const b of data.blocks) {
  switch (b.type) {
    case 'h1': {
      const isModule = /^3\.\d/.test(b.text);
      if (isModule && !module3) {
        module3 = true;
        body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: true, children: [new TextRun({ text: '3 各模块设计', font: FONT })] }));
        HEADINGS.push({ level: 1, text: '3 各模块设计' });
      }
      HEADINGS.push({ level: isModule ? 2 : 1, text: b.text });
      body.push(new Paragraph({ heading: isModule ? HeadingLevel.HEADING_2 : HeadingLevel.HEADING_1,
        pageBreakBefore: !isModule, children: [new TextRun({ text: b.text, font: FONT })] }));
      break;
    }
    case 'h2': HEADINGS.push({ level: 2, text: b.text }); body.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: b.text, font: FONT })] })); break;
    case 'h3': body.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: b.runs.map(r => new TextRun({ text: r.t, font: FONT, bold: true, color: r.color })) })); break;
    case 'p': if (b.runs.length) body.push(para(b.runs)); break;
    case 'note': body.push(para(b.runs, { size: 19, after: 200 })); break;
    case 'ul': for (const it of b.items) body.push(new Paragraph({ numbering: { reference: 'bullets', level: 0 },
      spacing: { after: 60, line: 300 }, children: it.map(r => tr(r)) })); break;
    case 'table': body.push(...table(b)); break;
    case 'flow': body.push(...flow(b)); break;
    case 'arch': body.push(...arch(b)); break;
    case 'callout': body.push(...callout(b)); break;
    case 'states': body.push(para([txt('状态：', { b: true }), txt(b.items.join(' → '))], { after: 140 })); break;
    case 'protos': body.push(...protos()); break;
  }
}

// ---------------------------------------------------------------- 封面
const m = data.meta;
const cover = [
  new Paragraph({ spacing: { before: 2400, after: 200 }, children: [new TextRun({ text: '上海顺翼紧固件有限公司', font: FONT, size: 28, color: MUTED })] }),
  new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: '质量管理系统（QMS）', font: FONT, size: 52, bold: true, color: INK })] }),
  new Paragraph({ spacing: { after: 400 }, border: { bottom: { style: BorderStyle.SINGLE, size: 18, color: BLUE, space: 8 } },
    children: [new TextRun({ text: '开发设计方案 V2', font: FONT, size: 44, bold: true, color: BLUE })] }),
  para([txt(m.lead)], { after: 400 }),
];
const metaRows = [
  ['文档版本', 'V2.0'], ['编制日期', '2026-10-22'],
  ['依据文档', '《上海顺翼质量管理系统需求调研文档（字段级）》D01–D24'],
  ['平台', '通达 OA 流程中心低代码引擎 / 审批中心 + QMS 扩展服务 + 鼎捷 ERP'],
  ['替代版本', 'V1（依据 2026-10-15 调研报告）'],
];
cover.push(new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: [2200, CONTENT_W - 2200],
  rows: metaRows.map(([k, v]) => new TableRow({ children: [cell([txt(k, { b: true })], 2200, { fill: SOFT, size: 20 }), cell([txt(v)], CONTENT_W - 2200, { size: 20 })] })) }));
cover.push(new Paragraph({ children: [], spacing: { after: 240 } }));
const cw = Math.floor(CONTENT_W / m.counters.length);
const cws = m.counters.map((_, i) => i === m.counters.length - 1 ? CONTENT_W - cw * (m.counters.length - 1) : cw);
cover.push(new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: cws, rows: [
  new TableRow({ children: m.counters.map(([l], i) => cell([txt(l, { color: MUTED })], cws[i], { fill: HEAD_BG, size: 17 })) }),
  new TableRow({ children: m.counters.map(([, v], i) => cell([txt(v, { b: true })], cws[i], { fill: HEAD_BG, size: 32 })) }),
] }));

const toc = [
  new Paragraph({ pageBreakBefore: true, spacing: { after: 200 }, children: [new TextRun({ text: '目录', font: FONT, size: 32, bold: true })] }),
];
const PAGES = fs.existsSync(path.join(DIR, 'pages.json')) ? JSON.parse(fs.readFileSync(path.join(DIR, 'pages.json'), 'utf8')) : {};
for (const h of HEADINGS) {
  toc.push(new Paragraph({ spacing: { after: h.level === 1 ? 70 : 30, before: h.level === 1 ? 90 : 0 }, indent: { left: h.level === 1 ? 0 : 420 },
    children: [new TextRun({ text: h.text, font: FONT, size: h.level === 1 ? 21 : 19, bold: h.level === 1 }),
      new TextRun({ children: [new PositionalTab({ alignment: PositionalTabAlignment.RIGHT, relativeTo: PositionalTabRelativeTo.MARGIN, leader: PositionalTabLeader.DOT })], font: FONT }),
      new TextRun({ text: String(PAGES[h.text] || ''), font: FONT, size: h.level === 1 ? 21 : 19 })] }));
}

const doc = new Document({
  creator: 'QMS 项目组', title: '顺翼 QMS 开发设计方案 V2', description: '按字段级需求调研文档调整的开发设计',
  features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: 21, color: INK } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { font: FONT, size: 32, bold: true, color: INK }, paragraph: { spacing: { before: 240, after: 200 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: BLUE, space: 4 } } } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { font: FONT, size: 26, bold: true, color: BLUE }, paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
      { id: 'Heading3', name: 'Heading 3', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { font: FONT, size: 22, bold: true, color: INK }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [{ reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 420, hanging: 260 } } } }] }] },
  sections: [
    { properties: { page: { margin: { top: 1300, bottom: 1200, left: 1134, right: 1134 } } }, children: cover },
    { properties: { page: { margin: { top: 1300, bottom: 1200, left: 1134, right: 1134 } } },
      headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 4 } },
        children: [new TextRun({ text: '顺翼 QMS 开发设计方案 V2', font: FONT, size: 17, color: MUTED })] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
        children: [new TextRun({ children: ['第 ', PageNumber.CURRENT, ' 页 / 共 ', PageNumber.TOTAL_PAGES, ' 页'], font: FONT, size: 17, color: MUTED })] })] }) },
      children: [...toc, ...body] },
  ],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log('written', OUT, buf.length); });
