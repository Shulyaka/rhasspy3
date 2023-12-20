#!/usr/bin/env python3
"""Run a pipeline all or part of the way."""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import IO, Optional, Union

from rhasspy3.asr import Transcript
from rhasspy3.audio import DEFAULT_SAMPLES_PER_CHUNK
from rhasspy3.core import Rhasspy
from rhasspy3.event import Event
from rhasspy3.handle import Handled, NotHandled
from rhasspy3.intent import Intent, NotRecognized
from rhasspy3.pipeline import StopAfterDomain
from rhasspy3.pipeline import run as run_pipeline
from rhasspy3.wake import Detection

_FILE = Path(__file__)
_DIR = _FILE.parent
_LOGGER = logging.getLogger(_FILE.stem)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        default=_DIR.parent / "config",
        help="Configuration directory",
    )
    parser.add_argument(
        "-p", "--pipeline", default="default", help="Name of pipeline to use"
    )
    #
    parser.add_argument(
        "--stop-after",
        choices=[domain.value for domain in StopAfterDomain],
        help="Domain to stop pipeline after",
    )
    #
    parser.add_argument(
        "--wake-name", help="Skip wake word detection and use name instead"
    )
    parser.add_argument(
        "--asr-wav",
        help="Use WAV file for speech to text instead of mic input (skips wake)",
    )
    parser.add_argument("--asr-text", help="Use text for asr transcript (skips wake)")
    parser.add_argument(
        "--intent-json", help="Use JSON for recognized intent (skips wake, asr)"
    )
    parser.add_argument(
        "--handle-text", help="Use text for handle response (skips handle)"
    )
    parser.add_argument(
        "--tts-wav", help="Play WAV file instead of text to speech response (skips tts)"
    )

    parser.add_argument(
        "--samples-per-chunk", type=int, default=DEFAULT_SAMPLES_PER_CHUNK
    )
    parser.add_argument("--asr-chunks-to-buffer", type=int, default=0)
    parser.add_argument("--loop", action="store_true", help="Keep pipeline running")
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    wake_detection: Optional[Detection] = None
    if args.wake_name:
        # Wake word detection will be skipped
        wake_detection = Detection(name=args.wake_name)

    asr_wav_in: Optional[IO[bytes]] = None
    if args.asr_wav:
        # asr input will come from WAV file instead of mic
        asr_wav_in = open(args.asr_wav, "rb")

    asr_transcript: Optional[Transcript] = None
    if args.asr_text:
        # asr transcription will be skipped
        asr_transcript = Transcript(text=args.asr_text)

    intent_result: Optional[Union[Intent, NotRecognized]] = None
    if args.intent_json:
        # intent recognition will be skipped
        intent_event = Event.from_dict(json.loads(args.intent_json))
        if Intent.is_type(intent_event.type):
            intent_result = Intent.from_event(intent_event)
        elif NotRecognized.is_type(intent_event.type):
            intent_result = NotRecognized.from_event(intent_event)

    handle_result: Optional[Union[Handled, NotHandled]] = None
    if args.handle_text:
        # text/intent handling will be skipped
        handle_result = Handled(text=args.handle_text)

    tts_wav_in: Optional[IO[bytes]] = None
    if args.tts_wav:
        # tts synthesis will be skipped
        tts_wav_in = open(args.tts_wav, "rb")

    rhasspy = Rhasspy.load(args.config)

    if not args.loop or args.stop_after in ("wake", "asr"):
        while True:
            pipeline_result = await run_pipeline(
                rhasspy,
                args.pipeline,
                samples_per_chunk=args.samples_per_chunk,
                asr_chunks_to_buffer=args.asr_chunks_to_buffer,
                wake_detection=wake_detection,
                asr_wav_in=asr_wav_in,
                asr_transcript=asr_transcript,
                intent_result=intent_result,
                handle_result=handle_result,
                tts_wav_in=tts_wav_in,
                stop_after=args.stop_after,
            )

            json.dump(pipeline_result.to_dict(), sys.stdout, ensure_ascii=False)
            print("")

            if not args.loop:
                break

    response_task = None
    while True:
        request_task = asyncio.create_task(
            run_pipeline(
                rhasspy,
                args.pipeline,
                samples_per_chunk=args.samples_per_chunk,
                asr_chunks_to_buffer=args.asr_chunks_to_buffer,
                wake_detection=wake_detection,
                asr_wav_in=asr_wav_in,
                asr_transcript=asr_transcript,
                stop_after="asr",
            )
        )

        aws = [request_task]
        if response_task is not None and not response_task.done():
            aws.append(response_task)

        while True:
            done, pending = await asyncio.wait(aws, return_when=asyncio.FIRST_COMPLETED)

            if response_task is not None and response_task in done:
                if not response_task.cancelled():
                    pipeline_result = response_task.result()
                json.dump(pipeline_result.to_dict(), sys.stdout, ensure_ascii=False)
                print("")
                response_task = None

            if (
                request_task in done
                and response_task is not None
                and response_task in pending
            ):
                response_task.cancel()

            aws = pending
            if not len(aws):
                break

        pipeline_result = request_task.result()

        if pipeline_result.asr_transcript is None:
            json.dump(pipeline_result.to_dict(), sys.stdout, ensure_ascii=False)
            print("")
            continue

        response_task = asyncio.create_task(
            run_pipeline(
                rhasspy,
                args.pipeline,
                samples_per_chunk=args.samples_per_chunk,
                asr_chunks_to_buffer=args.asr_chunks_to_buffer,
                wake_detection=pipeline_result.wake_detection,
                asr_transcript=pipeline_result.asr_transcript,
                intent_result=intent_result,
                handle_result=handle_result,
                tts_wav_in=tts_wav_in,
                stop_after=args.stop_after,
            )
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
