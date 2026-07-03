#!/usr/bin/env python3
"""Offline test: simulates a show night in three polls (early, mid, sign-off)
without touching the network or Telegram. Run: python test_local.py"""
import os, shutil, sys

import bot

P = lambda s: f"<p>{s}</p>"
B = lambda s: f"<p><strong>{s}</strong></p>"

INTRO = (
    P("Hey kids! Steve Cook here with you for the latest edition of WWE Raw on "
      "Netflix! It's a double taping night in Atlantic City so everyone can go "
      "home for the holiday weekend.")
    + P("Don't forget to subscribe to the 411Mania Wrestling Newsletter! Click "
        "here to do so, and then every Tuesday you'll get exclusive insight.")
    + P("We're in Atlantic City! Boardwalk Hall is sold out! We look back at "
        "Saturday's Night of Champions, where IYO SKY and Oba Femi won crowns.")
    + P("Oba Femi's music hits! The fans chant Oba's name as he struts down to "
        "the ring with the King of the Ring crown. He issues the challenge for "
        "SummerSlam! Brock accepts, on one condition: Hell in a Cell!")
)
MID = (
    B("Rey Mysterio vs. &#8220;All Ego&#8221; Ethan Page:")
    + P("Page with a headlock, Rey sends him off the ropes and gets shoulder "
        "blocked down. Rey hits the flying headscissors off the ropes. 619, "
        "then Rey goes up top and hits a frog splash for three!")
    + B("Winner: Rey Mysterio (7:41 shown via pinfall)")
    + P("Dom and JD enter Danhausen's lab looking for their money. Dom finds a "
        "present for Danhausen from the Knicks and decides to steal it.")
)
END = (
    B("LA Knight vs. Jimmy Uso (w/Jey Uso):")
    + P("Jimmy rolls LA up for the three count after a distraction from Jey on "
        "the apron. The fans do not love that at all tonight.")
    + B("Winner: Jimmy Uso (8:15 shown via pinfall)")
    + P("Roman says he needs to beat Seth at SummerSlam and needs his entire "
        "family to witness it. Roman accepts, brother. Roman holds up the World "
        "Championship in front of Seth as our live event comes to an end. So "
        "long for now!")
)

def page(body):
    return f"<html><body><article><h1>Join 411's Live WWE Raw Coverage</h1><div class='entry-content'>{body}</div></article></body></html>"

STAGES = [page(INTRO), page(INTRO + MID), page(INTRO + MID + END)]
stage = {"i": 0}

bot.find_coverage_url = lambda *a, **k: "https://411mania.com/wrestling/test/"
bot.fetch = lambda url: STAGES[stage["i"]]
os.environ.pop("ANTHROPIC_API_KEY", None)  # test the no-LLM fallback path

shutil.rmtree(bot.STATE_DIR, ignore_errors=True)

for i in range(3):
    stage["i"] = i
    print(f"\n{'='*20} POLL {i+1} {'='*20}")
    rc = bot.run(force_show="raw", dry_run=True)
    assert rc == 0, f"poll {i+1} returned {rc}"

# a 4th poll after recap should do nothing
stage["i"] = 2
print(f"\n{'='*20} POLL 4 (post-recap) {'='*20}")
assert bot.run(force_show="raw", dry_run=True) == 0

shutil.rmtree(bot.STATE_DIR, ignore_errors=True)
print("\nAll stages completed OK ✔")
