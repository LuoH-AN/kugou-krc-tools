# 酷狗 KRC 工具

一个只依赖 Python 标准库的独立命令行工具，用于：

- 按**严格歌手名 + 严格歌名**搜索并下载酷狗 KRC；
- 解码本地 `.krc`，无需重复下载；
- 读取已经解码的 UTF-8 KRC 文本；
- 导出标准逐行 LRC、逐字时间轴 YRC、解码文本；
- 按需导出 JSON 和 CSV。

要求 Python 3.10 或更高版本。不需要安装第三方包。

## 格式说明

### 标准 LRC

`*.lrc` 只写逐行时间戳，例如：

```text
[00:12.345]这一行的文本
```

这是兼容面更广的输出；它不伪装成逐字歌词，也不会生成不存在的逐字时间。

### 本工具的约定式 YRC

`*.yrc` 使用以下毫秒格式：

```text
[lineStart,duration](absoluteWordStart,duration,0)text
```

其中单词开始时间是**绝对时间**，不是相对行首的偏移；持续时间直接保留 KRC 源值，第三字段固定写 `0`。如果某一行在源 KRC 中没有逐字时间，本工具只写：

```text
[lineStart,duration]整行文本
```

不会伪造一个覆盖整行的“单词”时间片。

这里的 YRC 是为时间轴交换及特定导入器采用的私有/约定格式，不是所有播放器都支持的通用歌词标准。它与 `lyrics-android` v1.3.7 的 canonical wordLyrics 导入约定对齐，但这不代表上游项目、所有版本或其他播放器普遍支持它。

本仓库不会承诺或生成所谓 `enhanced.lrc`；需要通用逐行歌词时请使用 `.lrc`，需要保留源逐字时间时请使用 `.yrc` 或 JSON。

## 使用

### 在线严格匹配并下载

```bash
python3 kugou_krc_tools.py \
  --artist "歌手名" \
  --title "歌曲名" \
  --output-dir output
```

工具不会根据子串或相似度猜歌。歌手或歌名只有相似候选、但没有严格匹配时会直接报错，避免下载错歌。

查看接口返回的结果摘要：

```bash
python3 kugou_krc_tools.py \
  --artist "歌手名" \
  --title "歌曲名" \
  --show-results
```

`--show-results` 不会放宽严格匹配规则。

### 转换本地 KRC

```bash
python3 kugou_krc_tools.py \
  --input /path/to/file.krc \
  --output-dir output
```

也可以输入已解码的 UTF-8 KRC 文本：

```bash
python3 kugou_krc_tools.py \
  --input /path/to/file.decoded.krc.txt \
  --output-dir output
```

### 额外输出 JSON / CSV

```bash
python3 kugou_krc_tools.py \
  --input /path/to/file.krc \
  --json \
  --csv
```

### offset 处理

默认行为是：

- 保留 KRC 原始行开始时间、逐字相对偏移和持续时间；
- 在 LRC/YRC 中保留 `[offset:N]` 标签；
- JSON 另存 `global_offset_ms`；
- CSV 同时提供源开始时间和减去 offset 后的 effective 开始时间。

如果目标导入器不识别 `[offset:N]`，可显式应用 offset：

```bash
python3 kugou_krc_tools.py --input file.krc --apply-offset
```

此时从行/字开始时间减去 offset（正数表示提前，负数表示延后，负结果限制为零），持续时间保持不变，并从导出的 LRC/YRC 标签中移除，以避免重复应用。默认不这样做，是为了最大限度保留源时间戳。

## 默认输出

假设文件名前缀为 `歌手 - 歌名`：

- `歌手 - 歌名.lrc`：标准逐行 LRC；
- `歌手 - 歌名.yrc`：约定式逐字 YRC；
- `歌手 - 歌名.decoded.krc.txt`：KRC 解码文本；
- `歌手 - 歌名.krc`：原始 KRC，仅当来源本身是 KRC 二进制时生成；
- `歌手 - 歌名.timeline.json`：仅 `--json` 时生成；
- `歌手 - 歌名.words.csv`：仅 `--csv` 时生成。

可用 `--stem NAME` 自定义文件名前缀。

## 安全与网络

- 歌曲搜索使用提供的 HTTP 接口（HTTPS 域名证书不匹配）；搜索关键词通过明文传输。歌词查询与下载使用 HTTPS 和系统默认的证书校验；
- 不包含关闭 TLS 校验或忽略证书错误的代码；
- 本地 `--input` 模式不会联网；
- 测试全部使用合成文本，不请求网络，也不包含真实歌曲歌词。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖：KRC1 XOR + zlib 解码、合成数据实际写盘、offset、绝对逐字开始时间、无逐字源时不伪造时间、末词和特殊文本不丢失，以及严格歌曲匹配。

## 许可

本仓库目前未指定开源许可证。请由仓库所有者决定许可证后再添加相应文件。
