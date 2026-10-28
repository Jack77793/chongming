# 基线输出目录

此目录存储 Python 系统的真值输出，用于验证 Godot 全栈重构的正确性。

## 目录结构

```
baseline/
├── empty/
│   ├── truth.jsonl          # Python 系统真值输出
│   ├── manifest.json        # 运行元数据
│   └── ...                  # 其他运行产物
├── constant_velocity/
│   └── ...
└── <scenario_name>/
    └── ...
```

## 生成基线

```bash
# 从项目根目录执行
python3 scripts/test_godot_integration.py --regenerate-baseline
```

## 注意事项

1. **版本控制**：基线输出应提交到 Git，作为回归测试的固定参考
2. **更新时机**：仅在 Python 系统逻辑修改后需要重新生成
3. **环境要求**：生成基线需要 Godot 4.7.2（设置 `CHONGMING_GODOT`）
4. **数据完整性**：不修改或删除已有基线，除非明确需要更新

## 验证基线完整性

```bash
# 检查必需文件是否存在
for scenario in empty constant_velocity; do
    [ -f "baseline/$scenario/truth.jsonl" ] && echo "✓ $scenario" || echo "✗ $scenario"
done
```

## 基线内容说明

- **truth.jsonl**：JSONL 格式真值数据，每行一个时间步的完整场景状态
- **manifest.json**：运行元数据（场景 ID、步数、耗时、来源记录）
- **其他文件**：timing.jsonl、online.jsonl 等，可选保留供诊断使用
