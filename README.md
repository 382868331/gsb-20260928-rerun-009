# 机器人地图编辑平台

当前阶段聚焦可行走区域编辑与通道验证，定位和导航能力可在后续版本扩展。

## 功能

- 导入 PNG/JPEG/BMP 人工栅格地图，输入米/像素分辨率。
- 画布按真实世界坐标（米）绘制，前端缩放只影响显示，不参与路径计算。
- 使用矩形禁行区修订地图，可调整 ROS 风格地图原点 `(x, y, yaw)`。
- 修改可在前端撤回，并可显式保存为服务端版本；版本支持恢复。
- 上传的原始文件按字节保留在 `data/<project>/source.<ext>`，规范化地图另存为 `original.png`。
- 通道验证检查完整矩形机器人，而不是只检查中心点。

## 完整机器人足迹验证

后端把每个离散朝向的机器人足迹与栅格做形态学腐蚀，得到 `(x, y, yaw)` 配置空间：

- 深色像素、透明像素和人工禁行区均视为障碍。
- 机器人用 `length × width` 矩形描述。
- 搜索平移和原地旋转相邻状态。
- 平移检查中间单元，旋转检查两个朝向之间的半角姿态，避免转动过程刮墙。
- 验证结果和搜索出的姿态路径保存到工程 JSON，重新打开后由同一栅格和参数复现。

## 启动

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --port 8000

cd frontend
npm install
npm run dev
```

打开 Vite 输出的本地地址，前端通过 `/api` 代理访问后端。

## 验证测试

```bash
.venv/bin/python -m pytest -q
npm run build --prefix frontend
```

测试覆盖：

- 1.0 m × 0.5 m 机器人可以通过 1.0 m 宽门。
- 同一机器人不能通过 0.4 m 窄门，即使通道中心线是自由像素。
- 机器人贴边时，中心自由但整机越界会被拒绝。
- 保存、重开、回滚和再次验证得到相同结论。
