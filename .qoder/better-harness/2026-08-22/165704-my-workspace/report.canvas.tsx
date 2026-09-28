import {
  BarChart,
  Callout,
  ChartContainer,
  H1,
  MetricsGrid,
  PieChart,
  ReportSection,
  ReportShell,
  Stack,
  Table,
  Text,
} from "qoder/canvas";

const severityData = [
  { label: "Critical", value: 3, tone: "danger" as const },
  { label: "Major", value: 5, tone: "warning" as const },
  { label: "Minor", value: 4, tone: "info" as const },
];

const categoryCategories = [
  "AI 引导",
  "代码结构",
  "配置一致性",
  "代码质量",
  "测试",
  "架构",
  "类型安全",
  "代码模式",
  "发行",
  "可观测性",
];
const categorySeries = [
  {
    name: "发现数",
    data: [1, 2, 1, 1, 1, 2, 1, 1, 1, 1],
    tone: "warning" as const,
  },
];

const headlineMetrics = [
  { label: "总发现数", value: "12", tone: "neutral" as const },
  { label: "Critical", value: "3", tone: "danger" as const },
  { label: "Major", value: "5", tone: "warning" as const },
  { label: "Minor", value: "4", tone: "info" as const },
];

const findingsRows = [
  {
    id: "F01",
    severity: "Critical",
    category: "AI 引导",
    title: "缺少 AI Agent 规则文件",
    recommendation: "创建 .qoder/rules 或 AGENTS.md",
  },
  {
    id: "F02",
    severity: "Critical",
    category: "代码结构",
    title: "bot_core.py 上帝文件 — 7566 行",
    recommendation: "渐进拆分为 contact_api / commands / event_handler / humanize",
  },
  {
    id: "F03",
    severity: "Critical",
    category: "配置一致性",
    title: "Python 版本要求文档与配置矛盾",
    recommendation: "统一 pyproject.toml / README / CONTRIBUTING 版本声明",
  },
  {
    id: "F04",
    severity: "Major",
    category: "代码质量",
    title: "Ruff 大面积 per-file 豁免 — 20+ 文件",
    recommendation: "优先清理 F821/F823，渐进缩减豁免范围",
  },
  {
    id: "F05",
    severity: "Major",
    category: "测试",
    title: "测试覆盖严重不足 — 仅 1 个测试文件",
    recommendation: "为 config / protocols / plugins 补测试，加 CI 门禁",
  },
  {
    id: "F06",
    severity: "Major",
    category: "代码结构",
    title: "WebUI 单文件 SPA — 3969 行 HTML",
    recommendation: "按功能拆分模板片段或迁移轻量前端",
  },
  {
    id: "F07",
    severity: "Major",
    category: "架构",
    title: "配置加载路径复杂且无文档化优先级",
    recommendation: "添加配置优先级图到文档和 AI 规则",
  },
  {
    id: "F08",
    severity: "Major",
    category: "类型安全",
    title: "无类型检查配置",
    recommendation: "添加 pyright 配置，新模块强制类型注解",
  },
  {
    id: "F09",
    severity: "Minor",
    category: "架构",
    title: "插件系统缺少正式接口定义",
    recommendation: "定义 PluginProtocol + PluginMeta TypedDict",
  },
  {
    id: "F10",
    severity: "Minor",
    category: "代码模式",
    title: "广泛的 try-import + sys.path 操作",
    recommendation: "集中降级策略到 compat.py，移除 sys.path 操作",
  },
  {
    id: "F11",
    severity: "Minor",
    category: "发行",
    title: "EggBloomDist 与源码无同步机制",
    recommendation: "创建同步脚本 + VERSION 文件",
  },
  {
    id: "F12",
    severity: "Minor",
    category: "可观测性",
    title: "日志约定不统一 — print/logging 混用",
    recommendation: "统一使用 logging.getLogger，提供插件日志工具函数",
  },
];

const severityToneMap: Record<string, "danger" | "warning" | "info"> = {
  Critical: "danger",
  Major: "warning",
  Minor: "info",
};

const findingsRowTones = findingsRows.map(
  (r) => severityToneMap[r.severity] ?? "info"
) as ("danger" | "warning" | "info")[];

const recommendationsRows = [
  { priority: "P0", action: "创建 AI Agent 规则文件", effort: "低", impact: "高" },
  { priority: "P0", action: "统一 Python 版本文档", effort: "极低", impact: "中" },
  { priority: "P1", action: "为 bot_core.py 关键路径补测试", effort: "中", impact: "高" },
  { priority: "P1", action: "清理 ruff F821/F823 豁免", effort: "中", impact: "中" },
  { priority: "P2", action: "渐进拆分 bot_core.py", effort: "高", impact: "极高" },
  { priority: "P2", action: "添加 pyright 类型检查", effort: "低", impact: "中" },
  { priority: "P3", action: "WebUI 前端拆分", effort: "高", impact: "中" },
  { priority: "P3", action: "统一日志约定", effort: "低", impact: "低" },
];

export default function EggBloomHarnessReport() {
  return (
    <ReportShell width="wide" ariaLabel="EggBloom Harness 实践洞察报告">
      <Stack gap="section">
        <Stack gap="component">
          <H1>EggBloom Harness 实践洞察</H1>
          <Text tone="secondary">
            工作区：EggBloom (源码) + EggBloomDist (发行包) · 2026-08-22
          </Text>
          <MetricsGrid variant="header" columns={4} items={headlineMetrics} />
        </Stack>

        <ReportSection title="严重度分布" divided description="按 Critical / Major / Minor 三级分类">
          <ChartContainer ariaLabel="严重度分布饼图">
            <PieChart donut data={severityData} centerLabel="发现" />
          </ChartContainer>
        </ReportSection>

        <ReportSection title="分类覆盖" divided description="10 个维度扫描结果">
          <ChartContainer ariaLabel="分类发现柱状图">
            <BarChart
              categories={categoryCategories}
              series={categorySeries}
              horizontal
              colorByCategory
            />
          </ChartContainer>
        </ReportSection>

        <ReportSection title="发现明细" divided description="12 项发现按 ID 排列，含严重度、分类与建议">
          <Table
            headers={["ID", "严重度", "分类", "发现", "建议"]}
            rows={findingsRows.map((r) => [r.id, r.severity, r.category, r.title, r.recommendation])}
            rowTone={findingsRowTones}
            density="compact"
          />
        </ReportSection>

        <ReportSection title="优先行动建议" divided description="按优先级排序，兼顾投入产出比">
          <Table
            headers={["优先级", "行动", "投入", "收益"]}
            rows={recommendationsRows.map((r) => [r.priority, r.action, r.effort, r.impact])}
            density="compact"
          />
        </ReportSection>

        <ReportSection title="关键结论" divided>
          <Callout tone="critical">
            <Text weight="semibold">最紧迫：缺少 AI Agent 规则文件</Text>
            <Text tone="secondary" size="small">
              项目无 .cursorrules / AGENTS.md / .qoder/rules，AI Agent 每次交互都需大量探索。
              创建规则文件是投入最低、收益最高的改进。
            </Text>
          </Callout>
          <Callout tone="warning">
            <Text weight="semibold">结构性风险：bot_core.py 7566 行 + 测试覆盖不足</Text>
            <Text tone="secondary" size="small">
              核心文件承载 8+ 职责，仅 1 个测试文件。任何重构前需先补测试安全网。
              建议渐进拆分：contact_api → commands → event_handler → humanize。
            </Text>
          </Callout>
          <Callout tone="info">
            <Text weight="semibold">快速修复：Python 版本文档矛盾</Text>
            <Text tone="secondary" size="small">
              pyproject.toml 锁定 3.13，README/CONTRIBUTING 写 3.10+。
              统一文档是 5 分钟可完成的改进。
            </Text>
          </Callout>
        </ReportSection>
      </Stack>
    </ReportShell>
  );
}
