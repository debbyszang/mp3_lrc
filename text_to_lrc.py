#!/usr/bin/env python3
"""
文案朗读 + 字幕生成工具

根据输入的纯文本文件，使用Edge-TTS生成AI语音朗读的MP3音频，
并同步生成逐句对齐的LRC字幕文件。

Usage:
    python3 text_to_lrc.py -t 文件名 [选项]

Examples:
    # 最简用法：默认读取 test_en.txt
    python3 text_to_lrc.py -t Kris头像

    # 指定输入文件
    python3 text_to_lrc.py --input 其他文案.txt -t 我的播客

    # 生成 M4A + MKV 字幕文件（需要 ffmpeg）
    python3 text_to_lrc.py -t Kris头像 --mkv

    # 列出所有可用音色
    python3 text_to_lrc.py --list-voices

Output:
    - {文件名}.mp3       完整朗读音频
    - {文件名}.lrc       逐句对齐的LRC字幕
    - {文件名}.m4a       音频+内嵌字幕（需加 --mkv）
    - {文件名}.mkv       硬字幕封装（需加 --mkv）

    生成后自动拷贝到: ~/storage/shared/albert-eng/

Options:
    --title NAME      输出文件名（不含扩展名，必填）
    --input PATH      输入纯文本文件路径（默认 test_en.txt）
    --output DIR      输出目录（默认当前目录）
    --voice NAME      TTS音色名称或简写（默认 brian）
    --rate RATE       语速调节，如 "+20%" 加速，"-10%" 减速
    --list-voices     列出所有可用的音色
    --mkv             生成 M4A + MKV 字幕文件（默认关闭）
"""

import argparse
import asyncio
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import edge_tts


# 生成后自动拷贝到的目录（支持 ~ 路径展开）
SHARED_OUTPUT_DIR = os.path.expanduser("~/storage/shared/albert-eng")

# 常用音色列表（中英文）
POPULAR_VOICES = {
    # 美语音色
    "brian": "en-US-BrianNeural",               # 美国男声，清晰稳重（默认）
    "jenny": "en-US-JennyNeural",               # 美国女声，自然地道
    "emma": "en-US-EmmaNeural",                  # 美国女声，柔和自然
    "andrew": "en-US-AndrewNeural",              # 美国男声，专业
    "aria": "en-US-AriaNeural",                  # 美国女声，播报
    "christopher": "en-US-ChristopherNeural",     # 美国男声，专业
    "roger": "en-US-RogerNeural",                # 美国男声，播音员
    # 中文音色
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",          # 女声，温柔
    "xiaoyi": "zh-CN-XiaoyiNeural",              # 女声，活泼
    "yunxi": "zh-CN-YunxiNeural",                # 男声，阳光
    "yunyang": "zh-CN-YunyangNeural",            # 男声，新闻播报
    "xiaohan": "zh-CN-XiaohanNeural",            # 女声，甜美
    "xiaomo": "zh-CN-XiaomoNeural",              # 女声，成熟
    "xiaorui": "zh-CN-XiaoruiNeural",            # 女声，沉稳
    "xiaoshuang": "zh-CN-XiaoshuangNeural",       # 女声，儿童
    "xiaofeng": "zh-CN-XiaofengNeural",          # 男声，成熟
    "yunhao": "zh-CN-YunhaoNeural",              # 男声，沉稳
    "yunxia": "zh-CN-YunxiaNeural",              # 男声，儿童
    "yunjian": "zh-CN-YunjianNeural",            # 男声，磁性
}


@dataclass
class Sentence:
    """一句话的字幕单元"""
    text: str           # 文本内容
    start: float        # 开始时间（秒）
    end: float          # 结束时间（秒）


def format_srt_time(seconds: float) -> str:
    """将秒数格式化为SRT时间戳 HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_lrc_time(seconds: float) -> str:
    """将秒数格式化为LRC时间戳 [mm:ss.xx]"""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def escape_lrc_text(text: str) -> str:
    """LRC文本转义（处理特殊字符）"""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def merge_lines_to_paragraphs(lines: list[str]) -> list[str]:
    """
    将多行文本合并为段落。

    规则：非空行相连则视为同一段落（段内用空格连接），
    连续的空行分隔不同段落。
    """
    paragraphs = []
    current = []

    for line in lines:
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        else:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            # 多个空行只产生一个段落分隔
    if current:
        paragraphs.append(" ".join(current))

    return paragraphs


async def synthesize_text_get_sentences(
    text: str,
    voice: str,
    rate: str,
) -> tuple[bytes, list[Sentence]]:
    """
    合成整段文字，返回合并后的音频数据和各句的起止时间。
    通过两次流遍历：第一次收集音频+句子边界，第二次计算时长。
    """
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    audio_chunks = []
    sentence_boundaries: list[tuple[int, int, str]] = []  # (offset, duration, text)

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "SentenceBoundary":
            sentence_boundaries.append((chunk["offset"], chunk["duration"], chunk["text"]))

    audio_data = b"".join(audio_chunks)

    # 计算每句的起止时间
    # offset 和 duration 单位是 100 纳秒（10^-7 秒）
    sentences = []
    for offset, duration, text in sentence_boundaries:
        start_sec = offset / 10_000_000
        end_sec = (offset + duration) / 10_000_000
        sentences.append(Sentence(text=text.strip(), start=start_sec, end=end_sec))

    # 如果没有 SentenceBoundary（极短文本），用总时长作为整句
    if not sentences:
        total_duration = 0.0
        async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
            if chunk["type"] == "SentenceBoundary":
                end_sec = (chunk["offset"] + chunk["duration"]) / 10_000_000
                if end_sec > total_duration:
                    total_duration = end_sec
        sentences.append(Sentence(text=text.strip(), start=0.0, end=total_duration))

    return audio_data, sentences


async def synthesize_paragraph(
    paragraph: str,
    voice: str,
    rate: str,
    silence_between: float = 0.5,
) -> tuple[bytes, list[Sentence], float]:
    """
    合成单个段落，返回音频数据、句子列表、全文总时长。

    Args:
        paragraph: 要合成的段落文本
        voice: 音色名称
        rate: 语速
        silence_between: 句子之间的静音时长（秒）

    Returns:
        (audio_data, sentences, total_duration)
    """
    audio_data, sentences = await synthesize_text_get_sentences(paragraph, voice, rate)

    if not sentences:
        return b"", [], 0.0

    # 累加句子间的静音，重新计算每句的绝对起始时间
    total = 0.0
    for sent in sentences:
        sent.start = total
        total += (sent.end - (sent.start if sent.end > sent.start else 0)) + silence_between
        sent.end = total - silence_between  # 减去最后一个静音

    total_duration = total - silence_between  # 最后一句不加静音
    return audio_data, sentences, total_duration


def generate_silence_ms(duration_ms: int) -> bytes:
    """
    生成MP3静音帧。

    使用标准的MP3静音帧头（128kbps, 44100Hz, Stereo），
    每帧约26ms，生成指定毫秒数的静音。
    """
    frame_header = b'\xff\xfb\x90\x00'
    frame_size = 417  # 128kbps, 44100Hz, stereo, padding
    frame_duration_ms = 26.12

    silent_frame = frame_header + b'\x00' * (frame_size - 4)
    num_frames = int(duration_ms / frame_duration_ms) + 1
    return silent_frame * num_frames


async def text_to_subtitles(
    input_path: str,
    output_dir: str,
    voice: str,
    rate: str,
    stem: str,
    generate_mkv: bool = False,
) -> tuple[str, str, tuple]:
    """
    主处理流程：读取文本，分段落合成，生成MP3、LRC、SRT。

    Returns:
        (mp3_path, lrc_path, (m4a_path, mkv_path))
    """
    with open(input_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    # 过滤空行但保留段落结构
    lines = [line.rstrip("\n\r") for line in raw_lines]
    paragraphs = merge_lines_to_paragraphs(lines)

    if not paragraphs:
        print("错误：输入文件为空", file=sys.stderr)
        sys.exit(1)

    print(f"读取到 {len(paragraphs)} 个段落")

    # ---------- 清理历史产物（清理输出目录下的所有 mp3/lrc/m4a/mkv） ----------
    os.makedirs(output_dir, exist_ok=True)
    for ext in ("mp3", "lrc", "m4a", "mkv"):
        for old in glob.glob(os.path.join(output_dir, f"*.{ext}")):
            os.unlink(old)
            print(f"  清理: {old}")

    mp3_path = os.path.join(output_dir, f"{stem}.mp3")
    m4a_path = os.path.join(output_dir, f"{stem}.m4a")
    mkv_path = os.path.join(output_dir, f"{stem}.mkv")

    # LRC 放在输出目录
    lrc_path = os.path.join(output_dir, f"{stem}.lrc")
    # SRT 用临时文件（封装后立即删除），防止播放器自动加载外挂字幕导致双层
    srt_fd, srt_path = tempfile.mkstemp(suffix=".srt", prefix=f"{stem}_")
    os.close(srt_fd)

    all_sentences: list[Sentence] = []
    current_offset = 0.0
    silence_ms = 500

    with open(mp3_path, "wb") as mp3_file:
        for i, para in enumerate(paragraphs, 1):
            print(f"  处理第 {i}/{len(paragraphs)} 段: {para[:40]}{'...' if len(para) > 40 else ''}")

            audio_data, sentences, para_duration = await synthesize_paragraph(
                para, voice, rate, silence_between=0.0
            )

            # 写入音频
            mp3_file.write(audio_data)

            # 调整句子时间为全局绝对时间
            for sent in sentences:
                sent.start += current_offset
                sent.end += current_offset
                all_sentences.append(sent)

            # 段落之间加静音间隔
            if i < len(paragraphs):
                silence = generate_silence_ms(silence_ms)
                mp3_file.write(silence)
                current_offset += silence_ms / 1000.0

            current_offset += para_duration

    total_duration = current_offset

    # ---------- 生成 LRC ----------
    # LRC 可不写 metadata 头，但写一个最小化的标签更兼容
    with open(lrc_path, "w", encoding="utf-8") as f:
        f.write(f"[ti:{stem}]\n")
        f.write(f"[ar:Edge-TTS]\n")
        f.write(f"[by:auto]\n")
        for sent in all_sentences:
            f.write(f"{format_lrc_time(sent.start)}{escape_lrc_text(sent.text)}\n")

    print(f"  LRC 生成: {lrc_path}")

    # ---------- 生成 SRT（内部使用，封装后删除） ----------
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, sent in enumerate(all_sentences, 1):
            f.write(f"{i}\n")
            f.write(f"{format_srt_time(sent.start)} --> {format_srt_time(sent.end)}\n")
            f.write(f"{sent.text}\n\n")
    print(f"  SRT 生成: {srt_path}")

    # ---------- 封装 M4A + MKV ----------
    m4a_written = ""
    mkv_written = ""
    if generate_mkv:
        m4a_written = m4a_mux(mp3_path, srt_path, m4a_path)
        mkv_written = mkv_mux(mp3_path, srt_path, mkv_path, duration=total_duration)
        # SRT 是临时文件，封装后立即删除
        try:
            os.unlink(srt_path)
        except OSError:
            pass
        if m4a_written:
            print(f"  封装: {m4a_written}")
        if mkv_written:
            print(f"  封装: {mkv_written}")

    print(f"\n生成完成：")
    print(f"  音频: {mp3_path}")
    print(f"  字幕: {lrc_path}")

    print(f"  总时长: {total_duration:.1f}s  ({len(all_sentences)} 句)")

    # ---------- 拷贝到共享目录 ----------
    shared_dir = SHARED_OUTPUT_DIR
    copied = []
    if shared_dir:
        os.makedirs(shared_dir, exist_ok=True)
        for src_path in (mp3_path, lrc_path):
            if os.path.exists(src_path):
                dst = os.path.join(shared_dir, os.path.basename(src_path))
                shutil.copy2(src_path, dst)
                copied.append(dst)
        if m4a_written and os.path.exists(m4a_written):
            shutil.copy2(m4a_written, os.path.join(shared_dir, os.path.basename(m4a_written)))
            copied.append(m4a_written)
        if mkv_written and os.path.exists(mkv_written):
            shutil.copy2(mkv_written, os.path.join(shared_dir, os.path.basename(mkv_written)))
            copied.append(mkv_written)
        if copied:
            print(f"\n  已拷贝到: {shared_dir}")
            for p in copied:
                print(f"    {p}")

    return mp3_path, lrc_path, (m4a_written, mkv_written)


async def list_voices():
    """列出可用的音色"""
    print("常用音色：")
    print("-" * 50)
    for short, full in POPULAR_VOICES.items():
        print(f"  {short:12s}  ->  {full}")
    print()
    print("正在从微软获取完整音色列表...")

    voices = await edge_tts.list_voices()
    en_voices = [v for v in voices if v["Locale"].startswith("en-")]
    zh_voices = [v for v in voices if v["Locale"].startswith("zh-")]

    print(f"\n共有 {len(en_voices)} 个英文音色：")
    print("-" * 50)
    for v in en_voices[:20]:
        gender = "女" if v["Gender"] == "Female" else "男"
        print(f"  {v['ShortName']:40s}  [{gender}]  {v['Locale']}")

    print(f"\n共有 {len(zh_voices)} 个中文音色：")
    print("-" * 50)
    for v in zh_voices:
        gender = "女" if v["Gender"] == "Female" else "男"
        print(f"  {v['ShortName']:30s}  [{gender}]  {v['Locale']}")


def resolve_voice(voice_arg: str) -> str:
    """解析音色参数，支持简写和全称"""
    if voice_arg in POPULAR_VOICES:
        return POPULAR_VOICES[voice_arg]
    return voice_arg


def m4a_mux(mp3_path: str, srt_path: str, m4a_path: str) -> str:
    """
    将 MP3 音频 + SRT 字幕封装为 M4A 文件（MP4 容器）。

    M4A 容器内嵌字幕轨道，IINA 打开时自动加载字幕，
    无需黑屏视频、无需手动开启，体验与普通音频文件一致。

    Returns:
        生成的 M4A 文件路径
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", mp3_path,
        "-i", srt_path,
        "-c:a", "aac", "-b:a", "128k",       # AAC 音频编码（M4A 标准格式）
        "-c:s", "mov_text",                    # mov_text 字幕（MP4 原生支持）
        "-map", "0:a",
        "-map", "1:s",
        "-metadata:s:s:0", "language=eng",
        "-disposition:s:0", "default",
        m4a_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  M4A封装警告: {result.stderr.strip()}", file=sys.stderr)
            return ""
        size_kb = os.path.getsize(m4a_path) / 1024
        print(f"  M4A 封装完成: {m4a_path} ({size_kb:.0f}KB)")
        return m4a_path
    except FileNotFoundError:
        print("  M4A封装跳过: 未找到 ffmpeg", file=sys.stderr)
        return ""
    except subprocess.TimeoutExpired:
        print("  M4A封装超时", file=sys.stderr)
        return ""


def mkv_mux(mp3_path: str, srt_path: str, mkv_path: str, duration: float = 0.0) -> str:
    """
    用 ffmpeg 将 MP3 音频 + SRT 字幕封装为 MKV 文件。

    采用硬字幕（burn-in）方案：将 SRT 字幕烧录到黑屏视频画面上，
    确保任何播放器（IINA/mpV/VLC）都能正常显示字幕，无需手动开启。

    Returns:
        生成的 MKV 文件路径
    """
    dur = max(duration + 0.5, 5.0) if duration > 0 else 300.0

    # 硬字幕：subtitles 滤镜把 SRT 直接画到视频帧上
    # 字幕样式：白色文字、底部居中、大字号、带黑色描边（抗锯齿）
    force_style = (
        "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000008,BorderStyle=1,Outline=2,"
        "Shadow=1,MarginV=30,Alignment=2"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=960x540:d={dur}:r=4",
        "-i", mp3_path,
        "-vf", f"subtitles='{srt_path}':force_style='{force_style}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-shortest",
        mkv_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  MKV封装警告: {result.stderr.strip()}", file=sys.stderr)
            return ""
        size_kb = os.path.getsize(mkv_path) / 1024
        print(f"  MKV 封装完成: {mkv_path} ({size_kb:.0f}KB)")
        return mkv_path
    except FileNotFoundError:
        print("  MKV封装跳过: 未找到 ffmpeg", file=sys.stderr)
        return ""
    except subprocess.TimeoutExpired:
        print("  MKV封装超时", file=sys.stderr)
        return ""


def main():
    parser = argparse.ArgumentParser(
        description="文案朗读 + LRC/SRT字幕生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用音色简写:
  brian       美国男声，清晰稳重（默认）
  jenny       美国女声，自然地道
  emma        美国女声，柔和

示例:
  python3 text_to_lrc.py -t Kris头像
  python3 text_to_lrc.py -t 我的播客
  python3 text_to_lrc.py --input 其他.txt -t 文件名
  python3 text_to_lrc.py -t 文件名 --mkv   # 生成 M4A + MKV 字幕文件
  python3 text_to_lrc.py --list-voices
        """,
    )

    # 必填：输出文件名
    parser.add_argument("--title", "-t", required=True,
                        help="输出文件名（不含扩展名）")

    # 可选：输入文件路径，默认 test_en.txt
    parser.add_argument("--input", "-i", default="test_en.txt",
                        help="输入纯文本文件路径（默认 test_en.txt）")

    # 可选：输出目录、音色、语速等
    parser.add_argument("--output", "-o", default=".",
                        help="输出目录（默认当前目录）")
    parser.add_argument("--voice", "-v", default="brian",
                        help="TTS音色名称或简写（默认 brian）")
    parser.add_argument("--rate", "-r", default="+0%",
                        help="语速调节，如 +20%% 或 -10%%（默认 +0%%）")
    parser.add_argument("--list-voices", "-l", action="store_true",
                        help="列出所有可用的音色")
    parser.add_argument("--mkv", "-m", action="store_true", default=False,
                        help="生成 M4A + MKV 字幕文件（默认关闭，需 ffmpeg）")

    args = parser.parse_args()

    if args.list_voices:
        asyncio.run(list_voices())
        return

    if not os.path.isfile(args.input):
        print(f"错误：文件不存在 - {args.input}", file=sys.stderr)
        sys.exit(1)

    voice = resolve_voice(args.voice)
    stem = args.title

    print(f"输入: {args.input}")
    print(f"文件名: {stem}")
    print(f"音色: {voice}")
    print(f"语速: {args.rate}")
    print(f"输出: {args.output}")
    print(f"字幕封装: {'是' if args.mkv else '否'}")
    print()

    asyncio.run(text_to_subtitles(
        args.input, args.output, voice, args.rate, stem=stem,
        generate_mkv=args.mkv
    ))


if __name__ == "__main__":
    main()
