# 金融与交易 Agent Benchmark：EveryBench 接入调研

调研日期：2026-10-25。

## 结论

这是一轮探索性调研与静态工程评估，不是穷尽式文献综述，也不是跑通认证。核对了 18 个候选的官方仓库、数据卡、运行说明或项目页面；重点检查了近期项目的实际文件、公开数据量、评分入口和授权声明。没有执行第三方 benchmark harness，没有调用付费模型，没有把任何新候选加入公开 catalog。

最值得优先做原型的五项：

1. **FinDeepIndicator**：3,350 条公开数据，覆盖公式、取数、计算、回答的完整指标研究链路。数据卡声明 Apache-2.0；代码仓库未查到独立许可证，建议基于已授权数据自写 Inspect task，不默认复制上游代码。
2. **BigFinanceBench public-50**：完整研究问题、专家 rubric、参考 harness，代码与公开子集分别有明确授权。只能标为 50 题公开子集，不能宣称支持全部 928 题。
3. **QuantCodeEval public-10**：从量化论文复现策略，已有确定性的 property checkers；先接不需要 WRDS 订阅的 10 个任务，不把全部 30 个标为人人可运行。
4. **QuantCode-Bench**：400 个 Backtrader 策略代码生成任务，适合评估量化开发 agent。需要冻结行情缓存、固定 judge，并隔离模型生成的代码。
5. **FinDeepResearch**：64 家公司的完整研报生成，公开参考报告和评价脚本已找到。授权、长报告 artifacts、judge 与成本处理需要补齐。

如果产品要验证“交易是否赚钱”，另开 **StockBench** 接入路线。上面五项主要验证研究和开发能力，不能等同于交易收益或实盘安全。

## 如何理解接入状态

- **可做原型**：找到真实输入和可实现的评价合同；不是已验证跑通，也不自动意味着适合公开上线。
- **有条件接入**：需要补授权、可访问数据、完整 scorer 或更多平台能力。
- **暂缓公开**：关键条件不成立，不应放进用户能点击运行的 benchmark 列表。
- **Inspect task**：实现数据、solver/agent wrapper、scorer；不是只添加 YAML。
- **Harbor packaging**：把任务、沙箱、提交文件和 verifier 打包成 Harbor 任务；不是声明上游已经支持 Harbor。
- **交易 adapter**：保留状态和完整 episode，接回测及多指标；不能把每天的决策当独立 QA。

下面的接入难度是基于本仓库代码的工程判断，不是作者承诺。代码可下载、数据可访问、评分可执行、授权可使用是四个不同条件。

## 许可证复核（2026-10-25）

代码、作者自有评测数据、第三方资料/API 条款分别核对。以下是官方可见声明，不是对全部上游材料的权利担保。“未明确”指本次未找到足够明确的许可，而不是认定永远无法取得授权。

| 候选 | 代码 | 数据/公开任务 | 接入时的边界 |
| --- | --- | --- | --- |
| BigFinanceBench | Apache-2.0 | 公开 50 题 CC BY 4.0 | 完整 928 题另需申请；保留署名、来源和评估限制 |
| FinDeepIndicator | 代码仓库未找到 LICENSE | 官方数据卡 Apache-2.0 | 可优先基于已授权数据独立实现，不默认复制无许可证代码 |
| QuantCodeEval | 原创代码 CC BY 4.0 | 自有任务、元数据、标注同为 CC BY 4.0；第三方来源各自授权 | DATA_LICENSE.md 明确包含原创代码；20 个任务涉及 WRDS，不可将订阅面板打包给所有用户 |
| QuantCode-Bench | MIT | 未找到独立任务/行情数据许可 | 代码 MIT 不替代 Reddit、TradingView、StackExchange、行情提供商条款 |
| FinDeepResearch | OpenFinArena 仓库未找到 LICENSE | 官方数据卡 Apache-2.0 | 数据与现成评分代码的授权分开处理 |
| FinDocResearch | 同上 | 官方数据卡 Apache-2.0 | 原始年报权利另查；许可证不能解决未公开 ground truth 的问题 |
| FinDeepForecast | 同上 | 官方数据卡 Apache-2.0 | 数据声明不等于官方结算服务可自由使用 |
| Kensho FrontierFinance | 未确认独立代码许可证 | 官方数据卡 CC BY 4.0 | 不自动覆盖所有第三方工作材料；依然缺有效自动 scorer |
| Finance Agent v2/v2.1 | MIT | public-27 独立数据许可未确认；完整套件需平台批准 | 不把代码 MIT 当成全量题库或平台访问许可 |
| FinToolBench | 未找到 LICENSE | 未确认独立数据许可；API 各自条款 | 不因标注 free-tier 就推断可以自动订阅、再分发或商用 |
| FinSearchComp | 仓库级 CC BY 4.0，未分拆代码/数据 | 仓库作者自有内容按仓库许可理解；第三方内容/API 另查 | [根 LICENSE](https://github.com/randomtutu/FinSearchComp/blob/main/LICENSE) 不扩张外部财经数据权利 |
| FailSafeQA | 本次核对的外部 Inspect adapter 为 MIT | Writer 官方数据卡 MIT | adapter 的 MIT 不代表任意同名实现都采用 MIT |
| TheAgentCompany 财务子集 | 原项目和 Inspect Evals 均为 MIT | 仓库自有任务受根许可约束，附件/第三方资料逐项查 | 原项目和适配代码均需保留版权/许可证 |
| StockBench | Apache-2.0 | 行情、新闻、基本面数据另受提供商条款约束 | 不自动取得 Polygon/Finnhub 数据的再分发权 |
| InvestorBench | MIT | 多来源，独立数据授权未确认 | 特别核查行情、新闻和第三方数据权利 |
| FinGAIA | README 声明 MIT，但未找到 LICENSE 文件 | 独立数据许可未确认 | 建议向作者确认并补齐许可证文本及数据授权 |
| FinArena | 未定位可核实的官方评价代码许可 | 官方链接数据集卡标注 MIT | 数据许可不填补 harness/scorer 缺口 |
| NoFxAiOS/FinBench | README 声明 MIT，但未找到 LICENSE 文件 | 快照/行情独立授权未确认 | 建议取得明确许可证及数据来源记录 |

新增核对来源：[FinDeepResearch 数据卡](https://huggingface.co/datasets/OpenFinArena/FinDeepResearch/blob/main/README.md)、[FinDocResearch 数据卡](https://huggingface.co/datasets/OpenFinArena/FinDocResearch/blob/main/README.md)、[FinDeepForecast 数据卡](https://huggingface.co/datasets/OpenFinArena/FinDeepForecast/blob/main/README.md)、[Writer/FailSafeQA 数据卡](https://huggingface.co/datasets/Writer/FailSafeQA/blob/main/README.md)、[FinArena 数据卡](https://huggingface.co/datasets/Illogicaler/FinArena-low-cost-dataset/blob/main/README.md)、[TheAgentCompany LICENSE](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/LICENSE)、[Inspect Evals LICENSE](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/LICENSE)、[InvestorBench LICENSE](https://github.com/felis33/INVESTOR-BENCH/blob/main/LICENSE)、[StockBench LICENSE](https://github.com/ChenYXxxx/stockbench/blob/main/LICENSE)。重点候选的固定版本许可证来源见下文对应小节。

MIT、Apache-2.0、CC BY 4.0 都通常允许商业使用，但仍须遵守署名、许可证/版权通知、修改说明等各自条件；它们不要求将整个 EveryBench 项目改成同一种许可证。Apache-2.0 还涉及 NOTICE 和专利条款；CC BY 4.0 用于代码较不常见，整合时应保留清晰的逐文件来源与授权记录。公开可下载、非 gated、免费 API 和可商用/可再分发不是同义词。

## 一、近期重点候选

| 候选 | 时间依据 | 实际测什么 | 公开范围与现成评价 | EveryBench 判断 |
| --- | --- | --- | --- | --- |
| FinDeepIndicator | 2026-08 论文；代码仓库创建于 2026-08-01 | 公式识别、数据检索、指标计算、最终回答；美中市场 | 实际读取公开 CSV：3,350 行；有搜索/agent、抽取、比较、数值核对流水线 | 优先 Inspect task；中等工作量；先解决代码授权与协议固定 |
| BigFinanceBench | 2026-06 论文；仓库创建于 2026-05-06 | 多步金融研究、SEC 文件、Python 计算、rubric 评分 | 实际读取公开 JSONL：50 条；完整 benchmark 为 928 条，其余需申请；有研究 harness 和 judge | 优先 public-50 Inspect wrapper；中等工作量 |
| QuantCodeEval | 2026 匿名 artifact；未核实首次发布日期 | 根据金融研究论文复现可执行策略，检查领域和论文特定性质 | 30 个任务、572 个 checker；10 个任务可无订阅完整重跑，另 20 个需 WRDS 数据 | 优先 public-10；Inspect+沙箱或 Harbor packaging；中等工作量 |
| QuantCode-Bench | 2026；仓库创建于 2026-04-10 | 自然语言策略转 Backtrader 代码，支持多轮修复 | 实际读取任务 JSON：400 条；编译、回测、至少一笔交易、语义 judge 四阶段 | 可做原型；沙箱化 coding task；中等工作量 |
| FinDeepResearch | 2025-10 论文；仓库 2026 年仍有更新 | 多源公司研究并生成结构化长报告 | 64 家公司、8 个市场；官方声明 15,808 个 grading items；参考报告和本地 evaluator 都已找到 | 很相关；Inspect report task+artifact/metric 支持；中到高工作量 |
| FinDocResearch | ICAIF 2025 challenge；2026-01 technical report | 仅依据给定两年年报写公司研究报告，禁止额外联网资料 | 80 家公司、160 份年报；有 evaluator，但官方 README 明确不提供 ground-truth 报告 | 可执行生成，不能声称复现完整官方评分；暂缓公开 scored entry |
| FinDeepForecast | 2026-01 论文 | 预测未来公司指标及宏观数据，待事实发生后评价 | 有公开题目/提交路径；不是立即可结算的固定 QA 集 | 需要 delayed scoring、截止时间及结果版本管理；单独路线 |
| Kensho/S&P FrontierFinance | 2026-04 论文 | DCF、LBO、并购、三表、信用分析等长时程文件/Excel 建模 | 25 任务、491 条 rubric；有 Inspect eval.yaml 与 computer-use 示例，但自动 scorer 是占位值 | 暂缓 scored entry；需真实 artifact/rubric scorer；高工作量 |
| Finance Agent v2 / v2.1 | v2 仓库创建于 2026-04-01；v2.1 官方页面 2026-10 更新 | SEC、搜索、检索、历史价格等工具支持的复杂分析 | 实际读取仓库 public.csv：27 条，带 rubric；完整平台测试需批准；不同版本不可混用 | public-27 可做条件原型；完整 benchmark 需平台/数据许可；非直接 catalog 接入 |
| FinToolBench | 2026-03 论文；仓库创建于 2026-03-04 | 金融 API 选择、调用、执行及时间/意图/领域匹配 | README 声明 760 工具、295 查询；有 evaluator，但完整 agent/build 流程未公开 | 有价值，但需补 agent/tool executor、端点 replay、授权；中到高工作量 |
| StockBench | 2025-10 论文/仓库 | 连续多日股票交易与组合决策 | 有回测入口、收益/Sortino/最大回撤；依赖行情、基本面、新闻 | 真正交易候选；需状态、组合指标、时间泄漏控制；高工作量 |
| TheAgentCompany 财务任务 | 原项目不是最新金融 benchmark；本次核实当前 Inspect 实现 | 预算差异、发票匹配、费用核验、10-K 报表等文件与工具工作流 | inspect_evals 中实际存在 7 个 finance_* task_name，另有股票分析 slides 任务 | 复用程度高，但须处理结构化评分和可选依赖；只能命名为自定义子集 |

### 1. FinDeepIndicator：比金融常识问答更贴近研究 Agent

输入是一个金融指标问题，期望输出覆盖公式 F、原始数据 D、计算值 I、最终答案 A。官方说明覆盖 234 个指标、21 个子类别、800 家公司。这里的公司数与指标数都不是独立任务数；实际公开 CSV 有 3,350 行。

核实的文件与入口：

- 数据列包含 `question`、`answer`、`formula`、`data`、`data_indicator` 等。给 agent 的输入不能误带这些参考答案字段。
- `script/pipeline.sh` 串联搜索或 agent、抽取、比较、数值检查。
- `compare_numerical.py` 有数值与表格比较逻辑；默认相对误差容忍度为 1%，零值有单独规则。仍需用小样本检查缺失输出、解析失败和分母为零等边界，不能只凭有脚本就认定 scorer 正确。
- 依赖 OpenRouter/模型 API 和 Serper 搜索。应固定工具与预算，不能将某个平台的原生搜索结果与另一套工具悄悄混在同一 variant 下。
- 数据卡声明 Apache-2.0。代码仓库没有查到独立 LICENSE，不能将数据授权延伸到全部代码或第三方财经数据。

建议：基于已授权数据自写 Inspect task，先做 20–50 题覆盖不同指标/市场的离线验证；正式成绩保留公式、数据、计算和答案的分项指标。不能将临时抽样原型标为完整 benchmark。

来源：[官方数据卡](https://huggingface.co/datasets/OpenFinArena/FinDeepIndicator/blob/main/README.md)、[官方说明](https://github.com/OpenFinArena/OpenFinArena/blob/103ce6b33e8db6fccbc0dfcf2782a4b3cec0312c/competitions/FinDeepIndicator_README.md)、[代码](https://github.com/transcend-0/FinDeepIndicator/tree/22c3a1f495e87fe6ff416d8de20619bae6786f19)。

### 2. BigFinanceBench：近期公开接入条件比较清楚的研究评测

已核实公开 50 题，包含问题、reference answer 和专家加权 rubric。参考 harness 提供 web search、EDGAR、URL/PDF 读取、Python 执行。公开数据还含 `evaluation_only`、`do_not_train`、canary 等字段，应用于评估并保留这些限制与来源记录。

- 代码 Apache-2.0；公开子集 CC BY 4.0，完整数据另需申请。
- 有 `scripts/run_eval_set.py` 和评估/打分流程；headline 协议包含多次 trials 和多 judge。单 judge 的小原型不能宣称复现 headline 成绩。
- Python 工具明确不是安全沙箱。不能把外来模型代码直接跑在 API/worker 主机上；取数网络与代码执行隔离需要分别设计。
- LiteLLM 调用、judge 和搜索费用不会因“外面包一层 Inspect”自动被现有预算 guard 覆盖。

建议命名类似 `bigfinancebench-public-50`，实现 Inspect agent wrapper 或参考 harness adapter，保留每条 rubric、最终答案、judge 和 trial 记录。

来源：[固定版本 README](https://github.com/Rogo-Technologies/big-finance-benchmark/blob/3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/README.md)、[数据许可证](https://github.com/Rogo-Technologies/big-finance-benchmark/blob/3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/data/LICENSE-DATA)、[公开数据](https://huggingface.co/datasets/RogoAI/big-finance-benchmark)。

### 3. QuantCodeEval：策略复现，不是“收益越高越好”

注意它和下一项 QuantCode-Bench 是两个不同项目。

已核实：30 个论文复现任务、572 个 property checkers、golden references、公开运行入口。数据许可证明确说明原始代码、任务元数据、标注、文档采用 CC BY 4.0；第三方论文和数据的原授权不变。

- 可不使用 WRDS 订阅完整重跑的 10 个任务：T01、T12、T16、T18、T19、T24、T26、T27、T28、T29。
- 其余 20 个任务需要用户自己的 WRDS/CRSP/Compustat 等数据权限；不能打包这些授权数据供所有用户下载。
- 判断策略有没有遵循论文规定的行为，不是用最高回测收益代替正确性。
- 保留全任务通过与 property-completion 两种结果；pass@k 必须按真实重复尝试聚合。
- 代码生成 agent 的工作空间不能带入 golden reference、参考输出、评分答案；这些只给 verifier。

建议先做 public-10 的 Harbor 包装或 Inspect 沙箱任务；全 30 项作为需自带授权的数据模式，不能与 public-10 混用名称或分数。

来源：[公开 artifact](https://huggingface.co/datasets/quantcodeeval/task_data/blob/9bdacc4898aeec08813764290b12d356e0a011d1/README.md)、[明确授权与数据分层](https://huggingface.co/datasets/quantcodeeval/task_data/blob/9bdacc4898aeec08813764290b12d356e0a011d1/DATA_LICENSE.md)、[项目页](https://www.quantcodeeval.cloud/)。首次发布日期与正式同行评审状态未核实，不应自行标注已发表。

### 4. QuantCode-Bench：更直接的量化策略代码工程评测

实际任务 JSON 有 400 项。四阶段分别检查编译、回测无异常、产生至少一笔交易、LLM judge 认定符合策略描述；单轮和最多 10 轮修复是不同评测模式。

- 代码 MIT；任务文本来自 Reddit、TradingView、StackExchange、GitHub 等，不能仅根据代码 LICENSE 推断所有第三方文本和行情缓存都可再分发。
- 先冻结 yfinance 缓存及时间窗，否则运行日期、可用历史长度、行情修订会改变任务难度。
- “产生交易”是有效性检查，不是盈利、风险控制或交易安全证明。
- 固定 judge 身份、是否启用 judge、轮次上限和反馈机制；禁用 judge 的成绩不能混作完整四阶段成绩。

建议用 Inspect coding solver+隔离环境，或把数据与 verifier 封装到 Harbor。不是新增一种通用 execution backend。

来源：[固定版本仓库](https://github.com/LimexAILab/QuantCode-Bench/tree/f8bda951addb409a81aa316c00401dbde60774ae)、[任务数据](https://github.com/LimexAILab/QuantCode-Bench/blob/f8bda951addb409a81aa316c00401dbde60774ae/data/benchmark_tasks_multiframe.json)。

### 5. FinDeepResearch：研报生成与评价均有实际入口

官方给出 64 家公司的分析结构、参考研报和评分要求；15,808 是 grading items 数量，不能当成 15,808 个独立 agent 任务。

已找到 `competitions/evaluation/run.py`、extractor、evaluator、judge prompts、requirements，以及从 Markdown 报告抽取内容再打分的运行说明。不是只有一个提交邮箱。

- 每家公司报告应作为样本；按公司/任务聚合，不按报告中每条 claim 当成独立实验。
- 保留格式分和内容分、认知层级与国家/市场分项，不能任意平均成所谓准确率。
- 研究来源包含实时网络，需要存证和时间截断；参考报告不是允许直接提供给 agent 的输入。
- 后续许可证复核确认官方 HF 数据卡标注 Apache-2.0；共享 OpenFinArena 代码仓库仍未查到明确 LICENSE。数据声明不自动授权复制评分代码，也不代替第三方文档/行情的权利核验。
- 长报告及附件需要独立 artifact 存储，而不是只放进当前截断的预览文本。

来源：[官方说明与执行命令](https://github.com/OpenFinArena/OpenFinArena/blob/103ce6b33e8db6fccbc0dfcf2782a4b3cec0312c/competitions/FinDeepResearch_README.md)、[评价目录](https://github.com/OpenFinArena/OpenFinArena/tree/103ce6b33e8db6fccbc0dfcf2782a4b3cec0312c/competitions/evaluation)、[公开数据与参考报告](https://huggingface.co/datasets/OpenFinArena/FinDeepResearch)。

### 6. 暂缓项：并不是“不支持 Inspect”

**FinDocResearch**：官方说明有 80 家公司、160 份报告，且禁止额外联网资料；但 Ground Truth 一节明确表示出于未来比赛限制不提供参考报告。自行写参考答案后可以形成我们的定制评测，不能冒充官方完整评分。

来源：[官方 README 的 Ground Truth](https://github.com/OpenFinArena/OpenFinArena/blob/103ce6b33e8db6fccbc0dfcf2782a4b3cec0312c/competitions/FinDocResearch_README.md)。

**Kensho/S&P FrontierFinance**：数据卡声明 CC BY 4.0，25 个金融建模任务，有 Docker、Inspect eval.yaml 和示例 solver；但 Scoring 一节明确没有自动 scorer。占位 `includes` 无论文件质量如何都输出 accuracy 0.000。这不是模型能力分数。需文件提交、Excel/公式依赖检查、rubric 评分、人审或明确验证过的 judge。独立代码授权范围仍需核对。不能仅把 `hf/kensho/frontierfinance` 加入 catalog 就承诺完整评分。另有 Samaya 同名金融研究 benchmark，不能混淆。

来源：[固定数据卡与 Scoring](https://huggingface.co/datasets/kensho/frontierfinance/blob/74b4896bf77dbe3a2328c10222450b665f0582eb/README.md)。

**Finance Agent v2/v2.1**：当前公开仓库真实存在 public.csv 和 public.txt，均为 27 条；CSV 含 Question、Question Type、Expert time、Rubric。不能说完全没有公开数据，但平台审批与完整官方 test suites 仍是另一回事。代码 MIT，数据独立授权未确认；v2.1 更换了问题/rubric/jury 等协议，27 条文件不能未经核实就冠以 v2.1。仅适合显式版本的 public-27 原型。

来源：[仓库说明](https://github.com/vals-ai/finance-agent-v2/blob/03c7c432b645f42f81ef3c13957100b182217d7c/README.md)、[公开 CSV](https://github.com/vals-ai/finance-agent-v2/blob/03c7c432b645f42f81ef3c13957100b182217d7c/data/public.csv)、[v2.1 官方页](https://www.vals.ai/benchmarks/fabv2-1)。

**FinToolBench**：公开了评价 pipeline、工具清单、题目和订阅辅助脚本；README 同时明确完整 agent/build 组件未公开。评价入口接收已生成的 result JSONL，不等同于“已经有完整 end-to-end agent”。RapidAPI 订阅/端点、AkShare、模型调用、数据 schema 和权限都要检查。未找到独立 LICENSE。它测工具时效/意图/领域匹配，不是下单资金安全认证；本轮没有执行任何自动订阅脚本。

来源：[固定版本 README](https://github.com/Double-wk/FinToolBench/blob/5469c85f9523e5f8976471b670c992a3563dfcde/README.md)。

**FinDeepForecast**：真正前瞻预测适合减少回看评测的问题，但结果要等未来发生。需要 prediction timestamp、截止后不可修改、pending/resolved 状态、目标实际值的 first-release/revised 口径与延期结算流程。当前“模型回答完就立即最终打分”的执行路径不足。

来源：[官方参与说明](https://github.com/OpenFinArena/OpenFinArena/blob/103ce6b33e8db6fccbc0dfcf2782a4b3cec0312c/competitions/FinDeepForecast_README.md)。

### 7. TheAgentCompany 财务任务：最接近复用现成 Inspect 的路线

当前上游 Inspect README 与代码实际列有：

- `finance_apply_tax_credit`
- `finance_budget_variance`
- `finance_check_attendance_payroll`
- `finance_create_10k_income_report`
- `finance_expense_validation`
- `finance_invoice_matching`
- `finance_nonqualified_bill_ask_for_reimburse`

另有 `ds_stock_analysis_slides`。这些是办公/财务流程任务，不是交易收益 benchmark。上游完整项目的 175 项也不是当前 Inspect 实现都已接完。

任务可以通过 `inspect_evals/theagentcompany` 和 `task_args.task_name` 选取，但需要 theagentcompany 可选依赖、Docker/服务环境与部分任务的 judge/NPC 设置。须核实所选 inspect-evals 发布版本是否包含当前 main 的实现。

特别注意 scorer 返回形如 `{'score': 'C', 'checkpoints': 1.0}` 的结构化值。当前 EveryBench 通用转换并不正确支持这一结构，不能仅添加 catalog 条目就发布。财政子集需要独立命名，不能将它的结果当成官方完整 TheAgentCompany 成绩。

来源：[核对版本的 Inspect README](https://github.com/UKGovernmentBEIS/inspect_evals/blob/cac63f6d942ff63c9a0ac1185a026123e62970f4/src/inspect_evals/theagentcompany/README.md)。

## 二、其余六项与前一轮结论更新

| 候选 | 核实结果 | 接入判断 |
| --- | --- | --- |
| [FinSearchComp](https://github.com/randomtutu/FinSearchComp) | 635 题，完整文件与 AkShare-compatible 版本有区别；有生成和评价脚本 | 仍值得做。先固定 split、时间敏感题的快照和工具。Inspect research wrapper，中等工作量；数据再分发范围另核对 |
| [FailSafeQA](https://github.com/theruviparambil/failsafeqa-inspect) | 220 例；Inspect register 真实指向一个外部仓库和固定 commit，而非内置 inspect_evals task | 低到中等工作量。安装/固定外部实现，保留 robustness、grounding、compliance 原定义和 judge；先验证再发布 |
| [FinGAIA](https://github.com/SUFE-AIFLM-Lab/FinGAIA) | 报告总计 407 任务，但完整公开输入、答案和 scorer 的可复现范围未确认 | 保留候选，暂不承诺全量支持；不能把数据网页展示的行数当实际任务数 |
| [InvestorBench](https://github.com/felis33/INVESTOR-BENCH) | 有可运行框架，包含记忆预热、Qdrant 和多资产评估；数据来源/权限复杂 | 与 StockBench 同属状态化交易路线，基础设施更重；不是薄薄一层 QA wrapper |
| [FinArena](https://arxiv.org/abs/2503.02692) | 找到人机协作研究框架和实验材料，但未核实标准化可执行 scorer | 暂缓公开条目；它不是 OpenFinArena 平台，也不自动等于固定 benchmark |
| [NoFxAiOS/FinBench](https://github.com/NoFxAiOS/FinBench) | 有 Go CLI 与静态行情模式，侧重技术指标数值计算；README 声明 MIT，GitHub 未检测到独立 LICENSE | 可做狭窄数值诊断，不优先作为完整 agent benchmark；授权与数据快照需核对，也不要与 FinBen/LDBC FinBench 混淆 |

FailSafeQA 的外部注册来源已直接从 Inspect 仓库确认：[register/failsafeqa/eval.yaml](https://github.com/UKGovernmentBEIS/inspect_evals/blob/cac63f6d942ff63c9a0ac1185a026123e62970f4/register/failsafeqa/eval.yaml)，固定外部 commit 为 `515c2b9b0d195904b5f3f7949e40eb45a073a858`。没有因此宣称我们已经装好或运行过该实现。

FinQA、FinanceBench 仍可作为较老的基础诊断，不属于本轮“近期新候选”的重点。FinanceBench 的公开 150 例不能混同完整 10,231 题。

## 三、与当前 EveryBench 代码对照后的真实缺口

### 1. 主指标选择、量纲和方向：接入前先补

`src/everybench/inspect_convert.py:68` 的 `primary_score()` 直接选第一个 scorer；`outcome_for()` 把 >=1 视为 correct、<=0 视为 incorrect。这只适合部分归一化指标，不能直接解释 0–100 分、金额、收益、误差、回撤。

本轮对当前环境执行了不联网的转换检查：

- `value_to_float()({'score': 'C', 'checkpoints': 1.0})` 打印无法转换的警告，但返回 **0.0**。
- `value_to_float()({'accuracy': 1.0, 'risk': 0.1})` 同样返回 **0.0**。
- 数值 `80.0` 原样返回 80.0，随后现有逻辑会将其当作 correct，UI 还可能按百分数显示。

因此，结构化分数当前不只是“少展示一个字段”，还可能变成错误的零分。接入 TheAgentCompany、多阶段金融评测前要显式声明主 scorer、value key、尺度、方向、单位、可选 pass 条件；转换失败必须可见，不能静默吞成有效成绩。

已有 `Run.metrics` 能保留一部分聚合结果，不意味着逐样本多指标及其比较已正确支持。应保留所有 scorer 和分项，避免只保留第一个数字。

### 2. 真正评测用户 Agent，而不是替换成默认模型

`src/everybench/child.py:145` 只有 Harbor 分支消费 custom agent；Inspect 分支执行 `entry.task` 与 `task_args`。金融研究接入要提供明确的 solver/agent wrapper，使用户自己的模型调用、工具、检索、记忆与最终结果受测。

直接运行参考 ReAct harness 可以比较模型，但不能自动宣称比较了用户的整套 agent。两种模式应在 manifest 中分别记录，并固定工具能力和预算。

### 3. 外部工具、judge 与多模型成本

`src/everybench/service.py:228` 的报价主要根据样本输入/输出 tokens 与请求模型价格外推；Inspect 的 budget hook 也不能自动计量外部 LiteLLM、搜索服务、行情 API 或独立 judge 的全部开销。

需要分开记录 evaluated agent、judge、搜索/数据服务、环境模型成本，明确哪些能硬性限制、哪些只是估计。多模型分工与 judge 不应被误认成“同一个模型发生了版本漂移”。

### 4. 长报告、工作簿与代码 Artifacts

`src/everybench/inspect_convert.py:18` 的文本预览上限为 8,000 字符。Inspect 原始日志仍保留，但预览不能作为完整研报/代码/Excel 提交物的唯一载体。

需要按样本保存完整 artifact、哈希、MIME 类型与引用；UI 展示预览并提供下载/查看。评分必须使用完整提交物，不是截断预览。文件、公式、计算表和 verifier 结果应与 trace 链接。

### 5. 比较的统计单位与失败语义

`src/everybench/compare.py:124` 使用普通 paired bootstrap；`compare_runs()` 会把执行 error 排除出分数差。对于独立问题可以有意义，但不能不加判断应用到以下场景：

- 同一公司下多条 rubric 或相关问题。
- 同一题的多次 agent trials/pass@k。
- 同一行情路径上的连续交易日。
- 回测中 agent 的无效订单、超限或执行失败。

应分别采用 question/company cluster、attempt-aware 聚合、whole episode 或 block-aware 统计。对交易 agent，将策略失败从收益分析中排除会有幸存者偏差；须区分基础设施失败与任务/策略失败，预先定义惩罚和报告方式。

### 6. Point-in-time、快照与前瞻结算

`catalog.py` 现有 variant identity 已覆盖 task、task_args、grader 和部分环境，但不是完整金融实验合同。dataset 版本、行情/新闻发布时间、复权/退市口径、市场日历、模型与 judge、检索 replay、scorer commit、工具预算都要进入可审核的版本记录；影响可比性的项目不能只写进备注。

不能因为数据是“2025 年”就宣称对 2026 年模型无训练污染。实时研究与冻结 replay 应明确分模式。预测 benchmark 要记录首次公开的真实值还是事后修订值；历史重放也不能冒充前瞻预测。

## 四、建议的实施顺序

### 阶段 0：小范围基础能力补齐

不新建一个庞大通用框架，先实现下面三件会直接影响成绩正确性的事：

1. 显式主指标和结构化分数支持；错误不可默认为零分。
2. 完整 artifacts 和外部 agent/tool/judge 的成本、trace 记录。
3. 不可变 evaluation spec，以及按任务类型选择比较/失败规则。

保持默认 benchmark 目录只展示真正可运行、授权条件清楚的条目。内部原型、申请中的数据集、占位 scorer 不应出现为“支持”。

### 阶段 1：金融研究与数值核验

并行候选而非自动上线承诺：

- BigFinanceBench public-50：授权清楚，优先验证工具、judge 和成本协议。
- FinDeepIndicator：基于已授权数据自写 task，保留四阶段输出；先审计输入防止答案泄漏。
- 若只想快速复用现成环境，可额外验证 TheAgentCompany 的一到两个财务 task，但先修结构化分数。

每个都需完成：公开数据下载/授权核验、至少一组已知正确/错误的 scorer 测试、低预算端到端 smoke run、失败/取消/trace 验证、文档与默认依赖检测。只有完成这些才发布。

### 阶段 2：量化开发

- QuantCodeEval public-10：只允许无订阅任务集，隔离 agent 与 verifier。
- QuantCode-Bench：冻结价格缓存，固定轮次、judge 和四阶段完整定义。

两者都是代码能力/忠实实现评测，不应在 UI 显示成“策略盈利能力排名”。

### 阶段 3：深度研究与实际决策

- FinDeepResearch：先完成授权和 artifacts，再复用完整报告 scorer。
- StockBench：单独接 episode/portfolio、return/Sortino/drawdown/turnover、手续费/滑点、时间窗口/市场基线；需要更多窗口验证，不能凭单个历史区间宣称泛化。
- FinDeepForecast：等 delayed evaluation 生命周期设计完成后再接。

本轮没有核实出一个可以直接用来认证券商下单安全的独立开源 benchmark。工具匹配、银行客服或历史收益都不能替代订单幂等、风险限额、权限与 kill switch 测试。

## 五、复核边界与可重复性记录

- 仅静态检查了源码、运行说明、公开 CSV/JSON 的结构和数量，以及当前 EveryBench 的分数转换。未安装候选依赖、运行上游脚本或回测、构建镜像、调用付费 API、订阅金融数据服务。
- GitHub 检查的代表版本：BigFinance `3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71`；QuantCode-Bench `f8bda951addb409a81aa316c00401dbde60774ae`；FAB v2 `03c7c432b645f42f81ef3c13957100b182217d7c`；FinToolBench `5469c85f9523e5f8976471b670c992a3563dfcde`；OpenFinArena `103ce6b33e8db6fccbc0dfcf2782a4b3cec0312c`；FinDeepIndicator `22c3a1f495e87fe6ff416d8de20619bae6786f19`；Inspect Evals `cac63f6d942ff63c9a0ac1185a026123e62970f4`。
- HF 检查版本：FinDeepIndicator `f59dad280fba4e736ccf346eb234265d13fc6737`；QuantCodeEval `9bdacc4898aeec08813764290b12d356e0a011d1`；Kensho FrontierFinance `74b4896bf77dbe3a2328c10222450b665f0582eb`。
- 日期列尽量区分论文提交、仓库创建和最近更新，不将 GitHub 创建时间强行当正式发布日。
- 授权结论只是可见许可声明的核对，不是法律意见；没有 LICENSE 不等于一定不可合作，但不应默认为可复制/再分发。
- 这份文件记录可行性，不改变当前实际支持范围。所有新候选仍需实现与验证。
