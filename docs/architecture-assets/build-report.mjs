import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "file:///C:/Users/Tishan%20Kumar%20B/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/marked/lib/marked.esm.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const docsDir = path.resolve(here, "..");
const inputPath = path.join(docsDir, "ECDAT_IMPLEMENTED_ARCHITECTURE.md");
const outputPath = path.join(docsDir, "ECDAT_IMPLEMENTED_ARCHITECTURE.html");
const diagramPath = path.join(here, "ecdat-current.architecture.html");

const markdown = fs.readFileSync(inputPath, "utf8");
fs.accessSync(diagramPath, fs.constants.R_OK);

const slugCounts = new Map();
const headings = [];
const renderer = new marked.Renderer();
renderer.heading = ({ tokens, depth }) => {
  const text = tokens.map((token) => token.raw ?? token.text ?? "").join("").replace(/[*_`]/g, "");
  const base = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "section";
  const count = slugCounts.get(base) ?? 0;
  slugCounts.set(base, count + 1);
  const id = count ? `${base}-${count + 1}` : base;
  if (depth === 2 || depth === 3) headings.push({ depth, text, id });
  return `<h${depth} id="${id}">${marked.parseInline(text)}</h${depth}>`;
};
renderer.code = ({ text, lang }) => {
  const cls = lang === "mermaid" ? "diagram-source" : "code-block";
  const label = lang === "mermaid" ? '<div class="diagram-label">Architecture flow specification</div>' : "";
  const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `${label}<pre class="${cls}"><code>${escaped}</code></pre>`;
};

let body = marked.parse(markdown, { renderer, gfm: true });
body = body.replace(
  /<p><img src="architecture-assets\/ecdat-current\.architecture\.visual-check\.2048x1320\.light\.png" alt="ECDAT implemented architecture"><\/p>/,
  `<figure class="architecture-figure"><img src="architecture-assets/ecdat-current.architecture.visual-check.2048x1320.light.png" alt="ECDAT implemented architecture"><figcaption>Figure 1. Implemented ECDAT runtime and evidence path.</figcaption></figure>`,
);

const toc = headings
  .filter((heading) => heading.depth === 2)
  .map((heading) => `<li><a href="#${heading.id}">${heading.text}</a></li>`)
  .join("");

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ECDAT Implemented Architecture</title>
<style>
  @page { size: A4; margin: 17mm 16mm 18mm; }
  * { box-sizing: border-box; }
  html { color: #111827; font-family: "Segoe UI", Arial, sans-serif; font-size: 10pt; line-height: 1.48; }
  body { margin: 0 auto; max-width: 186mm; background: white; }
  .cover { min-height: 258mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }
  .eyebrow { color: #075985; font-size: 9pt; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 18mm; }
  .cover h1 { font-size: 34pt; line-height: 1.06; margin: 0 0 8mm; max-width: 155mm; }
  .cover .subtitle { font-size: 16pt; line-height: 1.35; color: #334155; max-width: 145mm; margin-bottom: 26mm; }
  .cover dl { display: grid; grid-template-columns: 34mm 1fr; gap: 2mm 6mm; margin: 0; font-size: 10.5pt; }
  .cover dt { font-weight: 700; color: #0f172a; }
  .cover dd { margin: 0; color: #475569; }
  .toc { page-break-after: always; }
  .toc ol { columns: 2; column-gap: 14mm; padding-left: 6mm; }
  .toc li { break-inside: avoid; margin: 0 0 2.5mm; }
  .toc a { color: #0f172a; text-decoration: none; }
  main > h1, main > p:nth-of-type(-n+3) { display: none; }
  h1, h2, h3, h4 { color: #000; break-after: avoid; }
  h1 { font-size: 25pt; line-height: 1.12; }
  h2 { font-size: 17pt; margin: 10mm 0 4mm; padding-top: 1mm; }
  h3 { font-size: 12.5pt; margin: 7mm 0 2.5mm; }
  h4 { font-size: 10.5pt; margin: 5mm 0 2mm; }
  p { margin: 0 0 3.2mm; orphans: 3; widows: 3; }
  ul, ol { margin: 0 0 4mm 5mm; padding-left: 5mm; }
  li { margin-bottom: 1.2mm; }
  code { font-family: "Cascadia Mono", Consolas, monospace; font-size: .91em; }
  p code, li code, td code { background: #f1f5f9; padding: .15em .3em; border-radius: 3px; }
  pre { white-space: pre-wrap; break-inside: avoid; font-size: 8.1pt; line-height: 1.35; padding: 4mm; background: #f8fafc; border: 1px solid #d9e2ec; border-radius: 5px; margin: 3mm 0 5mm; }
  .diagram-source { color: #0f172a; background: #f8fafc; }
  .diagram-label { font-size: 8pt; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: .06em; margin-top: 4mm; }
  table { width: 100%; border-collapse: collapse; margin: 3mm 0 6mm; font-size: 8.3pt; break-inside: auto; }
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  th, td { border: 1px solid #d9d9d9; padding: 2.2mm 2.4mm; vertical-align: middle; text-align: left; }
  th { background: #12324a; color: white; font-weight: 700; }
  tbody tr:nth-child(even) td { background: #f5f9fc; }
  blockquote { margin: 4mm 0; padding-left: 4mm; border-left: 3px solid #0e7490; color: #334155; }
  .architecture-figure { margin: 5mm 0 7mm; break-inside: avoid; }
  .architecture-figure img { width: 100%; height: auto; display: block; }
  figcaption { margin-top: 2mm; color: #475569; font-size: 8.3pt; }
  hr { border: 0; margin: 9mm 0 3mm; }
  a { color: #075985; }
  @media print {
    body { max-width: none; }
    a { color: inherit; text-decoration: none; }
    h2 { break-before: auto; }
    .architecture-figure { break-before: auto; }
  }
</style>
</head>
<body>
<section class="cover">
  <div class="eyebrow">System architecture report</div>
  <h1>ECDAT Implemented Architecture</h1>
  <div class="subtitle">End to end frontend backend scanner data and deployment architecture</div>
  <dl>
    <dt>Repository</dt><dd>ECDAT-ImpactX</dd>
    <dt>Snapshot</dt><dd>20 September 2026</dd>
    <dt>Application</dt><dd>Enterprise Cryptographic Discovery and Analysis Tool</dd>
    <dt>Coverage</dt><dd>Frontend, API, scanner, data model, security, deployment, operations, and verification</dd>
  </dl>
</section>
<section class="toc">
  <h1>Contents</h1>
  <ol>${toc}</ol>
</section>
<main>${body}</main>
</body>
</html>`;

fs.writeFileSync(outputPath, html, "utf8");
console.log(outputPath);
