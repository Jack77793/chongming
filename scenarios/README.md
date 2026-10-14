# 可编辑测试场景

所有 JSON 都是独立的 `scenario-authoring-v2` 文件，可直接编辑后重新加载。相机固定输出主 1920×1080、辅 640×480、RGB8；`fps: 30` 定义实验曝光周期，墙钟运行可以更慢。

| 文件 | 内容 | 复查重点 |
| --- | --- | --- |
| `review.json` | 两站、24 秒、球/盒/飞机绕行一周 | 四路身份、轮廓、地物遮挡、完整循环、切站与暂停 |
| `heightfield.json` | 一站、12 秒、非零原点的 16 米山脊 | 上升球体中心先被地形挡住、后露出；高处盒体中心始终可捕获 |
| `constant_velocity.json` | 两站、12 秒、同高反向匀速运动 | 约第 6 秒中心投影重合；两站与目标自定义 ID |
| `empty.json` | 两站、2 秒、无飞行目标 | 背景与参考面板仍存在，目标射线为零，校准事件仍可存在 |

目前 JSON 使用内置的建筑、球形地物、参考面板和相机朝向模板；这些尚不能独立编辑。地形网格的外部区域仍是平面。`heights_m` 的每行从西向东排列，行从南向北排列，数值是相对基础平面的高度；`origin_enu_m` 是网格西南角，`spacing_m` 是正数。地形表面参与主辅图像、逻辑遮挡和场景总览。

轨道运动当前编译为单周期插值轨迹，因此场景时长应不超过 `period_s`；更长的连续运动可先用匀速行为。飞行物在图像中参与深度遮挡，但当前逻辑可见性只检查地形、静态地物与植被，不检查其他飞行物；交叉场景用于观察投影重合，不用于验收互遮挡后的虚拟射线。风、云、雾、雨、植被、高级传感器效果和实际检测/标定/融合链路仍未接入，不能用这些场景声称其已验收。

## 启动与导出

在仓库根目录执行，端口可自行更换；一次启动一个场景。初始暂停，点击播放或单步。

```sh
uv sync
uv run chongming-preview --scenario scenarios/heightfield.json --port 8878 --no-browser
```

浏览器打开 `http://127.0.0.1:8878/`。替换 `--scenario` 后的路径可检查其余场景；`--render-fps 8` 只限制墙钟节拍，保持图像尺寸与实验时间。

导出完整有限流时不需要预览服务或端口，建议先停预览以便测量模块耗时：

```sh
uv run chongming-scene --scenario scenarios/heightfield.json --output results/heightfield-full
uv run chongming-scene --scenario scenarios/heightfield.json --output results/heightfield-every3 --render-every 3
```

输出目录必须尚不存在。完整流用于图像质量评估；降频流用于检查事件/真值/逻辑计数不变、跳过记录与 GPU 提交计数变化，不将跳过帧算成检测漏帧。证据包括按相机分组的 PNG、普通事件、独立真值、逐步耗时、manifest 和 summary。

只检查尺寸、时间、语义、遮挡变化与确定性，不维护逐像素 golden。自动检查加载仓库内的全部 JSON，并针对地形原点和匀速位移做几何验证；视觉表现仍需人工复查。
