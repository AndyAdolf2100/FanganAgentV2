# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

from typing import List

# Tag → Module mapping table
# C tags represent content types, A tags represent phases, B tags represent modules
MODULE_MAPPING = {
    "C001": {  # 抖音
        "A001_research": ["B001", "B003", "B004", "B005", "B007", "B008", "B009", "B010", "B011", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B023", "B025", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
    "C002": {  # 小红书
        "A001_research": ["B001", "B003", "B004", "B005", "B007", "B008", "B009", "B010", "B011", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B023", "B025", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
    "C003": {  # 常规复合型
        "A001_research": ["B001", "B002", "B003", "B004", "B005", "B007", "B008", "B009", "B010", "B011", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B020", "B023", "B025", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B043", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
    "C004": {  # 影视娱乐
        "A001_research": ["B001", "B003", "B004", "B005", "B007", "B008", "B009", "B010", "B011", "B012", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B023", "B025", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
    "C005": {  # 游戏
        "A001_research": ["B001", "B003", "B004", "B005", "B006", "B007", "B008", "B009", "B010", "B011", "B013", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B021", "B022", "B023", "B024", "B025", "B026", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
    "C006": {  # IP/账号运营
        "A001_research": ["B001", "B003", "B004", "B005", "B007", "B008", "B009", "B010", "B011", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B023", "B025", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
    "C008": {  # 通用
        "A001_research": ["B001", "B002", "B003", "B004", "B005", "B007", "B008", "B009", "B010", "B011", "B014", "B015", "B016", "B017"],
        "A002_insight": ["B018", "B019", "B023", "B025", "B027", "B028", "B029", "B030", "B031", "B032", "B033", "B034", "B035", "B036"],
        "A003_strategy": ["B040", "B041", "B042", "B043", "B044", "B045"],
        "A031_theme": ["B046"],
        "A004_creative": ["B047", "B048"],
        "A006_kpi": ["B049"],
    },
}

# Phases that use ReAct agent with search tool
SEARCH_ENABLED_PHASES = ["A001_research", "A002_insight", "A003_strategy", "A004_creative"]

# Theme prompt method by C tag
THEME_METHOD_MAP = {
    "C001": "marketing_v2_theme_douyin",
    "C002": "marketing_v2_theme_xiaohongshu",
    "C003": "marketing_v2_theme_integrated",
    "C004": "marketing_v2_theme_entertainment",
    "C005": "marketing_v2_theme_game",
    "C006": "marketing_v2_theme_account",
    "C008": "marketing_v2_theme_general",
}

# KPI prompt method by C tag
KPI_METHOD_MAP = {
    "C001": "marketing_v2_kpi_douyin",
    "C002": "marketing_v2_kpi_xiaohongshu",
    "C005": "marketing_v2_kpi_game",
    "_default": "marketing_v2_kpi_general",
}


def get_modules_for_phase(c_tag: str, phase: str) -> List[str]:
    """Get module list for a specific phase based on C tag."""
    mapping = MODULE_MAPPING.get(c_tag, MODULE_MAPPING["C008"])
    return mapping.get(phase, [])


def phase_needs_search(phase: str) -> bool:
    """Determine if a phase's ReAct agent should have search tools."""
    return phase in SEARCH_ENABLED_PHASES


def get_theme_prompt_name(c_tag: str) -> str:
    """Get theme prompt name for a given C tag."""
    return THEME_METHOD_MAP.get(c_tag, THEME_METHOD_MAP["C008"])


def get_kpi_prompt_name(c_tag: str) -> str:
    """Get KPI prompt name for a given C tag."""
    return KPI_METHOD_MAP.get(c_tag, KPI_METHOD_MAP["_default"])
