# 模型完整 HTML 协议

## 模板约束分析

输入 `elements` 保留前端原标签及来源，`reference_html` 是模板的实际结构，`visual_analysis` 是项目视觉模型读取截图的描述（未完成时明确标记）。

返回 JSON：

```json
{"reason":"模板理解与保护依据","protected_elements":[0],"text_frames":[2],"title_element":2,"body_frame":{"x":60,"y":120,"width":1080,"height":480}}
```

元素ID为 `data-template-element` 的零基索引。`protected_elements` 的节点和样式完整保留；`text_frames` 保留元素外框和身份，但内部文字、字号、行距、对齐由模型决定。正文标题应列入text_frames，背景/页眉/页脚/品牌应列入protected_elements；中央示例不锁死。固定页保留原构图中的非文字元素与文字外框。无正文区域时body_frame为null。不要把待替换标题/示例文字误锁成品牌。

`text_frames` 也属于固定位置区域，并非“非固定文字列表”。正文中央的示例小标题、说明与内容图形由模型重建，通常应同时从protected_elements和text_frames中排除。自动标签title可能是中央“填加文字”小标题，brand也可能是页眉“单击此处添加文字标题”的误标；结合坐标、截图与语义识别页眉主标题，不能按标签直接锁定。收到冲突反馈时修改实际JSON列表/边界，不只修改reason；previous_contract是待修正的上一版，不是必须遵守的约束。

不用的目录条目、示例说明等文字框可清空或省略；使用的text_frames仍保留原位置和尺寸。每页必须显示已规划的proposal.title，正文title_element不可省略。文字框样式可以改用CSS类，宿主比较实际计算位置而不要求CSS字符串一致。

正文标题外框有一个受限例外：原框过窄而页眉右侧确有空间时，可由模型在约束分析中提供 `title_frame:{x,y,width,height}` 和 `title_frame_reason`。扩宽方向依据不可变前端快照中标题段落的单一align，不能由模型另报锚点：left保持原左边，right保持原右边，center保持原中心；y/height不变，新框包含原框。混合对齐、无文字对齐或旋转标题保持原框，不使用扩宽例外。不得超出画布、碰到左右新增区域的其他固定前景或移动/修改背景与品牌；整幅背景不视为前景障碍。仅已通过约束校验的正文title_element使用此新外框，其余文字框和固定页仍用原几何。生成阶段不能自行扩框，须先重新分析合同。正文同类页眉沿用模板主标题字号和基线，优先安全扩宽、修正内边距/强制换行；不要按页随机改变字号来塞入同一窄框；字号来自当前模板的对应标题样式族，不固定成某个数值。确实过长时由模型选择合理的层级和换行，必须保留完整文字与可读性。

正文必须明确提供 `title_element`，且该元素在text_frames内，不在protected_elements内。自动brand/fixed可能来自母版误标，应识别“单击添加标题”等可替换示例。正文text_frames只保留顶部标题区域，不保留中央分栏的示例itemTitle/itemBody外框。中央箭头、图标、流程块等内容装饰可以移除重设计，不能因为自动标了fixed就当成企业品牌；有意义的页眉页脚、企业logo和白色内容底板须保留。完整正文优先选择常规顶部标题与宽敞内容区的模板，过渡性大字版式只适合简短导语。

## 页面生成和修复

修复输入可能包含repair_policy与local_repair_scope。前者明确本轮是否允许换正文模板、重析合同或续页；没有相应权限时保持当前模板与分页。后者enforced=true时仅允许修改current_pages中data-enterprise-repair-region标出的区域，__PPT_REPAIR_LOCK_别名原样保留，工具无损展开并比较非目标DOM。不得修改全局style，也不得改非目标图表的数据绑定/类型；current_pages.charts是可直接复用的已校验来源请求。始终返回完整HTML，不返回局部patch或几何填框数据。JSON/来源纠错以previous_result最近候选为基础，不能撤销已完成的局部修复。

contents_source_bindings描述已与章节目录完整对应的原稿来源。使用同一份可见文字承接data-source-block及data-agenda-item（同一叶节点允许双标，禁止嵌套），按segments保留原编号与标点。data-agenda-number只标数字，分隔符可另作来源片段。所有来源片段及目录条目在本组全部续页中各完整覆盖一次，不另抄一份原稿目录。

返回 JSON：

```json
{"pages":[{"html":"<!doctype html><html><head>…</head><body>…</body></html>","charts":[]}],"reason":"本页设计或修复依据"}
```

每个文档含且仅含一个 `.ppt-slide`，画布使用输入canvas的px尺寸，body无margin。普通完整HTML/CSS可用，禁止script/iframe/外链资源/事件处理/网络字体。图片与复杂路径用提供的 `__PPT_RESOURCE_N__` 别名原样引用，宿主无损展开；不要复制base64。

文字必须是真实可见DOM文本，不能用CSS content、SVG text、隐藏节点或截图代替。使用 `data-ppt-slot="唯一ID"` 标记独立文本区域以检查碰撞，正文通常24–28px且不低于18px。不要给包含多个独立块的大容器重复标slot。

所有文字必须完整清晰，与实际背景可辨；不得重叠、被图形遮挡或裁切。图内文字留足内边距，图外说明与实际可见轮廓（包括尖角/凹口）保持合适间隔，标题/正文/注释层级分明。按当前模板字号和尺度决定间距，不硬套固定数值。复杂背景对比或不规则形状安全区未被可靠测量时须用截图复核，不能以矩形不相交或祖先背景色估算宣称完成。

来源正文或表格使用 `data-source-block="b0001"`；目录条目的完整章节名使用 `data-agenda-item="a1"`。标记不可嵌套。DOM读取顺序与原文顺序一致，跨页同ID可分段但拼接必须完整且恰好一次；表格使用真实table/tr/th/td，续表重复原表头。标题重复展示不必重复来源标记。可在正文顶部的原模板标题中放一个来源标记。

首页尾页的信息分别标 `data-metadata="presenter"`、`advisor`、`date`，节点只含对应值，字段标签写在外面。缺失值是AiPPT。章节号标 `data-section-number`，目录号码标 `data-agenda-number="a1"`，可保留模板的前导零，但必须与实际序号一致。

正文中央新增内容放在 `data-enterprise-body` 容器，其实际矩形在body_frame内；它只是整体边界，内部允许多列、多组和不规则形状布局，不是统一填字矩形。图形真实可读区域由DOM事实与截图共同确认，不能把SVG/CSS/图片的外接矩形当安全区；模板顶部标题中的来源文字不受正文区边界限制。整套封面/尾页各且仅有一页，分别位于第一和最后；章节页一页，正文/目录允许续页。目录续页连续放在序言之后、章节和正文之前；同一生成组的每张续页使用同一目录模板、同一固定构图与文字样式体系，只承载不同的目录条目。目录容量不足时增加续页，不复制首页/尾页、不缩小到难读、不遗漏来源。

序言页role为preface，仅当规划提供dedicated_preface_block_ids时启用，使用这些来源的真实可见data-source-block，不需要data-agenda-item或章节序号。它沿用固定页模板约束（body_frame为null），允许同一序言模板续页，所有续页连续放在封面后、目录前。序言不得用于一般正文。序言内容缺失时不会提供序言模板；有内容但模板缺失时使用body。

可选原生统计图：HTML使用空容器 `data-enterprise-chart="chart-0"`，模型完全决定位置/尺寸；返回charts项 `{"id":"chart-0","ref":"表格来源ID","column":1,"type":"bar","unit":"万元"}`。类型支持bar/column/line/donut，line须有真实顺序并设置ordered=true。图表数据由宿主绑定原表格校验，不能提供自造values；保留原表格，图表为辅助。不要用SVG/装饰假装统计数据图。

修复输入含current_pages和validation_feedback。直接返回完整的新pages，不返回补丁；保留所有原文、品牌约束及来源标记。禁止为通过校验删内容或删约束。

不可变品牌节点也可用 `__PPT_PROTECTED_N__` 无损别名给出。原样保留该标记及父级位置，不能包进移动/隐藏/缩放容器；它只展开为模板原节点，与图片别名同属资源处理，不由宿主补排页面。没有给出的别名不得引用。
