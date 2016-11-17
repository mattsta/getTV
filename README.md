getTV: an automated TV episode getter
=====================================

getTV uses public APIs (currently only one) to find magnet links for episodes
of TV shows after they air.

How to Use (quick, defaults)
----------
Run `getTV.py`.

`getTV` has two running modes:

- run once (default)
- run forever (run with `-f`) with a delay (`-i`) between query attempts

If you want to run `getTV` from cron or your own job system, use the default
mode.  Otherwise, it's useful to run `getTV` continuously and let it self-manage
download attempts.

If you want your own show list, create `SHOWS.local` and add your show
names there. If `SHOWS.local` exists, it will be used instead of
the defaults in `SHOWS`.

Features
--------

### resolution specific download hierarchy

- supports: 480p, 720p, 1080p, 2160p
- specify multiple resolutions you want to download in the configuration file
- e.g. 720 1080
    - if a 1080p version is available before a 720p version, the 1080p
version will download first and the 720p version will be ignored because
a higher quality version was already selected for download.
    - the main reason to have multiple resolutions is sometimes lower resolution
versions appear before higher resolution versions.  If you're trying to
watch shows the same night they air, you may not want to wait the extra 20-60 minutes
for a higher quality version.  Though, over the past few years, 1080p versions are
often being posted first for many releases.  Plus, some shows _only_ appear as 720p
until released on media months later.  More resolutions tend to get you a wider
selection quicker.

### non-duplication of downloads

- `getTV` maintains an internal database of previously accepted episodes
to prevent duplicate downloads. `getTV` will select episodes at your
preferred resolution(s) only once per resolution even if multiple releases
and versions appear.
    - example: a recent episode of The Simpsons had 7 versions posted:
```
The.Simpsons.S28E07.WEB-DL.x264-RARBG
The.Simpsons.S28E07.Havana.Wild.Weekend.720p.WEB-DL.DD5.1.H264-iT00NZ[rartv]
The.Simpsons.S28E07.Havana.Wild.Weekend.1080p.WEB-DL.DD5.1.H264-iT00NZ[rartv]
The.Simpsons.S28E07.1080p.HDTV.x264-CROOKS[rartv]
The.Simpsons.S28E07.HDTV.x264-KILLERS[ettv]
The.Simpsons.S28E07.HDTV.x264-KILLERS[rartv]
The.Simpsons.S28E07.720p.HDTV.x264-KILLERS[rartv]
```

You don't want seven copies of the same episode, so `getTV`:

- first filters by resolution(s) you want downloaded
- then selects your preferred resolution out of the filtered set
- then downloads one episode at the highest resolution available

If your resolution selector is "720 1080" you may still often end up with
both 720p and 1080p downloads because it's common for 720p downloads to
appear minutes/hours/days before 1080p downloads.  We can only select
the highest resolution we've seen _at the time an episode appears_ since
we don't have future oracle vision.

`getTV` remembers which shows and episode numbers it downloads. Only
the first available versions at your preferred resolutions will launch
a new download (though `getTV` *will* re-download episodes in two cases:
a PROPER release is posted fixing previous encoding problems and also
if an UNCENSORED language version is posted (i.e. no bleeps added)).

Non-Features
------------

`getTV` is *not* a historical show downloader.  It only processes lists of the
100 most recently posted episodes in publicly visible archives.

The goal is to enable unattended episode downloading of same-day shows shortly
after they air.

If you can't run `getTV` locally within a few hours of shows airing, it probably
won't see your shows since they'll scroll out of the most recently posted 100
episodes.  The only solution there is to run `getTV` on a remote server capable
of running in forever query mode.

How to Use (full)
----------
`getTV` is known to work against `python3.4` and `python3.5`.

You may need `pip3 install -r requirements.txt` to pick up dependencies.

### Command Line Arguments
```haskell
% ./getTV.py -h
usage: getTV.py [-h] [-c CONFIG] [-f] [-i INTERVAL]

optional arguments:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        config filename (default: tv.conf)
  -f, --forever         continuously check for new episodes (default: off)
  -i INTERVAL, --interval INTERVAL
                        seconds between new episode queries in forever mode
                        (default: 120)
```

### Configuration

The base configuration file is `tv.conf`.  You can copy `tv.conf` to `tv.conf.local`
and make any changes there so your settings won't conflict with future repository updates.

You can specify a specific config filename with the `-c` option, but
`[filename]` and `[filename].local`
will always both be consulted (and both files are optional if you are fine with
the in-code defaults).  Any options in the `.local` config *overwrite* the same setting
in the default `.conf`-only file.

The configuration file specifies locations of two additional files:

- `SHOWS` is a text file with show names to download.
    - one show per line
    - shows are prefix-matched, so "The Sim" would match "The Simpsons"
    - If you create `SHOWS.local`, the `.local` file will be used *instead* of `SHOWS`.
        - (Not modifying the repository-maintained `SHOWS` file
           can help with updates since the repository
           will never include a `.local` file)
- `downloads.db` is a sqlite3 database recording previous downloads
    - the database is required so we don't duplicate selections across each run

The configuration file does *not* specify a download location where files go
because `getTV` does not download anything — it just passes magnet links
to another application.

### OS X
On OS X, `getTV` opens magnet links directly with whichever app you've registered to
handle magnet links.  For the best experience, configure your torrent application with
start-on-add and with a default download location so you don't get popups on every addition.

Configuration option `speakDownload` enables or disables audible notices of new
episode downloads. (enabled by default, just 'cause)

### Linux
On Linux, `getTV` uses `transmission-remote` to add the magnet link
to a `transmission-daemon` instance.

Set login details matching your `transmission-daemon` instance in the configuration file.


Gotchas
-------
The current API `getTV` uses is fronted by cloudflare and can throw up annoying javascript
or image captchas rendering your server-side scripts useless. If cloudflare decides
to hate you, you can workaround it by:

- using proxy servers cloudflare doesn't hate (see configuration file)
- running `getTV` on a remote server without cloudflare blocks, then point
the `transmission-remote` login details back to your main download server

The current show API doesn't pick up _all_ shows because not all shows get
posted.  If you need certain non-US shows
or some animated series not showing up in the feed, you'll have to continue
using alternative sources for now (tvteam, loadstone2k12, horriblesubs).

`getTV` doesn't distinguish between TV and WEB releases or overall bitrate
quality.  Downloads are only selected based on resolution tagged in the filename.

Updates
-------
Code changes welcome.

The main feature missing is pulling from multiple listing sources.  We can eventually add multiple API providers for wider show coverage (e.g. parse TPB user pages sorted by date for alternative public feeds).

One meta-feature could also be a common service to front these APIs. An external service would run the API collector for real-time feeds then publish a combined feed with all recent episodes from multiple sources.  Some services already attempt to do that (plus weird RSS formats), but their show/format selections tend to be weak, their long term maintenance dodgy, and they don't solve the no-duplicate-downloads problem.  The meta-service could also run a high performance publish/subscribe front-end so subscribers wouldn't have to poll the service, but instead register their interest in a few dozen shows and get real-time push notifications of magnet links when new episodes of interest appear (then on the user-side, the script would perform deduplication of previous downloads as necessary).

Since `getTV` uses external API providers for episode listings,
there's no control over *when* or even *if* shows appear.
Sometimes shows are late (or sometimes they are early if
airing in weird canadian timezones), but in any case, we can't control
selection or quality. `getTV` just consumes public streams from kind upstream
episode providers.
