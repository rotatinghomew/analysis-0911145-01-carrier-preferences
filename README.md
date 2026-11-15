# 含否决条件与矛盾偏好的承运商稳健选型分析

这是一个本地运行的分析工作台。当前版本提供业务数据检查、已有口径的预览、稳健选型分析（`POST /recommend`）、HTTP 查询及 Python 消费端。字段的业务含义与兼容边界见 `docs/contracts.md`，验证范围见 `docs/verification.md`。

数据均为本地合成，不含真实姓名、电话号码或生产系统凭据。`standard` 是常规资料，`edge` 用于检查信息不足或约束冲突，`dominated` 用于检查新增候选不会重排既有关系。它们只描述观测，不预设分析结论。

## 分析口径

选型分析不用加权评分。它声明一个序数型、逐准则单调的偏好族，并按 `docs/contracts.md` 输出：

- `necessary_edges`：在区间资料（仅当区间分离才比较）、未矛盾评审意见及传递闭包下必然成立的关系；
- `possible_winners`：仍可能优选、且未被确定排除的候选，资料分不出高下时并列保留；
- `conflicts`/`diagnostics.most_urgent_conflict`：彼此矛盾、或与区间资料冲突的意见整组列出并暂停推断，最需要确认的一组单独点出，不降低任何一方权重；
- `questions`：最多两个能缩小选择范围的追问，并给出两种回答各自会新增/撤销哪些关系、移除哪些候选；
- `diagnostics`：输入口径、否决判定、条件排除、可逆条件、仍缺信息与限制说明。

业务否决是硬门槛：区间确定越过阈值即排除，不被低价等其他优点补偿；区间跨越阈值时视为不确定，列为条件排除，绝不按中点判过。分析不签约、不选择承运商。

## 本地使用

Python 3.11，依赖版本固定在 `requirements.txt`。NumPy/SciPy 是分析工作台的数值工具；预览逻辑主要使用标准库。准备阶段安装依赖，仓库不在启动或测试时下载任何资源。

支持 Python 3.11 系列的补丁版本；Docker 基础镜像通过 SHA256 固定为 Python 3.11.14，依赖清单保持精确版本。相同依赖还需在非 root 的 Python 3.11.2 Linux 环境中验证，应用不依赖特定补丁版本。

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m app.demo
python -m app.server
```

服务监听 `127.0.0.1:8080`，也可通过 `PORT` 调整。预览与分析请求由 `app/client.py` 发起（`client.analysis` 对应 `POST /recommend`）；`app.demo` 在临时端口启动真实服务，经实际 HTTP 调用完整打印输入口径、分析结果、追问预测、回答后的重算以及限制说明，不是录制日志的回放。服务接口和数据文件的可见性约定不能互相替代。

## 可复现环境

```sh
docker build -t local/carrier-preferences:baseline .
docker run --rm --network none local/carrier-preferences:baseline python -m unittest discover -s tests -v
docker run --rm --network none local/carrier-preferences:baseline
```

准备阶段允许构建镜像和预装依赖；正式演示不进行构建或安装。`app.demo` 通过实际 HTTP 消费调用展示输入口径、分析结果和限制，保留完整输出，小样本在 90 秒内结束。公开契约测试需显式启用：`RUN_ACCEPTANCE=1 python -m unittest tests.test_acceptance -v`；`tests/test_analysis.py` 用内存合成数据覆盖算法设计，默认随 `unittest discover` 运行，不依赖固定 fixture 名称。
