# 资料与待办02最终界面核对

日期：2026-10-16，上海时间。范围仅为电脑端。

本记录对应[02完整验收](evidence/compatibility-python311-node24-knowledge-inbox-release-20261016-02.json)，不复用失败01的截图或目视结论。界面专责逐张实际打开下列九张最终归档图片，直接读取PNG尺寸、重算SHA256并与02清单匹配，9/9一致；主代理另实际查看工作台1440、关联1440、待办1280及修订比较1280四张。图片均为隔离合成数据，不含作者原稿。

| 最终截图 | 尺寸 | 实际观察与边界 |
| --- | --- | --- |
| [资料关联](evidence/knowledge-inbox-release-20261016-02-knowledge-links-1440x1000.png) | 1440×1000 | 标题、本章范围、三个页签、关闭和底部操作清楚。前两条及第三条上部可见，28条关联和分页未全部目视。 |
| [原文来源](evidence/knowledge-inbox-release-20261016-02-knowledge-sources-1280x800.png) | 1280×800 | 原句、章节、有效状态和两个定位按钮完整，弹窗四边留有间距。3份来源仅前两份在当前滚动位置可见。 |
| [全书待办1440](evidence/knowledge-inbox-release-20261016-02-writing-inbox-1440x1000.png) | 1440×1000 | 分类及完整计数清楚，可见行按钮完整，无横向溢出。62项和底部分页不在同一屏。 |
| [全书待办1280](evidence/knowledge-inbox-release-20261016-02-writing-inbox-1280x800.png) | 1280×800 | 分类和可见行按钮完整。左导航下部“生成任务”露出半行，下方入口需滚动，是紧凑尺寸下的可发现性限制，图片不能证明入口不可用。 |
| [阅读设置1440](evidence/knowledge-inbox-release-20261016-02-editor-preferences-1440x1000.png) | 1440×1000 | 字号、行距、宽度标签、数值、滑杆和重置按钮完整，无横向溢出。打开的设置弹层覆盖正文前几行。 |
| [阅读设置1280](evidence/knowledge-inbox-release-20261016-02-editor-preferences-1280x800.png) | 1280×800 | 三项设置和重置完整，不遮目录或右侧主要任务按钮；正文和右侧下部仍各自滚动。 |
| [创作工作台1440](evidence/knowledge-inbox-release-20261016-02-writing-workbench-1440x1000.png) | 1440×1000 | 三栏、章标题、正文、准备计划、生成候选及正文底部操作可辨，无重叠或横向溢出。右栏下部不在首屏。 |
| [创作工作台1280](evidence/knowledge-inbox-release-20261016-02-writing-workbench-1280x900.png) | 1280×900 | 正文、目录、候选操作和本章依据清楚。右栏已滚动，上部任务内容不在图内；只读片段不据图认定可采纳。 |
| [整章修订比较](evidence/knowledge-inbox-release-20261016-02-writing-agent-polish-compare-1280x900.png) | 1280×900 | 原稿与候选两列换行正常，“继续比较”“确认采纳”完整无遮挡。此图为短候选，不能外推长章全部滚动内容。 |

九张可见区域未发现影响主要操作的遮挡、裁切或横向溢出。资料关联、阅读设置和总管三个对应浏览器报告均为`passed=true`且`page_errors=[]`；按钮点击、定位、旧任务读取、候选采纳及迟到响应的行为结论来自完整浏览器测试，不只来自目视检查。

全部86份附件的指纹、原图字节和逐组结果由[独立复核](evidence/knowledge-inbox-release-independent-review-20261016.json)另行登记。本记录不声称运行过七个参考项目的完整产品，也不提供文学质量、真实作者长期效率或第一梯队结论。
