from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    gate: str | None = None
    phases: tuple[str, ...] = ()


STAGES = (
    Stage("brief_intake", "需求梳理", "brief_confirm"),
    Stage("outline", "方案大纲", "outline_confirm"),
    Stage("research", "市场研究与洞察", "research_confirm", ("A001_research", "A002_insight")),
    Stage("strategy", "核心策略", phases=("A003_strategy",)),
    Stage("theme", "传播主题", "theme_confirm"),
    Stage("creative", "创意方案", "creative_confirm", ("A004_creative",)),
    Stage("kol", "达人策略"),
    Stage("assembly", "完整提案与KPI预算", "report_edit"),
)
GATE_TITLES = {
    "brief_confirm": "确认需求", "outline_confirm": "确认大纲",
    "research_confirm": "确认调研", "theme_confirm": "选择主题",
    "creative_feedback": "补充创意方向（可留空）", "creative_confirm": "确认创意",
    "report_edit": "确认最终提案",
}
TAGS = {"C001": "抖音", "C002": "小红书", "C003": "整合营销", "C004": "影视娱乐",
        "C005": "游戏", "C006": "IP/账号运营", "C008": "通用"}
