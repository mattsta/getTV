#!/usr/bin/env python3

import re
import os
import sys
import time
import bisect
import sqlite3
import argparse
import platform
import datetime
import requests
import subprocess
import configparser
import urllib.parse

from operator import itemgetter

import requests
from requests_toolbelt.adapters.source import SourceAddressAdapter

system = platform.system()


class TorrentApiController:
    def __init__(self, request, proxies={}):
        # API docs: https://torrentapi.org/apidocs_v2.txt
        self.BASE = "https://torrentapi.org/pubapi_v2.php?app_id=getTV&"
        self.token = None
        self.requestsFromSource = request  # must conform to 'requests' API
        self.proxs = proxies
        self.tokenAcquiredAt = None

    def get(self, url):
        return self.requestsFromSource.get(url,
                                           proxies=self.proxs,
                                           timeout=(5, 5))

    def invalidateToken(self):
        self.token = None
        self.tokenAcquiredAt = None

    def isTokenValid(self):
        if self.token:
            # tokens are only valid for 15 minutes
            tokenValidForSeconds = 15 * 60
            now = time.time()
            tokenValid = (now - self.tokenAcquiredAt) < tokenValidForSeconds

            if tokenValid:
                return True

            self.invalidateToken()
            return False
        return False

    def getToken(self):
        if self.isTokenValid():
            return self.token

        TOKEN = {"get_token": "get_token"}
        tokenURL = self.BASE + urllib.parse.urlencode(TOKEN)

        # loop because sometimes the API has temporary failures
        while True:
            try:
                r = self.get(tokenURL)
            except KeyboardInterrupt:
                # Allow CTRL-C during downloads
                raise
            except BaseException:
                print("Connection error when attempting to fetch API token")
                time.sleep(5)
                continue

            self.tokenAcquiredAt = time.time()

            if r.status_code != 200:
                # The API is fronted by cloudflare. Sometimes cloudflare
                # throws up a captcha, which is annoying on remote servers,
                # rendering our automation useless when it happens.
                # Log the complete error page so we know what failed.
                # If you get stuck by a cloudflare captcha block on your server,
                # look into using a proxy instead.  See configuration file
                # options under [network].
                with open("output.html", "w") as err:
                    err.write(r.text)

                sys.exit("Error getting API token. "
                         "Wrote error to output.html")
            j = r.json()
            if "token" in j:
                self.token = j["token"]
                return self.token
            time.sleep(5)

    # There are only two media queries: tv and movies
    def loadCurrentSearchResultsTV(self):
        return self.loadCurrentSearchResults("tv")

    def loadCurrentSearchResultsMovies(self):
        return loadCurrentSearchResults("movies")

    def loadCurrentSearchResults(self, category):
        while True:
            # Generate URL *inside* the while loop because if the token
            # expires during an error condition, we need to generate a
            # new token and a new URL instead of infinite looping with an
            # expired token that'll never return new results.
            MOST_RECENT_100 = {"mode": "list",
                               "category": category,
                               "sort": "last",
                               "limit": "100",
                               "token": self.getToken()}
            mostRecentURL = self.BASE + urllib.parse.urlencode(MOST_RECENT_100)

            try:
                r = self.get(mostRecentURL)
            except KeyboardInterrupt:
                # Allow CTRL-C during downloads
                raise
            except BaseException:
                print("Connection error when attempting to fetch episodes")
                time.sleep(5)
                continue

            if r.status_code == requests.codes.too_many_requests:
                print("Server denied token (known error). Retrying...")
                time.sleep(5)
                continue

            try:
                j = r.json()
            except BaseException:
                print("JSON Parsing error...", r.text)
                time.sleep(5)
                continue

            # Manually check for token error because sometimes 15 minute tokens
            # don't seem to last for 15 minutes.
            if "error" in j:
                self.invalidateToken()
                time.sleep(5)
                continue

            if "torrent_results" in j:
                # Sort results from highest resolution to lowest resolution so
                # if multiple downloads for the same release showing up at once,
                # we'll trigger the higher quality download first.
                # minor bug: only works with same-source, same-group releases.
                #            if filename has multiple releases across multiple
                #            groups, longer filename will sort after shorter
                #            name regardless of prefix/resolution matching.
                results = sorted(j["torrent_results"],
                                 key=itemgetter('filename'))

                # We're consuming exactly the JSON returned by the API without
                # any provider-independent intermediate representation.
                # If the torrentapi.org return values change, we'll need to
                # adjust how we use fields in other part of the code.
                #
                # We only use two fields from 'results' right now:
                #   - 'filename'
                #   - 'download' (the magnet link)
                return results

            print("torrent_results not found in JSON. Retrying.")
            time.sleep(5)


class TVTorrentController:
    def __init__(self, config):
        self.dbFilename = "downloads.db"
        self.showsFilename = "SHOWS"
        self.sourceIP = ""
        self.transmissionHostRemote = ""
        self.userpass = ""
        self.downloadQuality = [720, 1080]
        self.speakDownload = True

        # We don't retain 'proxs' or 'requestsFromSource' in this
        # instance since they get stored/retained inside TorrentApiController
        proxs = {}
        requestsFromSource = requests.Session()

        self.establishConfiguration(config, requestsFromSource, proxs)
        self.torrentController = TorrentApiController(
            requestsFromSource, proxs)

        self.establishDatabase()

    def establishConfiguration(
            self,
            configFilename,
            requestsFromSource,
            proxs):
        config = configparser.SafeConfigParser(allow_no_value=True)

        configFilenameLocal = configFilename + ".local"
        if not (os.path.isfile(configFilename) or
                os.path.isfile(configFilenameLocal)):
            print("Configuration file(s) not found. Running with defaults.")
            return

        try:
            print("Using config files {} and {}".format(configFilename,
                                                        configFilenameLocal))
            config.read([configFilename, configFilenameLocal])
        except BaseException:
            print("Configuration file error. Running with defaults.")
            return

        def getOrNot(section, field):
            return config.get(section, field, fallback=None)

        self.transmissionHostRemote = getOrNot('remote', 'host')

        username = getOrNot('remote', 'username')
        password = getOrNot('remote', 'password')
        self.userpass = "{}:{}".format(username, password)

        self.sourceIP = getOrNot('network', 'fetchFromSourceIP')
        proxs['https'] = getOrNot('network', 'proxy')

        self.dbFilename = getOrNot('files', 'db')
        self.showsFilename = getOrNot('files', 'shows')

        self.speakDownload = config.getboolean('content', 'speakDownload',
                                               fallback=False)

        resolutions = getOrNot('content', 'quality')
        if resolutions:
            # Strip 'p' if users entered '720p 1080p'
            # Convert strings to integers because we compare using math
            self.downloadQuality = [int(r) for r in
                                    resolutions.replace("p", "").split(" ")]

        # Bind specific source interface to https requestor (or not)
        requestsFromSource.mount("https://",
                                 SourceAddressAdapter(self.sourceIP or ""))

    def establishDatabase(self):
        self.conn = sqlite3.connect(self.dbFilename)
        self.c = self.conn.cursor()

        # Always try to create the database; it's a no-op if already exists
        try:
            self.c.execute('''CREATE TABLE episodes
                              (show, episode, quality, reencode,
                               uncensored, westlive,
                               UNIQUE (show, episode, quality, reencode,
                                       uncensored, westlive)
                               ON CONFLICT ABORT)''')
            self.c.execute('''CREATE INDEX epidx ON episodes
                              (show, episode, quality, reencode,
                               uncensored, westlive)''')
            self.conn.commit()
        except BaseException:
            pass

    def fetchEpisodeList(self):
        return self.torrentController.loadCurrentSearchResultsTV()

    def loadShowList(self):
        """ Load local list of shows to download.

        We don't cache results of self.showsFilename parsing because we want
        to pick up any updates to the file each time we search episodes
        (it's a quick operation anyway).
        """

        # if SHOWS.local exists, use it *instead* of SHOWS
        # otherwise, use SHOWS directly (no additional extension added)
        useFilename = self.showsFilename + ".local"
        if not os.path.isfile(useFilename):
            useFilename = self.showsFilename

        s = []
        with open(useFilename, "r") as shows:
            for line in shows:
                # Allow blank lines and start-of-line comments
                if line != "\n" and line[0] != "#":
                    showNameFromFile = line.rstrip()

                    # Show filenames won't have extraneous punctuation
                    # even if it's the proper canonical form of the show name,
                    # so strip filename-interfering punctuation
                    showNameFromFile = re.sub(r"['.]", "", showNameFromFile)

                    # Allow for case insensitive name matches so users
                    # don't have to worry about names like "iZombie, ONeals"
                    # etc
                    s.append(showNameFromFile.lower())

        # Sort here because we use a binary search to narrow down selections
        s.sort()

        return s

    def showEpisodeQualityExtraFromFilename(self, filename):
        """Extract metadata from filename.

        On success, return a tuple of extracted:
            (show, episode, quality, extra, reencode, uncensored, westLive)
        On failure to decode filename to components, return None

        Examples:
        Adventure.Time.S07E02.Varmints.720p.HDTV.x264-W4F[rarbg]
        The.Simpsons.S27E06.PROPER.720p.HDTV.x264-KILLERS[rarbg]
        Stephen.Colbert.2016.09.01.Larry.Wilmore.720p.CBS.WEBRip.AAC2.0.x264-RTN
        The.Simpsons.S27E21.WEST.FEED.720p.HDTV.x264-BATV[rartv]
        Mr.Robot.S02E07.UNCENSORED.1080p.WEB.X264-DEFLATE[rartv]
        """
        show = None
        episode = None

        # quality is 480p by default because 480p downloads have no quality tag,
        # so '480' is the default passthrough value. 720/1080 will override
        # 'quality' because they get extracted from filename details.
        # Also note: we don't have an explicit option for 540p, but 540p will
        #            fallback to the 480p selector.
        quality = 480
        reencode = False
        uncensored = False
        westLive = False

        # Show name is immediately before the S00E00 or date marker
        # (2016.01.01)
        nameMatch = re.match(r"(.*?)\.(\d\d\d\d|S\d\d)", filename)
        if not nameMatch:
            return None

        # Convert show name dot delimiters back to spaces
        show = nameMatch.group(1).replace(".", " ")

        episodeNumber = re.search(r"S\d\dE\d\d", filename)
        episodeDate = re.search(r"\d\d\d\d\.\d\d\.\d\d", filename)
        # Prefer episode numbers, but fall back to dates if that's all we have
        if episodeNumber:
            episode = episodeNumber.group(0)
        elif episodeDate:
            episode = episodeDate.group(0)
        else:
            # else, not a single episode (by S00E00 or by date), so skip.
            # otherwise, this would catch entire season/series
            # compilations tagged as e.g. Show.Name.S03
            return None

        # 480p has no quality tag and we set 480 as the default above, so
        # in the case of 480p, nothing matches and we assume it's not HD
        # (Basically: any resolution *not* in the regex below falls
        #             back to a default of 480p (that includes 540p))
        qualityMatch = re.search(r"(720|1080|2160)", filename)
        if qualityMatch:
            quality = int(qualityMatch.group(1))

        # If filename is a re-encode (REPACK or PROPER), then it's okay
        # to allow a duplicate selection of a previously seen episode
        # because the old one is known to be bad/corrupt/improper.
        reencodeMatch = re.finditer(r"(REPACK|PROPER)", filename)
        reencode = 0
        # Sometimes there's a rare REPACK.PROPER and we'd need to
        # redownload that one too. Just increase re-encode for each
        # re-encode update designator.
        for match in reencodeMatch:
            reencode += 1

        # Also download UNCENSORED episodes as new even if the episode
        # has been previously downloaded. Typically gets posted a few days
        # to a week after the original TV airing
        # (as happens with southpark, mr robot, etc).
        uncensoredMatch = re.search(r"UNCENSORED", filename,
                                    flags=re.IGNORECASE)
        if uncensoredMatch:
            uncensored = True

        # If a show is live, they usually do two versions: one for
        # the east coast (the "regular version") and then again
        # three hours later for the west coast.  The second airing
        # gets tagged with a WEST identifier and the same episode number.
        westLiveMatch = re.search(r"WEST\.FEED", filename)
        if westLiveMatch:
            westLive = True

        return (show, episode, quality, reencode, uncensored, westLive)

    def qualifiesForSelection(self, filename):
        def fileAlreadySelected(details):
            # Check if episode:
            #   - was exactly downloaded already for show+ep
            #     - OR -
            #   - higher quality of 'filename' already downloaded for show+ep
            #     (don't want to download 720p version if already got a 1080p)
            #     - OR -
            #   - needs to download anyway because we have extra tags tag due
            #     to an alternate encoding/release with different material
            if self.c.execute('''SELECT * FROM episodes WHERE
                                 show=? AND
                                 episode=? AND
                                 quality >= ? AND
                                 reencode >= ? AND
                                 uncensored=? AND
                                 westlive=?''', details) and self.c.fetchone():
                return True

            # else, the combination of show+ep (from 'filename') hasn't been
            # seen before.
            return False

        # Process 'filename' and determine if selection should happen
        details = self.showEpisodeQualityExtraFromFilename(filename)
        if details:
            (show, episode, quality, _, _, _) = details
        else:
            return False

        if quality in self.downloadQuality:
            if fileAlreadySelected(details):
                print("Skipping {} {} ({})".format(show, episode, quality))
                return False
            return True

        return False

    def showShouldBeSelected(self, shows, filename):
        """ If 'filename' is valid show at valid quality, allow download. """

        # if shows list is empty, we can't do anything
        if not shows:
            return False

        # Instead of linear lookup with an average 3,500 lookups per run
        # (100 API results * 70 shows in show list = 7,000 lookups, but on
        #  average we'd only need to ask half the list per lookup, so that
        #  ends up being 100 * (70/2) = 3,500)
        # we use sorted show list and binary search for an improved average
        # of 425 lookups per run
        # (100 API results * log(70 shows in show list) = 424.85).
        def showExistsInShowList(shows, filename):
            # lower() because we want case insensitive matches and we already
            # lower()'d the entire SHOWS list when we parsed it.
            # Replacing dots because our show names are space delimited.
            lowerFile = filename.replace(".", " ").lower()

            showPosition = bisect.bisect_left(shows, lowerFile)
            if showPosition != 0:
                # (because filenames are longer than our show names,
                #  filenames always sort +1 higher than the show name itself)
                showPosition -= 1

            # Verify sorted position we found is a reasonable match
            # for the filename given.
            if lowerFile.startswith(shows[showPosition]):
                return True

            return False

        if showExistsInShowList(shows, filename):
            return self.qualifiesForSelection(filename)

        return False

    def selectNewEpisodes(self):
        """ The main selection processor """

        def recordSelection(details):
            self.c.execute('INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?)',
                           details)
            self.conn.commit()

        # Fetch most recent 100 tv torrents from provider
        print("Asking TV torrent API for list of shows ready for download...")
        start = time.time()
        results = self.fetchEpisodeList()
        end = time.time()
        print("Downloaded current episode list in {:.2f} seconds".format(
              (end - start)))

        # Read local SHOWS text file (or its override file)
        start = time.time()
        shows = self.loadShowList()
        end = time.time()
        print("Loaded {1} file in {0:.2f} milliseconds".format(
            (end - start) * 1e3,
            self.showsFilename))

        for result in results:
            filename = result["filename"]

            if self.showShouldBeSelected(shows, filename):
                details = self.showEpisodeQualityExtraFromFilename(filename)
                (show, episode, quality, _, _, _) = details

                # Verify the link is properly formed
                magnetLink = result["download"]
                if not magnetLink.startswith("magnet:?"):
                    continue

                print("Downloading", result["filename"])

                try:
                    if system == "Darwin":
                        # On OS X, open magnet links directly with whichever
                        # app is registered for the filetype with the OS.
                        subprocess.check_call(["/usr/bin/open",
                                               '-g',  # don't bring to foreground
                                               magnetLink])
                        if self.speakDownload:
                            # Note: no error checking here because 'say'
                            # failure doesn't impact link opening success.
                            subprocess.call(["/usr/bin/say",
                                             "Downloading {}".format(show)])
                    elif system == "Linux":
                        # On Linux, connect to transmission-daemon remotely
                        subprocess.check_call(["transmission-remote",
                                               self.transmissionHostRemote,
                                               '-n', self.userpass,
                                               '-a', magnetLink])
                except subprocess.CalledProcessError:
                    # Either opening the link or connecting to remote
                    # transmission instance failed, so don't record this
                    # download as a success yet.
                    # Posting the download will retry again if
                    # the episode is still in the next result set.
                    continue
                recordSelection(details)
        completedAt = str(datetime.datetime.now())
        print("Done processing shows at", completedAt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config",
                        help="config filename (default: tv.conf)",
                        default="tv.conf")
    parser.add_argument("-f",
                        "--forever",
                        help="continuously check for new episodes "
                             "(default: off)",
                        default=False,
                        action="store_true")
    parser.add_argument("-i",
                        "--interval",
                        type=int,
                        help="seconds between new episode queries "
                             " in forever mode (default: 120)",
                        default=120)

    args = parser.parse_args()
    config = args.config

    runner = TVTorrentController(config)
    runner.selectNewEpisodes()

    forever = args.forever
    if forever:
        def countdown(seconds):
            print("")
            for i in range(seconds, 0, -1):
                print(' ' * 44, end='\r')  # clear width of string below
                print('{} seconds until next download attempt...'.format(i),
                      end="\r")
                sys.stdout.flush()
                time.sleep(1)

        intervalToCheckForNewEpisodes = args.interval

        while True:
            countdown(intervalToCheckForNewEpisodes)
            runner.selectNewEpisodes()
