# 常驻写作总管04最终界面核对

日期：2026-10-16，上海时间。范围仅为电脑端。

本记录对应[04完整验收](evidence/compatibility-python311-node24-agent-chat-release-20261016-04.json)，不把后段超时的03作为最终通过依据。04于上海时间18:24:03结束，124项检查全部通过，241个桌面场景通过；139份受验源码前后指纹一致，本次目视结束时重算当前源码也全部匹配。下列12张04原生截图均已逐张实际打开，直接读取PNG尺寸并重算SHA256，12/12与04附件清单一致。图片均为隔离合成数据，不含作者原稿。

| 最终截图 | 尺寸 | 实际观察与边界 |
| --- | --- | --- |
| [三栏1280](evidence/agent-chat-release-20261016-04-agent-chat-1280x900.png) | 1280×900 | 资料、编辑区和常驻聊天分栏清楚，未发送草稿、当前章开关、发送按钮完整。编辑区停在下部，只见正文框下半部分与展开的任务回顾，不能据此判断正文为空。 |
| [三栏1440](evidence/agent-chat-release-20261016-04-agent-chat-1440x1000.png) | 1440×1000 | 三栏无重叠，聊天输入框与发送按钮完整。正文标题和短原稿在当前滚动位置上方，截图未展示正文前几行。 |
| [三栏1920](evidence/agent-chat-release-20261016-04-agent-chat-1920x1080.png) | 1920×1080 | 宽屏中编辑区与右侧聊天边界清楚，回顾表单与聊天草稿不相互覆盖。该图同样处于编辑区下部，不能外推顶部正文的可见情况。 |
| [结果1280](evidence/agent-chat-release-20261016-04-agent-chat-results-1280x900.png) | 1280×900 | 三个结果页签、关闭、生成候选、上下文预览、任务契约和参考片段入口可见；结果区位于正文及回顾下方，右侧聊天保持独立。候选框底部操作不在当前屏内，需要滚动。 |
| [结果1440](evidence/agent-chat-release-20261016-04-agent-chat-results-1440x1000.png) | 1440×1000 | 结果页签、候选输入和底部复制、拒绝/停止、整理、比较采纳按钮可见，无横向溢出。图中尚未配置模型，按钮外观不能证明模型调用成功。 |
| [结果1920](evidence/agent-chat-release-20261016-04-agent-chat-results-1920x1080.png) | 1920×1080 | 结果区在正文下方展开，候选框和底部操作清楚；正文、结果和聊天没有相互覆盖。本图为空候选，不能外推长候选滚动体验。 |
| [讨论与正文](evidence/agent-chat-release-20261016-04-agent-chat-discussion-1440x1000.png) | 1440×1000 | 章节标题、两段短原稿、正文工具和底部状态清楚可见；右侧提问、回答、当前章范围与输入框完整。任务回顾下部不在本屏内。 |
| [确认改稿](evidence/agent-chat-release-20261016-04-agent-chat-compare-1440x1000.png) | 1440×1000 | 弹窗居中，修改前与候选两列换行正常，“继续比较”“确认采纳”完整无遮挡。此图是短候选，不能证明长章比较的全部滚动内容。 |
| [选区采纳后](evidence/agent-chat-release-20261016-04-agent-chat-selection-applied-1440x1000.png) | 1440×1000 | 正文改写内容清楚，右侧续写与选区改写两项显示已确认并有查看结果入口；章后记忆位于正文下方。候选底部操作被视口裁切，且出现保存提示，不能宣称底部按钮已全部目视。 |
| [独立总管设置](evidence/agent-chat-release-20261016-04-agent-settings-desktop.png) | 1440×1000 | 独立连接选项、地址、模型、密钥占位、温度、输出上限、思考设置、清除密钥选项及实测/保存按钮完整。界面明确显示连接尚未验证；下方主写作设置仅上部可见。 |
| [模型设置1280](evidence/agent-chat-release-20261016-04-task-settings-1280x900.png) | 1280×900 | 沿用主连接、实测/保存、主写作连接、写作要求、参数和右侧聊天输入区清楚，无横向溢出。用途表在本屏下方，本图不提供用途表逐行目视证据。 |
| [模型设置1440](evidence/agent-chat-release-20261016-04-task-settings-1440x1000.png) | 1440×1000 | 主表单和聊天清楚，用途设置标题与列标题刚进入底部。用途表的数据行未出现在本图，不能将表头可见视为全部用途控件已目视。 |

专项三栏和结果图的取景来自测试主动滚动到“本章任务与回顾”后继续切换结果、关闭结果和专注模式的步骤；编辑区会保留滚动位置。独立的临时合成库诊断曾记录编辑区向下滚动435像素、正文值仍为28个字符，回到顶部后内容不变。该诊断不属于04公开附件，也不作为04整轮通过依据。04原生的“讨论与正文”和“选区采纳后”截图提供本轮正文实际可见的目视依据。有正文时任务回顾默认折叠，专项图中的展开状态来自测试操作。

可见区域未发现阻断主要操作的新增遮挡或栏间重叠。[聊天交互9项](evidence/agent_chat_browser_regressions-python311-node24-agent-chat-release-20261016-04.json)、[真实操作链路5项](evidence/agent_chat_flow_browser_regressions-python311-node24-agent-chat-release-20261016-04.json)和[独立总管设置4项](evidence/agent_settings_browser_regressions-python311-node24-agent-chat-release-20261016-04.json)均通过，三份报告均为`page_errors=[]`、`paid_provider_calls=0`；[模型设置20项](evidence/model_settings_browser_regressions-python311-node24-agent-chat-release-20261016-04.json)也全部通过。前三份分别覆盖受控聊天响应、真实本机接口操作链路和独立设置，不能互相替代。用途表控件的布局与保存行为结论来自浏览器检查，不来自上述首屏图片。

本记录不新增测试、不修改产品源码，也不证明真实付费模型的文学质量、长篇创作的长期效率或第一梯队水平。

## 已目视文件指纹

以下为本次独立重算结果，文件均位于本目录的`evidence/`中。

```text
dcfd0716806fba349366a27f5f4074d055faf2ead6a4113380947c6b2ea0867e  agent-chat-release-20261016-04-agent-chat-1280x900.png
c03998997cb86729aa329ffe03e4ae30d84e809dc05d87c5ef87895b80b67939  agent-chat-release-20261016-04-agent-chat-1440x1000.png
da01852bb07904c43142637290e44e5911755ab72643833039a7d6731a27ad65  agent-chat-release-20261016-04-agent-chat-1920x1080.png
f6abc740f716dac2bccc6afdd58b623f1263989366526e239ffd8aacd0e9b4b8  agent-chat-release-20261016-04-agent-chat-results-1280x900.png
378eab4e8ec054f1ed0407a8ef494cc4bf2ca1fcca80b95c65d4809bb67c8d52  agent-chat-release-20261016-04-agent-chat-results-1440x1000.png
b619498deb4797bfdcdd637db9cba0cd989ea9837af830c52e9727855927455c  agent-chat-release-20261016-04-agent-chat-results-1920x1080.png
0369f378cdf13981c0ab34187c02b77298b307d69d07ec72c227a646da9da0cd  agent-chat-release-20261016-04-agent-chat-discussion-1440x1000.png
65b335e0b75612325827fa11efa90d2b66faa7ccf8d85daef53eb043d7ee914d  agent-chat-release-20261016-04-agent-chat-compare-1440x1000.png
2cdf667ee16dd80f87c0bfe82b1c4933e03a1863ccc0395d9c94a7644e1eb99a  agent-chat-release-20261016-04-agent-chat-selection-applied-1440x1000.png
35dc4c8d15c38f2d6898d94a0b2a020389c6130afbee554cda74eb3c8c409852  agent-chat-release-20261016-04-agent-settings-desktop.png
ed352457f1df927094b4fb60bace56a7aa2b9408ca8c1601fc9ec2f18aac0fc6  agent-chat-release-20261016-04-task-settings-1280x900.png
02d07079057a53c462130fb81687388560900820865bc425f2bdad13e986d3de  agent-chat-release-20261016-04-task-settings-1440x1000.png
```
