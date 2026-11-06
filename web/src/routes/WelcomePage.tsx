import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  FileText,
  GitBranch,
  ScanText,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs.tsx";
import "@/styles/welcome.css";
import sansLicense from "@/assets/fonts/hrs-sans-sc-OFL.txt?url";
import serifLicense from "@/assets/fonts/hrs-serif-sc-OFL.txt?url";
import sourceScan from "@/assets/product/youth-1915-page21.jpg";

const setupUrl =
  "https://github.com/TexcolCloud/historical-research-system#首次启动";
const sample = "靑年如初春，如朝日，如百卉之萌動。";
const sampleSource =
  "https://commons.wikimedia.org/wiki/File:新青年_第1卷第1號.djvu";
type PreviewKind = "review" | "reading" | "search";

function ProductPreview() {
  const [mode, setMode] = useState<PreviewKind>("review");
  const [query, setQuery] = useState("初春");
  const [note, setNote] = useState(false);
  const found = query.trim() && sample.includes(query.trim());
  return (
    <section className="product-preview" aria-label="产品功能交互示例">
      <div className="product-preview-title">
        <span>
          <span className="product-live-dot" />
          从原件到成果
        </span>
        <small>公版影印 · 功能演示</small>
      </div>
      <Tabs
        value={mode}
        onValueChange={(value) => {
          if (value === "review" || value === "reading" || value === "search")
            setMode(value);
        }}
        className="product-preview-tabs"
      >
        <TabsList aria-label="预览产品功能" variant="line">
          <TabsTrigger
            value="review"
            id="product-tab-review"
            aria-controls="product-preview-panel"
          >
            <ScanText />
            核对
          </TabsTrigger>
          <TabsTrigger
            value="reading"
            id="product-tab-reading"
            aria-controls="product-preview-panel"
          >
            <BookOpen />
            阅读
          </TabsTrigger>
          <TabsTrigger
            value="search"
            id="product-tab-search"
            aria-controls="product-preview-panel"
          >
            <Search />
            检索
          </TabsTrigger>
        </TabsList>
        <div
          className="product-preview-columns"
          id="product-preview-panel"
          role="tabpanel"
          aria-labelledby={`product-tab-${mode}`}
          tabIndex={0}
        >
          <div className="product-source">
            <div className="product-folio">
              <FileText size={15} />
              1915 年原刊<span>影印 21</span>
            </div>
            <figure className="product-source-scan">
              <img
                src={sourceScan}
                alt="陈独秀《敬告青年》原刊影印第21页，1915年《青年杂志》第1卷第1号"
                width="960"
                height="1392"
              />
              <span
                className="product-scan-focus"
                aria-label="标题区域定位示意"
              />
              <figcaption>标题区域 · 定位示意</figcaption>
            </figure>
            <a
              href={sampleSource}
              target="_blank"
              rel="noreferrer"
              className="product-source-caption"
            >
              《青年杂志》创刊号 · 查看原件来源 ↗
            </a>
          </div>
          <div className="product-result">
            <div className="product-folio">
              <GitBranch size={15} />
              {mode === "review"
                ? "原图对照"
                : mode === "reading"
                  ? "结构化阅读"
                  : "本书检索"}
            </div>
            {mode === "review" ? (
              <div className="product-review-example">
                <span className="product-example-state">字形对照示例</span>
                <h3>
                  敬告<mark>靑</mark>年
                </h3>
                <p>{sample}</p>
                <div className="product-example-location">
                  <span>保留原书字形</span>
                  <strong>靑 / 青</strong>
                  <small>陈独秀 · 影印第 21 页</small>
                </div>
                <p className="product-result-description">
                  原图与当前读法一起看。此处演示界面，不代表一次模型审核。
                </p>
              </div>
            ) : mode === "reading" ? (
              <div className="product-reading-example">
                <span className="product-example-state is-success">
                  <Check size={14} />
                  阅读与出处
                </span>
                <h3>敬告靑年</h3>
                <p>
                  {sample}
                  <button
                    className="product-note-link"
                    onClick={() => setNote(!note)}
                    aria-expanded={note}
                    aria-controls="product-note"
                    aria-label="查看样例出处"
                  >
                    [1]
                  </button>
                </p>
                {note && (
                  <aside id="product-note" className="product-preview-note">
                    [1]
                    样例来源说明，非原书脚注：陈独秀《敬告靑年》，《青年杂志》第1卷第1号，影印第21页。
                  </aside>
                )}
                <pre aria-label="Markdown 导出结构示例">
                  {
                    "## 敬告靑年\n\n靑年如初春，如朝日……\n\n<!-- 原件：影印第21页 -->"
                  }
                </pre>
              </div>
            ) : (
              <div className="product-search-example">
                <label htmlFor="product-query">试试一个关键词</label>
                <div className="product-example-search">
                  <Search size={16} />
                  <Input
                    id="product-query"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="例如：初春"
                  />
                </div>
                <div
                  className="product-example-search-result"
                  aria-live="polite"
                >
                  {found ? (
                    <>
                      <small>1 条示例结果 · 影印第 21 页</small>
                      <h3>敬告靑年</h3>
                      <p>{sample}</p>
                      <span>
                        <ArrowUpRight size={15} />
                        命中段落与原件相互对应
                      </span>
                    </>
                  ) : (
                    <>
                      <h3>没有匹配的示例文字</h3>
                      <p>试试“初春”或“朝日”。这里检索的是左侧原刊摘录。</p>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </Tabs>
    </section>
  );
}

export default function WelcomePage() {
  return (
    <div className="product-welcome">
      <header className="product-header">
        <Link
          to="/welcome"
          className="product-brand"
          aria-label="史料研究 · 产品首页"
        >
          <BookOpen />
          <span>史料研究</span>
        </Link>
        <nav aria-label="产品导航">
          <Button
            variant="ghost"
            onClick={() =>
              document.getElementById("product-features")?.scrollIntoView({
                block: "start",
                behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
                  .matches
                  ? "instant"
                  : "smooth",
              })
            }
          >
            功能与流程
          </Button>
          <a href={setupUrl} target="_blank" rel="noreferrer">
            安装说明
          </a>
          <Button asChild variant="outline">
            <Link to="/">
              打开工作台
              <ArrowUpRight />
            </Link>
          </Button>
        </nav>
      </header>
      <section className="product-hero" aria-labelledby="product-title">
        <div className="product-hero-copy">
          <p className="product-eyebrow">个人文献工作台 / 本机部署</p>
          <h1 id="product-title">
            从一份 PDF，
            <br />
            到有出处的资料库。
          </h1>
          <p className="product-lead">
            识别只是开始。对照原件核对文字，把文章、脚注与目录整理成可阅读、可检索、可导出的资料。
          </p>
          <div className="product-hero-actions">
            <Button asChild size="lg">
              <Link to="/">
                开始本机试用
                <ArrowRight />
              </Link>
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                document.getElementById("product-features")?.scrollIntoView({
                  block: "start",
                  behavior: window.matchMedia(
                    "(prefers-reduced-motion: reduce)",
                  ).matches
                    ? "instant"
                    : "smooth",
                })
              }
            >
              了解完整流程
              <ArrowDown />
            </Button>
          </div>
          <p className="product-trial-note">
            进入已有工作台，导入你的第一份 PDF。
            <br />
            首次使用先
            <a href={setupUrl} target="_blank" rel="noreferrer">
              完成本地安装
              <ArrowUpRight size={13} />
            </a>
            ；实际处理使用已登录的 Codex。
          </p>
          <div className="product-hero-baseline">
            <span>原件可追溯</span>
            <span>疑点由你终审</span>
            <span>Markdown 可导出</span>
          </div>
        </div>
        <ProductPreview />
      </section>

      <section
        className="product-features"
        id="product-features"
        aria-labelledby="product-features-heading"
      >
        <div className="product-section-heading">
          <p className="product-eyebrow">完整流程</p>
          <h2 id="product-features-heading">让每一步，都接得上下一步。</h2>
          <p>从导入到入库使用同一套任务与证据记录。</p>
        </div>
        <ol className="product-process">
          <li>
            <span>1</span>
            <div>
              <h3>导入原件</h3>
              <p>网页上传 PDF，原生文字、混合页和扫描页分别处理。</p>
            </div>
          </li>
          <li>
            <span>2</span>
            <div>
              <h3>独立原图核对</h3>
              <p>识别与审核分工。每页对照原图，明确的问题进入修正验证。</p>
            </div>
          </li>
          <li>
            <span>3</span>
            <div>
              <h3>处理剩余疑点</h3>
              <p>保留或修改当前文字。保存草稿后，由你提交最终决定。</p>
            </div>
          </li>
          <li>
            <span>4</span>
            <div>
              <h3>阅读、检索与导出</h3>
              <p>整书通过后入库。目录、段落与脚注保留原件出处。</p>
            </div>
          </li>
        </ol>
      </section>

      <section className="product-capabilities" aria-label="工作台能力">
        <article>
          <ScanText />
          <h3>原生与扫描，一起接入</h3>
          <p>有文字候选就提取，缺失内容再识别。原生提取的文字也要独立审核。</p>
        </article>
        <article>
          <BookOpen />
          <h3>文字之外，保留文献结构</h3>
          <p>目录关联、注释归属、表格和插图贯穿阅读与 Markdown 导出。</p>
        </article>
        <article>
          <GitBranch />
          <h3>可以暂停，也能接着处理</h3>
          <p>上传可续传，任务保留检查点。处理故障与内容待审分别呈现。</p>
        </article>
      </section>

      <section
        className="product-start"
        aria-labelledby="product-start-heading"
      >
        <div>
          <p className="product-eyebrow">开始使用</p>
          <h2 id="product-start-heading">用自己的资料，走一遍真实流程。</h2>
          <p>
            这是单人使用的本地应用。原件、草稿和处理记录保存在配置的工作区。
          </p>
          <details>
            <summary>首次运行，需要准备什么？</summary>
            <p>
              准备 Python、uv、Docker 和可用的 Codex
              登录；完整阅读检索平台还需要存储、搜索服务与本地 BGE
              权重。安装和启动步骤见项目 README。
            </p>
            <a href={setupUrl} target="_blank" rel="noreferrer">
              查看安装步骤
              <ArrowUpRight size={15} />
            </a>
          </details>
        </div>
        <Button asChild size="lg">
          <Link to="/">
            进入工作台
            <ArrowRight />
          </Link>
        </Button>
      </section>
      <footer className="product-footer">
        <span>史料研究 / Historical Research System</span>
        <span>原件、判断与成果，各有出处。</span>
        <details>
          <summary>字体许可</summary>
          <a href={sansLicense} target="_blank" rel="noreferrer">
            Noto Sans SC / OFL 1.1
          </a>
          <a href={serifLicense} target="_blank" rel="noreferrer">
            Noto Serif SC / OFL 1.1
          </a>
        </details>
      </footer>
    </div>
  );
}
