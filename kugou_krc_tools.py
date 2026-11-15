#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载、解码并转换酷狗 KRC 歌词。

仅使用 Python 标准库。默认输出标准逐行 LRC、私有/约定式逐字 YRC、
原始 KRC（若输入包含原始二进制）和解码文本；JSON/CSV 为可选输出。
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Iterable

__version__ = "1.0.0"

SONG_SEARCH_URL = "http://ioscdn.kugou.com/api/v3/search/song"
KRC_SEARCH_URL = "https://krcs.kugou.com/search"
KRC_DOWNLOAD_URL = "https://lyrics2.kugou.com/download"

# KRC1 固定循环异或密钥。
KRC_XOR_KEY = bytes(
    [
        0x40,
        0x47,
        0x61,
        0x77,
        0x5E,
        0x32,
        0x74,
        0x47,
        0x51,
        0x36,
        0x31,
        0x2D,
        0xCE,
        0xD2,
        0x6E,
        0x69,
    ]
)

LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
WORD_TAG_RE = re.compile(r"<(-?\d+),(\d+),(-?\d+)>")
META_RE = re.compile(r"^\[([^:\]]+):(.*)\]$")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def request_json(url: str, params: dict[str, Any], timeout: float = 25.0) -> dict[str, Any]:
    """通过系统默认 TLS 校验请求 JSON；不关闭证书验证。"""
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": f"kugou-krc-tools/{__version__}",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.URLError as error:
        raise RuntimeError(f"网络请求失败：{error}") from error

    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"接口没有返回有效 JSON：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("接口返回的 JSON 顶层不是对象。")
    return value


def normalize_identity(value: Any) -> str:
    """用于严格身份匹配：Unicode 归一化、忽略空白和大小写，不删除后缀。"""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", normalized).casefold()


def search_songs(keyword: str, timeout: float = 25.0, page_size: int = 40) -> list[dict[str, Any]]:
    result = request_json(
        SONG_SEARCH_URL,
        {
            "keyword": keyword,
            "page": 1,
            "pagesize": page_size,
            "showtype": 10,
            "plat": 2,
            "version": 7910,
            "tag": 1,
            "correct": 1,
            "privilege": 1,
            "sver": 5,
        },
        timeout=timeout,
    )
    songs = result.get("data", {}).get("info", [])
    return [item for item in (songs or []) if isinstance(item, dict)]


def song_matches(song: dict[str, Any], artist: str, title: str) -> bool:
    wanted_artist = normalize_identity(artist)
    wanted_title = normalize_identity(title)
    singer = normalize_identity(song.get("singername"))
    titles = {
        normalize_identity(song.get("songname_original")),
        normalize_identity(song.get("songname")),
    }
    titles.discard("")
    return bool(wanted_artist and wanted_title and singer == wanted_artist and wanted_title in titles)


def _song_summary(song: dict[str, Any]) -> str:
    singer = str(song.get("singername") or "?")
    title = str(song.get("songname_original") or song.get("songname") or "?")
    return f"{singer} - {title}"


def choose_song(songs: list[dict[str, Any]], artist: str, title: str) -> dict[str, Any]:
    """只从歌手和歌名都严格相等的结果中选择，不以模糊分数猜歌。"""
    exact = [song for song in songs if song_matches(song, artist, title)]
    if not exact:
        nearby = "；".join(_song_summary(item) for item in songs[:5]) or "无结果"
        raise RuntimeError(
            "没有找到歌手与歌名均严格匹配的歌曲，已拒绝自动选择相似结果。"
            f"返回结果示例：{nearby}"
        )

    # 若接口返回重复条目，只在严格匹配集合内优先选择信息完整者。
    return max(
        exact,
        key=lambda item: (
            bool(item.get("hash")),
            bool(item.get("album_audio_id")),
            bool(item.get("album_name")),
        ),
    )


def candidate_matches(candidate: dict[str, Any], artist: str, title: str) -> bool:
    return (
        normalize_identity(candidate.get("singer")) == normalize_identity(artist)
        and normalize_identity(candidate.get("song")) == normalize_identity(title)
    )


def search_krc(
    song: dict[str, Any], artist: str, title: str, timeout: float = 25.0
) -> dict[str, Any]:
    duration = int(song.get("duration") or 0)
    duration_ms = duration if duration >= 10_000 else duration * 1000
    song_hash = str(song.get("hash") or "")
    album_audio_id = song.get("album_audio_id") or ""

    keywords: list[str] = []
    for value in (song.get("filename"), f"{artist} - {title}", title):
        keyword = str(value or "").strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)

    examples: list[str] = []
    last_message: Any = None
    for keyword in keywords:
        result = request_json(
            KRC_SEARCH_URL,
            {
                "ver": 1,
                "man": "no",
                "client": "pc",
                "keyword": keyword,
                "duration": duration_ms,
                "hash": song_hash,
                "album_audio_id": album_audio_id,
                "lrctxt": 1,
            },
            timeout=timeout,
        )
        last_message = result.get("errmsg") or result.get("info")
        candidates = [
            item for item in (result.get("candidates") or []) if isinstance(item, dict)
        ]
        exact = [item for item in candidates if candidate_matches(item, artist, title)]
        if exact:
            return max(
                exact,
                key=lambda item: (
                    item.get("product_from") == "官方推荐歌词",
                    bool(item.get("accesskey")),
                    bool(item.get("id")),
                ),
            )
        examples.extend(
            f"{item.get('singer') or '?'} - {item.get('song') or '?'}"
            for item in candidates[:3]
        )

    nearby = "；".join(dict.fromkeys(examples)) or "无候选"
    raise RuntimeError(
        "没有找到歌手与歌名均严格匹配的 KRC，已拒绝下载相似歌曲歌词。"
        f"候选示例：{nearby}；接口信息：{last_message!r}"
    )


def download_krc(candidate: dict[str, Any], timeout: float = 25.0) -> bytes:
    accesskey = candidate.get("accesskey")
    lyrics_id = candidate.get("download_id") or candidate.get("id")
    if not accesskey or not lyrics_id:
        raise RuntimeError("KRC 候选缺少 accesskey 或 id，无法下载。")

    result = request_json(
        KRC_DOWNLOAD_URL,
        {
            "accesskey": accesskey,
            "charset": "utf8",
            "client": "pc",
            "fmt": "krc",
            "id": lyrics_id,
            "ver": 1,
        },
        timeout=timeout,
    )
    if int(result.get("status", 0)) != 200 or not result.get("content"):
        raise RuntimeError(
            f"KRC 下载失败：status={result.get('status')}, "
            f"error_code={result.get('error_code')}, info={result.get('info')!r}"
        )
    try:
        raw = base64.b64decode(result["content"], validate=True)
    except (ValueError, TypeError) as error:
        raise RuntimeError(f"KRC 下载内容不是有效 Base64：{error}") from error
    if not raw.startswith(b"krc1"):
        raise RuntimeError(f"下载内容不是受支持的 KRC1 文件，文件头为：{raw[:16]!r}")
    return raw


def decode_krc(raw: bytes) -> str:
    if not raw.startswith(b"krc1"):
        raise RuntimeError(f"输入不是受支持的 KRC1 文件，文件头为：{raw[:16]!r}")
    encrypted = raw[4:]
    compressed = bytes(
        value ^ KRC_XOR_KEY[index % len(KRC_XOR_KEY)]
        for index, value in enumerate(encrypted)
    )
    try:
        decoded = zlib.decompress(compressed)
    except zlib.error as error:
        raise RuntimeError(f"KRC zlib 解压失败：{error}") from error
    try:
        return decoded.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"KRC 解压结果不是 UTF-8 文本：{error}") from error


def encode_krc_for_test(decoded_text: str) -> bytes:
    """构造 KRC1 数据，供测试或兼容性验证使用；不会被下载流程调用。"""
    compressed = zlib.compress(decoded_text.encode("utf-8"))
    encrypted = bytes(
        value ^ KRC_XOR_KEY[index % len(KRC_XOR_KEY)]
        for index, value in enumerate(compressed)
    )
    return b"krc1" + encrypted


def parse_krc(text: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    metadata: dict[str, str] = {}
    lines: list[dict[str, Any]] = []

    for source_line in text.splitlines():
        line_match = LINE_RE.match(source_line)
        if line_match:
            line_start = int(line_match.group(1))
            line_duration = int(line_match.group(2))
            body = line_match.group(3)
            markers = list(WORD_TAG_RE.finditer(body))
            words: list[dict[str, Any]] = []
            prefix_text = body[: markers[0].start()] if markers else ""

            for index, marker in enumerate(markers):
                text_start = marker.end()
                text_end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
                value = body[text_start:text_end]
                relative_start = int(marker.group(1))
                duration = int(marker.group(2))
                source_flag = int(marker.group(3))
                absolute_start = line_start + relative_start
                words.append(
                    {
                        "text": value,
                        "offset_ms": relative_start,
                        "start_ms": absolute_start,
                        "duration_ms": duration,
                        "end_ms": absolute_start + duration,
                        "source_flag": source_flag,
                    }
                )

            plain_text = prefix_text + "".join(word["text"] for word in words)
            if not markers:
                plain_text = body

            lines.append(
                {
                    "start_ms": line_start,
                    "duration_ms": line_duration,
                    "end_ms": line_start + line_duration,
                    "text": plain_text,
                    "untimed_prefix": prefix_text,
                    "words": words,
                }
            )
            continue

        meta_match = META_RE.match(source_line)
        if meta_match:
            metadata[meta_match.group(1)] = meta_match.group(2)

    if not lines:
        raise RuntimeError("KRC 文本中没有解析出任何时间轴行。")
    return metadata, lines


def metadata_value(metadata: dict[str, str], key: str) -> str | None:
    wanted = key.casefold()
    for current_key, value in metadata.items():
        if current_key.casefold() == wanted:
            return value
    return None


def global_offset_ms(metadata: dict[str, str]) -> int:
    value = metadata_value(metadata, "offset")
    if value is None or not value.strip():
        return 0
    try:
        return int(value.strip())
    except ValueError as error:
        raise RuntimeError(f"无效的 KRC offset 元数据：{value!r}") from error


def safe_filename(value: str) -> str:
    value = INVALID_FILENAME_RE.sub("_", value).strip().rstrip(".")
    return value[:150] or "kugou-krc"


def lrc_time(milliseconds: int) -> str:
    sign = "-" if milliseconds < 0 else ""
    minutes, remainder = divmod(abs(milliseconds), 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{sign}{minutes:02d}:{seconds:02d}.{millis:03d}"


def _output_tags(
    metadata: dict[str, str], fallback: dict[str, Any], apply_offset: bool
) -> list[str]:
    values = [
        ("ti", metadata_value(metadata, "ti") or fallback.get("title")),
        ("ar", metadata_value(metadata, "ar") or fallback.get("artist")),
        ("al", metadata_value(metadata, "al") or fallback.get("album")),
        ("by", metadata_value(metadata, "by")),
    ]
    tags = [f"[{key}:{value}]" for key, value in values if str(value or "").strip()]
    raw_offset = metadata_value(metadata, "offset")
    if not apply_offset and raw_offset is not None:
        tags.append(f"[offset:{raw_offset}]")
    return tags


def render_lrc(
    metadata: dict[str, str],
    lines: list[dict[str, Any]],
    fallback: dict[str, Any] | None = None,
    apply_offset: bool = False,
) -> str:
    fallback = fallback or {}
    adjustment = global_offset_ms(metadata) if apply_offset else 0
    output = _output_tags(metadata, fallback, apply_offset)
    output.extend(
        f"[{lrc_time(max(0, line['start_ms'] - adjustment))}]{line['text']}" for line in lines
    )
    return "\n".join(output) + "\n"


def render_yrc(
    metadata: dict[str, str],
    lines: list[dict[str, Any]],
    fallback: dict[str, Any] | None = None,
    apply_offset: bool = False,
) -> str:
    fallback = fallback or {}
    adjustment = global_offset_ms(metadata) if apply_offset else 0
    output = _output_tags(metadata, fallback, apply_offset)

    for line in lines:
        line_start = max(0, line["start_ms"] - adjustment)
        parts = [f"[{line_start},{line['duration_ms']}]", line.get("untimed_prefix", "")]
        if line["words"]:
            for word in line["words"]:
                # 约定式 YRC 第三字段固定输出 0；KRC 原始 flag 保留在 JSON 中。
                parts.append(
                    f"({max(0, word['start_ms'] - adjustment)},{word['duration_ms']},0){word['text']}"
                )
        else:
            # 源文件没有逐字时间时只保留行文本，不伪造一个“整行单词”。
            parts.append(line["text"])
        output.append("".join(parts))
    return "\n".join(output) + "\n"


def timeline_json(
    metadata: dict[str, str],
    lines: list[dict[str, Any]],
    source: dict[str, Any],
    apply_offset: bool,
) -> dict[str, Any]:
    offset = global_offset_ms(metadata)
    return {
        "format": "kugou-krc-timeline-v1",
        "time_unit": "milliseconds",
        "global_offset_ms": offset,
        "offset_applied_to_rendered_timestamps": apply_offset,
        "source": source,
        "metadata": metadata,
        "lines": lines,
    }


def write_csv(
    path: Path,
    metadata: dict[str, str],
    lines: list[dict[str, Any]],
) -> None:
    offset = global_offset_ms(metadata)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "line_index",
                "word_index",
                "text",
                "line_start_ms",
                "line_effective_start_ms",
                "word_offset_ms",
                "word_start_ms",
                "word_effective_start_ms",
                "duration_ms",
                "end_ms",
                "source_flag",
            ]
        )
        for line_index, line in enumerate(lines):
            for word_index, word in enumerate(line["words"]):
                writer.writerow(
                    [
                        line_index,
                        word_index,
                        word["text"],
                        line["start_ms"],
                        max(0, line["start_ms"] - offset),
                        word["offset_ms"],
                        word["start_ms"],
                        max(0, word["start_ms"] - offset),
                        word["duration_ms"],
                        word["end_ms"],
                        word["source_flag"],
                    ]
                )


def write_outputs(
    output_dir: Path,
    stem: str,
    decoded_text: str,
    metadata: dict[str, str],
    lines: list[dict[str, Any]],
    raw_krc: bytes | None = None,
    fallback: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    include_json: bool = False,
    include_csv: bool = False,
    apply_offset: bool = False,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(stem)
    fallback = fallback or {}
    source = source or {}
    paths: list[Path] = []

    lrc_path = output_dir / f"{stem}.lrc"
    yrc_path = output_dir / f"{stem}.yrc"
    decoded_path = output_dir / f"{stem}.decoded.krc.txt"
    lrc_path.write_text(render_lrc(metadata, lines, fallback, apply_offset), encoding="utf-8")
    yrc_path.write_text(render_yrc(metadata, lines, fallback, apply_offset), encoding="utf-8")
    decoded_path.write_text(decoded_text, encoding="utf-8")
    paths.extend([lrc_path, yrc_path, decoded_path])

    if raw_krc is not None:
        raw_path = output_dir / f"{stem}.krc"
        raw_path.write_bytes(raw_krc)
        paths.append(raw_path)

    if include_json:
        json_path = output_dir / f"{stem}.timeline.json"
        json_path.write_text(
            json.dumps(
                timeline_json(metadata, lines, source, apply_offset),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        paths.append(json_path)

    if include_csv:
        csv_path = output_dir / f"{stem}.words.csv"
        write_csv(csv_path, metadata, lines)
        paths.append(csv_path)

    return paths


def load_local_input(path: Path) -> tuple[bytes | None, str]:
    payload = path.read_bytes()
    if payload.startswith(b"krc1"):
        return payload, decode_krc(payload)
    try:
        return None, payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RuntimeError(
            "本地输入既不是 KRC1，也不是 UTF-8 解码文本。"
        ) from error


def print_results(songs: Iterable[dict[str, Any]]) -> None:
    for index, item in enumerate(songs):
        print(
            f"  [{index}] {_song_summary(item)} | "
            f"duration={item.get('duration')} | album_audio_id={item.get('album_audio_id')}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="下载或读取本地酷狗 KRC，输出 LRC、约定式 YRC 和解码文本。"
    )
    parser.add_argument("--artist", help="歌手；联网下载时必填")
    parser.add_argument("--title", help="歌名；联网下载时必填")
    parser.add_argument("--keyword", help="搜索关键词；默认使用“歌手 歌名”")
    parser.add_argument(
        "--input",
        type=Path,
        help="本地 .krc 或 UTF-8 解码 KRC 文本；指定后不联网",
    )
    parser.add_argument(
        "--output-dir",
        "--output",
        dest="output_dir",
        type=Path,
        default=Path("output"),
        help="输出目录，默认：./output",
    )
    parser.add_argument("--stem", help="输出文件名前缀")
    parser.add_argument("--json", action="store_true", help="额外输出 timeline.json")
    parser.add_argument("--csv", action="store_true", help="额外输出 words.csv")
    parser.add_argument(
        "--apply-offset",
        action="store_true",
        help="从导出的 LRC/YRC 时间戳减去 [offset:N] 并移除 offset 标签；默认保留源时间戳",
    )
    parser.add_argument(
        "--show-results",
        action="store_true",
        help="打印搜索结果摘要；仍只接受严格匹配项",
    )
    parser.add_argument("--timeout", type=float, default=25.0, help="联网超时秒数")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.input:
        if not args.input.is_file():
            parser.error(f"--input 文件不存在：{args.input}")
        raw_krc, decoded_text = load_local_input(args.input)
        metadata, lines = parse_krc(decoded_text)
        stem = args.stem or args.input.name
        for suffix in (".decoded.krc.txt", ".krc.txt", ".krc", ".txt"):
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        fallback = {"artist": args.artist, "title": args.title}
        source = {"type": "local", "input": str(args.input)}
    else:
        if not args.artist or not args.title:
            parser.error("联网下载必须同时提供 --artist 和 --title；或使用 --input。")
        keyword = args.keyword or f"{args.artist} {args.title}"
        print(f"[1/5] 搜索歌曲：{keyword}")
        songs = search_songs(keyword, timeout=args.timeout)
        if args.show_results:
            print_results(songs[:10])
        song = choose_song(songs, args.artist, args.title)
        print(f"[2/5] 严格匹配：{_song_summary(song)}")
        print("[3/5] 搜索严格匹配的 KRC……")
        candidate = search_krc(song, args.artist, args.title, timeout=args.timeout)
        print("[4/5] 下载并解码 KRC……")
        raw_krc = download_krc(candidate, timeout=args.timeout)
        decoded_text = decode_krc(raw_krc)
        metadata, lines = parse_krc(decoded_text)
        stem = args.stem or f"{args.artist} - {args.title}"
        fallback = {
            "artist": args.artist,
            "title": args.title,
            "album": song.get("album_name"),
        }
        source = {
            "type": "kugou-download",
            "artist": args.artist,
            "title": args.title,
            "song_hash": song.get("hash"),
            "album_audio_id": song.get("album_audio_id"),
            "lyrics_id": candidate.get("id"),
            "lyrics_source": candidate.get("product_from"),
        }

    word_count = sum(len(line["words"]) for line in lines)
    paths = write_outputs(
        args.output_dir,
        stem,
        decoded_text,
        metadata,
        lines,
        raw_krc=raw_krc,
        fallback=fallback,
        source=source,
        include_json=args.json,
        include_csv=args.csv,
        apply_offset=args.apply_offset,
    )

    print(f"已解析 {len(lines)} 行、{word_count} 个源逐字时间片。")
    print("完成。输出文件：")
    for path in paths:
        print(f"  {path.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
