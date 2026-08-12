# docs/vs-tools — 竞品工具差异对比文档集

本目录按“工具 → 主题”分层，存放 supernova-py 与外部安全扫描工具之间的能力、规则和效果差异分析。

```text
docs/vs-tools/
├── README.md
├── deepsec/
│   ├── README.md
│   └── source-sink-rules-adoption-review.md
└── <another-tool>/
    └── ...
```

## 工具目录

| 工具 | 说明 |
|---|---|
| [`deepsec/`](./deepsec/) | DeepSec source/sink 规则覆盖、可迁移性与吸收优先级评估 |
| [`openant/`](./openant/) | OpenAnt source/sink、调用链、漏洞研判与动态验证实现审计 |
