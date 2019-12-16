#!/usr/bin/env python3

import requests
import urllib.request
from bs4 import BeautifulSoup
import bs4.element

import time
import sys

BASE = "https://rarbg.to"
SHOWS_AT = f"{BASE}/torrents.php?category=18;41"
NEXT_PAGE = "&page="

PAGES_BACK = 4


def urlForIdx(pidx):
    return f"{SHOWS_AT}{NEXT_PAGE}{pidx}"


def urlForEpisode(part):
    return f"{BASE}{part}"


def get(url):
    print("Fetching", url)

    fakeHeader = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_5) AppleWebKit/603.2.4 (KHTML, like Gecko) Version/10.1.1 Safari/603.2.4",
        "Cookies": "", # You could paste your browser cookies here...
    }

    got = requests.get(url, timeout=(5, 5), headers=fakeHeader).text
    time.sleep(0.5)  # rate limit

    # Debug
    with open(f"{time.process_time()}.html", "w") as gu:
        gu.write(got)

    return got


def parse(response):
    return BeautifulSoup(response, "html.parser")


def magnetLinkFromURL(url):
    pageText = get(url)
    s = parse(pageText)

    torrentLinks = s.select('a[href^="magnet:"]')

    # There *should* only be one magnet link on any given result page
    return torrentLinks[0]["href"]


def fetchEpisodeList():
    episodePage = []
    for pidx in range(1, PAGES_BACK + 1):
        # Get index page for page number requested...
        url = urlForIdx(pidx)
        response = get(url)
        s = parse(response)

        # Yes, this selector is weird because their page layout is multiple nested
        # tables, so selecting by target value is easier than navigating tree DOM
        torrentLinks = s.select('a[title][onmouseover][onmouseout][href^="/torrent/"]')

        # The above selector also includes "recent movie" links, but we only want
        # tv shows. Luckily the movie links are images, so elements with sub-tags
        # are not episode links.
        showLinks = filter(
            lambda x: not isinstance(x.contents[0], bs4.element.Tag), torrentLinks
        )

        if not showLinks:
            print(
                "No shows found on page! Did you get a verification/captcha/cookie error?"
            )
            sys.exit(1)

        # For each episode we found, process based on our criteria
        for show in showLinks:
            name = show.contents[0]
            resultPage = show["href"]
            episodePage.append(
                {"filename": name, "episodePage": urlForEpisode(resultPage)}
            )

    print("Full result:", episodePage)
    return episodePage
