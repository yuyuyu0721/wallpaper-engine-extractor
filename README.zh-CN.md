# wallpaper-engine-extractor

从 **Wallpaper Engine** 的 `.pkg` 文件和 `.tex` 贴图中提取图片、视频及其他资源。

Wallpaper Engine 会把每个已下载的壁纸放在 Steam 创意工坊目录里：

```
<steam>/steamapps/workshop/content/431960/<壁纸ID>/
├── scene.pkg        <- 壁纸需要的全部内容
├── project.json     <- 元数据
└── preview.gif      <- 缩略图
```

本工具解包 `scene.pkg`，并把其中的贴图转换成可以直接打开、编辑或复用的普通文件。

**其他语言：** [English](README.md) | 简体中文

## 特点

- **能识别载荷的真实类型。** 很多壁纸根本不是图片：那个"贴图"其实是一个完整的
  MP4、PNG 或 JPEG。本工具会嗅探载荷内容并正确导出，而不是盲信头部字段。
- **自带 S3TC/DXT 解码器。** DXT1、DXT3、DXT5 贴图直接转成 PNG，不需要 DirectX，
  也不需要外部转换工具。
- **覆盖所有容器版本。** `TEXB0001` 到 `TEXB0004`，各种原始像素格式
  （ARGB8888、RGB888、RGB565、RGBa1010102、RG88、R8），以及 LZ4 压缩的 mipmap。
- **在真实数据上验证过。** 一个 254 个壁纸的库中**全部 2321 张贴图**都能成功解析并
  解码。详见[验证](#验证)。
- **核心零原生依赖。** 纯 Python；`Pillow` 负责写 PNG，`lz4` 负责压缩贴图。

## 安装

```bash
pip install wepkg
```

或者直接从源码运行，无需安装：

```bash
python -m pip install Pillow lz4   # 只有这两个是必需的
python -m wepkg scene.pkg --list   # 需要把 src/ 加进 PYTHONPATH
```

## 用法

```bash
# 先看看里面有什么，不写任何文件（建议从这一步开始）
wepkg scene.pkg --list

# 提取到 ./<壁纸ID>/
wepkg scene.pkg

# 自己指定输出目录
wepkg scene.pkg --name miku
wepkg scene.pkg -o ./out

# 视频壁纸：额外导出一张预览图（需要 ffmpeg，见下文）
wepkg scene.pkg --poster

# 只处理贴图，跳过着色器/模型/特效
wepkg scene.pkg --textures-only

# 批量查看整个创意工坊目录
wepkg "C:/Program Files (x86)/Steam/steamapps/workshop/content/431960" --list

# 单独转换一个 .tex
wepkg materials/miku.tex -o ./out
```

| 选项 | 说明 |
| --- | --- |
| `-o, --outdir DIR` | 输出目录（默认 `./<名称>`） |
| `--name NAME` | 用指定名字替代壁纸 ID |
| `--list`、`--scan` | 只列出内容和识别到的格式，不写文件 |
| `--textures-only` | 跳过着色器、模型和特效 |
| `--poster` | 视频壁纸额外生成一张预览 PNG |
| `--json` | 输出机器可读的报告 |
| `-q, --quiet` | 只打印错误和最终统计 |
| `--version` | 显示版本号 |

## 会得到什么

容器头部并不总是如实描述内容，所以工具会**先检查载荷本身**，只有在识别不出时才
回退到头部声明的格式。

| 识别到的载荷 | 输出 |
| --- | --- |
| 内嵌 MP4（视频壁纸） | `.mp4` |
| 内嵌 PNG / JPEG / WebP / GIF / DDS | 原样输出，字节完全一致 |
| ARGB8888、RGB888、RGB565、RGBa1010102 | `.png` |
| RG88、R8 | `.png` |
| DXT1、DXT3、DXT5 | `.png` |
| LZ4 压缩的 mipmap | 先解压再处理 |
| 不支持的格式（如 BC7） | `.bin` 原始载荷，不丢数据 |

场景元数据（`scene.json`、`project.json`、材质与模型的 JSON）会和贴图一起导出。

> **视频壁纸很常见。** 如果某个包只产出一个 `.mp4`，那它本身就是壁纸——这是一个
> 视频而不是静态图。用 VLC、mpv 或 PotPlayer 播放即可，也可以直接在
> Wallpaper Engine 里"打开文件"导入。

## `--poster` 与 ffmpeg

把视频转成预览图需要 ffmpeg。工具会依次查找 `$FFMPEG`、`PATH`，如果都没有，就
下载一份静态版本到系统临时目录并缓存（只下这一次）。没有 ffmpeg 也不影响其他
功能，只是会跳过 `--poster`。

## 验证

`tools/validate_library.py` 会**在内存中**解析并解码整个创意工坊目录，只报告失败的
项目——速度很快，而且不写任何文件：

```bash
python tools/validate_library.py "<steam>/steamapps/workshop/content/431960"
```

在一个真实库上的当前结果：

```
scanning 254 packages ...
results: {'ok': 2321}
packages: 254

all textures parsed and decoded successfully.
```

单元测试使用合成构造的 `.pkg`/`.tex` 数据流，因此不需要任何游戏文件：

```bash
python -m pytest tests          # 如果环境里有 pytest
python tools/run_tests.py       # 零依赖的备用运行器
```

## 格式要点

以下内容都是通过实际检查得出的，也是格式一旦变动最容易出问题的部分。

**PKG**

```
int32   版本字符串长度
char[]  版本字符串，例如 "PKGV0021"
int32   条目数量
  int32  名称长度 / char[] 名称 / int32 偏移 / int32 长度
<数据区>：各条目载荷依次拼接，偏移量相对于数据区起点
```

版本字符串**自带长度前缀**。漏掉它会让后面所有字段偏移 4 字节，得到看起来合理
但完全错误的值。

**TEX**

```
"TEXV0005\0"  "TEXI0001\0"
uint32 格式, flags, 纹理宽/高, 图像宽/高, 未知
"TEXB000x\0"
uint32 imageCount
uint32 fif        （仅 TEXB0003/0004）
uint32 未知        （仅 TEXB0004）
每个图像：uint32 mipCount，然后 mipCount 组 (记录 + 载荷)
```

各容器的 mipmap 记录——`lead` 是记录之前的容器级字段，`size_index` 指出载荷长度
字段的位置：

| 容器 | 记录长度 | lead | size_index | 记录内容 |
| --- | --- | --- | --- | --- |
| `TEXB0001` | 12 | 0 | 2 | `w h size` |
| `TEXB0002` | 20 | 0 | 4 | `w h comp dec size` |
| `TEXB0003` | 20 | 0 | 4 | `w h comp dec size` |
| `TEXB0004` | 20 | 4 | 4 | `w h comp dec size` |

值得记住的坑：

1. `TEXB0001`/`TEXB0002` **没有** free-image-format 字段；多读一个会吃掉下一个
   字段，导致整个数据流错位。
2. mipmap 层级数并非按统一方式存储，所以解析器会尝试各种可能的层数，并保留
   那个"恰好消耗完整个文件"的解释。
3. `ARGB8888` 实际按 **BGRA** 存放；导出时必须交换 R 和 B，否则颜色会偏。
4. `RG88` 的含义是 `G = 亮度`、`R = alpha`。
5. DXT 的颜色要经过 RGB565 往返，所以解出来的某个通道可能与原作者的值相差一个
   量化步长。

## 项目结构

```
src/wepkg/
├── pkg.py        PKGV 包索引读取
├── tex.py        TEX 贴图读取（全部容器版本）
├── pixels.py     原始像素 + DXT 解码
├── sniff.py      载荷类型识别
├── convert.py    导出为文件
├── ffmpeg.py     可选的 ffmpeg 探测
└── cli.py        命令行界面
tests/            合成样本测试（无需游戏文件）
tools/            验证、冒烟测试与辅助脚本
```

## 开发

```bash
python -m pip install -e ".[dev]"   # 或者：pip install pytest pillow lz4
python -m pytest tests -q           # 单元测试
python tools/smoke_test.py          # 构造一个包并端到端提取
python tools/validate_library.py <创意工坊目录>   # 校验真实壁纸
```

`tools/smoke_test.py` 会驱动**真实的命令行**并检查产出文件，所以能抓到单元测试
覆盖不到的打包和接线问题。它同样不需要游戏文件——自己用合成样本造包。

CI 会在 Linux、Windows、macOS 上针对 Python 3.9 和 3.12 跑单元测试和冒烟测试，
然后构建 sdist 和 wheel。

## 参与贡献

欢迎提 Issue 和 Pull Request。如果某个壁纸提取失败，最有帮助的报告包含
`--list` 的输出和出错的文件名；如果能附上 `validate_library.py` 的失败行就更好。

## 许可证

[MIT](LICENSE)

## 致谢

PKG/TEX 格式最早由 [RePKG](https://github.com/notscuffed/repkg) 和
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine) 两个项目
逆向得出。本项目是一个独立的 Python 实现，自带解码器，并把重点放在载荷嗅探上。
