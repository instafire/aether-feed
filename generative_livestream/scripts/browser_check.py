"""Headless check: does the MSE player stitch clips and keep playing?

Usage: python3 scripts/browser_check.py http://localhost:8090   (backend mode)
       python3 scripts/browser_check.py http://localhost:8091   (static demo mode)
"""

from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright


async def main(url: str) -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        logs: list[str] = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        info = await page.evaluate(
            """() => ({
                mse: !!window.MediaSource,
                h264: window.MediaSource ? MediaSource.isTypeSupported('video/mp4; codecs="avc1.4d4028"') : null,
                mode: document.getElementById('stateLabel').textContent,
            })"""
        )
        print("env:", json.dumps(info))
        samples = []
        rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        beat_seen = False
        for _ in range(rounds):
            await page.wait_for_timeout(2000)
            s = await page.evaluate(
                """() => { const v = document.getElementById('feed');
                    const b = v.buffered; const end = b.length ? b.end(b.length-1) : 0;
                    return { t: +v.currentTime.toFixed(2), bufEnd: +end.toFixed(2), ranges: b.length,
                             paused: v.paused, readyState: v.readyState, err: v.error ? v.error.code : null,
                             state: document.getElementById('stateLabel').textContent,
                             now: document.getElementById('nowPlaying').textContent }; }"""
            )
            samples.append(s)
            if _ % 4 == 0 or s["state"] in ("SPLICING", "PLAYING_GENERATED") or s["err"]:
                print(json.dumps(s))
            if s["state"] == "PLAYING_GENERATED" and not beat_seen:
                beat_seen = True
                await page.wait_for_timeout(2500)
                await page.screenshot(path="/home/user/player_generated.png")
                print("BEAT_ON_AIR", json.dumps(s))
            if _ == 3:
                await page.fill("#promptInput", "night falls over the market")
                await page.click("#sendBtn")
                print("-> prompt sent")
        await page.screenshot(path="/home/user/player_check.png")
        await browser.close()
        for line in logs[-15:]:
            print("console", line)
        advancing = samples[-1]["t"] > samples[0]["t"] + 10
        contiguous = all(s["ranges"] <= 1 for s in samples if s["ranges"])
        print("PLAYHEAD_ADVANCING", advancing, "SINGLE_CONTIGUOUS_RANGE", contiguous)
        return 0 if advancing and contiguous else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090")))
