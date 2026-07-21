"""豆包 TTS「假小子 2.0」双向流式语音合成（小博的嗓音）。

走火山 openspeech bidirection WebSocket + 新版 X-Api-Key：
- resource_id 固定 ``seed-tts-2.0``（ICL 复刻音色必须用这个）
- speaker 固定 ``ICL_uranus_zh_female_jiaxiaozi_tob``（假小子 2.0，少年感嗓音）
- API key 从环境变量 ``DOUBAO_TTS_API_KEY`` 读（服务器写在 ~/.lebotclaw/.env，
  由 AppRuntime._load_env 注入），**不入库、不进 git**

两个实测致命坑（来自《豆包TTS假小子2.0调用教程.md》）：
1. TaskRequest 的文本必须包在 ``{"req_params": {"text": ...}}`` 里，
   顶层 ``{"text": ...}`` 会让服务器收到空文本 → 0 音频；
2. 发完 TaskRequest 要**立刻**发 FinishSession，否则服务器挂等更多文本直到超时。
"""
import asyncio
import json
import os
import uuid

from lebotclaw.web import tts_protocols as proto

_API_KEY = os.environ.get("DOUBAO_TTS_API_KEY", "")
_RESOURCE_ID = "seed-tts-2.0"
# 音色/音调可用环境变量覆盖（换音色不用改代码）：DOUBAO_TTS_SPEAKER / DOUBAO_TTS_PITCH
_SPEAKER = os.environ.get("DOUBAO_TTS_SPEAKER", "ICL_uranus_zh_female_jiaxiaozi_tob")
_PITCH = int(os.environ.get("DOUBAO_TTS_PITCH", "0"))  # [-12,12]，负数压更低沉，仅 ICL/mix 音色有效
_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"

# websockets 12.x 用 extra_headers，13.0+ 改名 additional_headers（服务器是 15.x）
try:
    import inspect

    import websockets

    _HDR_KW = ("additional_headers"
               if "additional_headers" in inspect.signature(websockets.connect).parameters
               else "extra_headers")
except Exception:  # pragma: no cover - websockets 必然已装（NiceGUI 依赖）
    _HDR_KW = "additional_headers"


def available() -> bool:
    """配了 DOUBAO_TTS_API_KEY 才启用豆包，否则调用方回退 edge-tts。"""
    return bool(_API_KEY)


async def synth(text: str, timeout: float = 25.0) -> bytes:
    """合成一段文本为 mp3 bytes。失败/无音频抛异常，由调用方回退。"""
    import websockets

    session_id = str(uuid.uuid4())
    headers = {
        "X-Api-Key": _API_KEY,
        "X-Api-Resource-Id": _RESOURCE_ID,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }
    audio = bytearray()

    async with websockets.connect(
        _URL,
        **{_HDR_KW: headers},
        max_size=8 * 1024 * 1024,
        open_timeout=15, close_timeout=10, ping_interval=20,
    ) as ws:
        # ① StartConnection → 等 ConnectionStarted
        await proto.start_connection(ws)
        msg = await asyncio.wait_for(proto.receive_message(ws), timeout=15)
        if msg.type == proto.MsgType.Error or msg.event == proto.EventType.ConnectionFailed:
            raise RuntimeError("doubao tts: connection failed")

        # ② StartSession（req_params 带 speaker + audio_params，不带 text）
        audio_params = {"format": "mp3", "sample_rate": 24000}
        if _PITCH:
            audio_params["pitch_rate"] = _PITCH
        payload = json.dumps({
            "user": {"uid": session_id},
            "namespace": "BidirectionalTTS",
            "req_params": {
                "speaker": _SPEAKER,
                "audio_params": audio_params,
            },
        }).encode()
        await proto.start_session(ws, payload, session_id)
        while True:
            msg = await asyncio.wait_for(proto.receive_message(ws), timeout=10)
            if msg.type == proto.MsgType.Error or msg.event == proto.EventType.SessionFailed:
                detail = msg.payload[:150].decode("utf-8", "ignore") if msg.payload else ""
                raise RuntimeError(f"doubao tts: session failed {detail}")
            if msg.type == proto.MsgType.AudioOnlyServer and msg.payload:
                audio += msg.payload
            if msg.event == proto.EventType.SessionStarted:
                break

        # ③ TaskRequest(text 包 req_params 里！) → ★立刻★ FinishSession
        await proto.task_request(
            ws, json.dumps({"req_params": {"text": text}}).encode(), session_id)
        await proto.finish_session(ws, session_id)

        # ④ 收音频直到 SessionFinished / ConnectionFinished
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                msg = await asyncio.wait_for(proto.receive_message(ws), timeout=15)
            except asyncio.TimeoutError:
                break
            if msg.type == proto.MsgType.Error:
                raise RuntimeError(f"doubao tts: error {msg.error_code}")
            if msg.type == proto.MsgType.AudioOnlyServer and msg.payload:
                audio += msg.payload
            if msg.event in (proto.EventType.SessionFinished, proto.EventType.ConnectionFinished):
                break
        try:
            await proto.finish_connection(ws)
        except Exception:
            pass

    if len(audio) < 1000:
        raise RuntimeError(f"doubao tts: no audio ({len(audio)}B)")
    return bytes(audio)
