# spurlos

*German for "without a trace".*

Every company that has ever sent you an email has your address, and usually much more.
spurlos finds them, asks them what they store about you and gets it deleted under the GDPR.
It runs on your own machine: no account, no server, no third party that you have to hand
your data to so that it can be deleted.

> Status: early. Day 1 of the build is done: reading your mailbox and listing the
> organisations that have your address. Requests, reply tracking and the agents follow,
> see [Roadmap](#roadmap).

## What it does today

```
$ spurlos demo
wrote 215 demo messages to data/demo.mbox, 215 imported into ~/.spurlos/vault.db

$ spurlos census
12 organisations have your email address (from 215 messages, 10 from private mailboxes left out).
7 of them have not written for over two years: likely forgotten accounts.

organisation                 domain                     mails  first       last        newsletter
CodeCamp Online              codecamp.io                   28  2017-11-12  2020-10-13  75%
Kartenhaus Games             kartenhaus-games.com          27  2014-10-25  2016-09-29  0%
Apotheke Rosengarten         apotheke-rosengarten.de       24  2024-11-05  2026-09-27  0%
...
```

The demo mailbox is made up. Run it on your own mail to see your real number.

## Privacy by design

- **Headers only.** Over IMAP, spurlos asks the server for five header fields
  (`From`, `Date`, `Subject`, `Message-ID`, `List-Unsubscribe`) with `BODY.PEEK`, so no
  message body is downloaded and nothing is marked as read. The mailbox is opened read-only.
  A test checks exactly this.
- **Local only.** Everything goes into one SQLite file, `~/.spurlos/vault.db`
  (or `$SPURLOS_HOME`), readable only by you. Nothing is uploaded anywhere.
- **Your own data only.** spurlos works on your own mailbox. It does not search for other
  people and will not send requests in anyone else's name.

## Install

```
pip install git+https://github.com/ihkuzu/spurlos.git
```

Python 3.10 or newer. Works on Linux, macOS and Windows.

## Use it on your mail

**From a file.** Export your mail as mbox (Google Takeout, Thunderbird with the
ImportExportTools add-on) or as a folder of `.eml` files:

```
spurlos import mbox ~/Downloads/All-mail.mbox
spurlos import eml ~/mail-export/
```

**Over IMAP.** Most providers need an app password for this (Gmail: Google account →
Security → App passwords). The password is read from `SPURLOS_IMAP_PASSWORD` or asked for
and is never stored.

```
spurlos import imap --host imap.gmail.com --user you@gmail.com --list-folders
spurlos import imap --host imap.gmail.com --user you@gmail.com --folder "[Gmail]/All Mail"
spurlos import imap --host imap.gmx.net --user you@gmx.de --all-folders --since 2015-01-01
```

Then:

```
spurlos census              # top 25
spurlos census --limit 0    # all
spurlos census --json       # for other tools
spurlos me other@address.de # leave out more of your own addresses
```

Mails from private mailboxes (gmail.com, web.de, gmx.de, ...) are counted as people and
left out of the list.

## Roadmap

1. Read the mailbox and list the organisations (done)
2. An open registry of companies and their privacy contacts, plus signals for real accounts
   (welcome mails, orders, password resets) and a local model that sorts them into categories
3. GDPR requests: access (Art. 15), erasure (Art. 17) and objection (Art. 21) in German and
   English, from fixed legal templates, sent from your own address only after you confirm
4. A reply agent that matches answers to requests and tells you what happened: confirmed,
   identity check, refused, data attached or no answer
5. Reminders after the one-month deadline and a complaint draft for the right data
   protection authority
6. Reading the data exports companies send back, so you can see who knows what about you
7. Docs, demo and a first release

## Not legal advice

spurlos helps you use rights the GDPR gives you. It is not a lawyer, and the letters it
prepares are templates you check before you send them.

## License

MIT
