# 产品数据与调用约定

criteria 的 direction 为 min/max，domain 是业务固定参照区间，不能由当前候选最大最小值重定义。observations 的 interval 是不确定性能范围，unit 可转换但不可跨维度混用；lane/coverage 决定可比人群。preferences 是特定线路和评审范围内的严格优选/不劣意见，不代表客观测量。vetoes 是业务硬门槛；否决可能触发但不确定时须保留条件，而非按中点通过。新增 POST /recommend {case, lane?, answers?}，answers 是追加的比较意见，不覆写原意见。返回 {snapshot, necessary_edges, possible_winners, conflicts, questions, diagnostics}。necessary_edges 为在声明模型和证据范围内必然成立的关系，possible_winners 是仍可能优选的候选标识，conflicts 需引用意见标识。分析方法可自行选择，但须说明适用的偏好族和性能区间语义，不能把任意一组打分当稳健结论。

当前 GET /cases、POST /preview 和 GET /health 已可用；client.analysis 是待接入的业务消费调用，新分析路由尚不存在。原始数据通过 app.store 读取，snapshot 为输入字节的 SHA-256。新增接口须保留现有预览能力和 JSON 有限数值约定。

necessary_edges 每项为 {better, worse, evidence}，possible_winners 为候选 id 列表。conflicts 可以为空；有矛盾时不得不加说明地输出完整确定名次。questions 应围绕尚未被偏好集蕴含、且能影响选择的比较；回答后的排序解释保留原意见出处。加入已被支配且同样覆盖的方案，不应仅因重新归一而改变既有关系。
