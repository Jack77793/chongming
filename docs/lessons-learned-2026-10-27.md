# 回退前保留文档（2026-10-27）

## 回退原因
发现代码库异常，包括：
1. 文档可能被破坏
2. 本地文件丢失（未受 Git 保护）
3. 供应链问题（OSM 数据源不稳定）

## 已知漏洞（PR #23 及更早版本存在）

### 1. CylinderGeometry 缺失支持 ⚠️ CRITICAL
**位置**: `src/chongming/rendering/godot.py`

**问题**: `CylinderGeometry` 在 `contracts/scene.py` 中定义，但渲染器不支持

**症状**: 
```
TypeError: unsupported geometry: CylinderGeometry
```

**修复**（已验证）:
```python
# 在 godot.py 顶部添加导入
from chongming.contracts.scene import (
    # ...
    CylinderGeometry,  # 添加这一行
    # ...
)

# 在 _entity 函数中添加
elif isinstance(geometry, CylinderGeometry):
    payload["parameters"] = [float(geometry.radius_m), float(geometry.height_m)]

# 在 _geometry_type 函数中添加
elif isinstance(geometry, CylinderGeometry):
    return 4
```

**影响范围**: 任何使用 CylinderGeometry 的场景都会崩溃

### 2. 飞行器模型选择不明确 ⚠️ MEDIUM

**问题**: `"type": "aircraft"` 默认使用硬编码三角网格，不使用 .glb 模型

**根本原因**: 文档缺失，开发者不知道需要显式指定 `"model": "f86"` 或 `"model": "la11"`

**正确用法**:
```json
{
  "geometry": {
    "type": "aircraft",
    "model": "f86"  // 或 "la11"，不指定则使用硬编码网格
  }
}
```

**位置**: `src/chongming/scenarios/authoring.py:1050-1080`

### 3. 测试覆盖不足 ⚠️ HIGH

**问题**: 
- 没有 CI 自动运行测试
- CylinderGeometry 漏洞长期未被发现
- 开发者不定期运行测试

**建议**:
```bash
# 开发流程中应强制运行
uv run --frozen python -m pytest tests/ -v
```

### 4. 外部资产依赖不稳定 ⚠️ HIGH

**问题**: OSM API 和 Overpass API 经常超时/拒绝服务

**已尝试的数据源**:
- ❌ `overpass-api.de` - 406 Not Acceptable
- ❌ `overpass.kumi.systems` - 超时（4+ 分钟）
- ✅ `api.openstreetmap.org` - 成功但慢（2分钟，2.4 MB）

**建议**: 
- 将关键测试资产（如 Hannover 地景）纳入 Git LFS
- 文档中明确说明资产准备流程
- 提供离线备份方案

## 经验教训

### 1. 资产本地化 (Asset Localization)
**问题**: Hannover 地景需要实时下载，但：
- OSM API 不稳定
- 下载耗时长（2-10 分钟）
- 数据会变化（无法复现）

**解决方案**:
- ✅ 将已验证的资产提交到 Git LFS
- ✅ 在 `scenarios/assets/` 下保留 `.json` 和 `.report.json`
- ✅ 文档中提供 SHA256 校验

### 2. 频繁运行单测
**当前状态**: 
- 测试存在但不强制运行
- CylinderGeometry 漏洞长期潜伏

**改进**:
- 在 AGENTS.md 中添加"每次修改后运行测试"要求
- 考虑 GitHub Actions CI
- Pre-commit hooks

### 3. 文档一致性
**问题**: 
- 飞行器模型用法未在用户指南中说明
- AGENTS.md 可能缺失关键指引

**改进**:
- 在 `docs/user-guide.md` 中补充 aircraft 模型用法
- 在开发文档中强调测试要求

## PR #24 有价值的内容（但不纳入代码库）

### Issue 拆解分析（保留在 GitHub Issues）
- Issue #26: Godot 双向通信
- Issue #27: Godot 场景编辑 UI
- Issue #28: Web 工作台简化
- Issue #29: Manhattan 高层测试用例
- Issue #30: 天气效果
- Issue #31: 场景视觉质量

### 技术研究（可独立重建）
- Realistic Environments 集成受限于静态快照架构
- Truck Town 验证了外部场景工作流（8-10 fps）
- OSM 地景导入流程已验证（Hannover 2.4 MB, 257 buildings）

## 回退操作记录

```bash
# 目标提交: 3f4ca4c (PR #23 合并后)
# 丢弃分支: codex/realistic-environments
# 保留: Issues #25-#31
```

## 下一步行动建议

1. **立即修复** CylinderGeometry 漏洞（单独 PR）
2. **补充文档**: 飞行器模型用法、测试要求
3. **建立 CI**: GitHub Actions 自动运行测试
4. **资产管理**: 将 Hannover 地景纳入 Git LFS
5. **重新实施** Issue #26-#31（在干净基础上）

---
**创建时间**: 2026-10-27  
**创建原因**: 保护性回退前的知识保存  
**回退目标**: 3f4ca4c (PR #23)
