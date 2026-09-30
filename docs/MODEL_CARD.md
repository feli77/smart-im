# 自带离线模型

`tiny_lm.npz` 是实际训练的字符级前馈神经网络：前 4 个字符 → embedding → tanh 隐层 → 下一个字符的概率。运行只需要 NumPy，在 CPU 上本地推理，不需要网络和 API key。

当前权重有 **27,484 个参数、412 项字符词表，约 103 KiB**。训练集为项目自编的短句 `src/smart_im/data/corpus.txt`，有效训练字符 2,638 个。训练脚本和语料随源码提供，没有从用户输入中预训练。

```sh
python scripts/train_tiny_lm.py
```

运行时将神经概率与语料 n-gram 概率插值。下一短语先检索语料中最长上下文后缀的续写，再用模型打分；它是有限语料内的短语建议，不能在任意主题上自由生成高质量文本。上下文无匹配时可以没有续写，不填充固定的虚假建议。

候选排序保留词典和分词证据，并限制模型得分对排名的影响，避免训练词表外的常用词被过度惩罚。个性化层使用确认上屏后的词频与最多 8 字的上下文后缀，不在线修改模型权重。当前没有代表性的独立中文语言质量评测集；训练重现和功能回归测试不等于泛化能力验证。

## 替换模型

实现 `types.LanguageModel` 协议的 `name`、`score(context, text)`、`predict(context, limit)` 即可通过 `Engine(model=...)` 替换。score 应返回越大越好的长度归一化对数概率；predict 返回不重复上下文的续写 Candidate，其 score 可包含检索证据。

可选 GGUF 适配通过 `llama-cpp-python` 从本地文件加载，使用 token 预算限制上下文。安装和编译该依赖可能需要 C/C++ 工具链。只有接口与边界测试，没有附带 GGUF 权重或真实 GGUF 硬件评测；自带模型的延迟数据不适用于 GGUF。

```sh
python -m pip install -e ".[gguf]"
smart-im --model /path/to/autocomplete-model.gguf suggest nihao --context 我们
```

续写适配使用原始文本 completion，因此优先使用中文 base/autocomplete 模型。Instruction/chat 模型可能需要单独实现其提示模板。MVP 不自动执行模型下载。
