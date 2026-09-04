# Setting up the DFI Video Maker

You only do this once. After that you just double-click **run** and it opens.

Written for a Mac. Nothing here needs any coding knowledge — it's copy, paste,
press return.

---

## Before you start

You need two things installed. Most likely you have neither, and that's fine.

### 1. Open Terminal

Press **⌘ + space**, type `Terminal`, press return. A window with text in it
opens. That's where the instructions below get pasted.

### 2. Install Homebrew

Homebrew is the thing that installs other things. Paste this into Terminal and
press return:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It will ask for your Mac password. Typing it shows nothing on screen — that's
normal, just type it and press return. It takes a few minutes.

*Already have Homebrew? Skip this.*

### 3. Install ffmpeg

ffmpeg is what actually makes the video. Paste this and press return:

```
brew install ffmpeg
```

A few minutes again.

---

## Getting the app

Paste this. It downloads the app into a folder called `dfi-video-maker` in your
Documents:

```
cd ~/Documents && git clone https://github.com/tiredlabrador/dfi-video-maker.git && cd dfi-video-maker && ./run
```

The first run sets itself up and then opens the app in your browser.

---

## Using it after that

Open the `dfi-video-maker` folder in Documents and **double-click `run`**.

If double-clicking opens a text editor instead of running it, do this once:
right-click `run` → **Open With** → **Other…** → turn "Enable" to **All
Applications** → choose **Terminal** → tick **Always Open With**.

A Terminal window appears and the app opens in your browser. **Leave the
Terminal window open** while you're using it — that window *is* the app. Press
**Ctrl + C** in it when you're done.

---

## Updates

There aren't any to install. Every time you run it, it fetches the latest
version from GitHub first. Whatever Dom has pushed is what you get.

---

## The font

The videos use the brand font, **Squid Boy**. It's a licensed font, so it can't
be included in a public repo — it isn't in the download.

Without it the app still works, but captions come out in a default font instead.
To fix that, put `SquidBoy.otf` into the `assets/fonts` folder inside
`dfi-video-maker`. Ask Dom for the file.

---

## When something goes wrong

**"ffmpeg isn't installed"** — go back and do step 3.

**The browser says it can't connect** — the Terminal window has probably been
closed. Double-click `run` again.

**"command not found: git"** — paste `xcode-select --install` and press return,
accept the prompt, wait for it to finish, then try again.

**Captions are in the wrong font** — see *The font* above.

**Anything else** — copy whatever the Terminal window says and send it to Dom.
The error text is the useful bit.
