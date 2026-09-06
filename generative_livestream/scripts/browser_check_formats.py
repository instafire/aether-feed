"""Headless check for audience UI + live format switch.

python3 scripts/browser_check_formats.py http://localhost:8092
"""

from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright

SAMPLE = """() => { const v = document.querySelector('#feed'); const b = v.buffered; const end = b.length ? b.end(b.length-1) : 0;
  return { t: +v.currentTime.toFixed(1), bufEnd: +end.toFixed(1), ranges: b.length, paused: v.paused, err: v.error ? v.error.code : null,
           vw: v.videoWidth, vh: v.videoHeight, fmt: document.getElementById('app').dataset.format,
           state: document.getElementById('stateLabel').textContent, chat: document.querySelectorAll('#chatList li').length,
           gifts: document.querySelectorAll('.gift-card').length, energy: document.getElementById('energyLabel').textContent,
           vote: document.getElementById('voteCard').hidden ? null : document.getElementById('votePrompt').textContent }; }"""


async def main(url: str) -> int:
    ok = True
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)
        s = await page.evaluate(SAMPLE)
        print("landscape:", json.dumps(s))
        ok &= s["t"] > 2 and s["ranges"] == 1 and not s["err"]

        await page.click("#simToggle")
        await page.click("#sendGiftBig")
        await page.wait_for_timeout(9000)
        s = await page.evaluate(SAMPLE)
        print("audience:", json.dumps(s))
        ok &= s["chat"] >= 2 and s["energy"] in ("medium", "high", "calm")
        await page.screenshot(path="/home/user/check_landscape_audience.png")

        await page.select_option("#formatSelect", "portrait")
        t0 = None
        for i in range(30):
            await page.wait_for_timeout(2000)
            s = await page.evaluate(SAMPLE)
            if s["fmt"] == "portrait" and s["vh"] > s["vw"] and s["t"] > 1 and not s["paused"]:
                t0 = s
                break
        print("portrait:", json.dumps(s))
        ok &= t0 is not None
        await page.wait_for_timeout(8000)
        s2 = await page.evaluate(SAMPLE)
        print("portrait+8s:", json.dumps(s2))
        ok &= s2["t"] > s["t"] + 5 and s2["ranges"] == 1 and not s2["err"] and s2["vh"] > s2["vw"]
        await page.screenshot(path="/home/user/check_portrait.png")
        await browser.close()
    for e in errors:
        print("pageerror:", e)
    ok &= not errors
    print("FORMATS_OK", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080")))
